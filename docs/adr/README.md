# Architecture decision records

An ADR captures one significant decision: the context that forced it, what was
chosen, what was rejected and why, and what it costs. The last part matters
most. A record that lists only benefits is marketing, and it teaches the next
person nothing about when to revisit the decision.

## Index

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-layered-decision-pipeline.md) | Layered decision pipeline, rules before models | Accepted |
| [0003](0003-three-valued-verdicts.md) | Three-valued verdicts | Accepted |
| [0004](0004-degrade-toward-human-review.md) | Degrade toward human review | Accepted |
| [0005](0005-separate-evidence-from-verdicts.md) | Separate evidence from verdicts | Accepted |
| [0006](0006-no-bundled-term-list.md) | Ship no bundled term list | Accepted |
| [0007](0007-optional-backends-with-in-process-defaults.md) | Optional backends, in-process defaults | Accepted |

## Format

```markdown
# ADR-NNNN: Title

**Status:** Proposed | Accepted | Superseded by ADR-MMMM
**Date:** YYYY-MM-DD

## Context
The forces at play. What made a decision necessary.

## Decision
What was chosen, stated plainly.

## Alternatives considered
What else was on the table, and the specific reason each was rejected.

## Consequences
What this makes easy. What it makes hard. What it rules out.
Costs are not optional in this section.
```

## Conventions

- Numbered sequentially, never renumbered.
- Immutable once accepted. To change a decision, write a new ADR and mark the
  old one superseded. The history is the value.
- One decision per record.
- Written in past tense from the point of decision.
