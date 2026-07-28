# ADR-0003: Three-valued verdicts

**Status:** Accepted
**Date:** 2026-07-28

## Context

The natural output of a moderation system is binary: publish or don't. It
matches what the calling application must ultimately do, and it makes the API
trivial.

It is also a lie about what the system knows.

Real score distributions are not bimodal. A large fraction of content sits in a
middle band where the system is genuinely uncertain — and the two errors there
are not symmetric. A false negative ships one piece of harmful content. A false
positive silences a real person, usually with no explanation and no recourse,
and they are far more likely to be a member of a group the training data
underrepresented.

Forcing a binary verdict does not remove the uncertainty. It hides it, and
picks a side by threshold placement, without recording that a choice was made.

## Decision

Three verdicts:

- **`allow`** — no action
- **`review`** — route to a human; the system declines to decide
- **`block`** — enforce automatically

`Verdict.severity` orders them so merging across categories is well-defined:
the most restrictive wins.

`review` is not a weaker `block`. It is a different kind of statement — about
the *system's* competence rather than the *content's* acceptability.

## Alternatives considered

**Binary allow/block.** Rejected above. Its real cost is that it makes system
uncertainty invisible, so nobody can measure how much of it there is.

**Return a raw score, let callers decide.** Superficially flexible. It pushes
policy into every integration, so the same content is judged differently by
different callers, and there is no single place to audit or change policy. It
also makes "what was our policy in March" unanswerable.

**Four or more verdicts** (`allow`, `flag`, `limit`, `review`, `block`).
Distinctions like rate-limiting or reduced distribution are real, but they are
*enforcement actions* the platform chooses given a verdict, not judgements the
moderation system makes. Keeping them out preserves the boundary. An operator
can map `review` to reduced distribution while a human looks.

## Consequences

**Easier.** The system can be honest about uncertainty. Thresholds become two
independent, separately-tunable decisions rather than one overloaded knob.
Shadow mode and human review have a natural attachment point. High-confidence,
low-authority detectors become expressible (see
[ADR-0005](0005-separate-evidence-from-verdicts.md)).

**Harder.** Integrators must handle three cases, and `review` has no obvious
default behaviour — that is a genuine burden pushed onto callers.

**Cost, and it is the serious one.** `review` is only honest if humans actually
clear the queue. An unbounded queue converts "a human will decide" into
"shadow-banned indefinitely, with a euphemism". Adopting three-valued verdicts
commits the operator to staffing and monitoring review capacity; queue depth
becomes a policy metric, not an ops metric.

Phase 1 ships the verdict without the queue behind it. That is a known gap,
recorded in [ROADMAP.md](../ROADMAP.md), not an oversight.

**Ruled out.** Pretending the system is certain. Every decision that reaches
`review` is one the platform knows it should not automate.
