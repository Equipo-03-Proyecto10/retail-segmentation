# F19-07 (#757): store-bound operations narrowed to the caller's stores

ADR-0060 confines the four floor roles (`STORE_MANAGER`, `SALES_ASSOCIATE`,
`LOYALTY_OFFICER`, `COMMERCIAL_SUPERVISOR`) to their assigned stores. This note
records what was run for the three services F19-07 changes, and what was not.

## What changed

| Service | Version | Narrowed operations |
|---|---|---|
| sales | 0.3.0 → 0.4.0 (contract 1.6.0 → 1.7.0) | `recordSale`, `listSales`, `getSale`, `recordSaleReversal`, `listSaleReversals`, `getSaleReversal` |
| engagement | 0.3.0 → 0.4.0 (contract 2.4.0 → 2.5.0) | `createEngagementEvent`, `listEngagementEvents`, `getEngagementEvent`, `getEngagementSummary`, and their `/v2` twins. Capture incidents were already narrowed |
| check-ins | 0.1.3 → 0.2.0 (contract 1.2.0 → 1.3.0) | `createCheckIn`, `listCheckIns`, `getCheckIn` |

Each reads `api_users.store_assignment_v1` inside the request, with no cache and
no token claim. Nothing is written and no schema changes. The refusal is 403
`forbidden_store`, in JSON and XML. A list with no `store_id` returns only the
caller's stores, and a floor user with no assignment works in no store.

Stock levels are **not** narrowed. The story's first criterion says "reads stock",
but ADR-0060 (accepted) lists stock levels as not narrowed, so a seller may
offer what another store holds. The ADR was followed.

## Run here

| Check | Result |
|---|---|
| sales, `uv run pytest` (PostgreSQL 16, Redis) | 218 passed; 3 failed for the environment (below) |
| sales, `test_store_scope.py` | 15 passed: record, read and list in JSON and XML, `ADMIN` unchanged, a floor user with no assignment, a removal refused on the next call |
| engagement, `uv run pytest` (PostgreSQL, MongoDB, Redis) | 147 passed outside the environment's failures (below); `test_store_scope.py` 10 passed |
| check-ins, `cargo test` (PostgreSQL) | all passed, `store_scope.rs` 2 passed; `cargo fmt --check` and `cargo clippy --all-targets` clean |
| black, ruff | clean on sales and engagement |
| `contracts/check.sh`, `contracts/streams/check_streams.sh`, `docs/check.py` | print nothing / pass (the "implemented before its contract" lines come only from a history-less checkout) |

The removal case runs with real PostgreSQL and real HTTP handlers, with no
fakes: the assignment row is deleted in its own committed transaction and the
next request is refused. The contract's bound is the requests already in flight
at the commit, so no waiting is needed.

## Not run here

- **`tests/negative/test_store_scope.py`.** It needs the full distributed stack
  (`MOSAIQ_BASE_URL`), which this environment does not have. It collects and
  lints clean. Its first run is the pull request's CI or a local stack run.
- **Redis 7.0 has no `WAITAOF`.** Three sales cases (`test_acl.py` twice,
  `test_imports.py` once) and the engagement ACL and stream cases (two failures
  and four errors) need Redis 7.2 and fail on 7.0 for that reason alone. They
  touch no code F19-07 changes.
- **`test_init.py` (engagement)** needs `mongosh` and a root URI; it was not run.
