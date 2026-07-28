# Architecture

How SafeGuard is put together, why, and what a production deployment still
needs. For the decisions themselves — with alternatives and costs — see
[`adr/`](adr/).

---

## 1. Constraints

Every choice below follows from four constraints that hold for any moderation
system sitting in a user-facing write path.

| Constraint | Consequence |
| --- | --- |
| **Latency.** Moderation blocks a user pressing *Post*. | Sub-100ms p99. The expensive layer cannot run on every request. |
| **Volume.** Traffic is dominated by unambiguous content. | Spending model inference on obvious cases is waste; resolve them cheaply. |
| **Auditability.** Every enforcement may be appealed or audited. | The reasoning must be part of the decision object, not reconstructed from logs. |
| **Partial failure is normal.** Models, caches, and brokers fail independently. | No single dependency may be able to fail the request. |

Financial fraud detection operates under strictly harder versions of the first
three. SafeGuard borrows its shape: **layered decisioning with an explicit
policy tier**.

## 2. Components

```
src/safeguard/
├── api/              HTTP gateway (FastAPI)
├── core/
│   ├── models.py     Domain model — Signal, Decision, Verdict, Category
│   ├── pipeline.py   Stage orchestration, timeouts, degradation
│   └── policy.py     Pure resolution: evidence -> verdict
├── rules/            Deterministic layer
├── classifier/       Probabilistic layer (interface + stand-in)
├── features/         Velocity state — in-memory | Redis
├── events/           Decision stream — in-memory | Kafka
└── observability/    Structured logging, optional OTel tracing
```

Each pluggable layer is an ABC with an in-process default and an optional
networked implementation. The default is always the one that requires no
infrastructure, which is what makes `git clone && make test` work with nothing
installed and keeps the test suite hermetic.

## 3. Request flow

```
POST /v1/moderate
   │
   ├─ 1. ENRICH ─────────────────────────────────────────────  ~0.1 ms
   │     FeatureStore.enrich(request)
   │     • actor velocity: events in 1m / 1h / 1d
   │     • content shape, actor reputation
   │     • never raises — sets features_degraded instead
   │
   ├─ 2. RULES ──────────────────────────────────────────────  ~0.1 ms
   │     RuleEngine.evaluate(request, features)
   │     • ordered by priority, cheapest and most precise first
   │     • per-rule try/except: one bad rule degrades, never fails
   │     • short-circuits on a terminal BLOCK
   │
   ├─ 3. CLASSIFY ───────────────────────────────────────────  ~10-50 ms
   │     Classifier.classify(request, features)
   │     • SKIPPED if a rule already proposed BLOCK
   │     • asyncio.wait_for timeout; a slow model degrades
   │
   ├─ 4. RESOLVE ────────────────────────────────────────────  ~0 ms
   │     policy.resolve(signals, ...)
   │     • pure function: no clock, no I/O, no hidden state
   │     • per-category resolution, then merge
   │
   └─ 5. PUBLISH ────────────────────────────────────────────  async
         DecisionPublisher.publish(decision)
         • outside the critical path; never raises
```

### Why this order

**Enrichment first** because both later stages want the features. Velocity is
what turns a stateless text judgement into a moderation decision: coordinated
spam and ban evasion are invisible in any single message and obvious in the
posting rate of the account behind it.

**Rules before the model** for three reasons in order of weight — explainability
(a rule that fires tells you exactly why), latency and cost (most traffic is
unambiguous), and response time (a new abuse pattern ships as a rule in minutes;
retraining takes days).

**The model last, and conditionally.** When a rule has already proposed BLOCK,
no probabilistic signal can make the outcome less restrictive, so the inference
would be paid for and thrown away. This short-circuit is the single largest cost
saving in the design.

**Policy resolution is pure.** It is the part of the system most likely to be
argued over, audited, and changed under time pressure. It should be reproducible
from its inputs alone.

## 4. The decision model

Three types carry the design.

**`Signal`** — one piece of evidence from one detector. Carries `source`,
`detector`, `category`, `score`, `reason_code`, and optionally two verdict
qualifiers:

- `proposed_verdict` — a **floor**. "This evidence justifies at least this."
- `max_verdict` — a **ceiling**. "This evidence may not exceed this on its own."

Two qualifiers rather than one because confidence and authority are different
axes. `PIIRule` is near-certain a Luhn-valid card number is present (score 0.95)
and explicitly not entitled to enforce on that alone (ceiling `review`), because
a shopkeeper posting their own number and a doxxer produce identical evidence.

**`Decision`** — the auditable output. Verdict, aggregate score, categories,
*every* contributing signal, policy version, latency, and the `degraded` and
`shadow` flags. Provenance is part of the object, not a log line beside it.

**`Verdict`** — three-valued: `allow`, `review`, `block`. Binary forces the
system to pretend certainty; `review` is the honest answer for the middle band
and the attachment point for human escalation. See [ADR-0003](adr/0003-three-valued-verdicts.md).

### Policy resolution

Resolution is per-category, then merged:

1. Group signals by category.
2. Within a category, combine scores with `p + w·(1−p)` — saturating, bounded
   at 1.0, commutative. Summation would let long documents accumulate past a
   threshold on length alone.
3. Map the category score to a verdict via thresholds.
4. Apply floors (`proposed_verdict`), then ceilings (`max_verdict`).
5. Merge: the most restrictive category verdict wins; the reported score is the
   highest category score.
6. Apply global modifiers: degradation, actor trust, shadow mode.

Evidence never combines *across* categories. Two weak spam signals are more than
one; a weak spam signal plus a weak PII signal is neither.

## 5. Failure modes

The central commitment: **every stage can fail without failing the request.**

| Failure | Behaviour | Verdict impact |
| --- | --- | --- |
| Feature store unreachable | Caught, `features_degraded` set | `degraded=true` |
| A rule raises | Caught per-rule, rule skipped, id recorded | `degraded=true` |
| Classifier times out | `asyncio.wait_for`, signals dropped | `degraded=true` |
| Classifier raises | Caught | `degraded=true` |
| Publisher fails | Logged at ERROR, swallowed | none — decision still returned |
| Latency budget exceeded | Logged; request completes | none — flagged, not truncated |

When `degraded` is true **and** some evidence fired, an `allow` is escalated to
`review`. Not fail-open (ships harm), not fail-closed (censors innocent users) —
both are wrong when the truthful answer is *this system does not currently know*.
A degraded decision on genuinely clean content still allows; degrading everything
would drown the review queue and is itself a denial of service.

See [ADR-0004](adr/0004-degrade-toward-human-review.md).

### What the API never does

`POST /v1/moderate` always returns 200 with a decision (barring schema
validation failure). A caller receiving a 500 has no useful fallback: it must
either publish unmoderated content or drop legitimate content, and it will
choose wrong. A degraded `review` is a better answer than an exception, and the
`degraded` flag tells the caller which it received.

## 6. Scaling

**Stateless gateway.** Every replica is identical; scale horizontally behind a
load balancer.

**Shared velocity state.** `InMemoryFeatureStore` is correct for exactly one
process. With multiple replicas, an actor spraying traffic across ten pods would
be counted at one-tenth their true rate — the abuse case the feature exists to
catch. Production requires `SAFEGUARD_REDIS_URL`.

**Redis windows are bucketed**, not exact: `sg:v:{actor}:{window}:{bucket}`,
`INCR` plus `EXPIRE` at 2× the window, pipelined. Costs boundary precision, buys
a single round trip with no read-modify-write. The trade every production rate
limiter makes.

**Kafka partitioning is by `request_id`.** A decision and any later correction
land on the same partition, so consumers see them in order. Partitioning by
actor would be the natural alternative, but hot actors create hot partitions —
and abusive actors are exactly the hot ones.

**The model is the scaling bottleneck**, which is why it is reached last, gated
behind rules, and behind a timeout. When Phase 2 lands, it belongs on separate
inference infrastructure scaled independently of the gateway.

## 7. Observability

Structured JSON logs outside development — a moderation decision is an audit
record, and audit records requiring a regex to parse are audit records nobody
queries when it matters. OpenTelemetry tracing attaches when
`SAFEGUARD_OTLP_ENDPOINT` is set, and is a no-op otherwise.

The signals worth alerting on:

- **Verdict mix drift.** The earliest indicator that a model or policy change
  went wrong — usually visible hours before user reports.
- **`degraded` rate.** Rising means a dependency is sick.
- **Latency budget breaches**, by stage.
- **Review queue depth.** `review` is only honest if humans actually clear it.
  An unbounded queue silently converts "escalate to a human" into "shadow-ban".

## 8. Not yet production-ready

An honest list. Phase 1 is a working skeleton, not a deployable service.

**Authentication and authorisation.** No API keys, no mTLS, no tenancy. Every
caller is anonymous and equal.

**Rate limiting.** The velocity counters exist but nothing throttles a caller.
The gateway is trivially floodable.

**Real classification.** `LexiconClassifier` is an explicit stand-in with an
empty default. There is no shipped model. See [ROADMAP](ROADMAP.md).

**Human review workflow.** `review` is a verdict with no queue, no reviewer UI,
no SLA, and no path back into training data. This is the largest gap by volume
of work, and the one that most determines whether the system is honest.

**Appeals.** Reason codes are stable and user-safe, but there is no appeals
endpoint or reversal path.

**Data retention.** Decision events have no retention policy or deletion path.
Any real deployment needs both to satisfy GDPR and equivalents.

**Multi-tenancy.** One global policy. No per-surface or per-tenant thresholds.

**Persistent rule storage.** Rules are code. Editing policy requires a deploy —
which contradicts the "ship a rule in minutes" claim above, and is honestly a
Phase 2 item.

**Multimodal.** `ContentType.IMAGE` and `MULTIMODAL` exist in the enum and are
not handled. The pipeline shape does not change; the classifier does.

## 9. Related documents

- [`POLICY.md`](POLICY.md) — taxonomy, thresholds, appeals, versioning
- [`THREAT-MODEL.md`](THREAT-MODEL.md) — adversaries and mitigations
- [`ROADMAP.md`](ROADMAP.md) — phases and scope boundaries
- [`adr/`](adr/) — decision records
