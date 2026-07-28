# ADR-0006: Ship no bundled term list

**Status:** Accepted
**Date:** 2026-07-28

## Context

Every content moderation library is expected to ship a default list of
prohibited terms. It makes the project immediately demoable, it is what users
expect, and writing one takes an afternoon.

It is also the single worst component such a library can ship, for four reasons
that compound:

**It looks like a safety baseline and is not.** A list is a published, stable,
exactly-known trigger set. Defeating it requires one homoglyph, one zero-width
joiner, or one space. Anyone motivated to evade will, on their first attempt.
Anyone *not* motivated to evade was not the threat.

**It encodes its author's blind spots.** A term list is written in one dialect,
one register, one cultural context. Reclaimed slurs used within a community
score identically to slurs used against it. Clinical, academic, and
counter-speech uses score identically to attacks. The consistent result is false
positives concentrated on exactly the communities the list was not written for —
those least able to appeal and most likely to be silenced.

**It is a weaponisation surface.** A known trigger set lets an attacker craft
content that trips moderation while appearing innocuous, then induce a target to
post it (see
[THREAT-MODEL T2](../THREAT-MODEL.md#t2--weaponised-false-positives-a2)).
A secret list is somewhat harder to weaponise; a shipped one is public by
definition.

**Shipping it invites the mistake it exists to prevent.** A default is an
endorsement. Operators adopt it, ship it, and believe the problem is handled.

## Decision

SafeGuard ships **no keyword or term list**, in the rule layer or the classifier.

`LexiconClassifier` defaults to an **empty** lexicon. `RegexRule` requires an
operator-supplied pattern. The baseline rules key exclusively off *structure* —
link counts, token diversity, Luhn checksums, account age, velocity — never off
meaning.

Operators supply their own lists, versioned alongside their policy, and the
documentation insists they be **scored as evidence rather than blocked outright**
([POLICY.md](../POLICY.md#term-lists)).

## Alternatives considered

**Ship a list, document it as illustrative.** The documentation would be read by
a small fraction of users. A default is an endorsement regardless of the caveat.

**Ship a list disabled by default.** Better, and still ships the biased artefact
and normalises enabling it. The one-line enable is exactly the path of least
resistance.

**Ship a list of only unambiguous slurs.** The premise fails: "unambiguous" is
context-dependent, which is the whole problem. Reclaimed usage, quotation,
counter-speech, and academic discussion are all legitimate, and none is
distinguishable by string match.

**Depend on a third-party list package.** Same problems, plus an unaudited
dependency in the safety-critical path, plus the list changing underneath the
operator without a policy version bump.

## Consequences

**Easier.** No shipped bias. No published trigger set to weaponise or evade. The
structural rules that *are* shipped generalise across languages and survive
adversarial rewording, because they do not depend on words. Operators are forced
to make a deliberate policy decision rather than inherit one.

**Harder — and this is the real cost.** SafeGuard does not detect slurs,
threats, or harassment out of the box. The demo is less impressive than a
competitor's. Some users will conclude it is incomplete, and by the standard
they are applying, it is. Adopting it requires policy work before it does
anything semantic.

**Cost.** The gap between "runs" and "useful" is larger, and closing it is the
operator's job. Phase 2's classifier narrows it — a calibrated model handles
context in a way no list can — but never to zero, and should not.

**Ruled out.** Any future PR adding default terms. If this decision is revisited,
it needs a superseding ADR that answers all four objections above.
