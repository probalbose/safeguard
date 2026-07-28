# ADR-0005: Separate evidence from verdicts

**Status:** Accepted
**Date:** 2026-07-28

## Context

The direct implementation lets each detector return a verdict, then takes the
most restrictive. It is obvious and it decentralises policy: a threshold change
becomes an edit to every detector, and "what is our policy" has no single
answer — you have to read the whole detection layer and simulate it.

It also collapses a distinction that turns out to matter.

A detector can be near-certain something is **present** while having no standing
to judge whether it **violates**. A Luhn-valid sixteen-digit number is a payment
card; that is arithmetic, not inference. But a shopkeeper posting their own
phone number and someone doxxing a stranger produce byte-identical evidence. The
detector is highly confident and completely unqualified to decide.

If the only channel a detector has is a score, high confidence necessarily
produces enforcement. That is how a moderation system ends up blocking people
for publishing their own contact details.

## Decision

Detectors emit **`Signal`s**. Only `policy.resolve()` emits a **`Verdict`**.

A `Signal` carries `score` plus two independent verdict qualifiers:

- **`proposed_verdict`** — a floor. "This justifies at least this outcome."
- **`max_verdict`** — a ceiling. "This may not exceed this on its own."

Floors apply before ceilings, so a ceiling always has the last word within its
category. Ceilings bind only their own category, so corroborating evidence
elsewhere can still escalate.

`PIIRule` sets both to `REVIEW`: the floor guarantees a human sees it, the
ceiling guarantees a human decides it.

`policy.resolve()` is pure — no clock, no I/O, no hidden state.

## Alternatives considered

**Detectors return verdicts, take the most restrictive.** Rejected: decentralises
policy, and cannot express confident-but-not-authoritative.

**Single global score, one threshold pair.** Rejected: evidence would accumulate
across unrelated categories, so a weak spam signal plus a weak PII signal could
combine into enforcement neither justified. Per-category resolution prevents it.

**Sum scores instead of saturating combination.** Rejected: summation is
unbounded and length-biased. A long document accumulates past any threshold on
volume alone, which systematically penalises verbose users for verbosity.
`p + w(1−p)` is bounded, monotonic, and commutative, so aggregate scores do not
depend on detector execution order.

**A per-category "never auto-block" list in policy config.** Simpler, and puts
the knowledge in the wrong place. The detector knows why it cannot judge alone;
policy config would encode that as a fact about the category, losing the reason
and making it wrong for any *other* detector in the same category that *is*
authoritative.

## Consequences

**Easier.** Policy is one small pure function — fully testable, fully auditable,
changed in one diff. Confident-but-unauthoritative detection is expressible.
Every decision carries its complete evidence chain by construction. Adding a
detector requires no policy knowledge.

**Harder.** Two concepts where one would do, and the floor/ceiling interaction
is genuinely subtle — enough that it needs the dedicated tests in
`test_policy.py` to stay correct. New contributors will reach for
`proposed_verdict=BLOCK` when they mean a high score.

**Cost.** Indirection. Tracing "why was this blocked" means reading the
detector *and* the policy function, rather than one place. The provenance in the
`Decision` object is what pays this back.

**Ruled out.** Any detector enforcing directly. If a detector needs to block, it
says so through `proposed_verdict` and policy decides whether that stands.
