# ADR-0048 — Store managers read every store's reports

**Status:** Partially superseded by ADR-0060 (no user-to-store association, for the services)
**Owner:** Raquel
**Issue:** #467
**Supersedes:** —
**Superseded by:** [ADR-0060](0060-four-store-profiles-map-to-four-roles-and-services-read-each-users-stores-per-request.md), in part

---

## Context

`docs/requirements.md` §3 gives `STORE_MANAGER` "read, own store" on Reports.
Nothing enforces it, and nothing can: `app_user` has no store, `/reports/` shows
every store, and the middleware gates routes, not rows (`web/middleware/authz.py`).
ADR-0010 already found the scoping "not buildable here" and noted that RF-11 asks
only that a user consults stock per store and product, not that a store manager is
confined to one store.

Two later decisions decide what the cell can mean:
- **ADR-0023** gave `STORE_MANAGER` `segment.read`, so a store manager reads the
  whole customer directory, every segment, recommendations, run history and the
  segment dashboards across all stores. Confining only the Reports pages would hide
  figures the same person reads on the next page: a restriction in name only.
- **The distributed stage** makes `STORE_MANAGER` the floor role. On Android it
  picks a store and checks in by GPS on each visit (ADR-0041, `contracts/check-ins/`),
  so the store a manager works in is a per-visit fact, not a fixed attribute of the
  account.

The judgement call is whether the cell records a requirement or an early intention.
`docs/scope.md` does not mention it, and no story ever claimed it; this record
treats it as an intention that was never specified.

## Decision

`STORE_MANAGER`'s Reports cell becomes `read`, the same as every other reading
role, and the matrix no longer promises store-level scoping. No user-to-store
association is added. If confinement is ever required, it is a new story with its
own record: the association, its seed, the administration that sets it, and the
same narrowing on every surface the role reads, not on Reports alone.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Add a user-to-store association and filter `/reports/` for `STORE_MANAGER` | A new table, its 30 seed rows or an exemption, a way to assign it, report-query filters, negative tests and an instance rebuild. It would narrow Reports only, while ADR-0023 keeps the same customers and segments readable everywhere else, so it would protect nothing |
| Keep the cell and leave it unenforced | The matrix is the agreed statement of who may do what; a cell the system does not honour misleads graders and reviewers, which is what #467 was raised for |

## Consequences

**What this makes easy.** The matrix, the middleware and the screens agree.
Store managers compare their store with the others on the pages they already
reach.

**What this makes hard.** A future need to confine a manager to one store is a
real feature, not a filter: it must cover every surface the role reads and decide
how a fixed store relates to per-visit check-ins.

**What must now be true elsewhere.**
- `docs/requirements.md` §3 shows `read` for `STORE_MANAGER` on Reports. Its notes
  no longer describe "own store" as a deferred query filter.
- The `web/middleware/authz.py` docstring names only `own` as row scoping.
- The rest of #467 is split into stories: the RFM monetary axis, filters on the
  campaign, experiment and run-history lists, and an Experiments navigation entry.

## Compliance

`grep -rni "own store" docs/requirements.md web/` prints nothing.
`web/middleware/authz.py` already grants `STORE_MANAGER` `report.read` with no
narrowing, and `tests/test_authz.py` keeps the matrix and the routes in step.
