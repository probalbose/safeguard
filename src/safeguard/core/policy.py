"""Policy resolution — turning evidence into a verdict.

This is the only place in the system that decides anything. Rules and models
produce evidence; this module resolves it. Keeping that boundary sharp is what
makes the platform governable: a policy change is a change to one small, pure,
fully-tested function, not an archaeology expedition across every detector.

Resolution happens **per category, then merges**. Doing it the other way — one
global score pool — loses the information needed to apply per-category rules,
and it lets unrelated weak evidence accumulate across categories into an
enforcement action no single category justified.

The order encodes five commitments:

1. **Categories are independent.** Evidence combines within a category and
   never across one. Two weak spam signals are more than one; a weak spam
   signal plus a weak PII signal is neither.
2. **Evidence combines, it does not sum.** Scores combine with ``p + w(1-p)``,
   bounded at 1.0. Summation would let a long document accumulate its way past
   a threshold on nothing but length.
3. **Detectors may cap their own authority.** A detector can be confident that
   something is present and still know it cannot judge whether it violates.
   ``Signal.max_verdict`` lets it say so, and the cap binds its category.
4. **Degradation resolves toward humans.** When a stage failed and evidence
   fired anyway, the answer is REVIEW. Fail-open ships harm; fail-closed
   censors innocent users; neither is acceptable when the real answer is
   "this system does not currently know."
5. **Trust caps enforcement, it does not grant immunity.** A trusted actor is
   never auto-blocked — but a trusted account is also the most valuable thing
   an attacker can compromise, so their content still escalates to review.
"""

from __future__ import annotations

from dataclasses import dataclass

from safeguard.core.models import Category, Signal, Verdict


@dataclass(frozen=True, slots=True)
class CategoryOutcome:
    """The resolved position for a single policy category."""

    category: Category
    score: float
    verdict: Verdict


@dataclass(frozen=True, slots=True)
class PolicyOutcome:
    """The resolved decision, before it is wrapped in a :class:`Decision`."""

    verdict: Verdict
    score: float
    categories: list[Category]
    #: What the verdict would have been without shadow-mode suppression.
    effective_verdict: Verdict
    #: Per-category detail, most significant first. Useful for explaining a
    #: decision without re-deriving it.
    per_category: list[CategoryOutcome]


def combine(a: float, b: float) -> float:
    """Saturating combination of two independent confidences in [0, 1].

    Commutative, associative, monotonic, and bounded — so the aggregate score
    does not depend on the order detectors happened to run in.
    """
    return a + b * (1.0 - a)


def aggregate_by_category(signals: list[Signal]) -> dict[Category, float]:
    """Fold signals into one score per category."""
    scores: dict[Category, float] = {}
    for signal in signals:
        scores[signal.category] = combine(scores.get(signal.category, 0.0), signal.score)
    return scores


def _more_restrictive(a: Verdict, b: Verdict) -> Verdict:
    return a if a.severity >= b.severity else b


def _less_restrictive(a: Verdict, b: Verdict) -> Verdict:
    return a if a.severity <= b.severity else b


def _resolve_category(
    category: Category,
    signals: list[Signal],
    *,
    block_threshold: float,
    review_threshold: float,
) -> CategoryOutcome:
    """Resolve one category's signals into a score and a verdict."""
    score = 0.0
    for signal in signals:
        score = combine(score, signal.score)

    if score >= block_threshold:
        verdict = Verdict.BLOCK
    elif score >= review_threshold:
        verdict = Verdict.REVIEW
    else:
        verdict = Verdict.ALLOW

    # A detector entitled to a verdict raises the floor. It never lowers it:
    # a rule proposing REVIEW must not soften a block the score already earned.
    for signal in signals:
        if signal.proposed_verdict is not None:
            verdict = _more_restrictive(verdict, signal.proposed_verdict)

    # Ceilings bind after the floor. The most restrictive ceiling among the
    # contributing detectors wins: if any one of them says this evidence cannot
    # enforce alone, the category cannot enforce alone.
    for signal in signals:
        if signal.max_verdict is not None:
            verdict = _less_restrictive(verdict, signal.max_verdict)

    return CategoryOutcome(category=category, score=round(score, 4), verdict=verdict)


def resolve(
    signals: list[Signal],
    *,
    block_threshold: float,
    review_threshold: float,
    degraded: bool = False,
    actor_trusted: bool = False,
    shadow_mode: bool = False,
) -> PolicyOutcome:
    """Resolve signals into a verdict.

    Pure and synchronous by construction. Policy resolution is the part of the
    system most likely to be argued over, audited, and changed under time
    pressure; it should be reproducible from its inputs alone, with no clock,
    no network, and no hidden state.
    """
    by_category: dict[Category, list[Signal]] = {}
    for signal in signals:
        by_category.setdefault(signal.category, []).append(signal)

    outcomes = sorted(
        (
            _resolve_category(
                category,
                group,
                block_threshold=block_threshold,
                review_threshold=review_threshold,
            )
            for category, group in by_category.items()
        ),
        key=lambda o: (-o.verdict.severity, -o.score),
    )

    verdict = Verdict.ALLOW
    for outcome in outcomes:
        verdict = _more_restrictive(verdict, outcome.verdict)

    score = max((o.score for o in outcomes), default=0.0)

    # (4) Partial evidence plus a partial pipeline resolves to a human.
    if degraded and verdict is Verdict.ALLOW and signals:
        verdict = Verdict.REVIEW

    # (5) Trust caps enforcement at REVIEW.
    if actor_trusted and verdict is Verdict.BLOCK:
        verdict = Verdict.REVIEW

    effective = verdict
    if shadow_mode:
        verdict = Verdict.ALLOW

    # Categories worth reporting: those that reached the review threshold, or
    # that drove enforcement on their own. An enforced decision must never be
    # returned without a stated category — an unexplainable decision cannot be
    # appealed.
    categories = [
        o.category
        for o in outcomes
        if o.score >= review_threshold or o.verdict is not Verdict.ALLOW
    ]
    if effective is not Verdict.ALLOW and not categories:
        categories = [o.category for o in outcomes]

    return PolicyOutcome(
        verdict=verdict,
        score=round(score, 4),
        categories=categories,
        effective_verdict=effective,
        per_category=outcomes,
    )
