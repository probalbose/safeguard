"""The classifier interface.

The contract is deliberately narrow: take a request, return signals. It says
nothing about *how* — in-process model, a call to a model server, an LLM
judge, an ensemble of all three. The pipeline depends on this interface and
nothing below it, so the model can be replaced without the decision path
changing shape.

The method is ``async`` even though the bundled implementations are pure CPU
work. Real inference is a network call to a model server; making the interface
async from the start avoids the retrofit that otherwise arrives exactly when
the system is under load and least able to absorb a refactor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from safeguard.core.models import ModerationRequest, Signal


class Classifier(ABC):
    """Scores content against the policy taxonomy."""

    #: Stable model identifier. Recorded on every signal so a decision can be
    #: traced back to the exact version that produced it.
    name: str = "classifier"

    @abstractmethod
    async def classify(
        self, request: ModerationRequest, features: dict[str, Any]
    ) -> list[Signal]:  # pragma: no cover - interface
        """Return zero or more signals for this request.

        Implementations should return signals only for categories that cleared
        their own reporting floor. Emitting a 0.02 score for every category
        buries the real evidence in noise and inflates every audit record.
        """

    async def warmup(self) -> None:
        """Optional: load weights, prime caches, establish connections.

        Called once at startup so the first production request does not pay the
        cold-start cost — which is otherwise the p99.9 nobody can explain.
        """
        return None

    async def aclose(self) -> None:
        """Optional: release connections and resources on shutdown."""
        return None


class NullClassifier(Classifier):
    """A classifier that never fires.

    Used to run the platform rules-only: during bring-up, as the control arm
    when a model is being evaluated in shadow, and as the documented fallback
    when a model backend is unavailable.
    """

    name = "null"

    async def classify(self, request: ModerationRequest, features: dict[str, Any]) -> list[Signal]:
        return []
