"""Shared fixtures.

Every fixture builds an isolated pipeline with in-process backends. No test
touches Redis, Kafka, or a model server — the suite must run identically on a
laptop with no infrastructure and on a CI runner with none either.
"""

from __future__ import annotations

import pytest

from safeguard.classifier.base import Classifier, NullClassifier
from safeguard.config import Settings
from safeguard.core.models import Actor, ModerationRequest
from safeguard.core.pipeline import Pipeline
from safeguard.events.publisher import InMemoryPublisher
from safeguard.features.store import InMemoryFeatureStore
from safeguard.rules.builtin import default_rules
from safeguard.rules.engine import RuleEngine


@pytest.fixture
def settings() -> Settings:
    """Deterministic settings, isolated from the ambient environment."""
    return Settings(
        environment="development",
        policy_version="test.1",
        block_threshold=0.90,
        review_threshold=0.60,
        shadow_mode=False,
        latency_budget_ms=1_000,
        redis_url=None,
        kafka_bootstrap_servers=None,
        _env_file=None,
    )


@pytest.fixture
def publisher() -> InMemoryPublisher:
    return InMemoryPublisher()


@pytest.fixture
def make_pipeline(settings: Settings, publisher: InMemoryPublisher):
    """Factory so individual tests can swap one layer without rebuilding all."""

    def _make(
        *,
        classifier: Classifier | None = None,
        rule_engine: RuleEngine | None = None,
        overrides: dict[str, object] | None = None,
    ) -> Pipeline:
        effective = settings.model_copy(update=overrides or {})
        return Pipeline(
            settings=effective,
            feature_store=InMemoryFeatureStore(),
            rule_engine=rule_engine if rule_engine is not None else RuleEngine(default_rules()),
            classifier=classifier or NullClassifier(),
            publisher=publisher,
        )

    return _make


@pytest.fixture
def pipeline(make_pipeline) -> Pipeline:
    return make_pipeline()


@pytest.fixture
def benign_request() -> ModerationRequest:
    return ModerationRequest(
        content="Thanks for the detailed write-up, this cleared up my confusion about the API.",
        actor=Actor(id="user_ok", account_age_days=400, prior_violations=0),
    )
