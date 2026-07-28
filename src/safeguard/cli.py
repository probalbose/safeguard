"""Command line entry points.

``safeguard serve``     run the API gateway
``safeguard check``     evaluate a single string and print the decision
``safeguard policy``    print the active policy configuration
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from safeguard import __version__
from safeguard.config import get_settings
from safeguard.core.models import Actor, ModerationRequest
from safeguard.core.pipeline import build_pipeline
from safeguard.events.publisher import decision_to_event
from safeguard.observability.telemetry import configure_logging


async def _check(content: str, actor_id: str | None, account_age_days: int | None) -> int:
    settings = get_settings()
    pipeline = build_pipeline(settings)
    await pipeline.startup()
    try:
        actor = (
            Actor(id=actor_id, account_age_days=account_age_days) if actor_id is not None else None
        )
        decision = await pipeline.decide(ModerationRequest(content=content, actor=actor))
    finally:
        await pipeline.shutdown()

    print(json.dumps(decision_to_event(decision), indent=2))
    # Exit non-zero on enforcement so the CLI composes in shell pipelines.
    return 0 if not decision.is_enforced else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="safeguard", description="SafeGuard control CLI")
    parser.add_argument("--version", action="version", version=f"safeguard {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API gateway")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    check = sub.add_parser("check", help="evaluate one piece of content")
    check.add_argument("content")
    check.add_argument("--actor-id", default=None)
    check.add_argument("--account-age-days", type=int, default=None)

    sub.add_parser("policy", help="print the active policy configuration")

    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings)

    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "safeguard.api.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return 0

    if args.command == "check":
        return asyncio.run(_check(args.content, args.actor_id, args.account_age_days))

    if args.command == "policy":
        print(json.dumps(settings.model_dump(mode="json"), indent=2))
        return 0

    return 1  # pragma: no cover - argparse enforces the subcommand


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
