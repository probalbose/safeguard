"""The decision pipeline.

Four stages, cheapest first::

    enrich  ->  rules  ->  classifier  ->  policy
    (state)     (fast,      (slow,          (pure,
                exact)      probabilistic)  auditable)

This ordering is the whole design. Classification is one to three orders of
magnitude more expensive than a rule evaluation, and the overwhelming majority
of traffic is unambiguous. Resolving the easy cases before reaching for the
model is what keeps the median cheap and leaves headroom for the hard cases —
the same reason a fraud stack runs velocity checks before it runs the model.

Two invariants hold throughout:

* **Every stage can fail without failing the request.** A dead feature store, a
  broken rule, a model timeout — each degrades the decision and marks it as
  such. Only policy resolution is required, and it is pure.
* **The latency budget is observed, not enforced by truncation.** Exceeding it
  flags the decision rather than aborting it, because a slightly late decision
  is more useful than no decision. What matters is that breaches are visible.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from safeguard.classifier.base import Classifier, NullClassifier
from safeguard.config import Settings, get_settings
from safeguard.core import policy
from safeguard.core.models import Decision, ModerationRequest, Signal, Verdict
from safeguard.events.publisher import DecisionPublisher, build_publisher
from safeguard.features.store import FeatureStore, build_feature_store
from safeguard.rules.builtin import default_rules
from safeguard.rules.engine import RuleEngine

logger = logging.getLogger(__name__)


class Pipeline:
    """Orchestrates one moderation decision end to end."""

    def __init__(
        self,
        *,
        settings: Settings,
        feature_store: FeatureStore,
        rule_engine: RuleEngine,
        classifier: Classifier,
        publisher: DecisionPublisher,
        classifier_timeout_ms: int = 100,
    ) -> None:
        self.settings = settings
        self.feature_store = feature_store
        self.rule_engine = rule_engine
        self.classifier = classifier
        self.publisher = publisher
        self.classifier_timeout_s = classifier_timeout_ms / 1000.0

    async def startup(self) -> None:
        """Warm the model and connect the broker before serving traffic."""
        await self.classifier.warmup()
        await self.publisher.start()

    async def shutdown(self) -> None:
        """Release resources. Ordered so in-flight publishes drain first."""
        await self.publisher.aclose()
        await self.classifier.aclose()
        await self.feature_store.aclose()

    async def decide(self, request: ModerationRequest) -> Decision:
        """Run the pipeline and return an auditable decision."""
        started = time.perf_counter()
        degraded = False
        signals: list[Signal] = []

        # --- 1. Enrich -----------------------------------------------------
        features: dict[str, Any] = {}
        try:
            features = await self.feature_store.enrich(request)
        except Exception:
            logger.exception("feature enrichment failed for %s", request.request_id)
            degraded = True
        degraded = degraded or bool(features.get("features_degraded"))

        # --- 2. Rules ------------------------------------------------------
        rule_result = self.rule_engine.evaluate(request, features)
        signals.extend(rule_result.signals)
        degraded = degraded or bool(rule_result.errored)

        # --- 3. Classifier -------------------------------------------------
        # Skipped when a rule already proposed BLOCK: no probabilistic signal
        # can make the outcome less restrictive, so the inference would be paid
        # for and discarded.
        if not rule_result.has_terminal_signal:
            try:
                model_signals = await asyncio.wait_for(
                    self.classifier.classify(request, features),
                    timeout=self.classifier_timeout_s,
                )
                signals.extend(model_signals)
            except TimeoutError:
                logger.warning(
                    "classifier %s timed out after %.0fms for %s",
                    self.classifier.name,
                    self.classifier_timeout_s * 1000,
                    request.request_id,
                )
                degraded = True
            except Exception:
                logger.exception(
                    "classifier %s failed for %s", self.classifier.name, request.request_id
                )
                degraded = True

        # --- 4. Policy -----------------------------------------------------
        actor_trusted = bool(request.actor and request.actor.trusted)
        outcome = policy.resolve(
            signals,
            block_threshold=self.settings.block_threshold,
            review_threshold=self.settings.review_threshold,
            degraded=degraded,
            actor_trusted=actor_trusted,
            shadow_mode=self.settings.shadow_mode,
        )

        latency_ms = (time.perf_counter() - started) * 1000.0
        if latency_ms > self.settings.latency_budget_ms:
            logger.warning(
                "latency budget breached: %.1fms > %dms (request %s)",
                latency_ms,
                self.settings.latency_budget_ms,
                request.request_id,
            )

        decision = Decision(
            request_id=request.request_id,
            verdict=outcome.verdict,
            score=outcome.score,
            categories=outcome.categories,
            signals=signals,
            policy_version=self.settings.policy_version,
            latency_ms=round(latency_ms, 3),
            degraded=degraded,
            shadow=self.settings.shadow_mode,
            shadow_verdict=outcome.effective_verdict if self.settings.shadow_mode else None,
        )

        # --- 5. Publish ----------------------------------------------------
        await self.publisher.publish(decision)
        return decision


def build_pipeline(
    settings: Settings | None = None,
    *,
    classifier: Classifier | None = None,
    rule_engine: RuleEngine | None = None,
) -> Pipeline:
    """Assemble a pipeline from configuration.

    Backends are selected by whether their connection string is set, so the
    same code path serves a laptop with no infrastructure and a production
    deployment with Redis and Kafka. ``classifier`` and ``rule_engine`` are
    injectable so tests and offline evaluation can vary one layer at a time.
    """
    settings = settings or get_settings()
    return Pipeline(
        settings=settings,
        feature_store=build_feature_store(settings.redis_url),
        rule_engine=rule_engine or RuleEngine(default_rules()),
        classifier=classifier or NullClassifier(),
        publisher=build_publisher(settings.kafka_bootstrap_servers, settings.kafka_decisions_topic),
    )


__all__ = ["Pipeline", "Verdict", "build_pipeline"]
