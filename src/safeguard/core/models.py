"""The domain model.

Three ideas carry the whole system:

``Signal``
    One piece of evidence from one detector. A rule fires, a classifier scores
    a category — each produces a Signal. Signals are never verdicts; they are
    inputs to a verdict. Keeping them separate is what makes a decision
    explainable after the fact.

``Decision``
    The single auditable output. It carries not just the verdict but every
    signal that contributed, the policy version that resolved them, and the
    latency consumed. A decision you cannot reconstruct is a decision you
    cannot defend to a regulator, an appeals process, or a user.

``Verdict``
    Deliberately three-valued. A binary allow/block forces the system to
    pretend it is certain. REVIEW is the honest answer for the large middle
    band where automated confidence is insufficient, and it is where a human
    escalation path attaches.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Verdict(StrEnum):
    """What the platform decided to do with a piece of content."""

    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"

    @property
    def severity(self) -> int:
        """Ordering used to merge verdicts: the most restrictive one wins."""
        return {"allow": 0, "review": 1, "block": 2}[self.value]


class Category(StrEnum):
    """Policy categories a piece of content may violate.

    This taxonomy is intentionally shallow. Deep hierarchies look rigorous and
    then collapse in practice, because annotators disagree about which of two
    adjacent leaves applies and the disagreement silently poisons the training
    label. Start flat, split a category only when the data demands it, and
    version the taxonomy (see ``docs/POLICY.md``).
    """

    HARASSMENT = "harassment"
    HATE = "hate"
    SELF_HARM = "self_harm"
    SEXUAL = "sexual"
    VIOLENCE = "violence"
    DANGEROUS_ADVICE = "dangerous_advice"
    MALICIOUS_CODE = "malicious_code"
    FRAUD = "fraud"
    SPAM = "spam"
    PII = "pii"


class SignalSource(StrEnum):
    """Which layer of the pipeline produced a signal."""

    RULE = "rule"
    CLASSIFIER = "classifier"
    REPUTATION = "reputation"


class ContentType(StrEnum):
    TEXT = "text"
    #: Reserved for later phases; the pipeline shape does not change.
    IMAGE = "image"
    MULTIMODAL = "multimodal"


class Actor(BaseModel):
    """Who produced the content.

    Actor history is what separates a moderation system from a text classifier.
    The same sentence from a five-year-old account with no violations and from
    an account created ninety seconds ago are not the same risk.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=128)
    account_age_days: int | None = Field(default=None, ge=0)
    prior_violations: int = Field(default=0, ge=0)
    trusted: bool = False


class ModerationRequest(BaseModel):
    """A single piece of content submitted for a decision."""

    model_config = ConfigDict(frozen=True)

    content: str = Field(min_length=1, max_length=100_000)
    content_type: ContentType = ContentType.TEXT
    actor: Actor | None = None
    #: Free-form call-site context, e.g. surface, locale, thread id.
    context: dict[str, Any] = Field(default_factory=dict)
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class Signal(BaseModel):
    """One piece of evidence contributing to a decision."""

    model_config = ConfigDict(frozen=True)

    source: SignalSource
    #: Stable identifier for the detector: a rule id or a model name.
    detector: str
    category: Category
    #: Confidence in [0, 1]. Deterministic rules emit 1.0.
    score: float = Field(ge=0.0, le=1.0)
    #: Machine-readable reason, stable across releases, safe to show users.
    reason_code: str
    #: Human-readable explanation for reviewers and audit logs.
    explanation: str = ""
    #: Verdict this detector would return on its own, if it is entitled to one.
    #: ``None`` means "evidence only, defer to the policy layer".
    proposed_verdict: Verdict | None = None
    #: The most restrictive verdict this evidence may cause on its own.
    #:
    #: A detector can be highly confident that something is *present* while
    #: knowing it cannot judge whether it is *violating*. Detecting a phone
    #: number is near-certain; deciding whether posting it is doxxing or a
    #: shopkeeper listing their own contact details is not a judgement any
    #: single-message detector can make. Such a detector sets a ceiling of
    #: REVIEW: confident, and explicitly not entitled to enforce alone.
    #:
    #: ``None`` means no ceiling. The ceiling binds only the category this
    #: signal belongs to — corroborating evidence in another category can still
    #: produce a more restrictive outcome.
    max_verdict: Verdict | None = None


class Decision(BaseModel):
    """The auditable output of the pipeline."""

    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str
    verdict: Verdict
    #: Aggregate risk in [0, 1] after policy resolution.
    score: float = Field(ge=0.0, le=1.0)
    categories: list[Category] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    policy_version: str
    latency_ms: float = Field(ge=0.0)
    #: True when the pipeline degraded (a stage failed) and the decision was
    #: made on partial evidence. Consumers should treat these as lower trust.
    degraded: bool = False
    #: True when shadow mode suppressed an enforcement verdict. The verdict
    #: field is ALLOW; ``shadow_verdict`` holds what would have happened.
    shadow: bool = False
    shadow_verdict: Verdict | None = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def reason_codes(self) -> list[str]:
        """Deduplicated reason codes, preserving first-seen order."""
        return list(dict.fromkeys(s.reason_code for s in self.signals))

    @property
    def is_enforced(self) -> bool:
        """Whether this decision actually restricted the content."""
        return self.verdict is not Verdict.ALLOW
