# Policy model

What SafeGuard evaluates, how thresholds are set, and how policy changes safely.

Nothing here is a recommended content policy. SafeGuard is *mechanism*: a
platform for expressing and enforcing a policy consistently and auditably. The
policy itself belongs to the operator, informed by their jurisdiction, their
users, and their obligations. Every threshold below is a default to be replaced,
not a recommendation to be adopted.

---

## Categories

```python
class Category(StrEnum):
    HARASSMENT        # targeted abuse directed at a person or group
    HATE              # attacks based on a protected characteristic
    SELF_HARM         # content promoting or instructing self-injury
    SEXUAL            # sexual content; surface-dependent
    VIOLENCE          # threats, incitement, glorification
    DANGEROUS_ADVICE  # guidance with serious physical-harm potential
    MALICIOUS_CODE    # malware, exploits, credential harvesting
    FRAUD             # scams, impersonation, deceptive commerce
    SPAM              # unsolicited bulk, engagement farming
    PII               # exposed personal data
```

### Why it is flat

Deep taxonomies look rigorous and then collapse in practice. Split
`HARASSMENT` into eight leaves and annotators start disagreeing about which of
two adjacent leaves applies. That disagreement does not stay in the annotation
tool — it becomes label noise, and label noise trains a worse model than the
coarse taxonomy would have. It also breaks every longitudinal metric, because
the same content is counted differently before and after the split.

Start flat. Split a category only when you have evidence the data demands it —
consistent annotator agreement on the proposed boundary, and a decision that
would actually differ across it. Then version the taxonomy.

### Adding a category

1. Write the definition, with three positive and three near-miss examples.
2. Confirm annotator agreement on the near-misses. If humans cannot agree,
   neither will the model, and the category is not ready.
3. Add the enum member and bump `SAFEGUARD_POLICY_VERSION`.
4. Ship in shadow mode. Measure volume and precision before enforcing.

Never reuse or renumber an existing member. Historical decisions reference these
values, and an audit trail whose vocabulary changes underneath it is not an
audit trail.

## Thresholds

| Setting | Default | Meaning |
| --- | --- | --- |
| `SAFEGUARD_REVIEW_THRESHOLD` | `0.60` | At or above, route to human review |
| `SAFEGUARD_BLOCK_THRESHOLD` | `0.90` | At or above, block automatically |

```
 0.0                    0.60                   0.90              1.0
  ├──────── allow ───────┼────── review ────────┼───── block ─────┤
     no action              human decides          auto-enforced
```

Thresholds are validated at startup: `review_threshold <= block_threshold`. The
service refuses to boot otherwise rather than silently resolving the
contradiction — a misconfigured threshold is a policy failure, not a warning.

### Choosing them

The gap between the thresholds is the honest part of the system. Widening it
routes more volume to humans: higher precision, more cost, slower resolution.
Narrowing it automates more: cheaper and faster, and every false positive lands
directly on a user with no human in the way.

Set them from measured distributions, not intuition:

1. Run in **shadow mode** against real traffic.
2. Plot the score distribution per category.
3. Sample and hand-label the score bands.
4. Set `block_threshold` where precision reaches the level your appeals process
   can absorb — the block band is where mistakes are least visible and most
   costly, because nobody reviews them.
5. Set `review_threshold` where recall justifies the queue volume your reviewers
   can actually clear.

Step 5 has a hard constraint: **`review` is only honest if humans clear the
queue.** An unbounded review queue silently converts "escalate to a human" into
"shadow-ban with extra steps". Queue depth is a policy metric, not an ops metric.

## Floors and ceilings

A detector expresses two independent things about its own authority:

**`proposed_verdict`** — a floor. "This evidence justifies at least this
outcome." A rule sets a BLOCK floor only when its precision is already
established. It also short-circuits the pipeline, skipping the model.

**`max_verdict`** — a ceiling. "This evidence may not exceed this on its own."

The ceiling exists because confidence and authority are different axes. A
detector can be near-certain something is *present* and have no standing to
judge whether it *violates*:

```python
# PIIRule: high confidence, deliberately low authority
return self._signal(
    score=0.95,                       # near-certain a card number is present
    proposed_verdict=Verdict.REVIEW,  # floor: a human must see this
    max_verdict=Verdict.REVIEW,       # ceiling: a human must decide it
)
```

A shopkeeper posting their own phone number and someone doxxing a stranger
produce byte-identical evidence. No single-message detector separates them, and
a system that blocks on the score alone silences the shopkeeper.

Ceilings bind only their own category. Corroborating evidence elsewhere can
still escalate — see `test_ceiling_binds_only_its_own_category`.

## Actor trust

`Actor.trusted` caps enforcement at `review`; it never suppresses a signal or
grants immunity. The reasoning cuts both ways: trusted accounts have earned the
benefit of the doubt, *and* a compromised trusted account is the most valuable
asset an attacker can acquire. Capping rather than exempting preserves both.

Trust is supplied by the caller. SafeGuard does not compute it — that requires
account lifecycle data the platform does not have.

## Versioning

`SAFEGUARD_POLICY_VERSION` is stamped on every decision. Bump it for **any**
change to observable behaviour:

- threshold changes
- adding, removing, or retuning a rule
- taxonomy changes
- a new model version

Without this, a decision from six months ago cannot be explained, because the
policy that produced it no longer exists anywhere. `GET /v1/policy` reports the
active configuration so an auditor can establish what is in force without
reading the deployment's environment.

## Shadow mode

`SAFEGUARD_SHADOW_MODE=true` runs the full pipeline, records what *would* have
happened in `shadow_verdict`, and returns `allow`. Scores, categories, and
signals are all preserved — shadow evaluation is worthless if it discards what
it measured.

This is how any policy or model change reaches production:

1. Deploy the change with shadow mode on.
2. Run against live traffic long enough to cover a weekly cycle.
3. Compare the shadow verdict distribution against the active one.
4. Hand-label a sample of the newly-enforced band.
5. Enable enforcement only if precision holds.

Nothing else in the system needs to know shadow mode is on. That is the point:
if enabling it required changes in the rules or the classifier, it would not be
testing the same code path that will run in production.

## Reason codes

Reason codes are a public API. Once a caller renders `SPAM_LINK_FLOOD` in a user
notice or routes on it in an appeals workflow, changing the string is a breaking
change regardless of what the internals do.

Rules:

- `SCREAMING_SNAKE_CASE`, prefixed with the category
- Stable across releases; deprecate rather than rename
- Safe to show a user — never leak detector internals, thresholds, or
  information that helps an adversary tune around the rule
- Deduplicated on the decision, preserving first-seen order

## Term lists

SafeGuard ships **no keyword or term list**, in either the rule layer or the
classifier. This is a policy decision, not an omission.

A bundled list would be treated as a safety baseline while being trivially
evaded by the first adversary who tried a homoglyph, and it would encode the
dialect, register, and blind spots of whoever wrote it — reliably producing
false positives against the communities it was not written for.

Operators supply their own, versioned with their policy:

```python
engine.register(
    RegexRule(
        id="policy.term_list.v3",
        pattern=load_pattern("terms/v3.txt"),
        category=Category.HARASSMENT,
        reason_code="HARASSMENT_TERM",
        score=0.7,          # evidence, not proof
        priority=25,
    )
)
```

Note the score. A term match is evidence that combines with everything else; it
is not a verdict. Term lists that block outright are the single most common
source of moderation false positives, because a word's meaning depends on who is
using it and to whom.

## Appeals

Not implemented — see [ARCHITECTURE.md](ARCHITECTURE.md#8-not-yet-production-ready).
The data model supports it: every `Decision` carries the full signal set,
`decision_id` is stable, and decisions are published to a durable stream.

A functioning appeals process needs, at minimum: a reversal endpoint that emits
a correction event on the same partition, reviewer attribution, and a path from
overturned decisions back into the training corpus. The third matters most —
appeals are the highest-quality label source a moderation system has, because
they are precisely the cases the automated system got wrong.
