# Roadmap

Where SafeGuard is, where it goes, and what it deliberately will not become.

---

## Phase 1 — Decision skeleton ✅

**Done.** The decision path exists end to end and is tested.

- Domain model: `Signal`, `Decision`, `Verdict`, `Category`, `Actor`
- Four-stage pipeline with per-stage failure isolation
- Rule engine with priority ordering, error isolation, and short-circuit
- Five structural baseline rules
- Pure per-category policy resolution with floors and ceilings
- Feature store: in-memory and Redis, velocity windows
- Decision events: in-memory and Kafka, content excluded by design
- FastAPI gateway, CLI, structured logging, optional OTel
- 100+ tests; ruff and mypy strict clean in CI

**Deliberately unfinished:** no real classifier, no auth, no review workflow.

## Phase 2 — Real classification

The point of Phase 1 was to make this a drop-in.

**2.1 Model integration**

- Fine-tuned transformer behind the existing `Classifier` interface
- Model served separately from the gateway, scaled independently
- Batching, request coalescing, warm pools
- Per-category calibration so scores mean the same thing across categories —
  uncalibrated scores make a single global threshold meaningless

**2.2 Adversarial robustness** (closes [T1](THREAT-MODEL.md#t1--evasion-a1))

- Unicode normalisation, confusable folding, whitespace canonicalisation
- Cross-message aggregation for content split across submissions
- An evasion test corpus in CI, so robustness regressions fail the build

**2.3 Evaluation harness**

- Held-out labelled sets per category
- Precision/recall by category *and by demographic slice* — an aggregate metric
  hides exactly the failure that matters most
- Threshold sweeps that report the operating curve rather than a single number
- Shadow-mode comparison tooling

**2.4 Dynamic rules**

Rules are currently code, which contradicts the "ship a rule in minutes" claim.
Needs persistent rule storage, a validation and staging path, hot reload, and an
audit log of who changed what.

## Phase 3 — Human review and MCP

**3.1 Review workflow.** The largest gap by volume of work, and the one that
most determines whether the system is honest. `review` is currently a verdict
with no queue behind it.

- Queue with prioritisation and SLA tracking
- Reviewer interface showing the full signal set
- Reviewer agreement measurement
- **Labels flow back into the training corpus** — this is the whole point.
  Reviewed decisions are the highest-quality labels a moderation system has,
  because they are precisely the cases automation could not resolve.

**3.2 Appeals.** Reversal endpoint emitting a correction event on the same
partition; reviewer attribution; overturned decisions weighted heavily in
retraining, since they are the cases the system got *wrong*.

**3.3 MCP server.** Expose moderation as tools to LLM agents:

| Tool | Purpose |
| --- | --- |
| `moderate_content` | Evaluate content, return a decision |
| `explain_decision` | Retrieve full provenance by `decision_id` |
| `describe_policy` | Report the active taxonomy and thresholds |
| `simulate_policy` | Evaluate under proposed thresholds without enforcing |

`simulate_policy` is the interesting one: it lets an agent reason about a policy
change before anyone ships it, which is only possible because resolution is a
pure function.

**Constraint.** MCP tools inherit the caller's authority and must not become a
privilege-escalation path. An agent that can call `moderate_content` must not
thereby be able to change policy, and `explain_decision` must not become an
unauthenticated read of the audit trail — see
[T4](THREAT-MODEL.md#t4--policy-extraction-a4).

## Phase 4 — Production hardening

Everything in [ARCHITECTURE.md §8](ARCHITECTURE.md#8-not-yet-production-ready):

- Authentication, authorisation, multi-tenancy
- Rate limiting and quota enforcement
- Data retention and deletion (GDPR erasure has nowhere to land today)
- Per-surface and per-tenant policy
- Load and soak testing against a stated SLO
- Multimodal: image and multimodal classification behind the same interface

---

## Out of scope

Saying no is what keeps the scope coherent.

**A recommended content policy.** SafeGuard is mechanism. What is acceptable
depends on jurisdiction, surface, and community, and is the operator's call.

**A bundled term list.** See [POLICY.md](POLICY.md#term-lists). It would be
treated as a safety baseline while being trivially evaded and quietly biased.

**Identity, account lifecycle, or reputation computation.** SafeGuard consumes
actor signals; it does not own accounts.

**Content storage.** Decisions reference content by request; the platform stores
it. Keeping content out of this system is a privacy property worth protecting.

**A general-purpose rules DSL.** Attractive and a trap: DSLs grow into
untestable, unversionable languages. Rules stay Python behind a narrow
interface.

**Real-time model training.** Online learning on adversarial input is a data
poisoning vector. Retraining stays offline, from reviewed labels.

## Ordering

Phase 2 before Phase 3 because a real classifier makes the review queue
meaningful — reviewing the output of a placeholder teaches nothing. Phase 4 is
required before *any* deployment regardless of what else has shipped, and
several of its items (auth, rate limiting) are genuinely prerequisites rather
than a later phase.
