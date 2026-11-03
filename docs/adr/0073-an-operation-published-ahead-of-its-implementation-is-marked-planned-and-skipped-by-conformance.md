# ADR-0073 — An operation published ahead of its implementation is marked planned and skipped by conformance

**Status:** Proposed
**Owner:** Max
**Issue:** #937
**Supersedes:** —
**Superseded by:** —

---

## Context

ADR-0035 merges a service's contract before its implementation. The conformance
suite (F13-09) reads every operation from `contracts/<svc>/openapi.yaml` and runs
it against the existing image. CI selects a service whenever `contracts/<svc>/**`
changes (ADR-0040), so a contract-only pull request that adds operations fails
at "Conformance suite against the image": the image answers 404 where the suite
expects 403 or 422. The two rules contradict each other. What is uncertain is
how to relax the suite without letting a skipped operation hide a regression.

## Decision

An operation may declare `x-mosaiq-status: planned`, the only value allowed
(`contracts/check.sh` rejects any other). The harness removes planned operations
from the list every case selects from, so existing operations stay fully tested.
One guard case lists the planned operations and fails if the image answers any
of them with a status other than 404 or 405, which means the route now exists
and the marker must be removed. The implementing pull request removes the marker.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Implement in the same pull request | Contradicts ADR-0035: clients cannot start from a merged contract, and review mixes contract and code. |
| Skip the service in `service-matrix.sh` when only its contract changes | Skips the whole service, so a contract edit to an existing operation is no longer checked against the image. |
| A skip list inside `tests/conformance` | Lives away from the contract, is invisible to contract review and goes stale silently. |

## Consequences

**What this makes easy.** Contract-only pull requests pass CI; planned
operations are visible in the contract diff and printed by the guard case.

**What this makes hard.** An author can mark an operation planned to dodge a
failing case. Review of the contract diff is the control. Marking an operation
the image already answers fails the guard, so only genuinely missing routes
can be hidden.

**What must now be true elsewhere.** The implementing pull request removes the
marker. Clients still wait for the contract merge, not for the implementation.

## Compliance

`tests/test_conformance_planned.py` covers the harness and the guard;
`tests/test_contracts_check.py` covers the check. CI's conformance job runs the
guard case on every service.
