# F18-04 — negative tests across every deployed service and boundary

Issue #459. Written by `tests/negative/` (see its README), one section per
target: each run replaces its own section and keeps the others. A case holds
when its actual result is the expected one.

Running the case that stops `redis-state` showed that a restart of it revokes
every session, although it keeps an append-only file
([#714](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/714)).
That case therefore runs last, and the cases that follow it in the same run
would fail on sessions that no longer exist.

## Local stack

Run at 2026-10-23 18:11 UTC against `http://127.0.0.1:8080`: 323 cases, 323 held, 0 did not.

- **redis, deploy/infra/check-redis.sh:** not run here. It checks the instance's mosaiq-infra project, so the instance run covers it (MOSAIQ_CHECK_REDIS).
- **stock, segments, recommendations, audit-trail, the body cases:** not applicable. Its contract takes no body.
- **auth, customer-preferences, recommendations, pagination:** not applicable. Its contract has no paginated read.

| Area | Case | Expected | Actual | |
|---|---|---|---|---|
| auth | a rotated refresh token reused | 401 refresh_reused | 401 refresh_reused | ✓ |
| auth | that session's access token, next call | 401 session_revoked | 401 session_revoked | ✓ |
| auth | sixth sign-in after 5 failures, same email and IP | 429 rate_limited | 429 rate_limited | ✓ |
| auth | that refusal's Retry-After | 800 to 900 s | 900 s | ✓ |
| users | sixth wrong current password | 429 rate_limited | 429 rate_limited | ✓ |
| database | contracts/db/check_grants.sh | exit 0, prints nothing | exit 0, prints nothing | ✓ |
| database | contracts/db/check_boundaries.sql | exit 0, prints nothing | exit 0, prints nothing | ✓ |
| database | as svc_sales: SELECT 1 FROM public.inventory | permission denied for table inventory | psql:<stdin>:3: ERROR:  permission denied for table inventory | ✓ |
| database | as svc_stock: SELECT api_stock.consume_v1(1::smallint, 1, 1) | permission denied for function consume_v1 | psql:<stdin>:3: ERROR:  permission denied for function consume_v1 | ✓ |
| database | a direct INSERT of a second administrator, as postgres | refused by ux_app_user_single_administrator | psql:<stdin>:2: ERROR:  duplicate key value violates unique constraint "ux_app_user_single_administrator" | ✓ |
| auth | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| users | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| products | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| categories | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| stores | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| stock | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| customers | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| customer-preferences | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| sales | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| segments | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| recommendations | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| engagement | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| check-ins | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| campaigns | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| audit-trail | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| auth | Content-Type: text/plain on POST /v1/sessions/refresh | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| auth | an XML body with a DOCTYPE on POST /v1/sessions/refresh | 400 malformed_body | 400 malformed_body | ✓ |
| auth | an XML body with an entity declaration on POST /v1/sessions/refresh | 400 malformed_body | 400 malformed_body | ✓ |
| auth | 65 KiB on POST /v1/sessions/refresh, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| auth | 65 KiB on POST /v1/sessions/refresh, on 127.0.0.1:5001 | 413 payload_too_large | 413 payload_too_large | ✓ |
| users | Content-Type: text/plain on POST /v1/administrator-transfers | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| users | an XML body with a DOCTYPE on POST /v1/administrator-transfers | 400 malformed_body | 400 malformed_body | ✓ |
| users | an XML body with an entity declaration on POST /v1/administrator-transfers | 400 malformed_body | 400 malformed_body | ✓ |
| users | 65 KiB on POST /v1/administrator-transfers, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| users | 65 KiB on POST /v1/administrator-transfers, on 127.0.0.1:5002 | 413 payload_too_large | 413 payload_too_large | ✓ |
| products | Content-Type: text/plain on POST /v1/products | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| products | an XML body with a DOCTYPE on POST /v1/products | 400 malformed_body | 400 malformed_body | ✓ |
| products | an XML body with an entity declaration on POST /v1/products | 400 malformed_body | 400 malformed_body | ✓ |
| products | 65 KiB on POST /v1/products, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| products | 65 KiB on POST /v1/products, on 127.0.0.1:5003 | 413 payload_too_large | 413 payload_too_large | ✓ |
| categories | Content-Type: text/plain on POST /v1/categories | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| categories | an XML body with a DOCTYPE on POST /v1/categories | 400 malformed_body | 400 malformed_body | ✓ |
| categories | an XML body with an entity declaration on POST /v1/categories | 400 malformed_body | 400 malformed_body | ✓ |
| categories | 65 KiB on POST /v1/categories, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| categories | 65 KiB on POST /v1/categories, on 127.0.0.1:5004 | 413 payload_too_large | 413 payload_too_large | ✓ |
| stores | Content-Type: text/plain on PUT /v1/stores/{store_id}/location | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| stores | an XML body with a DOCTYPE on PUT /v1/stores/{store_id}/location | 400 malformed_body | 400 malformed_body | ✓ |
| stores | an XML body with an entity declaration on PUT /v1/stores/{store_id}/location | 400 malformed_body | 400 malformed_body | ✓ |
| stores | 65 KiB on PUT /v1/stores/{store_id}/location, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| stores | 65 KiB on PUT /v1/stores/{store_id}/location, on 127.0.0.1:5005 | 413 payload_too_large | 413 payload_too_large | ✓ |
| customers | Content-Type: text/plain on POST /v1/customers | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| customers | an XML body with a DOCTYPE on POST /v1/customers | 400 malformed_body | 400 malformed_body | ✓ |
| customers | an XML body with an entity declaration on POST /v1/customers | 400 malformed_body | 400 malformed_body | ✓ |
| customers | 65 KiB on POST /v1/customers, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| customers | 65 KiB on POST /v1/customers, on 127.0.0.1:5007 | 413 payload_too_large | 413 payload_too_large | ✓ |
| customer-preferences | Content-Type: text/plain on PUT /v1/preference-profiles/{customer_id}/interest-categories | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| customer-preferences | an XML body with a DOCTYPE on PUT /v1/preference-profiles/{customer_id}/interest-categories | 400 malformed_body | 400 malformed_body | ✓ |
| customer-preferences | an XML body with an entity declaration on PUT /v1/preference-profiles/{customer_id}/interest-categories | 400 malformed_body | 400 malformed_body | ✓ |
| customer-preferences | 65 KiB on PUT /v1/preference-profiles/{customer_id}/interest-categories, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| customer-preferences | 65 KiB on PUT /v1/preference-profiles/{customer_id}/interest-categories, on 127.0.0.1:5008 | 413 payload_too_large | 413 payload_too_large | ✓ |
| sales | Content-Type: text/plain on POST /v1/sales | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| sales | an XML body with a DOCTYPE on POST /v1/sales | 400 malformed_body | 400 malformed_body | ✓ |
| sales | an XML body with an entity declaration on POST /v1/sales | 400 malformed_body | 400 malformed_body | ✓ |
| sales | 65 KiB on POST /v1/sales, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| sales | 65 KiB on POST /v1/sales, on 127.0.0.1:5009 | 413 payload_too_large | 413 payload_too_large | ✓ |
| engagement | Content-Type: text/plain on POST /v1/engagement-events | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| engagement | an XML body with a DOCTYPE on POST /v1/engagement-events | 400 malformed_body | 400 malformed_body | ✓ |
| engagement | an XML body with an entity declaration on POST /v1/engagement-events | 400 malformed_body | 400 malformed_body | ✓ |
| engagement | 65 KiB on POST /v1/engagement-events, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| engagement | 65 KiB on POST /v1/engagement-events, on 127.0.0.1:5012 | 413 payload_too_large | 413 payload_too_large | ✓ |
| check-ins | Content-Type: text/plain on POST /v1/check-ins | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| check-ins | an XML body with a DOCTYPE on POST /v1/check-ins | 400 malformed_body | 400 malformed_body | ✓ |
| check-ins | an XML body with an entity declaration on POST /v1/check-ins | 400 malformed_body | 400 malformed_body | ✓ |
| check-ins | 65 KiB on POST /v1/check-ins, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| check-ins | 65 KiB on POST /v1/check-ins, on 127.0.0.1:5013 | 413 payload_too_large | 413 payload_too_large | ✓ |
| campaigns | Content-Type: text/plain on POST /v1/campaigns | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| campaigns | an XML body with a DOCTYPE on POST /v1/campaigns | 400 malformed_body | 400 malformed_body | ✓ |
| campaigns | an XML body with an entity declaration on POST /v1/campaigns | 400 malformed_body | 400 malformed_body | ✓ |
| campaigns | 65 KiB on POST /v1/campaigns, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| campaigns | 65 KiB on POST /v1/campaigns, on 127.0.0.1:5014 | 413 payload_too_large | 413 payload_too_large | ✓ |
| users | page=0 on GET /v1/users | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| users | size=101 on GET /v1/users | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| products | page=0 on GET /v1/products | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| products | size=101 on GET /v1/products | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| categories | page=0 on GET /v1/categories | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| categories | size=101 on GET /v1/categories | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| categories | page=0 on GET /v1/categories/{category_id}/descendants | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| categories | size=101 on GET /v1/categories/{category_id}/descendants | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stores | page=0 on GET /v1/stores | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stores | size=101 on GET /v1/stores | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stock | page=0 on GET /v1/stock-levels | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stock | size=101 on GET /v1/stock-levels | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customers | page=0 on GET /v1/customers | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customers | size=101 on GET /v1/customers | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| sales | page=0 on GET /v1/sales | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| sales | size=101 on GET /v1/sales | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| segments | page=0 on GET /v1/segment-assignments | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| segments | size=101 on GET /v1/segment-assignments | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| engagement | page=0 on GET /v1/engagement-events | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| engagement | size=101 on GET /v1/engagement-events | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| check-ins | page=0 on GET /v1/check-ins | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| check-ins | size=101 on GET /v1/check-ins | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| campaigns | page=0 on GET /v1/campaigns | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| campaigns | size=101 on GET /v1/campaigns | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| audit-trail | page=0 on GET /v1/audit-entries | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| audit-trail | size=101 on GET /v1/audit-entries | 422 invalid_pagination | 422 invalid_pagination | ✓ |
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
| customers | statements logged for an accepted read (control) | at least one | 2 | ✓ |
| customers | the revoked token's request | 401 session_revoked | 401 session_revoked | ✓ |
| customers | statements logged for that request | 0 | 0 | ✓ |
| users | ANALYST on GET /v1/users | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on POST /v1/users | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on GET /v1/users/{user_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on PATCH /v1/users/{user_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on POST /v1/users/{user_id}/deactivation | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on DELETE /v1/users/{user_id}/deactivation | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on GET /v1/roles | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on GET /v1/roles/{role_code} | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on POST /v1/administrator-transfers | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | CUSTOMER on GET /v1/products | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | ANALYST on POST /v1/products | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | CUSTOMER on GET /v1/products/{product_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | ANALYST on PATCH /v1/products/{product_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| categories | CUSTOMER on GET /v1/categories | 403 forbidden_role | 403 forbidden_role | ✓ |
| categories | ANALYST on POST /v1/categories | 403 forbidden_role | 403 forbidden_role | ✓ |
| categories | CUSTOMER on GET /v1/categories/{category_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| categories | ANALYST on PATCH /v1/categories/{category_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| categories | ANALYST on DELETE /v1/categories/{category_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| categories | CUSTOMER on GET /v1/categories/{category_id}/descendants | 403 forbidden_role | 403 forbidden_role | ✓ |
| stores | CUSTOMER on GET /v1/stores | 403 forbidden_role | 403 forbidden_role | ✓ |
| stores | CUSTOMER on GET /v1/stores/{store_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| stores | ANALYST on PUT /v1/stores/{store_id}/location | 403 forbidden_role | 403 forbidden_role | ✓ |
| stores | ANALYST on DELETE /v1/stores/{store_id}/location | 403 forbidden_role | 403 forbidden_role | ✓ |
| stock | CUSTOMER on GET /v1/stock-levels | 403 forbidden_role | 403 forbidden_role | ✓ |
| stock | CUSTOMER on GET /v1/stock-levels/{store_id}/{product_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | INVENTORY_PLANNER on GET /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | ANALYST on POST /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | INVENTORY_PLANNER on GET /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | ANALYST on PATCH /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | ANALYST on PUT /v1/preference-profiles/{customer_id}/preferred-channels | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | ANALYST on PUT /v1/preference-profiles/{customer_id}/interest-categories | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | INVENTORY_PLANNER on GET /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | ANALYST on POST /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | INVENTORY_PLANNER on GET /v1/sales/{sale_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | CUSTOMER on GET /v1/channels | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segments | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segments/{label_code} | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| recommendations | INVENTORY_PLANNER on GET /v1/recommendations | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | INVENTORY_PLANNER on GET /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | ANALYST on POST /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | INVENTORY_PLANNER on GET /v1/engagement-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | ANALYST on POST /v1/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | CUSTOMER on GET /v1/engagement-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| check-ins | ANALYST on GET /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| check-ins | ANALYST on POST /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| check-ins | ANALYST on GET /v1/check-ins/{check_in_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| check-ins | ANALYST on POST /v1/check-ins/{check_in_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | STORE_MANAGER on GET /v1/campaigns | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | ANALYST on POST /v1/campaigns | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | STORE_MANAGER on GET /v1/campaigns/{campaign_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | ANALYST on PATCH /v1/campaigns/{campaign_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | ANALYST on POST /v1/campaigns/{campaign_id}/transitions | 403 forbidden_role | 403 forbidden_role | ✓ |
| audit-trail | ANALYST on GET /v1/audit-entries | 403 forbidden_role | 403 forbidden_role | ✓ |
| audit-trail | ANALYST on GET /v1/audit-entries/{audit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | POST /v1/users with role ADMIN | 409 single_administrator | 409 single_administrator | ✓ |
| users | PATCH an ANALYST to role ADMIN | 409 use_administrator_transfer | 409 use_administrator_transfer | ✓ |
| users | deactivating the only ADMIN | 409 administrator_required | 409 administrator_required | ✓ |
| sales | a line 1,000 over the stock on hand | 409 stock_unavailable | 409 stock_unavailable | ✓ |
| sales | no Idempotency-Key | 400 idempotency_key_required | 400 idempotency_key_required | ✓ |
| sales | the key of an accepted sale, with another request | 409 idempotency_key_reused | 409 idempotency_key_reused | ✓ |
| auth | no token | 401 invalid_token | 401 invalid_token | ✓ |
| auth | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| auth | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| auth | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| auth | a wrong iss | 401 invalid_token | 401 invalid_token | ✓ |
| auth | a wrong aud | 401 invalid_token | 401 invalid_token | ✓ |
| auth | an expired token | 401 invalid_token | 401 invalid_token | ✓ |
| users | no token | 401 invalid_token | 401 invalid_token | ✓ |
| users | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| users | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| users | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| users | a wrong iss | 401 invalid_token | 401 invalid_token | ✓ |
| users | a wrong aud | 401 invalid_token | 401 invalid_token | ✓ |
| users | an expired token | 401 invalid_token | 401 invalid_token | ✓ |
| products | no token | 401 invalid_token | 401 invalid_token | ✓ |
| products | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| products | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| products | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| products | a wrong iss | 401 invalid_token | 401 invalid_token | ✓ |
| products | a wrong aud | 401 invalid_token | 401 invalid_token | ✓ |
| products | an expired token | 401 invalid_token | 401 invalid_token | ✓ |
| categories | no token | 401 invalid_token | 401 invalid_token | ✓ |
| categories | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| categories | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| categories | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| categories | a wrong iss | 401 invalid_token | 401 invalid_token | ✓ |
| categories | a wrong aud | 401 invalid_token | 401 invalid_token | ✓ |
| categories | an expired token | 401 invalid_token | 401 invalid_token | ✓ |
| stores | no token | 401 invalid_token | 401 invalid_token | ✓ |
| stores | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| stores | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| stores | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| stores | a wrong iss | 401 invalid_token | 401 invalid_token | ✓ |
| stores | a wrong aud | 401 invalid_token | 401 invalid_token | ✓ |
| stores | an expired token | 401 invalid_token | 401 invalid_token | ✓ |
| stock | no token | 401 invalid_token | 401 invalid_token | ✓ |
| stock | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| stock | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| stock | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| stock | a wrong iss | 401 invalid_token | 401 invalid_token | ✓ |
| stock | a wrong aud | 401 invalid_token | 401 invalid_token | ✓ |
| stock | an expired token | 401 invalid_token | 401 invalid_token | ✓ |
| customers | no token | 401 invalid_token | 401 invalid_token | ✓ |
| customers | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| customers | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| customers | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| customers | a wrong iss | 401 invalid_token | 401 invalid_token | ✓ |
| customers | a wrong aud | 401 invalid_token | 401 invalid_token | ✓ |
| customers | an expired token | 401 invalid_token | 401 invalid_token | ✓ |
| customer-preferences | no token | 401 invalid_token | 401 invalid_token | ✓ |
| customer-preferences | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| customer-preferences | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| customer-preferences | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| customer-preferences | a wrong iss | 401 invalid_token | 401 invalid_token | ✓ |
| customer-preferences | a wrong aud | 401 invalid_token | 401 invalid_token | ✓ |
| customer-preferences | an expired token | 401 invalid_token | 401 invalid_token | ✓ |
| sales | no token | 401 invalid_token | 401 invalid_token | ✓ |
| sales | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| sales | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| sales | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| sales | a wrong iss | 401 invalid_token | 401 invalid_token | ✓ |
| sales | a wrong aud | 401 invalid_token | 401 invalid_token | ✓ |
| sales | an expired token | 401 invalid_token | 401 invalid_token | ✓ |
| segments | no token | 401 invalid_token | 401 invalid_token | ✓ |
| segments | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| segments | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| segments | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| segments | a wrong iss | 401 invalid_token | 401 invalid_token | ✓ |
| segments | a wrong aud | 401 invalid_token | 401 invalid_token | ✓ |
| segments | an expired token | 401 invalid_token | 401 invalid_token | ✓ |
| recommendations | no token | 401 invalid_token | 401 invalid_token | ✓ |
| recommendations | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| recommendations | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| recommendations | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| recommendations | a wrong iss | 401 invalid_token | 401 invalid_token | ✓ |
| recommendations | a wrong aud | 401 invalid_token | 401 invalid_token | ✓ |
| recommendations | an expired token | 401 invalid_token | 401 invalid_token | ✓ |
| engagement | no token | 401 invalid_token | 401 invalid_token | ✓ |
| engagement | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| engagement | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| engagement | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| engagement | a wrong iss | 401 invalid_token | 401 invalid_token | ✓ |
| engagement | a wrong aud | 401 invalid_token | 401 invalid_token | ✓ |
| engagement | an expired token | 401 invalid_token | 401 invalid_token | ✓ |
| check-ins | no token | 401 invalid_token | 401 invalid_token | ✓ |
| check-ins | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| check-ins | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| check-ins | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| check-ins | a wrong iss | 401 invalid_token | 401 invalid_token | ✓ |
| check-ins | a wrong aud | 401 invalid_token | 401 invalid_token | ✓ |
| check-ins | an expired token | 401 invalid_token | 401 invalid_token | ✓ |
| campaigns | no token | 401 invalid_token | 401 invalid_token | ✓ |
| campaigns | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| campaigns | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| campaigns | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| campaigns | a wrong iss | 401 invalid_token | 401 invalid_token | ✓ |
| campaigns | a wrong aud | 401 invalid_token | 401 invalid_token | ✓ |
| campaigns | an expired token | 401 invalid_token | 401 invalid_token | ✓ |
| audit-trail | no token | 401 invalid_token | 401 invalid_token | ✓ |
| audit-trail | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| audit-trail | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| audit-trail | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| audit-trail | a wrong iss | 401 invalid_token | 401 invalid_token | ✓ |
| audit-trail | a wrong aud | 401 invalid_token | 401 invalid_token | ✓ |
| audit-trail | an expired token | 401 invalid_token | 401 invalid_token | ✓ |
| auth | sign-in attempt 13 from one IP, varied emails | 429 rate_limited | 429 rate_limited | ✓ |
| auth | a protected route, redis-state stopped | 503 auth_unavailable | 503 auth_unavailable | ✓ |
| auth | /health/live, redis-state stopped | 2xx | 204 | ✓ |
| users | a protected route, redis-state stopped | 503 auth_unavailable | 503 auth_unavailable | ✓ |
| users | /health/live, redis-state stopped | 2xx | 204 | ✓ |
| products | a protected route, redis-state stopped | 503 auth_unavailable | 503 auth_unavailable | ✓ |
| products | /health/live, redis-state stopped | 2xx | 204 | ✓ |
| categories | a protected route, redis-state stopped | 503 auth_unavailable | 503 auth_unavailable | ✓ |
| categories | /health/live, redis-state stopped | 2xx | 204 | ✓ |
| stores | a protected route, redis-state stopped | 503 auth_unavailable | 503 auth_unavailable | ✓ |
| stores | /health/live, redis-state stopped | 2xx | 204 | ✓ |
| stock | a protected route, redis-state stopped | 503 auth_unavailable | 503 auth_unavailable | ✓ |
| stock | /health/live, redis-state stopped | 2xx | 204 | ✓ |
| customers | a protected route, redis-state stopped | 503 auth_unavailable | 503 auth_unavailable | ✓ |
| customers | /health/live, redis-state stopped | 2xx | 204 | ✓ |
| customer-preferences | a protected route, redis-state stopped | 503 auth_unavailable | 503 auth_unavailable | ✓ |
| customer-preferences | /health/live, redis-state stopped | 2xx | 204 | ✓ |
| sales | a protected route, redis-state stopped | 503 auth_unavailable | 503 auth_unavailable | ✓ |
| sales | /health/live, redis-state stopped | 2xx | 204 | ✓ |
| segments | a protected route, redis-state stopped | 503 auth_unavailable | 503 auth_unavailable | ✓ |
| segments | /health/live, redis-state stopped | 2xx | 204 | ✓ |
| recommendations | a protected route, redis-state stopped | 503 auth_unavailable | 503 auth_unavailable | ✓ |
| recommendations | /health/live, redis-state stopped | 2xx | 204 | ✓ |
| engagement | a protected route, redis-state stopped | 503 auth_unavailable | 503 auth_unavailable | ✓ |
| engagement | /health/live, redis-state stopped | 2xx | 204 | ✓ |
| check-ins | a protected route, redis-state stopped | 503 auth_unavailable | 503 auth_unavailable | ✓ |
| check-ins | /health/live, redis-state stopped | 2xx | 204 | ✓ |
| campaigns | a protected route, redis-state stopped | 503 auth_unavailable | 503 auth_unavailable | ✓ |
| campaigns | /health/live, redis-state stopped | 2xx | 204 | ✓ |
| audit-trail | a protected route, redis-state stopped | 503 auth_unavailable | 503 auth_unavailable | ✓ |
| audit-trail | /health/live, redis-state stopped | 2xx | 204 | ✓ |

## Instance

Run at 2026-10-23 18:25 UTC against `https://127.0.0.1`: 259 cases, 259 held, 0 did not.

- **database, a direct INSERT of a second administrator:** not run here. It writes to the live accounts table, even if rolled back, so it runs on the local stack's disposable database only.
- **stock, segments, recommendations, audit-trail, the body cases:** not applicable. Its contract takes no body.
- **auth, customer-preferences, recommendations, pagination:** not applicable. Its contract has no paginated read.
- **customers, no statement logged for a revoked token:** not run here. It changes the database's logging, so it runs on the local stack only, with MOSAIQ_DB_CONTAINER and MOSAIQ_CUSTOMERS_CONTAINER.
- **sales, a reused Idempotency-Key:** not run here. It needs an accepted sale under that key, which would persist; the local stack's run covers it (ADR-0039).
- **auth, users, products, categories, stores, stock, customers, customer-preferences, sales, segments, recommendations, engagement, check-ins, campaigns, audit-trail, a wrong iss:** not run here. It needs auth's private key, which never leaves auth: the local stack's run and each image's CI conformance suite cover it.
- **auth, users, products, categories, stores, stock, customers, customer-preferences, sales, segments, recommendations, engagement, check-ins, campaigns, audit-trail, a wrong aud:** not run here. It needs auth's private key, which never leaves auth: the local stack's run and each image's CI conformance suite cover it.
- **redis-state, stopped:** not run here. It stops a shared container, so it runs on the local stack only, with MOSAIQ_REDIS_STATE_CONTAINER.

| Area | Case | Expected | Actual | |
|---|---|---|---|---|
| auth | a rotated refresh token reused | 401 refresh_reused | 401 refresh_reused | ✓ |
| auth | that session's access token, next call | 401 session_revoked | 401 session_revoked | ✓ |
| auth | sixth sign-in after 5 failures, same email and IP | 429 rate_limited | 429 rate_limited | ✓ |
| auth | that refusal's Retry-After | 800 to 900 s | 890 s | ✓ |
| users | sixth wrong current password | 429 rate_limited | 429 rate_limited | ✓ |
| database | contracts/db/check_grants.sh | exit 0, prints nothing | exit 0, prints nothing | ✓ |
| database | contracts/db/check_boundaries.sql | exit 0, prints nothing | exit 0, prints nothing | ✓ |
| database | as svc_sales: SELECT 1 FROM public.inventory | permission denied for table inventory | psql:<stdin>:3: ERROR:  permission denied for table inventory | ✓ |
| database | as svc_stock: SELECT api_stock.consume_v1(1::smallint, 1, 1) | permission denied for function consume_v1 | psql:<stdin>:3: ERROR:  permission denied for function consume_v1 | ✓ |
| redis | deploy/infra/check-redis.sh (ACL DRYRUN per user, settings) | exit 0, prints nothing | exit 0, prints nothing | ✓ |
| auth | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| users | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| products | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| categories | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| stores | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| stock | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| customers | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| customer-preferences | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| sales | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| segments | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| recommendations | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| engagement | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| check-ins | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| campaigns | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| audit-trail | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| auth | Content-Type: text/plain on POST /v1/sessions/refresh | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| auth | an XML body with a DOCTYPE on POST /v1/sessions/refresh | 400 malformed_body | 400 malformed_body | ✓ |
| auth | an XML body with an entity declaration on POST /v1/sessions/refresh | 400 malformed_body | 400 malformed_body | ✓ |
| auth | 65 KiB on POST /v1/sessions/refresh, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| auth | 65 KiB on POST /v1/sessions/refresh, on 127.0.0.1:5001 | 413 payload_too_large | 413 payload_too_large | ✓ |
| users | Content-Type: text/plain on POST /v1/administrator-transfers | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| users | an XML body with a DOCTYPE on POST /v1/administrator-transfers | 400 malformed_body | 400 malformed_body | ✓ |
| users | an XML body with an entity declaration on POST /v1/administrator-transfers | 400 malformed_body | 400 malformed_body | ✓ |
| users | 65 KiB on POST /v1/administrator-transfers, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| users | 65 KiB on POST /v1/administrator-transfers, on 127.0.0.1:5002 | 413 payload_too_large | 413 payload_too_large | ✓ |
| products | Content-Type: text/plain on POST /v1/products | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| products | an XML body with a DOCTYPE on POST /v1/products | 400 malformed_body | 400 malformed_body | ✓ |
| products | an XML body with an entity declaration on POST /v1/products | 400 malformed_body | 400 malformed_body | ✓ |
| products | 65 KiB on POST /v1/products, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| products | 65 KiB on POST /v1/products, on 127.0.0.1:5003 | 413 payload_too_large | 413 payload_too_large | ✓ |
| categories | Content-Type: text/plain on POST /v1/categories | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| categories | an XML body with a DOCTYPE on POST /v1/categories | 400 malformed_body | 400 malformed_body | ✓ |
| categories | an XML body with an entity declaration on POST /v1/categories | 400 malformed_body | 400 malformed_body | ✓ |
| categories | 65 KiB on POST /v1/categories, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| categories | 65 KiB on POST /v1/categories, on 127.0.0.1:5004 | 413 payload_too_large | 413 payload_too_large | ✓ |
| stores | Content-Type: text/plain on PUT /v1/stores/{store_id}/location | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| stores | an XML body with a DOCTYPE on PUT /v1/stores/{store_id}/location | 400 malformed_body | 400 malformed_body | ✓ |
| stores | an XML body with an entity declaration on PUT /v1/stores/{store_id}/location | 400 malformed_body | 400 malformed_body | ✓ |
| stores | 65 KiB on PUT /v1/stores/{store_id}/location, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| stores | 65 KiB on PUT /v1/stores/{store_id}/location, on 127.0.0.1:5005 | 413 payload_too_large | 413 payload_too_large | ✓ |
| customers | Content-Type: text/plain on POST /v1/customers | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| customers | an XML body with a DOCTYPE on POST /v1/customers | 400 malformed_body | 400 malformed_body | ✓ |
| customers | an XML body with an entity declaration on POST /v1/customers | 400 malformed_body | 400 malformed_body | ✓ |
| customers | 65 KiB on POST /v1/customers, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| customers | 65 KiB on POST /v1/customers, on 127.0.0.1:5007 | 413 payload_too_large | 413 payload_too_large | ✓ |
| customer-preferences | Content-Type: text/plain on PUT /v1/preference-profiles/{customer_id}/interest-categories | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| customer-preferences | an XML body with a DOCTYPE on PUT /v1/preference-profiles/{customer_id}/interest-categories | 400 malformed_body | 400 malformed_body | ✓ |
| customer-preferences | an XML body with an entity declaration on PUT /v1/preference-profiles/{customer_id}/interest-categories | 400 malformed_body | 400 malformed_body | ✓ |
| customer-preferences | 65 KiB on PUT /v1/preference-profiles/{customer_id}/interest-categories, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| customer-preferences | 65 KiB on PUT /v1/preference-profiles/{customer_id}/interest-categories, on 127.0.0.1:5008 | 413 payload_too_large | 413 payload_too_large | ✓ |
| sales | Content-Type: text/plain on POST /v1/sales | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| sales | an XML body with a DOCTYPE on POST /v1/sales | 400 malformed_body | 400 malformed_body | ✓ |
| sales | an XML body with an entity declaration on POST /v1/sales | 400 malformed_body | 400 malformed_body | ✓ |
| sales | 65 KiB on POST /v1/sales, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| sales | 65 KiB on POST /v1/sales, on 127.0.0.1:5009 | 413 payload_too_large | 413 payload_too_large | ✓ |
| engagement | Content-Type: text/plain on POST /v1/engagement-events | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| engagement | an XML body with a DOCTYPE on POST /v1/engagement-events | 400 malformed_body | 400 malformed_body | ✓ |
| engagement | an XML body with an entity declaration on POST /v1/engagement-events | 400 malformed_body | 400 malformed_body | ✓ |
| engagement | 65 KiB on POST /v1/engagement-events, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| engagement | 65 KiB on POST /v1/engagement-events, on 127.0.0.1:5012 | 413 payload_too_large | 413 payload_too_large | ✓ |
| check-ins | Content-Type: text/plain on POST /v1/check-ins | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| check-ins | an XML body with a DOCTYPE on POST /v1/check-ins | 400 malformed_body | 400 malformed_body | ✓ |
| check-ins | an XML body with an entity declaration on POST /v1/check-ins | 400 malformed_body | 400 malformed_body | ✓ |
| check-ins | 65 KiB on POST /v1/check-ins, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| check-ins | 65 KiB on POST /v1/check-ins, on 127.0.0.1:5013 | 413 payload_too_large | 413 payload_too_large | ✓ |
| campaigns | Content-Type: text/plain on POST /v1/campaigns | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| campaigns | an XML body with a DOCTYPE on POST /v1/campaigns | 400 malformed_body | 400 malformed_body | ✓ |
| campaigns | an XML body with an entity declaration on POST /v1/campaigns | 400 malformed_body | 400 malformed_body | ✓ |
| campaigns | 65 KiB on POST /v1/campaigns, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| campaigns | 65 KiB on POST /v1/campaigns, on 127.0.0.1:5014 | 413 payload_too_large | 413 payload_too_large | ✓ |
| users | page=0 on GET /v1/users | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| users | size=101 on GET /v1/users | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| products | page=0 on GET /v1/products | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| products | size=101 on GET /v1/products | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| categories | page=0 on GET /v1/categories | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| categories | size=101 on GET /v1/categories | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| categories | page=0 on GET /v1/categories/{category_id}/descendants | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| categories | size=101 on GET /v1/categories/{category_id}/descendants | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stores | page=0 on GET /v1/stores | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stores | size=101 on GET /v1/stores | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stock | page=0 on GET /v1/stock-levels | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stock | size=101 on GET /v1/stock-levels | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customers | page=0 on GET /v1/customers | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customers | size=101 on GET /v1/customers | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| sales | page=0 on GET /v1/sales | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| sales | size=101 on GET /v1/sales | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| segments | page=0 on GET /v1/segment-assignments | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| segments | size=101 on GET /v1/segment-assignments | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| engagement | page=0 on GET /v1/engagement-events | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| engagement | size=101 on GET /v1/engagement-events | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| check-ins | page=0 on GET /v1/check-ins | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| check-ins | size=101 on GET /v1/check-ins | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| campaigns | page=0 on GET /v1/campaigns | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| campaigns | size=101 on GET /v1/campaigns | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| audit-trail | page=0 on GET /v1/audit-entries | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| audit-trail | size=101 on GET /v1/audit-entries | 422 invalid_pagination | 422 invalid_pagination | ✓ |
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
| users | ANALYST on GET /v1/users | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on POST /v1/users | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on GET /v1/users/{user_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on PATCH /v1/users/{user_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on POST /v1/users/{user_id}/deactivation | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on DELETE /v1/users/{user_id}/deactivation | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on GET /v1/roles | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on GET /v1/roles/{role_code} | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on POST /v1/administrator-transfers | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | CUSTOMER on GET /v1/products | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | ANALYST on POST /v1/products | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | CUSTOMER on GET /v1/products/{product_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | ANALYST on PATCH /v1/products/{product_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| categories | CUSTOMER on GET /v1/categories | 403 forbidden_role | 403 forbidden_role | ✓ |
| categories | ANALYST on POST /v1/categories | 403 forbidden_role | 403 forbidden_role | ✓ |
| categories | CUSTOMER on GET /v1/categories/{category_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| categories | ANALYST on PATCH /v1/categories/{category_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| categories | ANALYST on DELETE /v1/categories/{category_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| categories | CUSTOMER on GET /v1/categories/{category_id}/descendants | 403 forbidden_role | 403 forbidden_role | ✓ |
| stores | CUSTOMER on GET /v1/stores | 403 forbidden_role | 403 forbidden_role | ✓ |
| stores | CUSTOMER on GET /v1/stores/{store_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| stores | ANALYST on PUT /v1/stores/{store_id}/location | 403 forbidden_role | 403 forbidden_role | ✓ |
| stores | ANALYST on DELETE /v1/stores/{store_id}/location | 403 forbidden_role | 403 forbidden_role | ✓ |
| stock | CUSTOMER on GET /v1/stock-levels | 403 forbidden_role | 403 forbidden_role | ✓ |
| stock | CUSTOMER on GET /v1/stock-levels/{store_id}/{product_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | INVENTORY_PLANNER on GET /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | ANALYST on POST /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | INVENTORY_PLANNER on GET /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | ANALYST on PATCH /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | ANALYST on PUT /v1/preference-profiles/{customer_id}/preferred-channels | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | ANALYST on PUT /v1/preference-profiles/{customer_id}/interest-categories | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | INVENTORY_PLANNER on GET /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | ANALYST on POST /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | INVENTORY_PLANNER on GET /v1/sales/{sale_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | CUSTOMER on GET /v1/channels | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segments | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segments/{label_code} | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| recommendations | INVENTORY_PLANNER on GET /v1/recommendations | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | INVENTORY_PLANNER on GET /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | ANALYST on POST /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | INVENTORY_PLANNER on GET /v1/engagement-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | ANALYST on POST /v1/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | CUSTOMER on GET /v1/engagement-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| check-ins | ANALYST on GET /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| check-ins | ANALYST on POST /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| check-ins | ANALYST on GET /v1/check-ins/{check_in_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| check-ins | ANALYST on POST /v1/check-ins/{check_in_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | STORE_MANAGER on GET /v1/campaigns | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | ANALYST on POST /v1/campaigns | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | STORE_MANAGER on GET /v1/campaigns/{campaign_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | ANALYST on PATCH /v1/campaigns/{campaign_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | ANALYST on POST /v1/campaigns/{campaign_id}/transitions | 403 forbidden_role | 403 forbidden_role | ✓ |
| audit-trail | ANALYST on GET /v1/audit-entries | 403 forbidden_role | 403 forbidden_role | ✓ |
| audit-trail | ANALYST on GET /v1/audit-entries/{audit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | POST /v1/users with role ADMIN | 409 single_administrator | 409 single_administrator | ✓ |
| users | PATCH an ANALYST to role ADMIN | 409 use_administrator_transfer | 409 use_administrator_transfer | ✓ |
| users | deactivating the only ADMIN | 409 administrator_required | 409 administrator_required | ✓ |
| sales | a line 1,000 over the stock on hand | 409 stock_unavailable | 409 stock_unavailable | ✓ |
| sales | no Idempotency-Key | 400 idempotency_key_required | 400 idempotency_key_required | ✓ |
| auth | no token | 401 invalid_token | 401 invalid_token | ✓ |
| auth | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| auth | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| auth | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| users | no token | 401 invalid_token | 401 invalid_token | ✓ |
| users | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| users | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| users | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| products | no token | 401 invalid_token | 401 invalid_token | ✓ |
| products | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| products | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| products | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| categories | no token | 401 invalid_token | 401 invalid_token | ✓ |
| categories | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| categories | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| categories | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| stores | no token | 401 invalid_token | 401 invalid_token | ✓ |
| stores | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| stores | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| stores | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| stock | no token | 401 invalid_token | 401 invalid_token | ✓ |
| stock | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| stock | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| stock | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| customers | no token | 401 invalid_token | 401 invalid_token | ✓ |
| customers | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| customers | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| customers | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| customer-preferences | no token | 401 invalid_token | 401 invalid_token | ✓ |
| customer-preferences | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| customer-preferences | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| customer-preferences | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| sales | no token | 401 invalid_token | 401 invalid_token | ✓ |
| sales | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| sales | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| sales | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| segments | no token | 401 invalid_token | 401 invalid_token | ✓ |
| segments | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| segments | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| segments | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| recommendations | no token | 401 invalid_token | 401 invalid_token | ✓ |
| recommendations | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| recommendations | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| recommendations | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| engagement | no token | 401 invalid_token | 401 invalid_token | ✓ |
| engagement | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| engagement | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| engagement | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| check-ins | no token | 401 invalid_token | 401 invalid_token | ✓ |
| check-ins | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| check-ins | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| check-ins | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| campaigns | no token | 401 invalid_token | 401 invalid_token | ✓ |
| campaigns | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| campaigns | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| campaigns | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| audit-trail | no token | 401 invalid_token | 401 invalid_token | ✓ |
| audit-trail | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| audit-trail | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| audit-trail | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| auth | sign-in attempt 6 from one IP, varied emails | 429 rate_limited | 429 rate_limited | ✓ |
| auth | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| users | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| products | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| categories | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| stores | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| stock | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| customers | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| customer-preferences | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| sales | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| segments | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| recommendations | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| engagement | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| check-ins | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| campaigns | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| audit-trail | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |

## Instance, probed from outside

Run at 2026-10-23 18:30 UTC against `34.51.123.31`: 18 cases, 18 held, 0 did not.

| Area | Case | Expected | Actual | |
|---|---|---|---|---|
| network | control: tcp/22 on 34.51.123.31, which the firewall allows | open | open | ✓ |
| network | tcp/5001 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/5002 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/5003 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/5004 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/5005 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/5006 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/5007 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/5008 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/5009 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/5010 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/5011 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/5012 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/5013 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/5014 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/5015 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/6379 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
| network | tcp/27017 on 34.51.123.31 | closed or filtered | filtered (no answer in 4 s) | ✓ |
