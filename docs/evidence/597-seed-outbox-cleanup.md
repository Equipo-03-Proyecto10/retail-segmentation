# #597 — Seed outboxes and the existing instance

ADR-0054 accepts B1: bootstrap removes all eight outboxes' events before the
seed commits. ADR-0046's decision body is unchanged; only its supersession
metadata changes. Runtime relay and capture behavior is unchanged.

## Verification

- Regression first: the new tests failed against the original seed (30
  category events and 40 product events remained).
- PostgreSQL 16.15, matching CI's major version: all three scripts ran clean
  against an empty cluster; `pytest -q -rs`: **3590 passed, 31 skipped**.
  Skips require deployed conformance services or `E2E_BASE_URL`; no PostgreSQL
  test skipped. One pre-existing pytest parameterization deprecation warning.
- The two new tests discover every outbox and require zero rows, then prove a
  web-role product update still captures an unrelayed event transactionally.
  Another connection cannot see it before commit; rollback removes it.
- All 41 tables met the 30-row rule or their explicit seed exemptions.
- `black --check .`, `ruff check .`, repository guards and `git diff --check`
  passed.
- PostgreSQL 18.6: bootstrap and the new tests also pass. The full suite has an
  existing integrity expectation mismatch: N13, N14, N16 and N26 return SQLSTATE
  `23001` rather than the asserted `23503`. The same mismatch was reproduced
  with the original seed. No schema or integrity-test change belongs to #597.

## Authorized live correction

The requester authorized the verified cleanup with the ADR acceptance. On
`mosaiq-deployment-vm`, deployed commit
`d4817b54c8eb984acb7bdb189e8b683a7bd133b9`, the exact seed transaction timestamp
was `2026-10-20 07:48:39.423052+00`. Audit INSERT identities for categories,
products, stores and store locations corroborated the seed. All 280 candidates
were INSERT events, still unrelayed. The 38 user events occurred later.

| Outbox | Before | Deleted | After |
|---|---:|---:|---:|
| categories | 30 | 30 | 0 |
| products | 40 | 40 | 0 |
| stock | 150 | 150 | 0 |
| stores | 60 | 60 | 0 |
| users | 38 | 0 | 38 |

The [one-time procedure](../../deploy/cleanup-seed-outboxes-597.py) was rehearsed
in an isolated cluster with additional product and user updates: both survived.
Its live preview verified the counts before `--apply`. One transaction deleted
only archived IDs at the seed timestamp, rechecked every retained row, and
committed. A separate read confirmed the counts above and all 38 user events
still pending. The web service remained active; no database rebuild was used.

Archive: `/var/tmp/mosaiq-597-outboxes-before.json` on the instance, owner
`postgres`, mode `0600`. SHA256:
`fd8dd5136cc18ce9471a6e2c9be26f4522a79316de9c8d28a66e16eecf7a1a5e`.
It contains the removed rows and the preserved rows; database exports are not
committed. [Operator notes](../../deploy/README.md#seed-outboxes-and-the-one-time-597-cleanup).
