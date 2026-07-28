"""A deterministic stand-in classifier.

This exists so the pipeline can be exercised end to end — and so the seam where
a real model plugs in is visible and tested — without shipping a pretend safety
model.

It is a weighted lexicon and nothing more. It ships with an **empty** lexicon by
default: the operator supplies terms and weights, versioned alongside their
policy. A bundled default would be evaded by the first adversary who tried,
would encode the dialect and register of whoever wrote it, and would invite
exactly the mistake this docstring exists to prevent — treating it as a safety
baseline.

Phase 2 of the roadmap replaces this with a fine-tuned transformer behind the
same :class:`~safeguard.classifier.base.Classifier` interface. Nothing above
this file changes when it does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from safeguard.classifier.base import Classifier
from safeguard.core.models import Category, ModerationRequest, Signal, SignalSource

_TOKEN_PATTERN = re.compile(r"[\w']+")


@dataclass(frozen=True, slots=True)
class LexiconEntry:
    """One weighted term."""

    term: str
    category: Category
    #: Contribution to the category score, in [0, 1].
    weight: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.weight <= 1.0:
            raise ValueError(f"weight must be in [0, 1]; got {self.weight}")
        if not self.term.strip():
            raise ValueError("term must not be blank")


class LexiconClassifier(Classifier):
    """Scores content by summing the weights of matched terms.

    Scores saturate rather than accumulate linearly: repeating a term twice
    raises confidence, but not without bound. Linear accumulation makes long
    documents score higher than short ones purely by length, which is the most
    common way a naive scorer ends up biased against verbose users.
    """

    name = "lexicon-v0"

    def __init__(
        self,
        entries: list[LexiconEntry] | None = None,
        *,
        reporting_floor: float = 0.15,
    ) -> None:
        self.reporting_floor = reporting_floor
        self._by_term: dict[str, list[LexiconEntry]] = {}
        for entry in entries or []:
            self._by_term.setdefault(entry.term.lower(), []).append(entry)

    def add(self, entry: LexiconEntry) -> None:
        self._by_term.setdefault(entry.term.lower(), []).append(entry)

    async def classify(self, request: ModerationRequest, features: dict[str, Any]) -> list[Signal]:
        if not self._by_term:
            return []

        tokens = _TOKEN_PATTERN.findall(request.content.lower())
        if not tokens:
            return []

        scores: dict[Category, float] = {}
        matched: dict[Category, set[str]] = {}

        for token in tokens:
            for entry in self._by_term.get(token, ()):
                # Saturating combination: p_new = p + w*(1 - p). Bounded by 1.0,
                # monotonic in evidence, and order-independent.
                current = scores.get(entry.category, 0.0)
                scores[entry.category] = current + entry.weight * (1.0 - current)
                matched.setdefault(entry.category, set()).add(entry.term)

        return [
            Signal(
                source=SignalSource.CLASSIFIER,
                detector=self.name,
                category=category,
                score=round(score, 4),
                reason_code=f"MODEL_{category.name}",
                explanation=(
                    f"lexicon matched {len(matched[category])} term(s) for {category.value}"
                ),
            )
            for category, score in sorted(scores.items(), key=lambda kv: -kv[1])
            if score >= self.reporting_floor
        ]
