"""Policy resolution is pure, so it gets the densest tests in the suite.

This is where verdicts are actually made. Every other layer only produces
evidence; a bug here changes outcomes for real users, silently.
"""

from __future__ import annotations

import pytest

from safeguard.core.models import Category, Signal, SignalSource, Verdict
from safeguard.core.policy import aggregate_by_category, combine, resolve

THRESHOLDS = {"block_threshold": 0.90, "review_threshold": 0.60}


def signal(
    score: float,
    category: Category = Category.HARASSMENT,
    *,
    proposed: Verdict | None = None,
    ceiling: Verdict | None = None,
    source: SignalSource = SignalSource.CLASSIFIER,
    detector: str = "test",
) -> Signal:
    return Signal(
        source=source,
        detector=detector,
        category=category,
        score=score,
        reason_code="TEST",
        proposed_verdict=proposed,
        max_verdict=ceiling,
    )


# --- combine ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [(0.0, 0.0, 0.0), (0.5, 0.0, 0.5), (0.0, 0.5, 0.5), (1.0, 0.9, 1.0), (0.5, 0.5, 0.75)],
)
def test_combine_values(a: float, b: float, expected: float) -> None:
    assert combine(a, b) == pytest.approx(expected)


def test_combine_is_bounded_above_by_one() -> None:
    score = 0.0
    for _ in range(100):
        score = combine(score, 0.4)
    assert score <= 1.0


def test_combine_is_commutative() -> None:
    """Aggregate scores must not depend on detector execution order."""
    assert combine(0.3, 0.7) == pytest.approx(combine(0.7, 0.3))


# --- aggregation -----------------------------------------------------------


def test_aggregate_groups_by_category() -> None:
    scores = aggregate_by_category(
        [
            signal(0.5, Category.HARASSMENT),
            signal(0.5, Category.HARASSMENT),
            signal(0.2, Category.SPAM),
        ]
    )
    assert scores[Category.HARASSMENT] == pytest.approx(0.75)
    assert scores[Category.SPAM] == pytest.approx(0.2)


# --- verdicts --------------------------------------------------------------


def test_no_signals_allows() -> None:
    outcome = resolve([], **THRESHOLDS)
    assert outcome.verdict is Verdict.ALLOW
    assert outcome.score == 0.0
    assert outcome.categories == []


def test_score_above_block_threshold_blocks() -> None:
    assert resolve([signal(0.95)], **THRESHOLDS).verdict is Verdict.BLOCK


def test_score_in_middle_band_reviews() -> None:
    assert resolve([signal(0.7)], **THRESHOLDS).verdict is Verdict.REVIEW


def test_score_below_review_threshold_allows() -> None:
    assert resolve([signal(0.2)], **THRESHOLDS).verdict is Verdict.ALLOW


@pytest.mark.parametrize("score", [0.60, 0.90])
def test_thresholds_are_inclusive(score: float) -> None:
    """A score exactly on a threshold escalates. Boundary behaviour is policy,
    not an implementation detail, so it is pinned by test."""
    expected = Verdict.BLOCK if score >= 0.90 else Verdict.REVIEW
    assert resolve([signal(score)], **THRESHOLDS).verdict is expected


def test_terminal_rule_overrides_low_score() -> None:
    outcome = resolve([signal(0.1, proposed=Verdict.BLOCK, source=SignalSource.RULE)], **THRESHOLDS)
    assert outcome.verdict is Verdict.BLOCK


def test_proposed_review_raises_the_floor() -> None:
    outcome = resolve([signal(0.05, proposed=Verdict.REVIEW)], **THRESHOLDS)
    assert outcome.verdict is Verdict.REVIEW


def test_proposed_review_never_lowers_a_block() -> None:
    outcome = resolve(
        [signal(0.99, Category.HATE), signal(0.05, Category.PII, proposed=Verdict.REVIEW)],
        **THRESHOLDS,
    )
    assert outcome.verdict is Verdict.BLOCK


def test_weak_signals_accumulate_into_review() -> None:
    """Two independent 0.4 signals combine to 0.64 and cross the review line."""
    outcome = resolve([signal(0.4), signal(0.4)], **THRESHOLDS)
    assert outcome.score == pytest.approx(0.64)
    assert outcome.verdict is Verdict.REVIEW


def test_weak_signals_in_different_categories_do_not_accumulate() -> None:
    """Cross-category accumulation would let unrelated evidence convict."""
    outcome = resolve([signal(0.4, Category.SPAM), signal(0.4, Category.PII)], **THRESHOLDS)
    assert outcome.score == pytest.approx(0.4)
    assert outcome.verdict is Verdict.ALLOW


# --- verdict ceilings ------------------------------------------------------


def test_ceiling_prevents_a_high_score_from_blocking() -> None:
    """High confidence that something is present is not authority to enforce."""
    outcome = resolve([signal(0.99, ceiling=Verdict.REVIEW)], **THRESHOLDS)
    assert outcome.verdict is Verdict.REVIEW
    assert outcome.score == pytest.approx(0.99)


def test_ceiling_does_not_suppress_a_low_score() -> None:
    """A ceiling is a cap, not a floor. It must not manufacture escalation."""
    outcome = resolve([signal(0.1, ceiling=Verdict.REVIEW)], **THRESHOLDS)
    assert outcome.verdict is Verdict.ALLOW


def test_ceiling_binds_only_its_own_category() -> None:
    """Corroborating evidence elsewhere is not constrained by another
    detector's admission that it cannot judge alone."""
    outcome = resolve(
        [
            signal(0.99, Category.PII, ceiling=Verdict.REVIEW),
            signal(0.95, Category.HATE),
        ],
        **THRESHOLDS,
    )
    assert outcome.verdict is Verdict.BLOCK


def test_most_restrictive_ceiling_in_a_category_wins() -> None:
    outcome = resolve(
        [
            signal(0.8, Category.PII),
            signal(0.8, Category.PII, ceiling=Verdict.ALLOW),
        ],
        **THRESHOLDS,
    )
    assert outcome.verdict is Verdict.ALLOW


def test_ceiling_overrides_a_proposed_verdict() -> None:
    """Floors are applied before ceilings, so a ceiling always has the last
    word within its category."""
    outcome = resolve([signal(0.1, proposed=Verdict.BLOCK, ceiling=Verdict.REVIEW)], **THRESHOLDS)
    assert outcome.verdict is Verdict.REVIEW


# --- per-category resolution -----------------------------------------------


def test_per_category_detail_is_reported() -> None:
    outcome = resolve([signal(0.95, Category.HATE), signal(0.3, Category.SPAM)], **THRESHOLDS)
    detail = {o.category: o for o in outcome.per_category}

    assert detail[Category.HATE].verdict is Verdict.BLOCK
    assert detail[Category.SPAM].verdict is Verdict.ALLOW
    assert detail[Category.SPAM].score == pytest.approx(0.3)


def test_most_restrictive_category_sets_the_verdict() -> None:
    outcome = resolve([signal(0.65, Category.SPAM), signal(0.95, Category.HATE)], **THRESHOLDS)
    assert outcome.verdict is Verdict.BLOCK
    assert outcome.categories[0] is Category.HATE


# --- degradation and trust -------------------------------------------------


def test_degraded_with_evidence_escalates_to_review() -> None:
    outcome = resolve([signal(0.1)], degraded=True, **THRESHOLDS)
    assert outcome.verdict is Verdict.REVIEW


def test_degraded_without_any_evidence_still_allows() -> None:
    """Degrading every clean request to review would drown the queue."""
    assert resolve([], degraded=True, **THRESHOLDS).verdict is Verdict.ALLOW


def test_trusted_actor_is_capped_at_review() -> None:
    outcome = resolve([signal(0.99)], actor_trusted=True, **THRESHOLDS)
    assert outcome.verdict is Verdict.REVIEW


def test_trusted_actor_is_not_immune() -> None:
    """Trust caps enforcement; it does not suppress the signal."""
    outcome = resolve([signal(0.99)], actor_trusted=True, **THRESHOLDS)
    assert outcome.score == pytest.approx(0.99)
    assert Category.HARASSMENT in outcome.categories


# --- shadow mode -----------------------------------------------------------


def test_shadow_mode_returns_allow_but_records_the_real_verdict() -> None:
    outcome = resolve([signal(0.99)], shadow_mode=True, **THRESHOLDS)
    assert outcome.verdict is Verdict.ALLOW
    assert outcome.effective_verdict is Verdict.BLOCK


def test_shadow_mode_preserves_score_and_categories() -> None:
    """Shadow evaluation is worthless if it discards what it measured."""
    outcome = resolve([signal(0.99, Category.HATE)], shadow_mode=True, **THRESHOLDS)
    assert outcome.score == pytest.approx(0.99)
    assert outcome.categories == [Category.HATE]


# --- explainability --------------------------------------------------------


def test_enforcement_always_reports_a_category() -> None:
    """An enforced decision with no stated category is unexplainable, and an
    unexplainable decision cannot be appealed."""
    outcome = resolve(
        [signal(0.1, Category.SPAM, proposed=Verdict.BLOCK, source=SignalSource.RULE)],
        **THRESHOLDS,
    )
    assert outcome.verdict is Verdict.BLOCK
    assert outcome.categories == [Category.SPAM]
