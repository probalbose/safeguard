"""Normalisation — canonicalising content before detection.

STATUS: SPEC ONLY. Every function below raises NotImplementedError.
This module is the Phase 2.2 work item; see docs/ROADMAP.md.

--------------------------------------------------------------------------
The problem
--------------------------------------------------------------------------

Every detector in SafeGuard reads ``request.content`` verbatim. An adversary
who knows this defeats the entire rule layer with a keystroke:

    "https://evil.example"   ->  "https://evil.exam\u200bple"   (zero-width space)
    "scam"                   ->  "sc\u0430m"                     (Cyrillic a)
    "free money"             ->  "f r e e   m o n e y"
    "attack"                 ->  "a-t-t-a-c-k"

(Written as escapes on purpose. Rendered literally, the first two lines would
be indistinguishable from the originals — which is the entire attack, and the
reason ruff ships the RUF001/002/003 lints.)

None of these change what a human reads. All of them change what
``str.split()`` and ``re.search`` see. This is threat-model T1, currently rated
**high** residual risk, and it is the single largest gap in Phase 1.

--------------------------------------------------------------------------
The shape of the answer
--------------------------------------------------------------------------

Produce a canonical form of the content for detectors to read, while keeping
the original for display, audit, and appeals. Detection reads the normalised
view; humans read what was actually posted.

Then the interesting part. Legitimate text needs almost no normalisation.
Evasive text needs a lot. **The amount of transformation required is itself a
signal** — a structural one, computed from shape rather than meaning, so it
belongs in the rule layer alongside link counts and velocity (see ADR-0006).
That is what ``NormalisationResult.distance`` is for.

--------------------------------------------------------------------------
Before you write any code
--------------------------------------------------------------------------

Answer these. They are design decisions, not implementation details, and
getting them wrong costs more than a slow implementation would.

  Q1. Over-normalisation is the enemy. Fold aggressively enough and every
      string collapses toward every other string, and the false-positive rate
      goes with it. Where is the line? Is "l33t sp34k" evasion, or is it how a
      whole generation types? Does your answer change by category?

  Q2. Should normalisation be one function or a pipeline of independent steps?
      What does each choice cost you when you later need to know *which*
      transformation fired?

  Q3. Detectors currently read ``request.content``. If they read normalised
      text instead, what happens to ``Signal.explanation``? A reviewer reading
      "content contains 5 links" needs to see the content the user posted, not
      your canonical form. How do you keep both?

  Q4. NFKC maps "①" to "1" and "ﬁ" to "fi" — useful. It also maps "²" to "2",
      so "x²" becomes "x2". Is that acceptable? What about languages where
      NFKC is lossy?

  Q5. What is the complexity of your normaliser in the length of the input,
      and what is the maximum input? Look up ``ModerationRequest.content``'s
      constraint before answering. An adversary picks the worst case.

  Q6. Does normalisation run before rules, or before each detector that wants
      it? What does the pipeline's latency budget say about doing it once?

Write your answers somewhere before starting. You will change your mind about
at least two of them, and noticing that you changed your mind is the point.

--------------------------------------------------------------------------
Suggested order of attack
--------------------------------------------------------------------------

Smallest useful thing first, tests before code, one transformation at a time:

  1. ``strip_invisibles``  — no judgement calls, pure win, ~10 lines
  2. ``collapse_whitespace`` — nearly as easy, one decision to make
  3. ``fold_confusables`` — where the real thinking is
  4. ``normalise`` — compose them, compute distance
  5. Wire into the pipeline (a separate, later change — resist doing it early)

Run ``make check`` after each. Do not move to step 2 until step 1's tests pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Characters that are invisible or near-invisible when rendered, and are
#: therefore free evasion tooling. This list is deliberately incomplete —
#: extend it, and note in your commit message how you decided what belongs.
#:
#: Hint: the Unicode categories Cf (format) and Cc (control) are worth reading
#: about before you hand-roll a list. `unicodedata.category` will tell you a
#: character's category.
INVISIBLE_CHARS: frozenset[str] = frozenset()

#: Maps a visually-confusable character to its canonical form, e.g. Cyrillic
#: small a (U+0430) -> Latin small a (U+0061).
#:
#: The full Unicode confusables table has thousands of entries and lives at
#: https://www.unicode.org/Public/security/latest/confusables.txt — do NOT
#: vendor the whole thing. Start with the Cyrillic and Greek letters that
#: collide with Latin ASCII; that covers the overwhelming majority of real
#: homoglyph evasion.
#:
#: Q: why might folding *every* confusable be worse than folding a curated
#:    subset? Consider what happens to text legitimately written in Greek.
CONFUSABLES: dict[str, str] = {}


@dataclass(frozen=True, slots=True)
class NormalisationResult:
    """The canonical form of some content, plus what it took to get there.

    Attributes:
        original: The content exactly as submitted. Never mutated. This is what
            a human reviewer sees and what an appeal is judged against.
        normalised: The canonical form. This is what detectors read.
        applied: Names of the transformations that actually changed something.
            Ordered as applied. A transformation that ran but changed nothing
            does not appear — the field answers "what was done to this text",
            not "what was attempted".
        distance: How much transformation was required, in [0, 1]. 0.0 means
            the content was already canonical. Higher means more obfuscation
            was stripped.

            You choose the metric. Whatever you pick, it must be:
              - bounded in [0, 1], so it can be used as a Signal score
              - monotonic: more obfuscation must never lower the distance
              - stable: unaffected by content length alone, or every long
                document looks evasive (this bit is harder than it sounds —
                see how `combine()` in core/policy.py dodges the same trap)
    """

    original: str
    normalised: str
    applied: list[str] = field(default_factory=list)
    distance: float = 0.0


def strip_invisibles(text: str) -> str:
    """Remove zero-width and control characters.

    Contract:
        - Removes every character in INVISIBLE_CHARS.
        - Preserves ordinary whitespace (space, tab, newline). Those are
          visible in effect and handled by collapse_whitespace.
        - Idempotent: strip_invisibles(strip_invisibles(x)) == strip_invisibles(x).
        - Returns the input unchanged if nothing matched.

    Q: newline is a control character by some definitions. Should it be
       stripped? What breaks in the repetition rule if you get this wrong?
    """
    raise NotImplementedError("Phase 2.2 — see module docstring")


def collapse_whitespace(text: str) -> str:
    """Collapse whitespace runs and trim the ends.

    Contract:
        - Any run of whitespace becomes a single space.
        - Leading and trailing whitespace is removed.
        - Idempotent.

    Q: "f r e e   m o n e y" collapses to "f r e e m o n e y", which still
       does not match "free money". Is defeating letter-spacing this
       function's job, or does it belong somewhere else — or nowhere, because
       the cure is worse than the disease? Think about what happens to
       legitimate acronyms and to languages that space differently.
    """
    raise NotImplementedError("Phase 2.2 — see module docstring")


def fold_confusables(text: str) -> str:
    """Map visually-confusable characters to a canonical form.

    Contract:
        - Every key in CONFUSABLES is replaced by its value.
        - Characters absent from the map are left alone.
        - Idempotent — which constrains the map. If you map 'a' -> 'b' and
          'b' -> 'c', you have a bug. Assert this property in a test.

    Q: should this apply Unicode NFKC normalisation as well, or is that a
       separate transformation with a separate name? What does your answer to
       Q4 in the module docstring imply?
    """
    raise NotImplementedError("Phase 2.2 — see module docstring")


def normalise(text: str) -> NormalisationResult:
    """Apply the full normalisation pipeline and report what it took.

    Contract:
        - `original` is the untouched input.
        - `applied` lists only the transformations that changed the text.
        - `distance` is 0.0 exactly when `normalised == original`.
        - Pure: no I/O, no clock, no global state. Same input, same output,
          always — this is an audit path, and it must be reproducible.

    Q: order matters. Does stripping invisibles before folding confusables
       give the same result as the reverse? Construct an input where it does
       not, then decide which order is correct and write that test.
    """
    raise NotImplementedError("Phase 2.2 — see module docstring")
