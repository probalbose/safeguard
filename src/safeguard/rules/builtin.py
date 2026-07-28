"""Baseline rules shipped with SafeGuard.

These are *structural* detectors: link flooding, token repetition, PII shapes,
new-account risk, repeat offenders. Every one of them keys off the shape of the
content or the history of the actor rather than its meaning.

That is a deliberate boundary. Structural signals generalise across languages
and survive adversarial rewording, and they can be reasoned about precisely.
Meaning is the model layer's job. A bundled keyword list would look like a
safety baseline while being trivially evaded and quietly biased against the
dialects it was not written for — so none is included. Operators supply term
lists through :class:`~safeguard.rules.engine.RegexRule`, versioned with their
own policy.
"""

from __future__ import annotations

import re
from typing import Any

from safeguard.core.models import Category, ModerationRequest, Signal, Verdict
from safeguard.rules.engine import Rule

URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")
# Requires either an international prefix or at least one separator between
# digit groups. Matching bare digit runs would fire on every order reference,
# timestamp, and identifier in the corpus — the single most common way a PII
# rule becomes noise nobody reads.
PHONE_PATTERN = re.compile(
    r"(?<![\d.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})(?:[\s.-]\d{2,4}){1,4}(?![\d.])"
)
CARD_CANDIDATE_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


#: Payment card numbers run 13 to 19 digits (ISO/IEC 7812).
CARD_LENGTH_RANGE = (13, 19)


def _luhn_valid(digits: str) -> bool:
    """Luhn checksum — the same check that gates a card number at authorisation.

    Length is deliberately *not* checked here: the checksum and the card-number
    length rule are separate facts, and conflating them makes the function
    impossible to reuse or to test independently. :func:`_is_card_number`
    applies both.
    """
    if not digits.isdigit() or len(digits) < 2:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _is_card_number(candidate: str) -> bool:
    """True when a digit run has both a plausible card length and a valid Luhn."""
    digits = re.sub(r"[ -]", "", candidate)
    low, high = CARD_LENGTH_RANGE
    return low <= len(digits) <= high and _luhn_valid(digits)


class LinkFloodRule(Rule):
    """Many outbound links in one message is a durable spam signal."""

    id = "spam.link_flood"
    category = Category.SPAM
    reason_code = "SPAM_LINK_FLOOD"
    priority = 20

    def __init__(self, threshold: int = 4) -> None:
        self.threshold = threshold
        self.enabled = True

    def evaluate(self, request: ModerationRequest, features: dict[str, Any]) -> Signal | None:
        count = len(URL_PATTERN.findall(request.content))
        if count < self.threshold:
            return None
        # Saturating score: 4 links is suspicious, 40 is not ten times worse.
        score = min(1.0, 0.5 + 0.1 * (count - self.threshold))
        return self._signal(score, f"content contains {count} links")


class RepetitionRule(Rule):
    """Low lexical diversity — the shape of generated or copy-pasted spam."""

    id = "spam.repetition"
    category = Category.SPAM
    reason_code = "SPAM_REPETITION"
    priority = 30

    def __init__(self, min_tokens: int = 12, max_unique_ratio: float = 0.28) -> None:
        self.min_tokens = min_tokens
        self.max_unique_ratio = max_unique_ratio
        self.enabled = True

    def evaluate(self, request: ModerationRequest, features: dict[str, Any]) -> Signal | None:
        tokens = request.content.lower().split()
        # Short strings are naturally repetitive; scoring them is noise.
        if len(tokens) < self.min_tokens:
            return None
        ratio = len(set(tokens)) / len(tokens)
        if ratio > self.max_unique_ratio:
            return None
        score = min(1.0, (self.max_unique_ratio - ratio) / self.max_unique_ratio + 0.5)
        return self._signal(score, f"unique-token ratio {ratio:.2f} below threshold")


class PIIRule(Rule):
    """Detects exposed personal data: emails, phone numbers, payment cards.

    This rule is unusual in setting **both** a floor and a ceiling of REVIEW.

    Detection here is near-certain — a Luhn-valid sixteen-digit number is a
    payment card. But certainty that something is *present* is not authority to
    judge that it *violates*. A shopkeeper posting their own phone number and
    someone doxxing a stranger produce byte-identical evidence, and no detector
    reading one message can separate them.

    So the floor guarantees a human sees it, and the ceiling guarantees a human
    is the one who decides. High confidence and low authority are different
    axes, and conflating them is how a moderation system ends up silencing
    people for publishing their own contact details.
    """

    id = "pii.exposure"
    category = Category.PII
    reason_code = "PII_EXPOSURE"
    priority = 40

    def __init__(self) -> None:
        self.enabled = True

    def evaluate(self, request: ModerationRequest, features: dict[str, Any]) -> Signal | None:
        found: list[str] = []

        if EMAIL_PATTERN.search(request.content):
            found.append("email address")
        if PHONE_PATTERN.search(request.content):
            found.append("phone number")
        if any(_is_card_number(c) for c in CARD_CANDIDATE_PATTERN.findall(request.content)):
            found.append("payment card number")

        if not found:
            return None

        # A validated card is materially more serious than a stray email.
        score = 0.95 if "payment card number" in found else 0.65
        return self._signal(
            score,
            "content appears to contain " + ", ".join(found),
            proposed_verdict=Verdict.REVIEW,
            max_verdict=Verdict.REVIEW,
        )


class NewAccountRiskRule(Rule):
    """A young account posting links is the classic spam-ring fingerprint.

    Neither half is suspicious alone — every account is new once, and links are
    ordinary. The conjunction is what carries signal, which is why this is one
    rule rather than two.
    """

    id = "fraud.new_account_links"
    category = Category.FRAUD
    reason_code = "FRAUD_NEW_ACCOUNT_LINKS"
    priority = 50

    def __init__(self, max_account_age_days: int = 7) -> None:
        self.max_account_age_days = max_account_age_days
        self.enabled = True

    def evaluate(self, request: ModerationRequest, features: dict[str, Any]) -> Signal | None:
        actor = request.actor
        if actor is None or actor.trusted or actor.account_age_days is None:
            return None
        if actor.account_age_days > self.max_account_age_days:
            return None
        if not URL_PATTERN.search(request.content):
            return None

        # Risk decays as the account ages out of the window.
        recency = 1.0 - (actor.account_age_days / (self.max_account_age_days + 1))
        return self._signal(
            round(0.55 + 0.35 * recency, 4),
            f"account is {actor.account_age_days}d old and content contains links",
        )


class RepeatOffenderRule(Rule):
    """Escalates based on the actor's enforcement history.

    Emits evidence, never a verdict. History justifies looking harder at a
    borderline case; it must not by itself convict a message that is otherwise
    fine, or moderation becomes a ratchet no user can ever escape.
    """

    id = "reputation.repeat_offender"
    category = Category.HARASSMENT
    reason_code = "REPUTATION_PRIOR_VIOLATIONS"
    priority = 60

    def __init__(self, threshold: int = 3) -> None:
        self.threshold = threshold
        self.enabled = True

    def evaluate(self, request: ModerationRequest, features: dict[str, Any]) -> Signal | None:
        actor = request.actor
        if actor is None or actor.trusted or actor.prior_violations < self.threshold:
            return None
        score = min(0.6, 0.2 + 0.1 * (actor.prior_violations - self.threshold))
        return self._signal(score, f"actor has {actor.prior_violations} prior violations")


def default_rules() -> list[Rule]:
    """The baseline rule set wired into :func:`safeguard.build_pipeline`."""
    return [
        LinkFloodRule(),
        RepetitionRule(),
        PIIRule(),
        NewAccountRiskRule(),
        RepeatOffenderRule(),
    ]
