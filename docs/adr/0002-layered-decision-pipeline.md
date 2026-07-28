# ADR-0002: Layered decision pipeline, rules before models

**Status:** Accepted
**Date:** 2026-07-28

## Context

The obvious design for content moderation is a classifier behind an endpoint.
It is simple, it is what most tutorials show, and it fails on four axes in
production:

- **Cost.** Model inference on every message, when most messages are trivially
  fine.
- **Latency.** Moderation sits in the path of a user pressing *Post*. Inference
  is one to three orders of magnitude more expensive than string and arithmetic
  operations.
- **Explainability.** "0.94" does not answer an appeal, a regulator, or an
  engineer debugging a false positive at 3am.
- **Response time.** A novel abuse pattern needs data collection, labelling,
  retraining, and revalidation — days at best. Abuse patterns emerge in hours.

Financial fraud detection faced the same shape under harder constraints —
single-digit millisecond budgets, mandatory auditability, a regulator entitled
to ask about any individual decision — and converged on layered decisioning.

## Decision

Four stages, cheapest first:

1. **Enrich** — attach actor velocity and content shape
2. **Rules** — deterministic, exact, explainable
3. **Classify** — probabilistic, expensive, *skipped when a rule already
   proposed BLOCK*
4. **Resolve** — pure policy function over the accumulated evidence

The short-circuit is the load-bearing part. No probabilistic signal can make a
BLOCK less restrictive, so running the model after a terminal rule fires is
inference paid for and discarded.

Each stage may fail independently without failing the request (see
[ADR-0004](0004-degrade-toward-human-review.md)).

## Alternatives considered

**Classifier only.** Simplest. Rejected on all four axes above. Notably, it has
no answer at all to response time — the operational property that matters most
during an active abuse incident.

**Rules only.** Fast, explainable, cheap; trivially evaded by rewording, and
incapable of the semantic judgement most categories require.

**Parallel execution, merge results.** Run rules and model concurrently and
combine. Lower latency in the worst case — but it forfeits the short-circuit
entirely, so *every* request pays for inference. That is the dominant cost, and
the tail latency it saves is the case that matters least.

**Cascade with a cheap model first.** A small model gates a large one. A real
technique, and complementary rather than alternative: it belongs *inside* stage
3. Adopting it does not change the pipeline shape.

## Consequences

**Easier.** The median request never touches the model. A new abuse pattern
ships as a rule in minutes. Most decisions are explainable by construction,
because a rule that fires says exactly what it matched. The model can be
swapped, or absent entirely, without the decision path changing.

**Harder.** Four stages to reason about instead of one. Rule ordering matters
and is a source of subtle bugs. The short-circuit means production and offline
analysis see different signal sets — which is why `RuleEngine.evaluate` takes
`short_circuit=False` for the offline case.

**Cost.** Rules require maintenance and accrue their own debt; a stale rule set
becomes a false-positive generator. Two detection systems must be kept
coherent — a rule and the model can disagree, and reconciling them is ongoing
work, not a one-time task.

**Ruled out.** Treating the classifier as the system. It is one layer, and the
architecture assumes it can be slow, wrong, or missing.
