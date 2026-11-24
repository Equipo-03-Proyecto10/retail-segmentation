# F18-04 — negative tests across every deployed service and boundary

Issue #459. Written by `tests/negative/` (see its README), one section per
target: each run replaces its own section and keeps the others. A case holds
when its actual result is the expected one.

Running the case that stops `redis-state` showed that a restart of it revokes
every session, although it keeps an append-only file
([#714](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/714)).
That case therefore runs last, and the cases that follow it in the same run
would fail on sessions that no longer exist.

## Instance

Run at 2026-11-24 04:04 UTC against `https://127.0.0.1`: 40 cases, 40 held, 0 did not.

- **customers, no statement logged for a revoked token:** not run here. It changes the database's logging, so it runs on the local stack only, with MOSAIQ_DB_CONTAINER and MOSAIQ_CUSTOMERS_CONTAINER.

| Area | Case | Expected | Actual | |
|---|---|---|---|---|
| auth | the request before sign-out is authenticated | not 401 | 200 | ✓ |
| users | the request before sign-out is authenticated | not 401 | 403 forbidden_role | ✓ |
| products | the request before sign-out is authenticated | not 401 | 200 | ✓ |
| categories | the request before sign-out is authenticated | not 401 | 200 | ✓ |
| stores | the request before sign-out is authenticated | not 401 | 200 | ✓ |
| stock | the request before sign-out is authenticated | not 401 | 200 | ✓ |
| customers | the request before sign-out is authenticated | not 401 | 200 | ✓ |
| customer-preferences | the request before sign-out is authenticated | not 401 | 200 | ✓ |
| sales | the request before sign-out is authenticated | not 401 | 200 | ✓ |
| segments | the request before sign-out is authenticated | not 401 | 200 | ✓ |
| recommendations | the request before sign-out is authenticated | not 401 | 200 | ✓ |
| engagement | the request before sign-out is authenticated | not 401 | 403 forbidden_role | ✓ |
| check-ins | the request before sign-out is authenticated | not 401 | 403 forbidden_role | ✓ |
| campaigns | the request before sign-out is authenticated | not 401 | 200 | ✓ |
| audit-trail | the request before sign-out is authenticated | not 401 | 403 forbidden_role | ✓ |
| rfm-engine | the request before sign-out is authenticated | not 401 | 200 | ✓ |
| model-registry | the request before sign-out is authenticated | not 401 | 200 | ✓ |
| experimentation | the request before sign-out is authenticated | not 401 | 200 | ✓ |
| analytics-kpis | the request before sign-out is authenticated | not 401 | 200 | ✓ |
| auth | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| users | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| products | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| categories | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| stores | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| stock | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| customers | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| customer-preferences | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| sales | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| segments | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| recommendations | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| engagement | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| check-ins | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| campaigns | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| audit-trail | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| rfm-engine | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| model-registry | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| experimentation | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| analytics-kpis | the next request after sign-out | 401 session_revoked | 401 session_revoked | ✓ |
| auth | a rotated refresh token reused | 401 refresh_reused | 401 refresh_reused | ✓ |
| auth | that session's access token, next call | 401 session_revoked | 401 session_revoked | ✓ |
