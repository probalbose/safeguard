"""Contract tests for the normalisation module.

STATUS: these fail. That is correct — the module is a spec (see
`src/safeguard/normalise.py`). Make them pass one function at a time.

--------------------------------------------------------------------------
How to use this file
--------------------------------------------------------------------------

The tests here pin the behaviour that is *not* negotiable: idempotence,
purity, the invariants on `distance`. They are the floor.

Sections marked  >>> YOUR TESTS  are gaps you fill. They are the decisions
only you can make, and they are the part that will come up in an interview.
Writing them is the exercise; the implementation is comparatively mechanical.

Run one function's tests at a time:

    pytest tests/test_normalise.py -k invisibles -v

--------------------------------------------------------------------------
The habit worth building
--------------------------------------------------------------------------

Before writing the body of any function below, work one example through by
hand — on paper, in a comment, anywhere. Write the expected output *first*.
Two of the tests here will surprise you if you skip that step.
"""

from __future__ import annotations

import pytest

from safeguard.normalise import (
    NormalisationResult,
    collapse_whitespace,
    fold_confusables,
    normalise,
    strip_invisibles,
)

# --------------------------------------------------------------------------
# Fixtures: real evasion patterns
# --------------------------------------------------------------------------

# Written as escape sequences, not literals, and deliberately so. A literal
# zero-width space is invisible in your editor and in a code review diff; a
# literal Cyrillic 'a' is worse, because it looks exactly like the Latin one.
# Test fixtures for evasion characters must be readable as *what they are*.
# Ruff would have flagged the literal forms (RUF001/002/003) — that lint is
# worth knowing about; it exists to catch precisely this class of confusion.
ZWSP = "\u200b"  # zero-width space
ZWNJ = "\u200c"  # zero-width non-joiner
ZWJ = "\u200d"  # zero-width joiner
BOM = "\ufeff"  # byte-order mark, and a favourite hiding place
CYRILLIC_A = "\u0430"  # indistinguishable from Latin 'a' in most fonts
CYRILLIC_O = "\u043e"
GREEK_OMICRON = "\u03bf"


# --------------------------------------------------------------------------
# strip_invisibles
# --------------------------------------------------------------------------


def test_strip_invisibles_removes_zero_width_space() -> None:
    assert strip_invisibles(f"ev{ZWSP}il") == "evil"


def test_strip_invisibles_removes_every_known_invisible() -> None:
    assert strip_invisibles(f"a{ZWSP}b{ZWNJ}c{ZWJ}d{BOM}e") == "abcde"


def test_strip_invisibles_preserves_ordinary_whitespace() -> None:
    """Spaces and newlines are visible in effect. Removing them here would
    silently change what collapse_whitespace and the repetition rule see."""
    assert strip_invisibles("a b\tc\nd") == "a b\tc\nd"


def test_strip_invisibles_is_idempotent() -> None:
    once = strip_invisibles(f"a{ZWSP}b")
    assert strip_invisibles(once) == once


def test_strip_invisibles_leaves_clean_text_untouched() -> None:
    text = "Thanks, that fixed it!"
    assert strip_invisibles(text) == text


def test_strip_invisibles_handles_empty_string() -> None:
    assert strip_invisibles("") == ""


# >>> YOUR TESTS: strip_invisibles
#
# 1. What should happen to a string that is *entirely* invisible characters?
#    Decide, then pin it.
# 2. Q from the docstring: is "\n" stripped? Write the test for whichever
#    answer you chose, and a comment saying why.
# 3. Find one invisible character not in the fixtures above and add it.
#    `unicodedata.category(c) == "Cf"` is a good place to look.


# --------------------------------------------------------------------------
# collapse_whitespace
# --------------------------------------------------------------------------


def test_collapse_whitespace_collapses_runs() -> None:
    assert collapse_whitespace("free      money") == "free money"


def test_collapse_whitespace_normalises_mixed_whitespace() -> None:
    assert collapse_whitespace("free \t\n money") == "free money"


def test_collapse_whitespace_trims_ends() -> None:
    assert collapse_whitespace("   hello   ") == "hello"


def test_collapse_whitespace_is_idempotent() -> None:
    once = collapse_whitespace("a   b")
    assert collapse_whitespace(once) == once


def test_collapse_whitespace_leaves_single_spaces_alone() -> None:
    text = "the build is green"
    assert collapse_whitespace(text) == text


# >>> YOUR TESTS: collapse_whitespace
#
# 1. Does a newline between paragraphs matter to any detector? Check
#    RepetitionRule before deciding, then pin your answer.
# 2. Non-breaking space (U+00A0) — whitespace or invisible? It renders as a
#    space but str.split() treats it as whitespace only in some contexts.
#    Decide which function owns it and test it there.


# --------------------------------------------------------------------------
# fold_confusables
# --------------------------------------------------------------------------


def test_fold_confusables_maps_cyrillic_to_latin() -> None:
    assert fold_confusables(f"sc{CYRILLIC_A}m") == "scam"


def test_fold_confusables_handles_multiple_scripts() -> None:
    assert fold_confusables(f"c{CYRILLIC_O}nt{GREEK_OMICRON}ur") == "contour"


def test_fold_confusables_leaves_ascii_alone() -> None:
    text = "ordinary latin text"
    assert fold_confusables(text) == text


def test_fold_confusables_is_idempotent() -> None:
    """This is a constraint on your CONFUSABLES map, not just the function.
    If any value in the map is also a key, this test will catch it."""
    once = fold_confusables(f"sc{CYRILLIC_A}m")
    assert fold_confusables(once) == once


def test_confusables_map_has_no_chained_mappings() -> None:
    """The structural version of the test above — checks the data, not a
    sample. Prefer this kind of test: it cannot be satisfied by luck."""
    from safeguard.normalise import CONFUSABLES

    assert not (set(CONFUSABLES.values()) & set(CONFUSABLES.keys()))


# >>> YOUR TESTS: fold_confusables
#
# 1. Text legitimately written in Greek or Cyrillic must survive. Write a test
#    with a real word in one of those scripts and assert it is not mangled
#    into Latin nonsense. If your implementation fails it, that is the
#    trade-off in Q from the docstring — decide deliberately, document it.
# 2. Digits: the ASCII digit zero, Latin capital O, and Cyrillic small o all
#    render near-identically. Which direction do you fold, and why does the
#    direction matter to PIIRule's Luhn check?


# --------------------------------------------------------------------------
# normalise
# --------------------------------------------------------------------------


def test_normalise_returns_a_result_object() -> None:
    result = normalise("hello")
    assert isinstance(result, NormalisationResult)


def test_normalise_preserves_the_original() -> None:
    """Non-negotiable. The original is what a human reviewer sees and what an
    appeal is judged against."""
    dirty = f"ev{ZWSP}il"
    assert normalise(dirty).original == dirty


def test_normalise_composes_every_transformation() -> None:
    dirty = f"  sc{CYRILLIC_A}m{ZWSP}   here  "
    assert normalise(dirty).normalised == "scam here"


def test_clean_text_has_zero_distance() -> None:
    result = normalise("the build is green")
    assert result.distance == 0.0
    assert result.applied == []


def test_obfuscated_text_has_positive_distance() -> None:
    result = normalise(f"f{ZWSP}r{ZWSP}e{ZWSP}e m{CYRILLIC_O}ney")
    assert result.distance > 0.0
    assert result.applied


def test_distance_is_bounded() -> None:
    """It is used as a Signal score, so [0, 1] is a hard requirement."""
    for text in ["", "clean", ZWSP * 50, f"{CYRILLIC_A}" * 100, "a" + ZWSP * 200]:
        assert 0.0 <= normalise(text).distance <= 1.0, repr(text)


def test_distance_is_monotonic_in_obfuscation() -> None:
    """More evasion must never score lower. Get this wrong and an adversary
    optimises against your metric."""
    light = normalise(f"sc{CYRILLIC_A}m")
    heavy = normalise(f"s{ZWSP}c{CYRILLIC_A}{ZWSP}m{ZWNJ}")
    assert heavy.distance >= light.distance


def test_distance_is_not_driven_by_length_alone() -> None:
    """A long clean document must not look evasive. core/policy.py's
    `combine()` dodges the same trap — read it if this one bites."""
    short = normalise("the build is green")
    long = normalise("the build is green. " * 200)
    assert short.distance == long.distance == 0.0


def test_normalise_is_pure() -> None:
    """Same input, same output, always. This is an audit path."""
    text = f"sc{CYRILLIC_A}m{ZWSP}"
    first, second = normalise(text), normalise(text)
    assert first.normalised == second.normalised
    assert first.distance == second.distance
    assert first.applied == second.applied


def test_applied_lists_only_transformations_that_changed_something() -> None:
    """"What was done to this text", not "what was attempted"."""
    result = normalise(f"clean text{ZWSP}")
    assert "strip_invisibles" in result.applied
    assert "fold_confusables" not in result.applied


def test_normalise_handles_empty_string() -> None:
    result = normalise("")
    assert result.normalised == ""
    assert result.distance == 0.0


# >>> YOUR TESTS: normalise
#
# 1. Order dependence. Construct an input where strip-then-fold differs from
#    fold-then-strip. If you cannot, that is a finding too — write down why
#    the operations commute for your implementation.
# 2. The distance metric is yours. Whatever you choose, add two tests that
#    would fail for a *wrong* metric — not just tests your metric passes.
#    (This is the single most valuable habit in this file.)


# --------------------------------------------------------------------------
# Integration — do this LAST, after all of the above is green
# --------------------------------------------------------------------------


@pytest.mark.skip(reason="Phase 2.2 step 5 — wire normalisation into the pipeline")
async def test_zero_width_evasion_no_longer_defeats_link_flood(pipeline) -> None:
    """The whole point, stated as a test.

    Five links with zero-width spaces injected into each URL currently sail
    past LinkFloodRule, because URL_PATTERN never matches. Once detectors read
    the normalised view, this fires.

    Before unskipping this: answer Q3 and Q6 in the module docstring. Wiring
    normalisation in touches ModerationRequest, Pipeline.decide, and every
    rule's `explanation` string. It is a bigger change than it looks, and it
    is the right one to make last.
    """
    raise NotImplementedError
