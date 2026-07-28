"""Rule layer tests.

Rules are the layer most likely to be edited under time pressure during a live
abuse incident, so they carry the false-positive tests: it must be immediately
visible when a hurried change starts firing on ordinary content.
"""

from __future__ import annotations

import pytest

from safeguard.core.models import Actor, Category, ModerationRequest, Verdict
from safeguard.rules.builtin import (
    LinkFloodRule,
    NewAccountRiskRule,
    PIIRule,
    RepeatOffenderRule,
    RepetitionRule,
    _is_card_number,
    _luhn_valid,
    default_rules,
)
from safeguard.rules.engine import RegexRule, Rule, RuleEngine


def request(content: str, actor: Actor | None = None) -> ModerationRequest:
    return ModerationRequest(content=content, actor=actor)


# --- Luhn ------------------------------------------------------------------


@pytest.mark.parametrize(
    "number",
    [
        "4539578763621486",  # Visa test number
        "4111111111111111",  # Visa test number
        "79927398713",  # the canonical Luhn example — valid, but too short for a card
    ],
)
def test_luhn_accepts_valid_checksums(number: str) -> None:
    assert _luhn_valid(number)


@pytest.mark.parametrize("number", ["4539578763621487", "1234567890123", "", "abcd"])
def test_luhn_rejects_invalid_checksums(number: str) -> None:
    assert not _luhn_valid(number)


@pytest.mark.parametrize(
    "number", ["4539578763621486", "4539 5787 6362 1486", "4111-1111-1111-1111"]
)
def test_card_detection_accepts_formatted_numbers(number: str) -> None:
    assert _is_card_number(number)


def test_card_detection_rejects_valid_checksum_of_wrong_length() -> None:
    """Checksum and length are separate facts. An 11-digit number can pass Luhn
    and still not be a card."""
    assert _luhn_valid("79927398713")
    assert not _is_card_number("79927398713")


# --- LinkFloodRule ---------------------------------------------------------


def test_link_flood_fires_above_threshold() -> None:
    content = " ".join(f"https://site{i}.example" for i in range(5))
    result = LinkFloodRule(threshold=4).evaluate(request(content), {})
    assert result is not None
    assert result.category is Category.SPAM


def test_link_flood_ignores_ordinary_link_use() -> None:
    content = "Here are the docs https://example.com and the repo https://git.example"
    assert LinkFloodRule(threshold=4).evaluate(request(content), {}) is None


def test_link_flood_score_saturates() -> None:
    content = " ".join(f"https://site{i}.example" for i in range(60))
    result = LinkFloodRule(threshold=4).evaluate(request(content), {})
    assert result is not None
    assert result.score == 1.0


# --- RepetitionRule --------------------------------------------------------


def test_repetition_fires_on_low_diversity() -> None:
    result = RepetitionRule().evaluate(request("buy now " * 12), {})
    assert result is not None
    assert result.category is Category.SPAM


def test_repetition_ignores_short_content() -> None:
    """Short strings are naturally repetitive; scoring them is pure noise."""
    assert RepetitionRule().evaluate(request("no no no"), {}) is None


def test_repetition_ignores_normal_prose() -> None:
    content = (
        "The deployment finished cleanly this morning and the error rate has "
        "returned to its usual baseline across every region we monitor."
    )
    assert RepetitionRule().evaluate(request(content), {}) is None


# --- PIIRule ---------------------------------------------------------------


def test_pii_detects_email() -> None:
    result = PIIRule().evaluate(request("reach me at someone@example.com"), {})
    assert result is not None
    assert result.proposed_verdict is Verdict.REVIEW


def test_pii_scores_payment_card_higher_than_email() -> None:
    card = PIIRule().evaluate(request("card 4539 5787 6362 1486"), {})
    email = PIIRule().evaluate(request("mail me at a@b.example"), {})
    assert card is not None and email is not None
    assert card.score > email.score


def test_pii_ignores_long_numbers_that_fail_luhn() -> None:
    """Order references and IDs are long digit strings too. The checksum is
    what stops this rule firing on half the support corpus."""
    result = PIIRule().evaluate(request("order reference 1234567890123456"), {})
    assert result is None


def test_pii_sets_both_a_floor_and_a_ceiling_of_review() -> None:
    """Sharing your own contact details is usually legitimate; only a human can
    tell that from doxxing. The floor guarantees a human sees it; the ceiling
    guarantees a human is the one who decides."""
    result = PIIRule().evaluate(request("card 4539 5787 6362 1486"), {})
    assert result is not None
    assert result.score >= 0.9  # highly confident it is present
    assert result.proposed_verdict is Verdict.REVIEW
    assert result.max_verdict is Verdict.REVIEW  # and still not entitled to block


# --- NewAccountRiskRule ----------------------------------------------------


def test_new_account_with_links_fires() -> None:
    actor = Actor(id="u1", account_age_days=1)
    result = NewAccountRiskRule().evaluate(request("see https://x.example", actor), {})
    assert result is not None
    assert result.category is Category.FRAUD


def test_new_account_without_links_does_not_fire() -> None:
    actor = Actor(id="u1", account_age_days=1)
    assert NewAccountRiskRule().evaluate(request("hello everyone", actor), {}) is None


def test_established_account_with_links_does_not_fire() -> None:
    actor = Actor(id="u1", account_age_days=900)
    assert NewAccountRiskRule().evaluate(request("see https://x.example", actor), {}) is None


def test_trusted_new_account_is_exempt() -> None:
    actor = Actor(id="u1", account_age_days=1, trusted=True)
    assert NewAccountRiskRule().evaluate(request("see https://x.example", actor), {}) is None


def test_new_account_risk_decays_with_age() -> None:
    fresh = NewAccountRiskRule().evaluate(
        request("https://x.example", Actor(id="a", account_age_days=0)), {}
    )
    older = NewAccountRiskRule().evaluate(
        request("https://x.example", Actor(id="b", account_age_days=6)), {}
    )
    assert fresh is not None and older is not None
    assert fresh.score > older.score


def test_anonymous_request_does_not_fire_actor_rules() -> None:
    assert NewAccountRiskRule().evaluate(request("https://x.example"), {}) is None


# --- RepeatOffenderRule ----------------------------------------------------


def test_repeat_offender_emits_evidence_never_a_verdict() -> None:
    """History justifies a closer look. It must not convict on its own, or
    moderation becomes a ratchet nobody escapes."""
    actor = Actor(id="u1", prior_violations=9)
    result = RepeatOffenderRule().evaluate(request("perfectly ordinary message", actor), {})
    assert result is not None
    assert result.proposed_verdict is None
    assert result.score <= 0.6


def test_repeat_offender_below_threshold_is_silent() -> None:
    actor = Actor(id="u1", prior_violations=1)
    assert RepeatOffenderRule().evaluate(request("hello", actor), {}) is None


# --- RuleEngine ------------------------------------------------------------


def test_engine_orders_rules_by_priority() -> None:
    engine = RuleEngine(default_rules())
    priorities = [rule.priority for rule in engine.rules]
    assert priorities == sorted(priorities)


def test_engine_rejects_duplicate_ids() -> None:
    engine = RuleEngine([LinkFloodRule()])
    with pytest.raises(ValueError, match="duplicate rule id"):
        engine.register(LinkFloodRule())


def test_engine_skips_disabled_rules() -> None:
    rule = LinkFloodRule(threshold=1)
    rule.enabled = False
    engine = RuleEngine([rule])
    assert engine.evaluate(request("https://a.example https://b.example")).signals == []


class ExplodingRule(Rule):
    id = "test.exploding"
    category = Category.SPAM
    reason_code = "TEST_BOOM"
    priority = 1
    enabled = True

    def evaluate(self, request, features):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")


def test_engine_isolates_a_failing_rule() -> None:
    """One bad regex must not take down moderation for everything."""
    engine = RuleEngine([ExplodingRule(), LinkFloodRule(threshold=1)])
    result = engine.evaluate(request("https://a.example https://b.example"))

    assert result.errored == ["test.exploding"]
    assert len(result.signals) == 1  # the healthy rule still ran


def test_engine_short_circuits_on_terminal_block() -> None:
    blocker = RegexRule(
        id="test.blocker",
        pattern="tripwire",
        category=Category.FRAUD,
        reason_code="TEST_BLOCK",
        proposed_verdict=Verdict.BLOCK,
        priority=1,
    )
    engine = RuleEngine([blocker, LinkFloodRule(threshold=1)])
    result = engine.evaluate(request("tripwire https://a.example https://b.example"))

    assert result.short_circuited
    assert len(result.signals) == 1


def test_short_circuit_can_be_disabled_for_offline_analysis() -> None:
    blocker = RegexRule(
        id="test.blocker",
        pattern="tripwire",
        category=Category.FRAUD,
        reason_code="TEST_BLOCK",
        proposed_verdict=Verdict.BLOCK,
        priority=1,
    )
    engine = RuleEngine([blocker, LinkFloodRule(threshold=1)])
    result = engine.evaluate(
        request("tripwire https://a.example https://b.example"), short_circuit=False
    )

    assert not result.short_circuited
    assert len(result.signals) == 2


def test_default_rules_are_silent_on_benign_content() -> None:
    """The most important test in the file. A moderation system that flags
    ordinary conversation is worse than no moderation system."""
    engine = RuleEngine(default_rules())
    benign = [
        "Thanks, that fixed it!",
        "The build is green now, merging.",
        "I disagree with the conclusion but the methodology is sound.",
        "Docs are at https://example.com/guide if anyone needs them.",
        "Meeting moved to 3pm — see you all there.",
    ]
    for content in benign:
        actor = Actor(id="regular_user", account_age_days=500, prior_violations=0)
        assert engine.evaluate(request(content, actor)).signals == [], content
