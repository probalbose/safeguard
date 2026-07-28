"""Wire contracts.

Kept separate from the domain model in ``safeguard.core.models`` on purpose.
The two look similar today and will diverge: the domain model is free to change
as the system learns, while the wire format is a promise to callers that can
only change through versioning. Collapsing them saves fifty lines now and costs
a breaking API change the first time an internal field is renamed.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from safeguard.core.models import (
    Category,
    ContentType,
    Decision,
    SignalSource,
    Verdict,
)


class ActorPayload(BaseModel):
    """Caller-supplied information about who produced the content."""

    id: str = Field(min_length=1, max_length=128, examples=["user_8812"])
    account_age_days: int | None = Field(default=None, ge=0, examples=[3])
    prior_violations: int = Field(default=0, ge=0, examples=[0])
    trusted: bool = Field(default=False)


class ModerateRequestPayload(BaseModel):
    """Request body for ``POST /v1/moderate``."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content": "Check out https://a.example https://b.example https://c.example "
                "https://d.example",
                "actor": {"id": "user_8812", "account_age_days": 1},
            }
        }
    )

    content: str = Field(min_length=1, max_length=100_000)
    content_type: ContentType = ContentType.TEXT
    actor: ActorPayload | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    #: Supply your own to correlate with your logs; generated when omitted.
    request_id: str | None = None


class SignalPayload(BaseModel):
    """One piece of evidence, as returned to callers."""

    source: SignalSource
    detector: str
    category: Category
    score: float
    reason_code: str
    explanation: str


class ModerateResponsePayload(BaseModel):
    """Response body for ``POST /v1/moderate``."""

    decision_id: str
    request_id: str
    verdict: Verdict
    score: float
    categories: list[Category]
    reason_codes: list[str]
    signals: list[SignalPayload]
    policy_version: str
    latency_ms: float
    degraded: bool
    shadow: bool

    @classmethod
    def from_decision(cls, decision: Decision) -> ModerateResponsePayload:
        return cls(
            decision_id=decision.decision_id,
            request_id=decision.request_id,
            verdict=decision.verdict,
            score=decision.score,
            categories=decision.categories,
            reason_codes=decision.reason_codes,
            signals=[
                SignalPayload(
                    source=s.source,
                    detector=s.detector,
                    category=s.category,
                    score=s.score,
                    reason_code=s.reason_code,
                    explanation=s.explanation,
                )
                for s in decision.signals
            ],
            policy_version=decision.policy_version,
            latency_ms=decision.latency_ms,
            degraded=decision.degraded,
            shadow=decision.shadow,
        )


class PolicyInfoPayload(BaseModel):
    """The active policy configuration.

    Exposed so that a caller — or an auditor — can establish which policy was
    in force without reading the deployment's environment.
    """

    policy_version: str
    block_threshold: float
    review_threshold: float
    shadow_mode: bool
    latency_budget_ms: int
    categories: list[Category]
    rules: list[str]
    classifier: str


class HealthPayload(BaseModel):
    status: str
    version: str
    environment: str
