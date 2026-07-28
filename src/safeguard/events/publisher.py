"""Publishing decisions.

Every decision is emitted as an event. That single stream serves four consumers
that would otherwise each need their own plumbing:

* **Audit.** The immutable record of what was decided and why.
* **Appeals.** Reconstructing a decision needs the signals, not just the verdict.
* **Training.** Today's decisions plus tomorrow's human review labels are the
  corpus the next model is trained on. A moderation platform that does not
  capture its own decisions cannot improve.
* **Monitoring.** Verdict-mix drift is the earliest signal that a model or a
  policy change has gone wrong — usually visible hours before user reports.

The publish call sits outside the request's critical path. A decision that was
made correctly but not published is a serious operational problem; a decision
that was never made because the log was down is a worse one. So publication
failures are logged loudly and swallowed, and the durability guarantee is
delegated to the broker's own acknowledgement settings.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections import deque
from typing import Any

from safeguard.core.models import Decision

logger = logging.getLogger(__name__)


def decision_to_event(decision: Decision) -> dict[str, Any]:
    """Serialise a decision into the wire format for ``safeguard.decisions.v1``.

    Note what is absent: the content itself. The event carries the decision and
    its justification, not the material that was moderated. Fanning raw user
    content — often the very content judged harmful — into every downstream
    analytics consumer is how a safety system becomes a privacy incident.
    Consumers needing the content join back to primary storage under their own
    access controls.
    """
    return {
        "schema_version": 1,
        "decision_id": decision.decision_id,
        "request_id": decision.request_id,
        "verdict": decision.verdict.value,
        "score": decision.score,
        "categories": [c.value for c in decision.categories],
        "reason_codes": decision.reason_codes,
        "signals": [
            {
                "source": s.source.value,
                "detector": s.detector,
                "category": s.category.value,
                "score": s.score,
                "reason_code": s.reason_code,
            }
            for s in decision.signals
        ],
        "policy_version": decision.policy_version,
        "latency_ms": decision.latency_ms,
        "degraded": decision.degraded,
        "shadow": decision.shadow,
        "shadow_verdict": decision.shadow_verdict.value if decision.shadow_verdict else None,
        "decided_at": decision.decided_at.isoformat(),
    }


class DecisionPublisher(ABC):
    """Emits decision events."""

    @abstractmethod
    async def publish(self, decision: Decision) -> None:  # pragma: no cover - interface
        """Publish one decision. Must not raise."""

    async def start(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


class InMemoryPublisher(DecisionPublisher):
    """Bounded in-process ring buffer.

    The default, so the platform runs with no broker. Explicitly *not* durable —
    it exists for development, tests, and inspecting recent decisions through
    the debug endpoint.
    """

    def __init__(self, maxlen: int = 1_000) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=maxlen)

    async def publish(self, decision: Decision) -> None:
        self._events.append(decision_to_event(decision))

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()


class KafkaPublisher(DecisionPublisher):
    """Publishes to Kafka, partitioned by ``request_id``.

    Partitioning by request keeps a decision and any later correction to it on
    the same partition, so consumers see them in order. Partitioning by actor
    would be the natural alternative but hot actors would create hot partitions —
    and abusive actors are exactly the hot ones.
    """

    def __init__(self, bootstrap_servers: str, topic: str, *, acks: str | int = "all") -> None:
        try:
            from aiokafka import AIOKafkaProducer
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError(
                "KafkaPublisher requires the kafka extra: pip install 'safeguard[kafka]'"
            ) from exc

        self._topic = topic
        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode(),
            key_serializer=lambda k: k.encode() if k else None,
            # acks=all: the audit trail is worth the extra round trip.
            acks=acks,
            enable_idempotence=True,
            compression_type="gzip",
        )
        self._started = False

    async def start(self) -> None:
        if not self._started:
            await self._producer.start()
            self._started = True

    async def publish(self, decision: Decision) -> None:
        try:
            if not self._started:
                await self.start()
            await self._producer.send(
                self._topic,
                value=decision_to_event(decision),
                key=decision.request_id,
            )
        except Exception:
            # Loud, but not fatal. See the module docstring.
            logger.error(
                "failed to publish decision %s; audit record lost",
                decision.decision_id,
                exc_info=True,
            )

    async def aclose(self) -> None:
        if self._started:
            await self._producer.stop()
            self._started = False


def build_publisher(bootstrap_servers: str | None, topic: str) -> DecisionPublisher:
    """Return a Kafka publisher when brokers are configured, else in-process."""
    if not bootstrap_servers:
        return InMemoryPublisher()
    try:
        return KafkaPublisher(bootstrap_servers, topic)
    except ImportError:
        logger.warning("kafka extra not installed; falling back to in-memory publisher")
        return InMemoryPublisher()
