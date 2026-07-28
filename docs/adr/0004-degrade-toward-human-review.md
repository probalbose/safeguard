# ADR-0004: Degrade toward human review

**Status:** Accepted
**Date:** 2026-07-28

## Context

A moderation pipeline depends on a feature store, a rule set, a model server,
and an event broker. Each fails independently, and at scale something is always
partially degraded.

The standard answers are both wrong here.

**Fail-open** — allow when impaired — ships harm exactly when the system is
least able to detect it, and creates an incentive: an adversary who can induce
degradation gets a free path. Degrading the system becomes an attack.

**Fail-closed** — block when impaired — censors innocent users at scale during
an outage, converting a partial technical failure into a total product failure
and a stream of unanswerable support tickets.

Both are attempts to answer a question the system cannot currently answer. The
truthful answer is *this system does not currently know.*

Three-valued verdicts ([ADR-0003](0003-three-valued-verdicts.md)) mean that
answer is expressible.

## Decision

Every stage catches its own failures and marks the decision `degraded=true`
rather than propagating an exception. Only policy resolution is required, and it
is pure, so it cannot fail on I/O.

When a decision is `degraded` **and** at least one signal fired, an `allow` is
escalated to `review`.

The qualifier is deliberate. Degrading *every* request to review during an
outage would drown the queue — itself a denial of service, and a self-inflicted
one. Clean content with no signals still allows.

`POST /v1/moderate` therefore always returns 200 with a decision, barring schema
validation failure.

## Alternatives considered

**Fail-open.** Rejected: creates an incentive to attack availability.

**Fail-closed.** Rejected: turns a dependency outage into mass false positives.

**Return 5xx and let the caller decide.** Superficially clean, and it just moves
the choice to someone worse positioned. The caller must either publish
unmoderated or drop legitimate content, with no visibility into *what* failed.
A degraded `review` is strictly more information than an exception.

**Degrade every request to review.** Simpler rule, no qualifier. Rejected on
queue exhaustion: an outage would produce review volume proportional to total
traffic, and the queue would never recover.

**Cached last-known decision per actor.** Interesting, and wrong for this
domain — content, not actors, is being judged, and the correlation between an
actor's previous content and their next is exactly what an adversary exploits.

## Consequences

**Easier.** Degradation makes the system more conservative, not less, so
attacking availability gains an adversary nothing (see
[THREAT-MODEL T6](../THREAT-MODEL.md#t6--failure-mode-exploitation-a1--a3)).
Callers have one code path. The `degraded` flag makes blast radius measurable
after the fact.

**Harder.** Errors are swallowed, so monitoring must be good or failures go
unnoticed — this is the real risk the design creates, and it is why `degraded`
rate is a first-class alerting signal rather than a debug field. Tests must
cover degraded paths explicitly, since nothing throws to reveal them.

**Cost.** Degradation increases review volume, so the queue must have headroom
for outages. It composes badly with a review-queue flooding attack
([T3](../THREAT-MODEL.md#t3--resource-exhaustion-a3)): an adversary who can
induce degradation *and* submit borderline content attacks the queue from two
directions.

**Ruled out.** Any dependency being able to fail a moderation request. If a new
stage cannot degrade gracefully, it does not belong in the request path.
