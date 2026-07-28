"""Logging and tracing setup."""

from safeguard.observability.telemetry import configure_logging, instrument_app

__all__ = ["configure_logging", "instrument_app"]
