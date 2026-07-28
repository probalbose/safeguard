"""Feature enrichment.

A classifier sees one message. A moderation system sees an actor's behaviour
over time, and that difference is where most real abuse is caught. Coordinated
spam, brigading, and ban evasion are invisible in any single message and
obvious in the velocity of the account posting it.

So the pipeline's first stage attaches counters to every request: posts in the
last minute, hour, and day. These are the same velocity features a payment
authorisation system computes on a card before scoring the transaction, and
they are computed the same way — fixed-size time buckets in a fast key-value
store, incremented on write, expired automatically.

Two implementations, one interface:

``InMemoryFeatureStore``
    Zero dependencies, correct for a single process. Right for development and
    tests; wrong for more than one replica, because each replica would see only
    its own share of an actor's traffic.

``RedisFeatureStore``
    Shared state across replicas. Required in production, where the whole point
    is that an actor spraying traffic across ten pods is still counted once.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from typing import Any

from safeguard.core.models import ModerationRequest

logger = logging.getLogger(__name__)

#: (feature suffix, window length in seconds)
VELOCITY_WINDOWS: tuple[tuple[str, int], ...] = (
    ("1m", 60),
    ("1h", 3_600),
    ("1d", 86_400),
)


class FeatureStore(ABC):
    """Computes and persists the behavioural features a decision needs."""

    @abstractmethod
    async def enrich(self, request: ModerationRequest) -> dict[str, Any]:
        """Return the feature dictionary for this request.

        Enrichment must never raise. Features improve a decision; their absence
        must not prevent one. Implementations catch their own backend errors and
        return ``{"features_degraded": True}`` so the pipeline can record that
        the decision was made on partial information.
        """

    async def aclose(self) -> None:
        return None


class InMemoryFeatureStore(FeatureStore):
    """Single-process sliding-window counters.

    Uses exact timestamp deques rather than Redis-style fixed buckets: within
    one process the memory cost is trivial and exact windows make the tests
    that assert on velocity deterministic.
    """

    def __init__(self, max_actors: int = 100_000) -> None:
        self._events: defaultdict[str, deque[float]] = defaultdict(deque)
        self._max_actors = max_actors
        self._longest_window = max(seconds for _, seconds in VELOCITY_WINDOWS)

    async def enrich(self, request: ModerationRequest) -> dict[str, Any]:
        features: dict[str, Any] = {
            "content_length": len(request.content),
            "content_type": request.content_type.value,
        }

        actor = request.actor
        if actor is None:
            return features

        now = time.monotonic()
        events = self._events[actor.id]
        events.append(now)

        # Drop anything outside the widest window; nothing else can need it.
        cutoff = now - self._longest_window
        while events and events[0] < cutoff:
            events.popleft()

        for suffix, seconds in VELOCITY_WINDOWS:
            window_start = now - seconds
            features[f"actor_events_{suffix}"] = sum(1 for ts in events if ts >= window_start)

        features["actor_prior_violations"] = actor.prior_violations
        features["actor_account_age_days"] = actor.account_age_days
        features["actor_trusted"] = actor.trusted

        self._evict_if_needed()
        return features

    def _evict_if_needed(self) -> None:
        """Bound memory. Unbounded actor maps are how this class becomes a leak."""
        if len(self._events) <= self._max_actors:
            return
        overflow = len(self._events) - self._max_actors
        # Least-recently-active first.
        stalest = sorted(self._events, key=lambda k: self._events[k][-1])[:overflow]
        for actor_id in stalest:
            del self._events[actor_id]

    def reset(self) -> None:
        """Clear all state. Test helper."""
        self._events.clear()


class RedisFeatureStore(FeatureStore):
    """Shared velocity counters backed by Redis.

    Windows are approximated with fixed buckets — key ``sg:v:{actor}:{window}:
    {bucket_index}``, incremented and expired at twice the window length. The
    approximation costs boundary precision and buys a single round trip with no
    read-modify-write, which is the trade every production rate limiter makes.
    """

    def __init__(self, url: str, *, key_prefix: str = "sg:v", timeout_s: float = 0.05) -> None:
        try:
            from redis.asyncio import Redis
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError(
                "RedisFeatureStore requires the redis extra: pip install 'safeguard[redis]'"
            ) from exc

        self._redis = Redis.from_url(url, decode_responses=True)
        self._prefix = key_prefix
        self._timeout_s = timeout_s

    async def enrich(self, request: ModerationRequest) -> dict[str, Any]:
        features: dict[str, Any] = {
            "content_length": len(request.content),
            "content_type": request.content_type.value,
        }

        actor = request.actor
        if actor is None:
            return features

        features["actor_prior_violations"] = actor.prior_violations
        features["actor_account_age_days"] = actor.account_age_days
        features["actor_trusted"] = actor.trusted

        try:
            now = int(time.time())
            pipe = self._redis.pipeline(transaction=False)
            for suffix, seconds in VELOCITY_WINDOWS:
                key = f"{self._prefix}:{actor.id}:{suffix}:{now // seconds}"
                pipe.incr(key)
                pipe.expire(key, seconds * 2)
            results = await pipe.execute()
        except Exception:
            # The store is an optimisation, never a dependency of correctness.
            logger.warning("feature store unavailable; degrading", exc_info=True)
            features["features_degraded"] = True
            return features

        # Results interleave INCR and EXPIRE replies; take every other one.
        counts = results[::2]
        for (suffix, _), count in zip(VELOCITY_WINDOWS, counts, strict=False):
            features[f"actor_events_{suffix}"] = int(count)

        return features

    async def aclose(self) -> None:
        await self._redis.aclose()


def build_feature_store(redis_url: str | None) -> FeatureStore:
    """Return a Redis-backed store when a URL is configured, else in-process.

    Falls back rather than failing if the redis extra is missing: a missing
    optional dependency should degrade the deployment, not refuse to start it.
    """
    if not redis_url:
        return InMemoryFeatureStore()
    try:
        return RedisFeatureStore(redis_url)
    except ImportError:
        logger.warning("redis extra not installed; falling back to in-memory feature store")
        return InMemoryFeatureStore()
