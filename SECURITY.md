# Security policy

## Status

SafeGuard is **Phase 1 — a working skeleton**, not a production-ready service.
It ships with no authentication, no authorisation, and no rate limiting. See
[ARCHITECTURE.md §8](docs/ARCHITECTURE.md#8-not-yet-production-ready) for the
full list and [THREAT-MODEL.md](docs/THREAT-MODEL.md) for what is and is not
defended.

Do not deploy it to handle real user traffic without addressing those gaps.

## Reporting a vulnerability

Please report privately, via GitHub's
[private vulnerability reporting](https://github.com/probalbose/safeguard/security/advisories/new)
on this repository. Do not open a public issue.

Useful to include: what the issue is, how to reproduce it, and what an attacker
gains. A proof of concept helps but is not required.

Expect an acknowledgement within a few days. This is a personal project rather
than a funded one, so please calibrate expectations accordingly — response times
are best effort.

## Scope

**In scope.** Anything in `src/safeguard/`: bypasses of the decision pipeline,
ways to force an incorrect verdict, data exposure through decision events or
logs, denial of service beyond the documented gaps, dependency vulnerabilities.

**Known and documented, so not a report.** Missing authentication, missing rate
limiting, the unauthenticated `/v1/policy` endpoint, and the absence of a real
classifier are all recorded in
[THREAT-MODEL.md](docs/THREAT-MODEL.md) with their residual risk. A *new*
consequence of one of these that is not documented there is worth reporting.

## Safety issues

Bias, disproportionate false positives against a particular group, or any way
the system can be used to silence legitimate speech are welcome as **public
issues**. They benefit from open discussion, and unlike security bugs, disclosure
does not help an attacker.

If you are unsure which category something falls into, report it privately and
we can move it.
