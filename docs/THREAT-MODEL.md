# Threat model

A moderation system is adversarial by construction: some fraction of its input
is produced by people actively trying to defeat it. This document records who
those adversaries are, what they can do, what SafeGuard does about it, and —
more usefully — what it currently does not.

Scope: the SafeGuard service itself. The surrounding platform (identity, content
storage, the client) is out of scope but assumed hostile at its edges.

---

## Assets

| Asset | Why it matters |
| --- | --- |
| Decision integrity | A wrong verdict either ships harm or silences a user |
| Decision availability | A down moderation service blocks the write path |
| Policy configuration | Thresholds and rules reveal exactly how to evade |
| Decision records | Audit data containing behavioural traces of real users |
| Review queue capacity | A finite human resource, and therefore exhaustible |

## Adversaries

**A1 — Evader.** Wants prohibited content through. Iterates cheaply, observes
the verdict on every attempt.

**A2 — Weaponiser.** Wants *someone else's* legitimate content removed. Exploits
false positives as a censorship tool. Consistently underweighted relative to A1
and often more damaging.

**A3 — Resource exhauster.** Wants the service degraded, either to force
fail-open or to deny the platform its write path.

**A4 — Extractor.** Wants the policy — thresholds, rules, model behaviour —
because knowing it converts evasion from search into arithmetic.

**A5 — Insider.** Has legitimate access to decision records or policy
configuration.

---

## T1 · Evasion (A1)

**Attack.** Homoglyphs, zero-width joiners, leetspeak, transliteration,
whitespace injection, image-of-text, semantic paraphrase, splitting content
across messages.

**Mitigated.** Structural rules key off content *shape* and actor history rather
than meaning — link counts, token diversity, checksums, velocity. These survive
rewording because they do not depend on the words. Actor velocity in particular
generalises across every text-level evasion: rephrasing does not change how fast
you are posting.

**Not mitigated.** No Unicode normalisation, confusable folding, or whitespace
canonicalisation. No cross-message aggregation — content split across five
messages is evaluated as five independent messages. No image or OCR path.

**Residual risk: high.** This is the gap Phase 2 exists to close, and it will
never fully close. Evasion is an arms race; the goal is raising cost, not
achieving completeness.

## T2 · Weaponised false positives (A2)

**Attack.** Craft content that trips a detector while appearing innocuous, then
induce a target to post it — a "poisoned" quotable phrase, an invisible payload
in copied markdown, a term-list trigger embedded in a template.

**Mitigated.**

- **No bundled term list.** The single largest weaponisable surface in most
  moderation systems is a shipped keyword list, because it is a published,
  stable, exactly-known trigger. SafeGuard ships none.
- **Verdict ceilings.** Detectors that are confident but not authoritative
  (`PIIRule`) cannot block on their own. A weaponised PII trigger routes to a
  human rather than silencing the target.
- **Trust caps enforcement.** Established accounts are never auto-blocked.
- **Category isolation.** Evidence does not accumulate across categories, so an
  attacker cannot stack unrelated weak triggers into an enforcement action.
- **Every decision is explainable.** A false positive can be diagnosed from the
  decision object rather than reproduced from logs.

**Not mitigated.** No cross-message or coordinated-report detection. No
detection of mass-reporting campaigns against a single actor.

**Residual risk: medium.** The structural choices help materially, but any
detector an operator adds — especially a term list — reopens this surface. It is
the reason the term-list guidance in [POLICY.md](POLICY.md#term-lists) insists on
scoring rather than blocking.

## T3 · Resource exhaustion (A3)

**Attack.** Flood `/v1/moderate`; submit maximum-size payloads; craft content
that maximises classifier latency; flood *borderline* content specifically, to
saturate the human review queue.

**Mitigated.**

- Content capped at 100,000 characters, rejected at the schema boundary.
- Classifier behind `asyncio.wait_for`; a slow model cannot hold a request.
- Latency budget breaches logged, so degradation is measurable.
- Feature store bounded (`max_actors`, LRU eviction) — an unbounded actor map is
  a memory exhaustion primitive.
- Kafka publish failures never fail the request.

**Not mitigated.** **No rate limiting or authentication on the gateway.** The
velocity counters exist but nothing throttles a caller. Content is scanned by
several regexes with no complexity budget. Nothing bounds review queue depth.

**Residual risk: high.** SafeGuard assumes it sits behind an authenticated,
rate-limited edge. That assumption must be made real before any deployment.

The review-queue variant deserves separate attention: flooding *borderline*
content is cheaper than flooding the endpoint and degrades the system in a way
no infrastructure metric detects. Queue depth needs an alert and an overflow
policy decided in advance.

## T4 · Policy extraction (A4)

**Attack.** Binary-search thresholds by submitting graded content and observing
verdicts. Enumerate rules from reason codes. Read `/v1/policy` directly.

**Mitigated.** Reason codes are deliberately coarse and carry no thresholds,
scores, or detector internals. OpenAPI and the debug endpoint are disabled in
production.

**Not mitigated.** `GET /v1/policy` is unauthenticated and returns thresholds
and rule ids. The response includes per-signal scores, which makes threshold
inference straightforward for anyone who can submit content.

**Residual risk: medium — and partly accepted.** Score transparency is what
makes the system debuggable, appealable, and integrable; hiding it would trade a
real, everyday benefit for a marginal defence against a determined adversary who
can binary-search regardless. The correct fix is authentication and per-caller
response shaping — full detail for first-party integrators, verdict only for
untrusted callers — not removing the detail.

## T5 · Data exposure (A5, and accident)

**Attack.** Read decision records to reconstruct user behaviour; harvest content
from the event stream; read moderated content out of logs.

**Mitigated.**

- **Decision events never carry content.** Only the verdict and its
  justification. Fanning raw user content — often the very content judged
  harmful — into every downstream analytics consumer is how a safety system
  becomes a privacy incident. This is enforced by test.
- Debug endpoints return 404 in production.
- Explanations describe *shape* ("content contains 5 links"), not content.
- `.gitignore` excludes `.env`, `data/`, `models/`, and key material.

**Not mitigated.** No authn/authz on any endpoint. No encryption at rest for the
event stream. No retention or deletion policy — a GDPR erasure request currently
has nowhere to land. No PII redaction in application logs. No access audit.

**Residual risk: high.** Retention and deletion are the most urgent gaps here,
because they are legal obligations rather than engineering preferences.

## T6 · Failure-mode exploitation (A1 + A3)

**Attack.** Induce degradation — exhaust the model, sever Redis, kill the broker
— then push prohibited content through the impaired pipeline.

**Mitigated.** This is the attack that drives the degradation design. When a
stage fails *and* evidence fired, the verdict escalates to `review` rather than
`allow`. Degrading the system therefore makes it more conservative, not less, so
the attack costs the adversary effort and gains them nothing.

The `degraded` flag is on every decision, so the blast radius is measurable
after the fact rather than invisible.

**Not mitigated.** Genuinely clean content still allows when degraded, which is
correct — the alternative drowns the queue — but means an adversary who can
produce content no detector fires on gains a marginally freer path. Also: the
review queue is where degraded traffic lands, so T6 composes with the T3 queue
attack.

**Residual risk: low.** The fail-toward-humans design removes the incentive.
Composition with T3 is the real concern.

---

## Summary

| Threat | Mitigation strength | Residual |
| --- | --- | --- |
| T1 Evasion | Structural signals only | **High** |
| T2 Weaponised false positives | Ceilings, no term list, category isolation | Medium |
| T3 Resource exhaustion | Input caps and timeouts; no rate limiting | **High** |
| T4 Policy extraction | Coarse reason codes; policy endpoint open | Medium |
| T5 Data exposure | Content excluded from events; no authz | **High** |
| T6 Failure exploitation | Degrade toward review | Low |

Three high residuals, all in Phase 1 scope gaps rather than design flaws:
authentication and rate limiting (T3, T5), real classification (T1), and data
lifecycle (T5). None is a research problem; all are work.

## Assumptions

SafeGuard assumes it runs behind an authenticated, rate-limited edge, in a
trusted network, with callers that supply honest `Actor` data. **Actor fields
are caller-supplied and unverified** — an integrator that lets end users set
`trusted: true` has handed out immunity from enforcement. Validate actor claims
at the boundary.

## Reporting

Security issues: see [SECURITY.md](../SECURITY.md). Please do not open a public
issue for a vulnerability.
