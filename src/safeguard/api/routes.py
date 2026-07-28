"""HTTP routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from safeguard import __version__
from safeguard.api.schemas import (
    HealthPayload,
    ModerateRequestPayload,
    ModerateResponsePayload,
    PolicyInfoPayload,
)
from safeguard.config import Settings, get_settings
from safeguard.core.models import Actor, Category, ModerationRequest
from safeguard.core.pipeline import Pipeline
from safeguard.events.publisher import InMemoryPublisher

router = APIRouter()


def get_pipeline(request: Request) -> Pipeline:
    """Resolve the pipeline from application state.

    Held on ``app.state`` rather than a module global so tests can build an app
    with a different pipeline without monkeypatching, and so the lifespan hook
    owns its lifecycle.
    """
    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:  # pragma: no cover - would be a wiring bug
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="pipeline not initialised",
        )
    return pipeline


def get_app_settings(request: Request) -> Settings:
    """Resolve the settings this application was built with.

    Deliberately *not* ``Depends(get_settings)``: that returns the process-wide
    singleton read from the environment, which is not necessarily the object
    the app was constructed with. Reading them from ``app.state`` keeps the
    running configuration and the reported configuration the same thing — the
    alternative is a ``/v1/policy`` endpoint that confidently reports
    thresholds the pipeline is not using.
    """
    return getattr(request.app.state, "settings", None) or get_settings()


PipelineDep = Annotated[Pipeline, Depends(get_pipeline)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


@router.post(
    "/v1/moderate",
    response_model=ModerateResponsePayload,
    summary="Evaluate content and return a verdict",
    tags=["moderation"],
)
async def moderate(
    payload: ModerateRequestPayload, pipeline: PipelineDep
) -> ModerateResponsePayload:
    """Run the full decision pipeline over one piece of content.

    Always returns 200 with a decision. The pipeline degrades internally rather
    than erroring, because a caller that receives a 500 has no useful fallback:
    it must either publish unmoderated content or drop legitimate content, and
    it will choose wrong. A degraded REVIEW is a better answer than an
    exception, and the ``degraded`` flag tells the caller which it received.
    """
    actor = None
    if payload.actor is not None:
        actor = Actor(
            id=payload.actor.id,
            account_age_days=payload.actor.account_age_days,
            prior_violations=payload.actor.prior_violations,
            trusted=payload.actor.trusted,
        )

    moderation_request = ModerationRequest(
        content=payload.content,
        content_type=payload.content_type,
        actor=actor,
        context=payload.context,
        # Honour a caller-supplied correlation id; generate one otherwise.
        **({"request_id": payload.request_id} if payload.request_id else {}),
    )

    decision = await pipeline.decide(moderation_request)
    return ModerateResponsePayload.from_decision(decision)


@router.get(
    "/v1/policy",
    response_model=PolicyInfoPayload,
    summary="Describe the active policy",
    tags=["policy"],
)
async def policy_info(pipeline: PipelineDep, settings: SettingsDep) -> PolicyInfoPayload:
    """Report the thresholds, rules, and model currently in force."""
    return PolicyInfoPayload(
        policy_version=settings.policy_version,
        block_threshold=settings.block_threshold,
        review_threshold=settings.review_threshold,
        shadow_mode=settings.shadow_mode,
        latency_budget_ms=settings.latency_budget_ms,
        categories=list(Category),
        rules=[rule.id for rule in pipeline.rule_engine.rules if rule.enabled],
        classifier=pipeline.classifier.name,
    )


@router.get("/healthz", response_model=HealthPayload, summary="Liveness", tags=["ops"])
async def healthz(settings: SettingsDep) -> HealthPayload:
    """Liveness: the process is up. Never touches dependencies.

    A liveness probe that checks downstreams turns a dependency outage into a
    restart loop, which is strictly worse than the outage.
    """
    return HealthPayload(status="ok", version=__version__, environment=settings.environment)


@router.get("/readyz", response_model=HealthPayload, summary="Readiness", tags=["ops"])
async def readyz(pipeline: PipelineDep, settings: SettingsDep) -> HealthPayload:
    """Readiness: the pipeline is assembled and can serve traffic."""
    return HealthPayload(
        status="ready" if pipeline is not None else "not-ready",
        version=__version__,
        environment=settings.environment,
    )


@router.get(
    "/v1/debug/decisions",
    summary="Recent decisions (non-production only)",
    tags=["ops"],
)
async def recent_decisions(
    pipeline: PipelineDep, settings: SettingsDep, limit: int = 20
) -> dict[str, object]:
    """Return recent decisions from the in-process buffer.

    Gated to non-production: decision records are audit data, and an unauthed
    debug endpoint is not an access-control model. In production these are read
    from the Kafka topic under proper authorisation.
    """
    if settings.is_production:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if not isinstance(pipeline.publisher, InMemoryPublisher):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="decisions are streaming to an external broker; read them there",
        )
    events = pipeline.publisher.events[-limit:]
    return {"count": len(events), "decisions": events}
