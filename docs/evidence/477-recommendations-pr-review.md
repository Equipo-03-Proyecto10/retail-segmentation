# #477 — Recommendations review of PR #655

Local review on 2026-10-22 with PostgreSQL 16.15, Redis 8.0.5, Python 3.12
and the service's frozen uv environment. Both integration dependencies were
required; no service test skipped.

The original 119 tests passed. Additional regression tests reproduced:

- A 5,000-digit `limit` returned 500 instead of 422 `invalid_query`.
- A computation finishing after invalidation repopulated the cache with stale
  data for 120 seconds.
- Evicting the cache's index let old results survive invalidation.

The limit is bounded before integer conversion. Cached values carry the
pre-computation random generation; invalidation replaces it even when the
index is absent. A later request rejects an older generation. Physical cleanup
still drains the index, with no SCAN or KEYS and no changed public response.

The service ACL also lacked MULTI, EXEC and DISCARD even though the cache uses
transactions. Targeted tests exposed the refusal in the cache's warning log.
Those three commands are now explicitly permitted, with ACL DRYRUN coverage;
key permissions and the six-stream read-only selector stay restricted.

`uv run pytest -q`: **125 passed**. `uv run black --check .` and
`uv run ruff check .` pass. Golden tests cover all 33 recorded cases, and all
six invalidation consumers and cache-unavailable behavior run against Redis.
