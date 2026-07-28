# ADR-0001: Record architecture decisions

**Status:** Accepted
**Date:** 2026-07-28

## Context

SafeGuard makes several choices that look arbitrary without their reasoning:
rules before models, three verdicts instead of two, no bundled term list,
detectors that cap their own authority. Each was a genuine trade-off with a
rejected alternative.

Reasoning that lives only in commit messages and the author's memory is lost
within months. The failure mode is specific and expensive: someone
"simplifies" a decision without knowing what it was protecting against, and the
class of bug it prevented returns.

In a moderation system this is worse than usual, because several decisions
exist to protect users from the system itself. Removing the PII verdict ceiling
would look like dead weight and would start silencing people for posting their
own contact details.

## Decision

Significant architectural decisions are recorded as ADRs in `docs/adr/`,
numbered sequentially and immutable once accepted. Superseding a decision means
writing a new ADR, not editing the old one.

"Significant" means: hard to reverse, or non-obvious enough that a reasonable
engineer would need the context to avoid undoing it.

Every ADR must state what the decision **costs**. A record listing only benefits
is marketing and teaches nothing about when to revisit.

## Alternatives considered

**Design docs.** Describe a system at a point in time and go stale silently,
with no signal that they have. ADRs are append-only, so an old one is
self-evidently historical rather than misleadingly current.

**Code comments only.** Right for local reasoning and wrong for cross-cutting
decisions — an ADR spanning six modules has no natural home in any of them.
Both are used here: comments explain the line, ADRs explain the shape.

**A wiki.** Detaches decisions from the code they describe and from review. An
ADR arrives in the pull request that implements it.

## Consequences

**Easier.** Onboarding starts with the reasoning rather than reverse-engineering
it. Reviewers can challenge a decision on its stated grounds. Revisiting a
decision starts from what it was protecting against.

**Harder.** Every significant change carries documentation work. Judging
significance is a judgement call and will sometimes be wrong in both directions.

**Ruled out.** Silently changing a decision. If the ADR is not updated, the
divergence between record and code becomes its own bug — and a visible one,
which is the point.
