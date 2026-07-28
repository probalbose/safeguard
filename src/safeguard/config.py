"""Runtime configuration.

Every setting has a default that lets SafeGuard boot with no external
infrastructure. Optional backends (Redis, Kafka, OTLP) are enabled purely by
supplying a connection string — there is no separate "enabled" flag to drift
out of sync with the endpoint it guards.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    """Process-wide settings, populated from the environment or a `.env` file."""

    model_config = SettingsConfigDict(
        env_prefix="SAFEGUARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Service ---------------------------------------------------------
    environment: Environment = "development"
    service_name: str = "safeguard"
    log_level: str = "INFO"

    # --- Decision policy -------------------------------------------------
    policy_version: str = "2026.07.1"
    block_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    review_threshold: float = Field(default=0.60, ge=0.0, le=1.0)

    #: Evaluate the full pipeline, emit events, but always return ALLOW. This is
    #: how a new policy or model version is validated against live traffic
    #: before it is allowed to affect users.
    shadow_mode: bool = False

    #: Soft budget for the whole pipeline. Exceeding it does not abort the
    #: request; it flags the decision so budget breaches are measurable.
    latency_budget_ms: int = Field(default=150, gt=0)

    # --- Backends (unset => in-process implementation) -------------------
    redis_url: str | None = None
    kafka_bootstrap_servers: str | None = None
    kafka_decisions_topic: str = "safeguard.decisions.v1"
    otlp_endpoint: str | None = None

    @model_validator(mode="after")
    def _thresholds_are_ordered(self) -> Settings:
        if self.review_threshold > self.block_threshold:
            raise ValueError(
                "review_threshold must be <= block_threshold; "
                f"got review={self.review_threshold} block={self.block_threshold}"
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that configuration is read once and cannot change underneath a
    request. Call ``get_settings.cache_clear()`` in tests that need to vary it.
    """
    return Settings()
