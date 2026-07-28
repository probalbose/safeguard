# Contributing

Thanks for taking an interest. This document covers the workflow and the
standards a change is held to.

## Setup

```bash
git clone https://github.com/probalbose/safeguard.git
cd safeguard
make install      # .venv + dev extras
make check        # lint, typecheck, test — everything CI runs
```

Python 3.11+. No external infrastructure needed; the test suite is hermetic by
design ([ADR-0007](docs/adr/0007-optional-backends-with-in-process-defaults.md)).

```
make help         # list targets
make test         # pytest
make cov          # with coverage
make fmt          # auto-format and autofix
make typecheck    # mypy strict
make run          # dev server on :8000
make infra-up     # Redis + Kafka, if you need them
```

## Workflow

1. Open an issue first for anything non-trivial — especially rule or policy
   changes, where the reasoning matters more than the diff.
2. Branch from `main`.
3. Write the test first. For a bug, the test should fail before your fix.
4. `make check` must pass locally.
5. Open a PR describing **what changed and why**. The why is the part review
   will focus on.

## Standards

**Type hints everywhere.** `mypy` runs in strict mode. `Any` needs a comment
justifying it.

**Formatting is not a discussion.** `ruff format`, 100 columns. `make fmt`.

**Comments explain *why*.** The code says what it does. A comment restating the
code is noise; a comment explaining why the obvious approach was rejected is the
most valuable line in the file.

```python
# Bad
score = min(1.0, 0.5 + 0.1 * excess)  # cap the score at 1.0

# Good
# Saturating score: 4 links is suspicious, 40 is not ten times worse.
score = min(1.0, 0.5 + 0.1 * excess)
```

**Docstrings on every public module, class, and function.** Module docstrings
carry the design reasoning — they are where a reader learns why the module is
shaped the way it is.

## Testing

New behaviour needs tests. In this codebase specifically:

**Policy changes need dense tests.** `core/policy.py` is the only place verdicts
are made. A bug there silently changes outcomes for real users. Test the
boundaries — a score exactly on a threshold is policy, not an implementation
detail.

**New rules need false-positive tests.** Add benign content to
`test_default_rules_are_silent_on_benign_content`. A rule that fires on ordinary
conversation is worse than no rule, and this is the test that catches it.

**Failure paths need explicit tests.** Errors are deliberately swallowed
([ADR-0004](docs/adr/0004-degrade-toward-human-review.md)), so nothing throws to
reveal a broken degradation path. If it is not tested, it is not working.

**No network in tests.** No Redis, no Kafka, no model server. If your change
cannot be tested without them, that is a design signal worth discussing in the
issue.

## Adding a rule

Rules go in `src/safeguard/rules/builtin.py` or your own module.

```python
class MyRule(Rule):
    id = "category.descriptive_name"     # stable — it appears in audit logs
    category = Category.SPAM
    reason_code = "SPAM_DESCRIPTIVE"     # public API — see POLICY.md
    priority = 45                        # lower runs first

    def evaluate(self, request, features) -> Signal | None:
        if not <condition>:
            return None
        return self._signal(score, "explanation of what matched")
```

Before you open the PR:

- **Is it structural or semantic?** Structural signals (shape, counts,
  checksums, velocity) belong in rules. Semantic judgement belongs in the
  classifier. A rule that tries to understand meaning will be evaded and will
  be biased.
- **No term lists.** See [ADR-0006](docs/adr/0006-no-bundled-term-list.md). This
  one is not negotiable without a superseding ADR.
- **Does it need `proposed_verdict`?** Only if the rule's precision is already
  established. It short-circuits the pipeline and skips the model.
- **Does it need `max_verdict`?** Set a ceiling if the rule can be confident
  something is *present* without being qualified to judge that it *violates*.
  `PIIRule` is the worked example.
- **What is the false-positive profile?** Who gets caught who shouldn't? If you
  cannot answer, the rule is not ready.

## Architecture decisions

Significant changes need an ADR — see [`docs/adr/README.md`](docs/adr/README.md).
"Significant" means hard to reverse, or non-obvious enough that someone would
reasonably undo it without the context.

ADRs are immutable once accepted. To change a decision, write a new one marking
the old superseded.

Every ADR states what the decision **costs**. A record listing only benefits
will be sent back.

## Safety-relevant changes

Extra scrutiny applies to anything touching what gets enforced. Say so in the PR
description if your change:

- moves a threshold or changes how scores combine
- lets a detector enforce more than it could before
- changes degradation behaviour
- adds a detector with meaning-based (rather than structural) triggers

Not to slow it down — to make sure the reasoning is on the record when someone
revisits it in a year.

## Scope

Please read [ROADMAP.md — Out of scope](docs/ROADMAP.md#out-of-scope) before
proposing a feature. Several attractive ideas are deliberately excluded, with
reasons.
