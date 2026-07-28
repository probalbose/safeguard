"""Deterministic rule layer — the cheap, explainable first pass."""

from safeguard.rules.builtin import default_rules
from safeguard.rules.engine import Rule, RuleEngine, RuleResult

__all__ = ["Rule", "RuleEngine", "RuleResult", "default_rules"]
