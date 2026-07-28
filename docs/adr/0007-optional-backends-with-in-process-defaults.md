# ADR-0007: Optional backends with in-process defaults

**Status:** Accepted
**Date:** 2026-07-28

## Context

SafeGuard needs shared velocity state (Redis) and a durable decision stream
(Kafka) in production. Requiring them everywhere has costs that are easy to
underestimate:

- Contributors need Docker running before the test suite passes.
- CI needs service containers, which are slow and flaky.
- Tests acquire ordering dependencies through shared state, and start failing
  intermittently for reasons unrelated to the code.
- Evaluating the project requires infrastructure setup, so most people don't.

The opposite failure is worse: making backends optional via mock objects that
diverge from the real implementations, so tests pass against behaviour
production does not have.

## Decision

Every pluggable backend is an ABC with two implementations:

| Interface | In-process default | Networked |
| --- | --- | --- |
| `FeatureStore` | `InMemoryFeatureStore` | `RedisFeatureStore` |
| `DecisionPublisher` | `InMemoryPublisher` | `KafkaPublisher` |
| `Classifier` | `NullClassifier` / `LexiconClassifier` | Phase 2 model client |

Selection is by **presence of a connection string**, not a separate enable flag:

```python
def build_feature_store(redis_url: str | None) -> FeatureStore:
    if not redis_url:
        return InMemoryFeatureStore()
    ...
```

A separate `REDIS_ENABLED` boolean would inevitably drift out of sync with the
URL it guards, producing a service that thinks a backend is on while pointing at
nothing.

A missing optional dependency logs a warning and falls back rather than
refusing to start. Optional dependencies should degrade a deployment, not
prevent it.

The in-process implementations are **real implementations**, not mocks. They
satisfy the same interface, are used by the test suite, and are correct within
their documented scope.

## Alternatives considered

**Require Redis and Kafka always.** Rejected on contributor friction and CI
fragility.

**Mock backends for tests.** Rejected: mocks encode assumed behaviour, and
assumed behaviour drifts. A real in-memory implementation exercises the same
interface contract.

**Explicit enable flags.** Rejected: two sources of truth for one fact.

**Dependency injection framework.** Rejected as disproportionate. Three factory
functions and constructor injection do the whole job, and are readable without
learning a framework.

**Hard-fail on missing optional dependency.** Rejected: `pip install safeguard`
without extras should still run something, and a deployment losing tracing
should lose tracing, not availability.

## Consequences

**Easier.** `git clone && make test` works with nothing installed. CI needs no
service containers and the suite runs in under a second. Tests are hermetic, so
no ordering dependencies. Evaluating the project costs nothing.

**Harder.** Two implementations of everything to maintain. The in-process
versions can drift from the networked ones — a contract test suite run against
both is the mitigation, and is not yet written.

**Cost, and it is a sharp one.** The defaults are correct for exactly one
process, and *silently* wrong for more than one. Two replicas with
`InMemoryFeatureStore` each see half of an actor's traffic, so velocity
detection quietly fails at precisely the scale where it matters. There is no
error — just worse decisions.

This is documented in `store.py`, in
[ARCHITECTURE §6](../ARCHITECTURE.md#6-scaling), and here. A production
readiness check that fails on `environment=production` with no `redis_url` would
be better than documentation, and is a Phase 4 item.

**Ruled out.** Any backend that cannot provide a working in-process
implementation. If a future dependency has no local equivalent, it does not
belong on the request path.
