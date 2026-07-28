"""API contract tests.

These pin the wire format. Once a caller depends on `verdict` or a reason code,
changing either is a breaking change regardless of what the internals do.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from safeguard.api.app import create_app
from safeguard.config import Settings


@asynccontextmanager
async def _client_for(app) -> AsyncIterator[AsyncClient]:
    """Yield a client with the app's lifespan running.

    The lifespan context is what populates ``app.state``; without it the
    dependency that resolves the pipeline would find nothing.
    """
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as ac,
    ):
        yield ac


@pytest.fixture
async def client(settings: Settings, pipeline):
    async with _client_for(create_app(settings, pipeline=pipeline)) as ac:
        yield ac


async def test_healthz(client) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readyz(client) -> None:
    assert (await client.get("/readyz")).status_code == 200


async def test_moderate_benign_content(client) -> None:
    response = await client.post(
        "/v1/moderate", json={"content": "Great write-up, thanks for sharing."}
    )
    assert response.status_code == 200

    body = response.json()
    assert body["verdict"] == "allow"
    assert body["score"] == 0.0
    assert body["signals"] == []
    assert body["policy_version"] == "test.1"


async def test_moderate_spam_content(client) -> None:
    content = " ".join(f"https://spam{i}.example" for i in range(12))
    response = await client.post(
        "/v1/moderate",
        json={"content": content, "actor": {"id": "u_new", "account_age_days": 0}},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["verdict"] in ("review", "block")
    assert body["reason_codes"]
    assert body["signals"]


async def test_caller_supplied_request_id_is_echoed(client) -> None:
    """Callers correlate their logs by request id; silently replacing it breaks
    every downstream investigation."""
    response = await client.post(
        "/v1/moderate", json={"content": "hello there", "request_id": "corr-12345"}
    )
    assert response.json()["request_id"] == "corr-12345"


async def test_empty_content_is_rejected(client) -> None:
    assert (await client.post("/v1/moderate", json={"content": ""})).status_code == 422


async def test_missing_content_is_rejected(client) -> None:
    assert (await client.post("/v1/moderate", json={})).status_code == 422


async def test_oversized_content_is_rejected(client) -> None:
    response = await client.post("/v1/moderate", json={"content": "x" * 100_001})
    assert response.status_code == 422


async def test_policy_endpoint_describes_active_configuration(client) -> None:
    response = await client.get("/v1/policy")
    assert response.status_code == 200

    body = response.json()
    assert body["policy_version"] == "test.1"
    assert body["block_threshold"] == 0.90
    assert body["review_threshold"] == 0.60
    assert "spam.link_flood" in body["rules"]
    assert body["classifier"] == "null"


async def test_debug_endpoint_returns_recent_decisions(client) -> None:
    await client.post("/v1/moderate", json={"content": "first message here"})
    await client.post("/v1/moderate", json={"content": "second message here"})

    body = (await client.get("/v1/debug/decisions")).json()
    assert body["count"] == 2
    assert all("verdict" in d for d in body["decisions"])


async def test_debug_endpoint_is_hidden_in_production(settings, pipeline) -> None:
    """Decision records are audit data. An unauthenticated debug endpoint is
    not an access-control model."""
    prod = settings.model_copy(update={"environment": "production"})
    async with _client_for(create_app(prod, pipeline=pipeline)) as ac:
        assert (await ac.get("/v1/debug/decisions")).status_code == 404


async def test_openapi_is_disabled_in_production(settings, pipeline) -> None:
    prod = settings.model_copy(update={"environment": "production"})
    async with _client_for(create_app(prod, pipeline=pipeline)) as ac:
        assert (await ac.get("/openapi.json")).status_code == 404
