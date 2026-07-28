# SafeGuard

**Real-time content safety decisioning, built on production fraud-detection architecture.**

[![CI](https://github.com/probalbose/safeguard/actions/workflows/ci.yml/badge.svg)](https://github.com/probalbose/safeguard/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## The problem

Content moderation is usually built as a classifier behind an endpoint. That works
until it meets production, where four things go wrong at once:

- **Cost.** Running a model on every message is expensive, and the overwhelming
  majority of messages are unambiguous.
- **Latency.** Moderation sits in the path of a user pressing *Post*. A budget
  measured in tens of milliseconds is not negotiable.
- **Explainability.** "The model scored 0.94" is not an answer to an appeal, a
  regulator, or an on-call engineer at 3am.
- **Failure.** When the model is down, both available answers are wrong.
  Fail-open ships harm; fail-closed censors innocent users.

Financial fraud detection solved this shape of problem twenty years ago, under
harder constraints: single-digit millisecond budgets, mandatory auditability,
and a regulator entitled to ask why any individual decision was made. SafeGuard
ports that architecture to content safety.

## The approach

A **layered decision pipeline**. Cheap deterministic checks run first,
probabilistic models run second, and a small pure function resolves their
evidence into one auditable verdict.

```
                 ┌──────────────────────────────────────────────┐
   POST          │                                              │
 /v1/moderate ──►│  1. ENRICH      actor velocity, content      │  ~1 ms
                 │                 shape, reputation            │
                 │        │                                     │
                 │        ▼                                     │
                 │  2. RULES       deterministic, explainable,   │  ~1 ms
                 │                 shippable in minutes          │
                 │        │                                     │
                 │        ├──── terminal BLOCK? ──────────┐     │
                 │        ▼                               │     │
                 │  3. CLASSIFY    probabilistic, the      │     │  ~50 ms
                 │                 expensive layer         │     │
                 │        │                               │     │
                 │        ▼                               ▼     │
                 │  4. POLICY      pure, versioned, fully tested │  ~0 ms
                 │                                              │
                 └──────────────────┬───────────────────────────┘
                                    │
                       ┌────────────┴────────────┐
                       ▼                         ▼
                  Decision                 Kafka topic
              allow │ review │ block   safeguard.decisions.v1
              + every contributing      (audit · appeals ·
                signal and reason        training · monitoring)
```

Each layer earns its place:

**1. Enrichment** attaches the state a stateless classifier cannot see — how
much this actor has posted in the last minute, hour, and day. Coordinated spam,
brigading, and ban evasion are invisible in any single message and obvious in
the velocity of the account posting it. These are the same velocity features a
payment authorisation system computes on a card before it scores the transaction.

**2. Rules** are deterministic, exact, and instantly explainable. A new abuse
pattern ships as a rule in minutes; retraining a model takes days. They also
resolve most traffic outright, which is what keeps the median cheap.

**3. Classification** is the expensive layer, so it is reached last and skipped
entirely when a rule has already proposed a block — no probabilistic signal can
make a block *less* restrictive, so the inference would be paid for and thrown
away. It sits behind a timeout: a slow model degrades the decision, it does not
hold the request.

**4. Policy resolution** is the only place in the system that decides anything.
Everything upstream produces *evidence*; this one pure function turns evidence
into a verdict. Keeping that boundary sharp is what makes the system governable —
a policy change is a change to one small tested function, not an archaeology
expedition across every detector.

## Five design commitments

These are the decisions that shaped everything else. Each is recorded as an ADR
in [`docs/adr/`](docs/adr/) with its alternatives and its costs.

### Verdicts are three-valued

`allow`, `review`, `block`. A binary system forces the platform to pretend it is
certain. `review` is the honest answer across the wide middle band where
automated confidence is insufficient, and it is where the human escalation path
attaches. Most real moderation volume lives in that band.

### Degradation resolves toward humans

When a stage fails and evidence fired anyway, the verdict is `review`. Not
fail-open, not fail-closed — both are wrong when the truthful answer is *this
system does not currently know*. Every stage can fail without failing the
request, and the decision is flagged `degraded` so consumers can see it was made
on partial evidence.

### Decisions are explainable by construction

A `Decision` carries every `Signal` that contributed to it, which detector
produced it, the policy version that resolved them, and the latency consumed.
Not a log line written alongside the decision — part of the decision object
itself. A decision you cannot reconstruct is a decision you cannot defend.

### Evidence and verdicts are different things

Rules and models emit `Signal`s. Only the policy layer emits a `Verdict`. This
sounds like bookkeeping and is the reason a threshold change is a one-line diff
instead of a migration across every detector in the system.

### Confidence and authority are separate axes

A detector can be near-certain that something is *present* and still have no
standing to judge whether it *violates*. A Luhn-valid card number is a card
number — but a shopkeeper posting their own phone number and someone doxxing a
stranger produce byte-identical evidence.

So a `Signal` carries a `max_verdict` ceiling alongside its score. `PIIRule`
scores 0.95 and caps itself at `review`: confident, and explicitly not entitled
to enforce alone. The ceiling binds only its own category, so corroborating
evidence elsewhere can still escalate. Collapsing these two axes into one number
is how a moderation system ends up silencing people for publishing their own
contact details.

## What is deliberately *not* here

**No bundled term list.** Not in the rules, not in the classifier. A shipped
keyword list would look like a safety baseline while being trivially evaded by
the first adversary who tried, and would encode the dialect and register of
whoever wrote it. Operators supply their own, versioned alongside their policy.
See [`docs/POLICY.md`](docs/POLICY.md).

**No pretend safety model.** The bundled `LexiconClassifier` is an explicit
stand-in — a weighted lexicon with an empty default — that exists so the seam
where a real model plugs in is visible and tested. Phase 2 replaces it with a
fine-tuned transformer behind the identical interface, and nothing above it
changes.

**No content in the event stream.** Decision events carry the verdict and its
justification, never the material that was moderated. Fanning raw user content
into every downstream analytics consumer is how a safety system becomes a
privacy incident.

---

## Quickstart

Requires Python 3.11+. No external infrastructure — Redis and Kafka are
optional, and the platform boots with in-process equivalents.

```bash
git clone https://github.com/probalbose/safeguard.git
cd safeguard
make install          # creates .venv and installs with dev extras
make test             # run the suite
make run              # API on http://127.0.0.1:8000  (docs at /docs)
```

Check one string without starting a server:

```bash
.venv/bin/safeguard check "Thanks, that fixed it!"
```

### Calling the API

```bash
curl -s localhost:8000/v1/moderate \
  -H 'content-type: application/json' \
  -d '{
        "content": "https://a.example https://b.example https://c.example https://d.example https://e.example",
        "actor": {"id": "user_8812", "account_age_days": 0}
      }' | jq
```

```json
{
  "decision_id": "e15b8eb0-1922-4754-97e6-f111c3507e29",
  "request_id": "0f55990d-124e-431b-8d79-5fbcb88e2e63",
  "verdict": "block",
  "score": 0.9,
  "categories": ["fraud", "spam"],
  "reason_codes": ["SPAM_LINK_FLOOD", "FRAUD_NEW_ACCOUNT_LINKS"],
  "signals": [
    {
      "source": "rule",
      "detector": "spam.link_flood",
      "category": "spam",
      "score": 0.6,
      "reason_code": "SPAM_LINK_FLOOD",
      "explanation": "content contains 5 links"
    },
    {
      "source": "rule",
      "detector": "fraud.new_account_links",
      "category": "fraud",
      "score": 0.9,
      "reason_code": "FRAUD_NEW_ACCOUNT_LINKS",
      "explanation": "account is 0d old and content contains links"
    }
  ],
  "policy_version": "2026.07.1",
  "latency_ms": 0.043,
  "degraded": false,
  "shadow": false
}
```

Neither signal alone would block. Link flooding scores 0.6 — review territory.
A day-zero account posting links scores 0.9, which reaches the block threshold
on its own; the spam signal corroborates it in a separate category. The response
shows both, so an integrator can see the decision rested on account age rather
than on link count.

Note what the response gives an integrator: not just what happened, but which
detector caused it, how confident it was, and why — enough to render a user-facing
explanation, route an appeal, or debug a false positive without reading logs.

### Endpoints

| Method | Path                   | Purpose                                        |
| ------ | ---------------------- | ---------------------------------------------- |
| `POST` | `/v1/moderate`         | Evaluate content, return a verdict             |
| `GET`  | `/v1/policy`           | Thresholds, rules, and model currently in force |
| `GET`  | `/healthz`             | Liveness — never touches dependencies          |
| `GET`  | `/readyz`              | Readiness                                      |
| `GET`  | `/v1/debug/decisions`  | Recent decisions (non-production only)         |

### Using it as a library

```python
import asyncio
from safeguard import ModerationRequest, build_pipeline
from safeguard.core.models import Actor

async def main():
    pipeline = build_pipeline()
    await pipeline.startup()

    decision = await pipeline.decide(
        ModerationRequest(
            content="buy now buy now buy now buy now buy now buy now",
            actor=Actor(id="user_8812", account_age_days=1),
        )
    )
    print(decision.verdict, decision.score, decision.reason_codes)

    await pipeline.shutdown()

asyncio.run(main())
```

### Configuration

Every setting has a default that boots with no infrastructure. Copy
[`.env.example`](.env.example) to `.env` to change any of them. Optional
backends turn on purely by supplying a connection string:

```bash
SAFEGUARD_REDIS_URL=redis://localhost:6379/0          # shared velocity counters
SAFEGUARD_KAFKA_BOOTSTRAP_SERVERS=localhost:9092      # durable decision stream
SAFEGUARD_SHADOW_MODE=true                            # evaluate, log, enforce nothing
```

```bash
make infra-up     # Redis + Kafka via docker compose
pip install -e ".[redis,kafka]"
```

**Shadow mode** deserves a note: it runs the full pipeline, records what *would*
have happened in `shadow_verdict`, and returns `allow`. It is how a new policy
or model version is validated against live traffic before it is allowed to
affect a single user. Nothing else in the system needs to know it is on.

### Adding a rule

```python
from safeguard.core.models import Category, Verdict
from safeguard.rules.engine import RegexRule, RuleEngine
from safeguard.rules.builtin import default_rules
from safeguard.core.pipeline import build_pipeline

engine = RuleEngine(default_rules())
engine.register(
    RegexRule(
        id="policy.invite_link",
        pattern=r"discord\.gg/\w+",
        category=Category.SPAM,
        reason_code="SPAM_INVITE_LINK",
        score=0.7,
        priority=25,               # lower runs first
    )
)

pipeline = build_pipeline(rule_engine=engine)
```

A rule returns a `Signal` or `None`. It sets `proposed_verdict=Verdict.BLOCK`
only when its precision is already established — that short-circuits the
pipeline and skips the model entirely.

---

## Documentation

| Document                                       | What it covers                                            |
| ---------------------------------------------- | --------------------------------------------------------- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Components, data flow, failure modes, scaling, production gaps |
| [`docs/POLICY.md`](docs/POLICY.md)             | Category taxonomy, thresholds, appeals, versioning        |
| [`docs/THREAT-MODEL.md`](docs/THREAT-MODEL.md) | Adversaries, attack surfaces, mitigations, residual risk  |
| [`docs/ROADMAP.md`](docs/ROADMAP.md)           | Phases, scope boundaries, what is intentionally out       |
| [`docs/adr/`](docs/adr/)                       | Architecture decision records, with alternatives rejected |
| [`CONTRIBUTING.md`](CONTRIBUTING.md)           | Development workflow and standards                        |
| [`SECURITY.md`](SECURITY.md)                   | Vulnerability disclosure                                  |

## Project layout

```
src/safeguard/
├── api/              FastAPI gateway — routes, wire schemas, app factory
├── core/
│   ├── models.py     Domain model: Signal, Decision, Verdict
│   ├── pipeline.py   Stage orchestration and degradation handling
│   └── policy.py     Pure resolution: evidence -> verdict
├── rules/            Deterministic layer — engine and baseline rules
├── classifier/       Probabilistic layer — interface and stand-in
├── features/         Velocity counters — in-memory and Redis
├── events/           Decision stream — in-memory and Kafka
└── observability/    Structured logging and optional tracing
```

## Status

**Phase 1 — working skeleton.** The decision path, degradation behaviour, and
audit trail are complete and tested end to end. The classifier layer is an
explicit placeholder. See [`docs/ROADMAP.md`](docs/ROADMAP.md) for what Phase 2
and 3 add, and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#not-yet-production-ready)
for the honest list of what a production deployment still needs.

## Contributing

Issues and pull requests are welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md)
for the development workflow. `make check` runs everything CI runs.

## License

MIT — see [LICENSE](LICENSE).
