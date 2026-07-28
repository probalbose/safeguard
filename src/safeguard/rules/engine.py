"""The deterministic rule layer.

Rules run before any model. Three reasons, in order of importance:

1. **Explainability.** A rule that fires tells you exactly why. When a user
   appeals or a regulator asks, "the model scored 0.94" is not an answer.
2. **Latency and cost.** Most traffic is unambiguous. Resolving it with string
   and arithmetic operations keeps the expensive layer for the hard cases.
3. **Immediate response.** A new abuse pattern can be shipped as a rule in
   minutes. Retraining and revalidating a model takes days at best.

The engine treats a failing rule as a bug in that rule, not a reason to fail
the request: the error is captured, the rule is skipped, and the decision is
marked degraded. One bad regex must not take down moderation for everything.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from safeguard.core.models import Category, ModerationRequest, Signal, SignalSource, Verdict

logger = logging.getLogger(__name__)


class Rule(ABC):
    """A single deterministic check.

    Subclasses implement :meth:`evaluate` and return a :class:`Signal` when the
    rule fires, or ``None`` when it does not. A rule never returns a verdict
    directly unless it is genuinely entitled to one (``proposed_verdict``);
    resolving competing evidence is the policy layer's job.
    """

    #: Stable identifier. Appears in audit logs, so treat it as an API.
    id: str
    #: Category this rule contributes evidence for.
    category: Category
    #: Reason code surfaced to callers and users.
    reason_code: str
    #: Lower runs first. Cheap, high-precision rules should sort early.
    priority: int = 100
    #: Rules can be disabled without deleting them, which keeps history intact.
    enabled: bool = True

    @abstractmethod
    def evaluate(
        self, request: ModerationRequest, features: dict[str, Any]
    ) -> Signal | None:  # pragma: no cover - interface
        """Return a signal if this rule fires, otherwise ``None``."""

    def _signal(
        self,
        score: float,
        explanation: str,
        proposed_verdict: Verdict | None = None,
        max_verdict: Verdict | None = None,
    ) -> Signal:
        return Signal(
            source=SignalSource.RULE,
            detector=self.id,
            category=self.category,
            score=score,
            reason_code=self.reason_code,
            explanation=explanation,
            proposed_verdict=proposed_verdict,
            max_verdict=max_verdict,
        )


@dataclass(slots=True)
class RuleResult:
    """Everything the rule layer produced for one request."""

    signals: list[Signal] = field(default_factory=list)
    #: Rule ids that raised. Non-empty means the decision is degraded.
    errored: list[str] = field(default_factory=list)
    #: True when a rule proposed a terminal verdict and evaluation stopped.
    short_circuited: bool = False

    @property
    def has_terminal_signal(self) -> bool:
        return any(s.proposed_verdict is Verdict.BLOCK for s in self.signals)


class RuleEngine:
    """Runs an ordered set of rules over a request."""

    def __init__(self, rules: list[Rule] | None = None) -> None:
        self._rules: list[Rule] = []
        for rule in rules or []:
            self.register(rule)

    def register(self, rule: Rule) -> None:
        """Add a rule, keeping the set ordered by priority and id-unique."""
        if any(existing.id == rule.id for existing in self._rules):
            raise ValueError(f"duplicate rule id: {rule.id!r}")
        self._rules.append(rule)
        self._rules.sort(key=lambda r: (r.priority, r.id))

    @property
    def rules(self) -> tuple[Rule, ...]:
        return tuple(self._rules)

    def evaluate(
        self,
        request: ModerationRequest,
        features: dict[str, Any] | None = None,
        *,
        short_circuit: bool = True,
    ) -> RuleResult:
        """Run every enabled rule.

        With ``short_circuit`` set, evaluation stops at the first rule proposing
        a BLOCK. That is the right default in production — no later signal can
        make the outcome less restrictive — but tests and offline analysis want
        the complete picture, so it can be turned off.
        """
        features = features or {}
        result = RuleResult()

        for rule in self._rules:
            if not rule.enabled:
                continue
            try:
                signal = rule.evaluate(request, features)
            except Exception:
                # A broken rule degrades the decision; it does not fail it.
                logger.exception("rule %s raised; skipping", rule.id)
                result.errored.append(rule.id)
                continue

            if signal is None:
                continue

            result.signals.append(signal)
            if short_circuit and signal.proposed_verdict is Verdict.BLOCK:
                result.short_circuited = True
                break

        return result


class RegexRule(Rule):
    """Fires when a compiled pattern matches the content.

    The building block for term lists and pattern-based abuse detection. Note
    the deliberate absence of any bundled term list: shipping one invites it to
    be treated as a safety baseline when it is nothing of the kind. Operators
    supply their own, versioned alongside their policy.
    """

    def __init__(
        self,
        *,
        id: str,
        pattern: str | re.Pattern[str],
        category: Category,
        reason_code: str,
        score: float = 1.0,
        proposed_verdict: Verdict | None = None,
        max_verdict: Verdict | None = None,
        priority: int = 100,
        explanation: str = "content matched a policy pattern",
        flags: int = re.IGNORECASE,
    ) -> None:
        self.id = id
        self.category = category
        self.reason_code = reason_code
        self.priority = priority
        self.enabled = True
        self._pattern = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern, flags)
        self._score = score
        self._proposed_verdict = proposed_verdict
        self._max_verdict = max_verdict
        self._explanation = explanation

    def evaluate(self, request: ModerationRequest, features: dict[str, Any]) -> Signal | None:
        if self._pattern.search(request.content) is None:
            return None
        return self._signal(
            self._score, self._explanation, self._proposed_verdict, self._max_verdict
        )
