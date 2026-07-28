"""Pipeline tests — orchestration, degradation, and the latency contract."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from safeguard.classifier.base import Classifier
from safeguard.classifier.lexicon import LexiconClassifier, LexiconEntry
from safeguard.core.models import (
    Actor,
    Category,
    ModerationRequest,
    Signal,
    SignalSource,
    Verdict,
)
from safeguard.features.store import InMemoryFeatureStore
from safeguard.rules.engine import RegexRule, RuleEngine


class SlowClassifier(Classifier):
    name = "slow"

    def __init__(self, delay_s: float = 5.0) -> None:
        self.delay_s = delay_s
        self.called = False

    async def classify(self, request: ModerationRequest, features: dict[str, Any]) -> list[Signal]:
        self.called = True
        await asyncio.sleep(self.delay_s)
        return []


class FailingClassifier(Classifier):
    name = "failing"

    async def classify(self, request: ModerationRequest, features: dict[str, Any]) -> list[Signal]:
        raise RuntimeError("model server unreachable")


class FixedClassifier(Classifier):
    name = "fixed"

    def __init__(self, score: float, category: Category = Category.HARASSMENT) -> None:
        self.score = score
        self.category = category
        self.calls = 0

    async def classify(self, request: ModerationRequest, features: dict[str, Any]) -> list[Signal]:
        self.calls += 1
        return [
            Signal(
                source=SignalSource.CLASSIFIER,
                detector=self.name,
                category=self.category,
                score=self.score,
                reason_code="MODEL_TEST",
            )
        ]


# --- happy path ------------------------------------------------------------


async def test_benign_content_is_allowed(pipeline, benign_request) -> None:
    decision = await pipeline.decide(benign_request)
    assert decision.verdict is Verdict.ALLOW
    assert not decision.degraded
    assert decision.signals == []


async def test_decision_carries_full_provenance(pipeline, benign_request) -> None:
    decision = await pipeline.decide(benign_request)
    assert decision.request_id == benign_request.request_id
    assert decision.decision_id
    assert decision.policy_version == "test.1"
    assert decision.latency_ms >= 0


async def test_every_decision_is_published(pipeline, publisher, benign_request) -> None:
    """The audit trail is not optional — including for allows. A stream that
    only records enforcement cannot measure a false negative."""
    await pipeline.decide(benign_request)
    assert len(publisher.events) == 1
    assert publisher.events[0]["verdict"] == "allow"


async def test_published_event_excludes_the_content(pipeline, publisher, benign_request) -> None:
    """Fanning raw user content into every analytics consumer turns a safety
    system into a privacy incident."""
    await pipeline.decide(benign_request)
    assert "content" not in publisher.events[0]


# --- rule-driven decisions -------------------------------------------------


async def test_spam_content_escalates(pipeline) -> None:
    content = " ".join(f"https://spam{i}.example" for i in range(12))
    decision = await pipeline.decide(
        ModerationRequest(content=content, actor=Actor(id="u_new", account_age_days=0))
    )
    assert decision.verdict in (Verdict.REVIEW, Verdict.BLOCK)
    assert Category.SPAM in decision.categories or Category.FRAUD in decision.categories


async def test_reason_codes_are_deduplicated(make_pipeline) -> None:
    engine = RuleEngine(
        [
            RegexRule(
                id=f"test.dup{i}",
                pattern="marker",
                category=Category.SPAM,
                reason_code="SAME_CODE",
                score=0.3,
                priority=i,
            )
            for i in range(3)
        ]
    )
    decision = await make_pipeline(rule_engine=engine).decide(
        ModerationRequest(content="marker here")
    )
    assert decision.reason_codes == ["SAME_CODE"]


# --- classifier integration ------------------------------------------------


async def test_classifier_signal_drives_the_verdict(make_pipeline) -> None:
    pipeline = make_pipeline(classifier=FixedClassifier(0.95), rule_engine=RuleEngine([]))
    decision = await pipeline.decide(ModerationRequest(content="anything at all"))
    assert decision.verdict is Verdict.BLOCK


async def test_classifier_is_skipped_when_a_rule_already_blocks(make_pipeline) -> None:
    """The optimisation that makes the layered design worth having: no
    probabilistic signal can make a BLOCK less restrictive, so paying for the
    inference would be pure waste."""
    classifier = FixedClassifier(0.99)
    engine = RuleEngine(
        [
            RegexRule(
                id="test.blocker",
                pattern="tripwire",
                category=Category.FRAUD,
                reason_code="TEST_BLOCK",
                proposed_verdict=Verdict.BLOCK,
                priority=1,
            )
        ]
    )
    pipeline = make_pipeline(classifier=classifier, rule_engine=engine)
    decision = await pipeline.decide(ModerationRequest(content="tripwire"))

    assert decision.verdict is Verdict.BLOCK
    assert classifier.calls == 0


async def test_classifier_runs_when_rules_are_non_terminal(make_pipeline) -> None:
    classifier = FixedClassifier(0.2)
    pipeline = make_pipeline(classifier=classifier)
    await pipeline.decide(ModerationRequest(content="ordinary message here"))
    assert classifier.calls == 1


async def test_lexicon_classifier_is_empty_by_default(make_pipeline) -> None:
    """No bundled term list, by design — see the module docstring."""
    pipeline = make_pipeline(classifier=LexiconClassifier())
    decision = await pipeline.decide(ModerationRequest(content="literally any text"))
    assert decision.signals == []


async def test_operator_supplied_lexicon_produces_signals(make_pipeline) -> None:
    classifier = LexiconClassifier(
        [LexiconEntry(term="widgetspam", category=Category.SPAM, weight=0.8)]
    )
    pipeline = make_pipeline(classifier=classifier, rule_engine=RuleEngine([]))
    decision = await pipeline.decide(ModerationRequest(content="buy widgetspam today"))

    assert decision.verdict is Verdict.REVIEW
    assert decision.categories == [Category.SPAM]


async def test_detected_payment_card_routes_to_review_not_block(pipeline) -> None:
    """End-to-end proof of the ceiling: near-certain detection, and still a
    human makes the call."""
    decision = await pipeline.decide(
        ModerationRequest(content="my card is 4539 5787 6362 1486, use it")
    )
    assert decision.verdict is Verdict.REVIEW
    assert decision.score >= 0.9
    assert decision.categories == [Category.PII]


# --- degradation -----------------------------------------------------------


async def test_classifier_timeout_degrades_rather_than_fails(make_pipeline) -> None:
    pipeline = make_pipeline(classifier=SlowClassifier())
    pipeline.classifier_timeout_s = 0.01

    decision = await pipeline.decide(ModerationRequest(content="ordinary message here"))

    assert decision.degraded
    assert decision.verdict is Verdict.ALLOW  # no evidence fired, so no escalation


async def test_classifier_crash_degrades_rather_than_fails(make_pipeline) -> None:
    pipeline = make_pipeline(classifier=FailingClassifier())
    decision = await pipeline.decide(ModerationRequest(content="ordinary message here"))
    assert decision.degraded
    assert decision.verdict is Verdict.ALLOW


async def test_degradation_with_partial_evidence_escalates_to_review(make_pipeline) -> None:
    """The core failure-mode commitment: when the system is impaired *and*
    something fired, a human decides."""
    engine = RuleEngine(
        [
            RegexRule(
                id="test.weak",
                pattern="hmm",
                category=Category.SPAM,
                reason_code="TEST_WEAK",
                score=0.1,
            )
        ]
    )
    pipeline = make_pipeline(classifier=FailingClassifier(), rule_engine=engine)
    decision = await pipeline.decide(ModerationRequest(content="hmm ok"))

    assert decision.degraded
    assert decision.verdict is Verdict.REVIEW


async def test_broken_feature_store_does_not_fail_the_request(make_pipeline) -> None:
    class BrokenStore(InMemoryFeatureStore):
        async def enrich(self, request: ModerationRequest) -> dict[str, Any]:
            raise RuntimeError("redis down")

    pipeline = make_pipeline()
    pipeline.feature_store = BrokenStore()
    decision = await pipeline.decide(ModerationRequest(content="ordinary message here"))

    assert decision.degraded
    assert decision.verdict is Verdict.ALLOW


# --- shadow mode -----------------------------------------------------------


async def test_shadow_mode_suppresses_enforcement_but_records_it(make_pipeline) -> None:
    pipeline = make_pipeline(
        classifier=FixedClassifier(0.99),
        rule_engine=RuleEngine([]),
        overrides={"shadow_mode": True},
    )
    decision = await pipeline.decide(ModerationRequest(content="anything"))

    assert decision.verdict is Verdict.ALLOW
    assert decision.shadow
    assert decision.shadow_verdict is Verdict.BLOCK


# --- feature store ---------------------------------------------------------


async def test_velocity_counters_accumulate_per_actor(pipeline) -> None:
    actor = Actor(id="chatty", account_age_days=100)
    for _ in range(3):
        await pipeline.decide(ModerationRequest(content="hello there everyone", actor=actor))

    features = await pipeline.feature_store.enrich(ModerationRequest(content="again", actor=actor))
    assert features["actor_events_1m"] == 4


async def test_velocity_counters_are_isolated_between_actors(pipeline) -> None:
    await pipeline.decide(
        ModerationRequest(content="hello", actor=Actor(id="a", account_age_days=100))
    )
    features = await pipeline.feature_store.enrich(
        ModerationRequest(content="hello", actor=Actor(id="b", account_age_days=100))
    )
    assert features["actor_events_1m"] == 1


async def test_anonymous_requests_are_handled(pipeline) -> None:
    decision = await pipeline.decide(ModerationRequest(content="no actor attached here"))
    assert decision.verdict is Verdict.ALLOW


def test_feature_store_evicts_stale_actors() -> None:
    store = InMemoryFeatureStore(max_actors=10)

    async def fill() -> None:
        for i in range(25):
            await store.enrich(ModerationRequest(content="x", actor=Actor(id=f"u{i}")))

    asyncio.run(fill())
    assert len(store._events) <= 10


# --- latency ---------------------------------------------------------------


async def test_latency_is_recorded(pipeline, benign_request) -> None:
    decision = await pipeline.decide(benign_request)
    assert 0 <= decision.latency_ms < 1_000


async def test_budget_breach_flags_but_does_not_abort(make_pipeline) -> None:
    """A slightly late decision beats no decision. The budget is measured, not
    enforced by truncation."""
    pipeline = make_pipeline(
        classifier=SlowClassifier(delay_s=0.05), overrides={"latency_budget_ms": 1}
    )
    pipeline.classifier_timeout_s = 1.0

    decision = await pipeline.decide(ModerationRequest(content="ordinary message here"))
    assert decision.latency_ms > 1
    assert decision.verdict is Verdict.ALLOW


# --- validation ------------------------------------------------------------


def test_empty_content_is_rejected_at_the_model_boundary() -> None:
    with pytest.raises(ValueError):
        ModerationRequest(content="")


def test_oversized_content_is_rejected() -> None:
    with pytest.raises(ValueError):
        ModerationRequest(content="x" * 100_001)
