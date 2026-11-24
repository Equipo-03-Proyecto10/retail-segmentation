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

Run at 2026-11-24 04:16 UTC against `https://127.0.0.1`: 1703 cases, 1701 held, 2 did not.

- **database, a direct INSERT of a second administrator:** not run here. It writes to the live accounts table, even if rolled back, so it runs on the local stack's disposable database only.
- **segments, recommendations, audit-trail, analytics-kpis, the body cases:** not applicable. Its contract takes no body.
- **auth, pagination:** not applicable. Its contract has no paginated read.
- **customers, no statement logged for a revoked token:** not run here. It changes the database's logging, so it runs on the local stack only, with MOSAIQ_DB_CONTAINER and MOSAIQ_CUSTOMERS_CONTAINER.
- **sales, a reused Idempotency-Key:** not run here. It needs an accepted sale under that key, which would persist; the local stack's run covers it (ADR-0039).
- **store scope, reads and reversals by id of another store's rows:** not run here. They need a sale, a reversal, an event and a check-in in another store, which persist; the local stack's run covers them.
- **auth, users, products, categories, stores, stock, customers, customer-preferences, sales, segments, recommendations, engagement, check-ins, campaigns, audit-trail, rfm-engine, model-registry, experimentation, analytics-kpis, a wrong iss:** not run here. It needs auth's private key, which never leaves auth: the local stack's run and each image's CI conformance suite cover it.
- **auth, users, products, categories, stores, stock, customers, customer-preferences, sales, segments, recommendations, engagement, check-ins, campaigns, audit-trail, rfm-engine, model-registry, experimentation, analytics-kpis, a wrong aud:** not run here. It needs auth's private key, which never leaves auth: the local stack's run and each image's CI conformance suite cover it.
- **redis-state, stopped:** not run here. It stops a shared container, so it runs on the local stack only, with MOSAIQ_REDIS_STATE_CONTAINER.

| Area | Case | Expected | Actual | |
|---|---|---|---|---|
| auth | a rotated refresh token reused | 401 refresh_reused | 401 refresh_reused | ✓ |
| auth | that session's access token, next call | 401 session_revoked | 401 session_revoked | ✓ |
| auth | sixth sign-in after 5 failures, same email and IP | 429 rate_limited | 429 rate_limited | ✓ |
| auth | that refusal's Retry-After | 800 to 900 s | 890 s | ✓ |
| users | sixth wrong current password | 429 rate_limited | 429 rate_limited | ✓ |
| database | contracts/db/check_grants.sh | exit 0, prints nothing | exit 1, 636 lines | ✗ |
| database | contracts/db/check_boundaries.sql | exit 0, prints nothing | exit 0, prints nothing | ✓ |
| database | as svc_sales: SELECT 1 FROM public.inventory | permission denied for table inventory | psql:<stdin>:3: ERROR:  permission denied for table inventory | ✓ |
| database | as svc_stock: SELECT api_stock.consume_v1(1::smallint, 1, 1) | permission denied for function consume_v1 | psql:<stdin>:3: ERROR:  permission denied for function consume_v1 | ✓ |
| redis | deploy/infra/check-redis.sh (ACL DRYRUN per user, settings) | exit 0, prints nothing | exit 0, prints nothing | ✓ |
| privacy: customers | INVENTORY_PLANNER on GET /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on GET /v1/customers, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on GET /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on GET /v1/customers, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | SALES_ASSOCIATE on GET /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | SALES_ASSOCIATE on GET /v1/customers, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on GET /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on GET /v1/customers, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ANALYST on POST /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | MARKETING on POST /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on POST /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | AUDITOR on POST /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on POST /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | SALES_ASSOCIATE on POST /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on POST /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on GET /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on GET /v1/customers/{customer_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on GET /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on GET /v1/customers/{customer_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | SALES_ASSOCIATE on GET /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | SALES_ASSOCIATE on GET /v1/customers/{customer_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on GET /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on GET /v1/customers/{customer_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ANALYST on PATCH /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | MARKETING on PATCH /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on PATCH /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | AUDITOR on PATCH /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on PATCH /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | SALES_ASSOCIATE on PATCH /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on PATCH /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ANALYST on PUT /v1/customers/{customer_id}/anonymization | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | STORE_MANAGER on PUT /v1/customers/{customer_id}/anonymization | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | MARKETING on PUT /v1/customers/{customer_id}/anonymization | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on PUT /v1/customers/{customer_id}/anonymization | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | AUDITOR on PUT /v1/customers/{customer_id}/anonymization | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on PUT /v1/customers/{customer_id}/anonymization | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | SALES_ASSOCIATE on PUT /v1/customers/{customer_id}/anonymization | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | LOYALTY_OFFICER on PUT /v1/customers/{customer_id}/anonymization | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on PUT /v1/customers/{customer_id}/anonymization | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ANALYST on GET /v1/customers/retention-candidates | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ANALYST on GET /v1/customers/retention-candidates, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | STORE_MANAGER on GET /v1/customers/retention-candidates | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | STORE_MANAGER on GET /v1/customers/retention-candidates, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | MARKETING on GET /v1/customers/retention-candidates | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | MARKETING on GET /v1/customers/retention-candidates, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on GET /v1/customers/retention-candidates | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on GET /v1/customers/retention-candidates, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | AUDITOR on GET /v1/customers/retention-candidates | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | AUDITOR on GET /v1/customers/retention-candidates, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on GET /v1/customers/retention-candidates | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on GET /v1/customers/retention-candidates, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | SALES_ASSOCIATE on GET /v1/customers/retention-candidates | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | SALES_ASSOCIATE on GET /v1/customers/retention-candidates, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | LOYALTY_OFFICER on GET /v1/customers/retention-candidates | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | LOYALTY_OFFICER on GET /v1/customers/retention-candidates, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on GET /v1/customers/retention-candidates | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on GET /v1/customers/retention-candidates, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ADMIN on GET /v1/customers/floor-search | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ADMIN on GET /v1/customers/floor-search, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ANALYST on GET /v1/customers/floor-search | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ANALYST on GET /v1/customers/floor-search, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | STORE_MANAGER on GET /v1/customers/floor-search | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | STORE_MANAGER on GET /v1/customers/floor-search, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | MARKETING on GET /v1/customers/floor-search | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | MARKETING on GET /v1/customers/floor-search, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on GET /v1/customers/floor-search | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on GET /v1/customers/floor-search, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | AUDITOR on GET /v1/customers/floor-search | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | AUDITOR on GET /v1/customers/floor-search, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on GET /v1/customers/floor-search | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on GET /v1/customers/floor-search, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on GET /v1/customers/floor-search | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on GET /v1/customers/floor-search, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ADMIN on GET /v1/customers/{customer_id}/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ADMIN on GET /v1/customers/{customer_id}/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ANALYST on GET /v1/customers/{customer_id}/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ANALYST on GET /v1/customers/{customer_id}/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | STORE_MANAGER on GET /v1/customers/{customer_id}/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | STORE_MANAGER on GET /v1/customers/{customer_id}/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | MARKETING on GET /v1/customers/{customer_id}/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | MARKETING on GET /v1/customers/{customer_id}/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on GET /v1/customers/{customer_id}/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on GET /v1/customers/{customer_id}/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | AUDITOR on GET /v1/customers/{customer_id}/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | AUDITOR on GET /v1/customers/{customer_id}/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on GET /v1/customers/{customer_id}/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on GET /v1/customers/{customer_id}/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on GET /v1/customers/{customer_id}/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on GET /v1/customers/{customer_id}/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ANALYST on GET /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ANALYST on GET /v1/loyalty-cards, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | STORE_MANAGER on GET /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | STORE_MANAGER on GET /v1/loyalty-cards, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | MARKETING on GET /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | MARKETING on GET /v1/loyalty-cards, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on GET /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on GET /v1/loyalty-cards, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | AUDITOR on GET /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | AUDITOR on GET /v1/loyalty-cards, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on GET /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on GET /v1/loyalty-cards, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | SALES_ASSOCIATE on GET /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | SALES_ASSOCIATE on GET /v1/loyalty-cards, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on GET /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on GET /v1/loyalty-cards, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ANALYST on POST /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | STORE_MANAGER on POST /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | MARKETING on POST /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on POST /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | AUDITOR on POST /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on POST /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | SALES_ASSOCIATE on POST /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on POST /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ANALYST on POST /v1/loyalty-cards/lookup | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | STORE_MANAGER on POST /v1/loyalty-cards/lookup | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | MARKETING on POST /v1/loyalty-cards/lookup | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on POST /v1/loyalty-cards/lookup | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | AUDITOR on POST /v1/loyalty-cards/lookup | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on POST /v1/loyalty-cards/lookup | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on POST /v1/loyalty-cards/lookup | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | ANALYST on PUT /v1/loyalty-cards/{card_id}/revocation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | STORE_MANAGER on PUT /v1/loyalty-cards/{card_id}/revocation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | MARKETING on PUT /v1/loyalty-cards/{card_id}/revocation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | INVENTORY_PLANNER on PUT /v1/loyalty-cards/{card_id}/revocation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | AUDITOR on PUT /v1/loyalty-cards/{card_id}/revocation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | CUSTOMER on PUT /v1/loyalty-cards/{card_id}/revocation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | SALES_ASSOCIATE on PUT /v1/loyalty-cards/{card_id}/revocation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customers | COMMERCIAL_SUPERVISOR on PUT /v1/loyalty-cards/{card_id}/revocation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on GET /v1/preference-profiles/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on GET /v1/preference-profiles/{customer_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | SALES_ASSOCIATE on GET /v1/preference-profiles/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | SALES_ASSOCIATE on GET /v1/preference-profiles/{customer_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on GET /v1/preference-profiles/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on GET /v1/preference-profiles/{customer_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | ANALYST on PUT /v1/preference-profiles/{customer_id}/preferred-channels | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | MARKETING on PUT /v1/preference-profiles/{customer_id}/preferred-channels | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on PUT /v1/preference-profiles/{customer_id}/preferred-channels | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | AUDITOR on PUT /v1/preference-profiles/{customer_id}/preferred-channels | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on PUT /v1/preference-profiles/{customer_id}/preferred-channels | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | SALES_ASSOCIATE on PUT /v1/preference-profiles/{customer_id}/preferred-channels | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on PUT /v1/preference-profiles/{customer_id}/preferred-channels | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | ANALYST on PUT /v1/preference-profiles/{customer_id}/interest-categories | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | MARKETING on PUT /v1/preference-profiles/{customer_id}/interest-categories | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on PUT /v1/preference-profiles/{customer_id}/interest-categories | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | AUDITOR on PUT /v1/preference-profiles/{customer_id}/interest-categories | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on PUT /v1/preference-profiles/{customer_id}/interest-categories | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | SALES_ASSOCIATE on PUT /v1/preference-profiles/{customer_id}/interest-categories | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on PUT /v1/preference-profiles/{customer_id}/interest-categories | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id}/interests | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id}/interests, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on GET /v1/preference-profiles/{customer_id}/interests | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on GET /v1/preference-profiles/{customer_id}/interests, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on GET /v1/preference-profiles/{customer_id}/interests | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on GET /v1/preference-profiles/{customer_id}/interests, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id}/restrictions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id}/restrictions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on GET /v1/preference-profiles/{customer_id}/restrictions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on GET /v1/preference-profiles/{customer_id}/restrictions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on GET /v1/preference-profiles/{customer_id}/restrictions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on GET /v1/preference-profiles/{customer_id}/restrictions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | ANALYST on GET /v1/preference-profiles/{customer_id}/consent | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | ANALYST on GET /v1/preference-profiles/{customer_id}/consent, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | MARKETING on GET /v1/preference-profiles/{customer_id}/consent | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | MARKETING on GET /v1/preference-profiles/{customer_id}/consent, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id}/consent | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id}/consent, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on GET /v1/preference-profiles/{customer_id}/consent | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on GET /v1/preference-profiles/{customer_id}/consent, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | SALES_ASSOCIATE on GET /v1/preference-profiles/{customer_id}/consent | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | SALES_ASSOCIATE on GET /v1/preference-profiles/{customer_id}/consent, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on GET /v1/preference-profiles/{customer_id}/consent | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on GET /v1/preference-profiles/{customer_id}/consent, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | ANALYST on GET /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | ANALYST on GET /v1/preference-profiles/{customer_id}/consent-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | MARKETING on GET /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | MARKETING on GET /v1/preference-profiles/{customer_id}/consent-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id}/consent-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on GET /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on GET /v1/preference-profiles/{customer_id}/consent-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | SALES_ASSOCIATE on GET /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | SALES_ASSOCIATE on GET /v1/preference-profiles/{customer_id}/consent-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on GET /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on GET /v1/preference-profiles/{customer_id}/consent-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | ANALYST on POST /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | STORE_MANAGER on POST /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | MARKETING on POST /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on POST /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | AUDITOR on POST /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on POST /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | SALES_ASSOCIATE on POST /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on POST /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | ANALYST on GET /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | ANALYST on GET /v1/preference-profiles/{customer_id}/restriction-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | MARKETING on GET /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | MARKETING on GET /v1/preference-profiles/{customer_id}/restriction-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id}/restriction-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on GET /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on GET /v1/preference-profiles/{customer_id}/restriction-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | SALES_ASSOCIATE on GET /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | SALES_ASSOCIATE on GET /v1/preference-profiles/{customer_id}/restriction-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on GET /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on GET /v1/preference-profiles/{customer_id}/restriction-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | ANALYST on POST /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | STORE_MANAGER on POST /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | MARKETING on POST /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on POST /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | AUDITOR on POST /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on POST /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | SALES_ASSOCIATE on POST /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on POST /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | ANALYST on GET /v1/preference-profiles/{customer_id}/preference-changes | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | ANALYST on GET /v1/preference-profiles/{customer_id}/preference-changes, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | MARKETING on GET /v1/preference-profiles/{customer_id}/preference-changes | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | MARKETING on GET /v1/preference-profiles/{customer_id}/preference-changes, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id}/preference-changes | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id}/preference-changes, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on GET /v1/preference-profiles/{customer_id}/preference-changes | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | CUSTOMER on GET /v1/preference-profiles/{customer_id}/preference-changes, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | SALES_ASSOCIATE on GET /v1/preference-profiles/{customer_id}/preference-changes | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | SALES_ASSOCIATE on GET /v1/preference-profiles/{customer_id}/preference-changes, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on GET /v1/preference-profiles/{customer_id}/preference-changes | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: customer-preferences | COMMERCIAL_SUPERVISOR on GET /v1/preference-profiles/{customer_id}/preference-changes, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sales, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sales, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sales, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | ANALYST on POST /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on POST /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on POST /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | AUDITOR on POST /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on POST /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on POST /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on POST /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sales/{sale_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sales/{sale_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sales/{sale_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sales/{sale_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sales/{sale_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sales/{sale_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-reversals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-reversals, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-reversals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-reversals, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-reversals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-reversals, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | ANALYST on POST /v1/sale-reversals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on POST /v1/sale-reversals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on POST /v1/sale-reversals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | AUDITOR on POST /v1/sale-reversals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on POST /v1/sale-reversals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on POST /v1/sale-reversals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on POST /v1/sale-reversals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-reversals/{reversal_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-reversals/{reversal_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-reversals/{reversal_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-reversals/{reversal_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-reversals/{reversal_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-reversals/{reversal_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | ANALYST on GET /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | ANALYST on GET /v1/sale-imports, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | STORE_MANAGER on GET /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | STORE_MANAGER on GET /v1/sale-imports, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on GET /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on GET /v1/sale-imports, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-imports, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | AUDITOR on GET /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | AUDITOR on GET /v1/sale-imports, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-imports, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on GET /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on GET /v1/sale-imports, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-imports, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on GET /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on GET /v1/sale-imports, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | ANALYST on POST /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | STORE_MANAGER on POST /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on POST /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on POST /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | AUDITOR on POST /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on POST /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on POST /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on POST /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on POST /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | ANALYST on GET /v1/sale-imports/{import_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | ANALYST on GET /v1/sale-imports/{import_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | STORE_MANAGER on GET /v1/sale-imports/{import_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | STORE_MANAGER on GET /v1/sale-imports/{import_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on GET /v1/sale-imports/{import_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on GET /v1/sale-imports/{import_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-imports/{import_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-imports/{import_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | AUDITOR on GET /v1/sale-imports/{import_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | AUDITOR on GET /v1/sale-imports/{import_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-imports/{import_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-imports/{import_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on GET /v1/sale-imports/{import_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on GET /v1/sale-imports/{import_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-imports/{import_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-imports/{import_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on GET /v1/sale-imports/{import_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on GET /v1/sale-imports/{import_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | ANALYST on PUT /v1/sale-imports/{import_id}/chunks/{sequence} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | STORE_MANAGER on PUT /v1/sale-imports/{import_id}/chunks/{sequence} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on PUT /v1/sale-imports/{import_id}/chunks/{sequence} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on PUT /v1/sale-imports/{import_id}/chunks/{sequence} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | AUDITOR on PUT /v1/sale-imports/{import_id}/chunks/{sequence} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on PUT /v1/sale-imports/{import_id}/chunks/{sequence} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on PUT /v1/sale-imports/{import_id}/chunks/{sequence} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on PUT /v1/sale-imports/{import_id}/chunks/{sequence} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on PUT /v1/sale-imports/{import_id}/chunks/{sequence} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | ANALYST on GET /v1/sale-imports/{import_id}/rejections | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | ANALYST on GET /v1/sale-imports/{import_id}/rejections, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | STORE_MANAGER on GET /v1/sale-imports/{import_id}/rejections | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | STORE_MANAGER on GET /v1/sale-imports/{import_id}/rejections, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on GET /v1/sale-imports/{import_id}/rejections | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on GET /v1/sale-imports/{import_id}/rejections, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-imports/{import_id}/rejections | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-imports/{import_id}/rejections, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | AUDITOR on GET /v1/sale-imports/{import_id}/rejections | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | AUDITOR on GET /v1/sale-imports/{import_id}/rejections, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-imports/{import_id}/rejections | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-imports/{import_id}/rejections, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on GET /v1/sale-imports/{import_id}/rejections | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on GET /v1/sale-imports/{import_id}/rejections, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-imports/{import_id}/rejections | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-imports/{import_id}/rejections, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on GET /v1/sale-imports/{import_id}/rejections | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on GET /v1/sale-imports/{import_id}/rejections, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | ANALYST on PUT /v1/sale-imports/{import_id}/status | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | STORE_MANAGER on PUT /v1/sale-imports/{import_id}/status | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on PUT /v1/sale-imports/{import_id}/status | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on PUT /v1/sale-imports/{import_id}/status | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | AUDITOR on PUT /v1/sale-imports/{import_id}/status | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on PUT /v1/sale-imports/{import_id}/status | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on PUT /v1/sale-imports/{import_id}/status | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on PUT /v1/sale-imports/{import_id}/status | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on PUT /v1/sale-imports/{import_id}/status | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | STORE_MANAGER on POST /v1/sale-imports/{import_id}/reconciliation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on POST /v1/sale-imports/{import_id}/reconciliation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on POST /v1/sale-imports/{import_id}/reconciliation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | AUDITOR on POST /v1/sale-imports/{import_id}/reconciliation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on POST /v1/sale-imports/{import_id}/reconciliation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on POST /v1/sale-imports/{import_id}/reconciliation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on POST /v1/sale-imports/{import_id}/reconciliation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on POST /v1/sale-imports/{import_id}/reconciliation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | STORE_MANAGER on GET /v1/sale-imports/{import_id}/reconciliation-cases | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | STORE_MANAGER on GET /v1/sale-imports/{import_id}/reconciliation-cases, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on GET /v1/sale-imports/{import_id}/reconciliation-cases | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on GET /v1/sale-imports/{import_id}/reconciliation-cases, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-imports/{import_id}/reconciliation-cases | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-imports/{import_id}/reconciliation-cases, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-imports/{import_id}/reconciliation-cases | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-imports/{import_id}/reconciliation-cases, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on GET /v1/sale-imports/{import_id}/reconciliation-cases | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on GET /v1/sale-imports/{import_id}/reconciliation-cases, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-imports/{import_id}/reconciliation-cases | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-imports/{import_id}/reconciliation-cases, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on GET /v1/sale-imports/{import_id}/reconciliation-cases | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on GET /v1/sale-imports/{import_id}/reconciliation-cases, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | STORE_MANAGER on PUT /v1/sale-imports/{import_id}/reconciliation-cases/{case_id}/resolution | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on PUT /v1/sale-imports/{import_id}/reconciliation-cases/{case_id}/resolution | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on PUT /v1/sale-imports/{import_id}/reconciliation-cases/{case_id}/resolution | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | AUDITOR on PUT /v1/sale-imports/{import_id}/reconciliation-cases/{case_id}/resolution | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on PUT /v1/sale-imports/{import_id}/reconciliation-cases/{case_id}/resolution | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on PUT /v1/sale-imports/{import_id}/reconciliation-cases/{case_id}/resolution | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on PUT /v1/sale-imports/{import_id}/reconciliation-cases/{case_id}/resolution | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on PUT /v1/sale-imports/{import_id}/reconciliation-cases/{case_id}/resolution | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | STORE_MANAGER on GET /v1/sale-imports/{import_id}/quality | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | STORE_MANAGER on GET /v1/sale-imports/{import_id}/quality, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on GET /v1/sale-imports/{import_id}/quality | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on GET /v1/sale-imports/{import_id}/quality, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-imports/{import_id}/quality | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-imports/{import_id}/quality, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-imports/{import_id}/quality | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-imports/{import_id}/quality, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on GET /v1/sale-imports/{import_id}/quality | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on GET /v1/sale-imports/{import_id}/quality, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-imports/{import_id}/quality | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-imports/{import_id}/quality, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on GET /v1/sale-imports/{import_id}/quality | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | COMMERCIAL_SUPERVISOR on GET /v1/sale-imports/{import_id}/quality, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on GET /v1/sale-promotion-totals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | MARKETING on GET /v1/sale-promotion-totals, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-promotion-totals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | INVENTORY_PLANNER on GET /v1/sale-promotion-totals, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | AUDITOR on GET /v1/sale-promotion-totals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | AUDITOR on GET /v1/sale-promotion-totals, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-promotion-totals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/sale-promotion-totals, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on GET /v1/sale-promotion-totals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | SALES_ASSOCIATE on GET /v1/sale-promotion-totals, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-promotion-totals | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | LOYALTY_OFFICER on GET /v1/sale-promotion-totals, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/channels | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: sales | CUSTOMER on GET /v1/channels, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segments/{label_code} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segments/{label_code}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segments/{label_code} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segments/{label_code}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segments/{label_code} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segments/{label_code}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-assignments/current, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-assignments/current, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | SALES_ASSOCIATE on GET /v1/segment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | SALES_ASSOCIATE on GET /v1/segment-assignments/current, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | LOYALTY_OFFICER on GET /v1/segment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | LOYALTY_OFFICER on GET /v1/segment-assignments/current, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-assignments/current, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-assignments/current-state | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-assignments/current-state, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-assignments/current-state | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-assignments/current-state, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | SALES_ASSOCIATE on GET /v1/segment-assignments/current-state | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | SALES_ASSOCIATE on GET /v1/segment-assignments/current-state, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | LOYALTY_OFFICER on GET /v1/segment-assignments/current-state | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | LOYALTY_OFFICER on GET /v1/segment-assignments/current-state, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-assignments/current-state | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-assignments/current-state, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | ADMIN on GET /v1/segment-assignments/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | ADMIN on GET /v1/segment-assignments/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | ANALYST on GET /v1/segment-assignments/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | ANALYST on GET /v1/segment-assignments/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | STORE_MANAGER on GET /v1/segment-assignments/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | STORE_MANAGER on GET /v1/segment-assignments/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | MARKETING on GET /v1/segment-assignments/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | MARKETING on GET /v1/segment-assignments/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-assignments/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-assignments/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | AUDITOR on GET /v1/segment-assignments/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | AUDITOR on GET /v1/segment-assignments/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-assignments/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-assignments/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-assignments/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-assignments/floor-view, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-assignments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-assignments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | SALES_ASSOCIATE on GET /v1/segment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | SALES_ASSOCIATE on GET /v1/segment-assignments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | LOYALTY_OFFICER on GET /v1/segment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | LOYALTY_OFFICER on GET /v1/segment-assignments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-assignments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-assignments/versioned | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-assignments/versioned, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-assignments/versioned | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-assignments/versioned, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | SALES_ASSOCIATE on GET /v1/segment-assignments/versioned | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | SALES_ASSOCIATE on GET /v1/segment-assignments/versioned, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | LOYALTY_OFFICER on GET /v1/segment-assignments/versioned | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | LOYALTY_OFFICER on GET /v1/segment-assignments/versioned, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-assignments/versioned | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-assignments/versioned, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-migrations/matrix | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-migrations/matrix, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-migrations/matrix | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-migrations/matrix, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | SALES_ASSOCIATE on GET /v1/segment-migrations/matrix | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | SALES_ASSOCIATE on GET /v1/segment-migrations/matrix, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | LOYALTY_OFFICER on GET /v1/segment-migrations/matrix | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | LOYALTY_OFFICER on GET /v1/segment-migrations/matrix, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-migrations/matrix | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-migrations/matrix, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-migrations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-migrations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-migrations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-migrations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | SALES_ASSOCIATE on GET /v1/segment-migrations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | SALES_ASSOCIATE on GET /v1/segment-migrations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | LOYALTY_OFFICER on GET /v1/segment-migrations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | LOYALTY_OFFICER on GET /v1/segment-migrations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-migrations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-migrations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-migrations/explanation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | INVENTORY_PLANNER on GET /v1/segment-migrations/explanation, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-migrations/explanation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | CUSTOMER on GET /v1/segment-migrations/explanation, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | SALES_ASSOCIATE on GET /v1/segment-migrations/explanation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | SALES_ASSOCIATE on GET /v1/segment-migrations/explanation, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | LOYALTY_OFFICER on GET /v1/segment-migrations/explanation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | LOYALTY_OFFICER on GET /v1/segment-migrations/explanation, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-migrations/explanation | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: segments | COMMERCIAL_SUPERVISOR on GET /v1/segment-migrations/explanation, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-profiles | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-profiles, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on GET /v1/rfm-profiles | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on GET /v1/rfm-profiles, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on GET /v1/rfm-profiles | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on GET /v1/rfm-profiles, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on GET /v1/rfm-profiles | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on GET /v1/rfm-profiles, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on GET /v1/rfm-profiles | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on GET /v1/rfm-profiles, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-profiles/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-profiles/{customer_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on GET /v1/rfm-profiles/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on GET /v1/rfm-profiles/{customer_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on GET /v1/rfm-profiles/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on GET /v1/rfm-profiles/{customer_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on GET /v1/rfm-profiles/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on GET /v1/rfm-profiles/{customer_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on GET /v1/rfm-profiles/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on GET /v1/rfm-profiles/{customer_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | STORE_MANAGER on GET /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | STORE_MANAGER on GET /v1/rfm-rule-sets, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-rule-sets, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on GET /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on GET /v1/rfm-rule-sets, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on GET /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on GET /v1/rfm-rule-sets, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on GET /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on GET /v1/rfm-rule-sets, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on GET /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on GET /v1/rfm-rule-sets, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | ANALYST on POST /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | STORE_MANAGER on POST /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | MARKETING on POST /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on POST /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | AUDITOR on POST /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on POST /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on POST /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on POST /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on POST /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | STORE_MANAGER on GET /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | STORE_MANAGER on GET /v1/rfm-rule-sets/{rule_set_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-rule-sets/{rule_set_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on GET /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on GET /v1/rfm-rule-sets/{rule_set_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on GET /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on GET /v1/rfm-rule-sets/{rule_set_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on GET /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on GET /v1/rfm-rule-sets/{rule_set_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on GET /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on GET /v1/rfm-rule-sets/{rule_set_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | ANALYST on PUT /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | STORE_MANAGER on PUT /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | MARKETING on PUT /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on PUT /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | AUDITOR on PUT /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on PUT /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on PUT /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on PUT /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on PUT /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | ANALYST on POST /v1/rfm-rule-sets/{rule_set_id}/versions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | STORE_MANAGER on POST /v1/rfm-rule-sets/{rule_set_id}/versions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | MARKETING on POST /v1/rfm-rule-sets/{rule_set_id}/versions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on POST /v1/rfm-rule-sets/{rule_set_id}/versions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | AUDITOR on POST /v1/rfm-rule-sets/{rule_set_id}/versions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on POST /v1/rfm-rule-sets/{rule_set_id}/versions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on POST /v1/rfm-rule-sets/{rule_set_id}/versions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on POST /v1/rfm-rule-sets/{rule_set_id}/versions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on POST /v1/rfm-rule-sets/{rule_set_id}/versions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | ANALYST on POST /v1/rfm-rule-sets/{rule_set_id}/publication | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | STORE_MANAGER on POST /v1/rfm-rule-sets/{rule_set_id}/publication | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | MARKETING on POST /v1/rfm-rule-sets/{rule_set_id}/publication | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on POST /v1/rfm-rule-sets/{rule_set_id}/publication | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | AUDITOR on POST /v1/rfm-rule-sets/{rule_set_id}/publication | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on POST /v1/rfm-rule-sets/{rule_set_id}/publication | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on POST /v1/rfm-rule-sets/{rule_set_id}/publication | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on POST /v1/rfm-rule-sets/{rule_set_id}/publication | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on POST /v1/rfm-rule-sets/{rule_set_id}/publication | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | STORE_MANAGER on GET /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | STORE_MANAGER on GET /v1/rfm-recalculations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | MARKETING on GET /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | MARKETING on GET /v1/rfm-recalculations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-recalculations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on GET /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on GET /v1/rfm-recalculations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on GET /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on GET /v1/rfm-recalculations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on GET /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on GET /v1/rfm-recalculations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on GET /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on GET /v1/rfm-recalculations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | ANALYST on POST /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | STORE_MANAGER on POST /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | MARKETING on POST /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on POST /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | AUDITOR on POST /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on POST /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on POST /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on POST /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on POST /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | STORE_MANAGER on GET /v1/rfm-recalculations/{recalculation_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | STORE_MANAGER on GET /v1/rfm-recalculations/{recalculation_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | MARKETING on GET /v1/rfm-recalculations/{recalculation_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | MARKETING on GET /v1/rfm-recalculations/{recalculation_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-recalculations/{recalculation_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-recalculations/{recalculation_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on GET /v1/rfm-recalculations/{recalculation_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on GET /v1/rfm-recalculations/{recalculation_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on GET /v1/rfm-recalculations/{recalculation_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on GET /v1/rfm-recalculations/{recalculation_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on GET /v1/rfm-recalculations/{recalculation_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on GET /v1/rfm-recalculations/{recalculation_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on GET /v1/rfm-recalculations/{recalculation_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on GET /v1/rfm-recalculations/{recalculation_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | STORE_MANAGER on GET /v1/rfm-recalculations/{recalculation_id}/differences | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | STORE_MANAGER on GET /v1/rfm-recalculations/{recalculation_id}/differences, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | MARKETING on GET /v1/rfm-recalculations/{recalculation_id}/differences | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | MARKETING on GET /v1/rfm-recalculations/{recalculation_id}/differences, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-recalculations/{recalculation_id}/differences | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-recalculations/{recalculation_id}/differences, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on GET /v1/rfm-recalculations/{recalculation_id}/differences | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | CUSTOMER on GET /v1/rfm-recalculations/{recalculation_id}/differences, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on GET /v1/rfm-recalculations/{recalculation_id}/differences | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | SALES_ASSOCIATE on GET /v1/rfm-recalculations/{recalculation_id}/differences, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on GET /v1/rfm-recalculations/{recalculation_id}/differences | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | LOYALTY_OFFICER on GET /v1/rfm-recalculations/{recalculation_id}/differences, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on GET /v1/rfm-recalculations/{recalculation_id}/differences | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: rfm-engine | COMMERCIAL_SUPERVISOR on GET /v1/rfm-recalculations/{recalculation_id}/differences, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | INVENTORY_PLANNER on GET /v1/recommendations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | INVENTORY_PLANNER on GET /v1/recommendations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | CUSTOMER on GET /v1/recommendations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | CUSTOMER on GET /v1/recommendations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | SALES_ASSOCIATE on GET /v1/recommendations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | SALES_ASSOCIATE on GET /v1/recommendations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | LOYALTY_OFFICER on GET /v1/recommendations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | LOYALTY_OFFICER on GET /v1/recommendations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | COMMERCIAL_SUPERVISOR on GET /v1/recommendations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | COMMERCIAL_SUPERVISOR on GET /v1/recommendations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | INVENTORY_PLANNER on GET /v2/recommendations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | INVENTORY_PLANNER on GET /v2/recommendations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | CUSTOMER on GET /v2/recommendations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | CUSTOMER on GET /v2/recommendations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | COMMERCIAL_SUPERVISOR on GET /v2/recommendations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | COMMERCIAL_SUPERVISOR on GET /v2/recommendations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | MARKETING on GET /v2/recommendation-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | MARKETING on GET /v2/recommendation-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | INVENTORY_PLANNER on GET /v2/recommendation-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | INVENTORY_PLANNER on GET /v2/recommendation-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | CUSTOMER on GET /v2/recommendation-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | CUSTOMER on GET /v2/recommendation-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | SALES_ASSOCIATE on GET /v2/recommendation-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | SALES_ASSOCIATE on GET /v2/recommendation-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | LOYALTY_OFFICER on GET /v2/recommendation-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | LOYALTY_OFFICER on GET /v2/recommendation-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | COMMERCIAL_SUPERVISOR on GET /v2/recommendation-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | COMMERCIAL_SUPERVISOR on GET /v2/recommendation-decisions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | MARKETING on GET /v2/recommendation-decisions/{decision_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | MARKETING on GET /v2/recommendation-decisions/{decision_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | INVENTORY_PLANNER on GET /v2/recommendation-decisions/{decision_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | INVENTORY_PLANNER on GET /v2/recommendation-decisions/{decision_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | CUSTOMER on GET /v2/recommendation-decisions/{decision_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | CUSTOMER on GET /v2/recommendation-decisions/{decision_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | SALES_ASSOCIATE on GET /v2/recommendation-decisions/{decision_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | SALES_ASSOCIATE on GET /v2/recommendation-decisions/{decision_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | LOYALTY_OFFICER on GET /v2/recommendation-decisions/{decision_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | LOYALTY_OFFICER on GET /v2/recommendation-decisions/{decision_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | COMMERCIAL_SUPERVISOR on GET /v2/recommendation-decisions/{decision_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: recommendations | COMMERCIAL_SUPERVISOR on GET /v2/recommendation-decisions/{decision_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on GET /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on GET /v1/engagement-events, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v1/engagement-events, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v1/engagement-events, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | ANALYST on POST /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | MARKETING on POST /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on POST /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | AUDITOR on POST /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on POST /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on POST /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | COMMERCIAL_SUPERVISOR on POST /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on GET /v1/engagement-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on GET /v1/engagement-events/{event_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v1/engagement-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v1/engagement-events/{event_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v1/engagement-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v1/engagement-events/{event_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | ANALYST on POST /v1/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | STORE_MANAGER on POST /v1/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | MARKETING on POST /v1/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on POST /v1/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | AUDITOR on POST /v1/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on POST /v1/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | SALES_ASSOCIATE on POST /v1/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on POST /v1/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | COMMERCIAL_SUPERVISOR on POST /v1/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v1/engagement-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v1/engagement-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | SALES_ASSOCIATE on GET /v1/engagement-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | SALES_ASSOCIATE on GET /v1/engagement-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v1/engagement-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v1/engagement-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | COMMERCIAL_SUPERVISOR on GET /v1/engagement-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | COMMERCIAL_SUPERVISOR on GET /v1/engagement-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | ANALYST on GET /v1/capture-incidents | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | ANALYST on GET /v1/capture-incidents, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | STORE_MANAGER on GET /v1/capture-incidents | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | STORE_MANAGER on GET /v1/capture-incidents, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | MARKETING on GET /v1/capture-incidents | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | MARKETING on GET /v1/capture-incidents, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on GET /v1/capture-incidents | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on GET /v1/capture-incidents, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | AUDITOR on GET /v1/capture-incidents | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | AUDITOR on GET /v1/capture-incidents, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v1/capture-incidents | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v1/capture-incidents, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | SALES_ASSOCIATE on GET /v1/capture-incidents | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | SALES_ASSOCIATE on GET /v1/capture-incidents, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v1/capture-incidents | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v1/capture-incidents, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | ANALYST on POST /v1/capture-incidents | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | MARKETING on POST /v1/capture-incidents | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on POST /v1/capture-incidents | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | AUDITOR on POST /v1/capture-incidents | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on POST /v1/capture-incidents | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | ANALYST on GET /v1/capture-incidents/{incident_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | ANALYST on GET /v1/capture-incidents/{incident_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | STORE_MANAGER on GET /v1/capture-incidents/{incident_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | STORE_MANAGER on GET /v1/capture-incidents/{incident_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | MARKETING on GET /v1/capture-incidents/{incident_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | MARKETING on GET /v1/capture-incidents/{incident_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on GET /v1/capture-incidents/{incident_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on GET /v1/capture-incidents/{incident_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | AUDITOR on GET /v1/capture-incidents/{incident_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | AUDITOR on GET /v1/capture-incidents/{incident_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v1/capture-incidents/{incident_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v1/capture-incidents/{incident_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | SALES_ASSOCIATE on GET /v1/capture-incidents/{incident_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | SALES_ASSOCIATE on GET /v1/capture-incidents/{incident_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v1/capture-incidents/{incident_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v1/capture-incidents/{incident_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | ANALYST on POST /v1/capture-incidents/{incident_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | STORE_MANAGER on POST /v1/capture-incidents/{incident_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | MARKETING on POST /v1/capture-incidents/{incident_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on POST /v1/capture-incidents/{incident_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | AUDITOR on POST /v1/capture-incidents/{incident_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on POST /v1/capture-incidents/{incident_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | SALES_ASSOCIATE on POST /v1/capture-incidents/{incident_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on POST /v1/capture-incidents/{incident_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | ANALYST on GET /v1/capture-incident-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | ANALYST on GET /v1/capture-incident-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | STORE_MANAGER on GET /v1/capture-incident-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | STORE_MANAGER on GET /v1/capture-incident-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | MARKETING on GET /v1/capture-incident-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | MARKETING on GET /v1/capture-incident-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on GET /v1/capture-incident-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on GET /v1/capture-incident-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | AUDITOR on GET /v1/capture-incident-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | AUDITOR on GET /v1/capture-incident-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v1/capture-incident-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v1/capture-incident-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | SALES_ASSOCIATE on GET /v1/capture-incident-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | SALES_ASSOCIATE on GET /v1/capture-incident-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v1/capture-incident-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v1/capture-incident-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on GET /v2/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on GET /v2/engagement-events, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v2/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v2/engagement-events, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v2/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v2/engagement-events, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | ANALYST on POST /v2/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | MARKETING on POST /v2/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on POST /v2/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | AUDITOR on POST /v2/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on POST /v2/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on POST /v2/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | COMMERCIAL_SUPERVISOR on POST /v2/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on GET /v2/engagement-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on GET /v2/engagement-events/{event_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v2/engagement-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v2/engagement-events/{event_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v2/engagement-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v2/engagement-events/{event_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | ANALYST on POST /v2/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | STORE_MANAGER on POST /v2/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | MARKETING on POST /v2/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | INVENTORY_PLANNER on POST /v2/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | AUDITOR on POST /v2/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on POST /v2/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | SALES_ASSOCIATE on POST /v2/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on POST /v2/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | COMMERCIAL_SUPERVISOR on POST /v2/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v2/engagement-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | CUSTOMER on GET /v2/engagement-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | SALES_ASSOCIATE on GET /v2/engagement-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | SALES_ASSOCIATE on GET /v2/engagement-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v2/engagement-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | LOYALTY_OFFICER on GET /v2/engagement-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | COMMERCIAL_SUPERVISOR on GET /v2/engagement-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: engagement | COMMERCIAL_SUPERVISOR on GET /v2/engagement-summaries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | ANALYST on GET /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | ANALYST on GET /v1/check-ins, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | MARKETING on GET /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | MARKETING on GET /v1/check-ins, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | INVENTORY_PLANNER on GET /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | INVENTORY_PLANNER on GET /v1/check-ins, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | CUSTOMER on GET /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | CUSTOMER on GET /v1/check-ins, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | SALES_ASSOCIATE on GET /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | SALES_ASSOCIATE on GET /v1/check-ins, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | LOYALTY_OFFICER on GET /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | LOYALTY_OFFICER on GET /v1/check-ins, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | COMMERCIAL_SUPERVISOR on GET /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | COMMERCIAL_SUPERVISOR on GET /v1/check-ins, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | ANALYST on POST /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | MARKETING on POST /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | INVENTORY_PLANNER on POST /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | AUDITOR on POST /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | CUSTOMER on POST /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | ANALYST on GET /v1/check-ins/{check_in_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | ANALYST on GET /v1/check-ins/{check_in_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | MARKETING on GET /v1/check-ins/{check_in_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | MARKETING on GET /v1/check-ins/{check_in_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | INVENTORY_PLANNER on GET /v1/check-ins/{check_in_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | INVENTORY_PLANNER on GET /v1/check-ins/{check_in_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | CUSTOMER on GET /v1/check-ins/{check_in_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | CUSTOMER on GET /v1/check-ins/{check_in_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | SALES_ASSOCIATE on GET /v1/check-ins/{check_in_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | SALES_ASSOCIATE on GET /v1/check-ins/{check_in_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | LOYALTY_OFFICER on GET /v1/check-ins/{check_in_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | LOYALTY_OFFICER on GET /v1/check-ins/{check_in_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | COMMERCIAL_SUPERVISOR on GET /v1/check-ins/{check_in_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | COMMERCIAL_SUPERVISOR on GET /v1/check-ins/{check_in_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | ANALYST on POST /v1/check-ins/{check_in_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | STORE_MANAGER on POST /v1/check-ins/{check_in_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | MARKETING on POST /v1/check-ins/{check_in_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | INVENTORY_PLANNER on POST /v1/check-ins/{check_in_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | AUDITOR on POST /v1/check-ins/{check_in_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | CUSTOMER on POST /v1/check-ins/{check_in_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | SALES_ASSOCIATE on POST /v1/check-ins/{check_in_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | LOYALTY_OFFICER on POST /v1/check-ins/{check_in_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | COMMERCIAL_SUPERVISOR on POST /v1/check-ins/{check_in_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | ANALYST on GET /v1/customer-visits | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | ANALYST on GET /v1/customer-visits, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | MARKETING on GET /v1/customer-visits | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | MARKETING on GET /v1/customer-visits, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | INVENTORY_PLANNER on GET /v1/customer-visits | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | INVENTORY_PLANNER on GET /v1/customer-visits, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | CUSTOMER on GET /v1/customer-visits | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | CUSTOMER on GET /v1/customer-visits, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | LOYALTY_OFFICER on GET /v1/customer-visits | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | LOYALTY_OFFICER on GET /v1/customer-visits, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | COMMERCIAL_SUPERVISOR on GET /v1/customer-visits | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | COMMERCIAL_SUPERVISOR on GET /v1/customer-visits, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | ANALYST on POST /v1/customer-visits | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | MARKETING on POST /v1/customer-visits | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | INVENTORY_PLANNER on POST /v1/customer-visits | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | AUDITOR on POST /v1/customer-visits | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | CUSTOMER on POST /v1/customer-visits | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | LOYALTY_OFFICER on POST /v1/customer-visits | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | COMMERCIAL_SUPERVISOR on POST /v1/customer-visits | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | ANALYST on GET /v1/customer-visits/{visit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | ANALYST on GET /v1/customer-visits/{visit_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | MARKETING on GET /v1/customer-visits/{visit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | MARKETING on GET /v1/customer-visits/{visit_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | INVENTORY_PLANNER on GET /v1/customer-visits/{visit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | INVENTORY_PLANNER on GET /v1/customer-visits/{visit_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | CUSTOMER on GET /v1/customer-visits/{visit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | CUSTOMER on GET /v1/customer-visits/{visit_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | LOYALTY_OFFICER on GET /v1/customer-visits/{visit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | LOYALTY_OFFICER on GET /v1/customer-visits/{visit_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | COMMERCIAL_SUPERVISOR on GET /v1/customer-visits/{visit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: check-ins | COMMERCIAL_SUPERVISOR on GET /v1/customer-visits/{visit_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on GET /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on GET /v1/experiments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/experiments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/experiments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on GET /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on GET /v1/experiments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/experiments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/experiments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | ANALYST on POST /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on POST /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on POST /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | AUDITOR on POST /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on POST /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on POST /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on POST /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on POST /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on GET /v1/experiments/{experiment_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on GET /v1/experiments/{experiment_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/experiments/{experiment_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/experiments/{experiment_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/experiments/{experiment_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/experiments/{experiment_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on GET /v1/experiments/{experiment_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on GET /v1/experiments/{experiment_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/experiments/{experiment_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/experiments/{experiment_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/experiments/{experiment_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/experiments/{experiment_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on GET /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on GET /v1/experiments/{experiment_id}/conversions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/experiments/{experiment_id}/conversions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/experiments/{experiment_id}/conversions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on GET /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on GET /v1/experiments/{experiment_id}/conversions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/experiments/{experiment_id}/conversions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/experiments/{experiment_id}/conversions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | ANALYST on POST /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on POST /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on POST /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | AUDITOR on POST /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on POST /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on POST /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on POST /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on POST /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | ANALYST on POST /v1/experiments/{experiment_id}/attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on POST /v1/experiments/{experiment_id}/attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on POST /v1/experiments/{experiment_id}/attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | AUDITOR on POST /v1/experiments/{experiment_id}/attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on POST /v1/experiments/{experiment_id}/attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on POST /v1/experiments/{experiment_id}/attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on POST /v1/experiments/{experiment_id}/attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on POST /v1/experiments/{experiment_id}/attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on GET /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on GET /v1/experiment-assignments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/experiment-assignments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/experiment-assignments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on GET /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on GET /v1/experiment-assignments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/experiment-assignments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/experiment-assignments, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | ANALYST on POST /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on POST /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on POST /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | AUDITOR on POST /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on POST /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on POST /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on POST /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on POST /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | ANALYST on GET /v1/experiment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | ANALYST on GET /v1/experiment-assignments/current, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | MARKETING on GET /v1/experiment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | MARKETING on GET /v1/experiment-assignments/current, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/experiment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/experiment-assignments/current, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | AUDITOR on GET /v1/experiment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | AUDITOR on GET /v1/experiment-assignments/current, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/experiment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/experiment-assignments/current, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/experiment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/experiment-assignments/current, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/experiment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/experiment-assignments/current, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on GET /v1/experiment-results/{experiment_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on GET /v1/experiment-results/{experiment_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/experiment-results/{experiment_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/experiment-results/{experiment_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/experiment-results/{experiment_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/experiment-results/{experiment_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on GET /v1/experiment-results/{experiment_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on GET /v1/experiment-results/{experiment_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/experiment-results/{experiment_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/experiment-results/{experiment_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/experiment-results/{experiment_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/experiment-results/{experiment_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on GET /v1/sale-attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on GET /v1/sale-attributions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/sale-attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/sale-attributions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/sale-attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/sale-attributions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on GET /v1/sale-attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on GET /v1/sale-attributions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/sale-attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/sale-attributions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/sale-attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/sale-attributions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on GET /v1/sale-attributions/{attribution_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | STORE_MANAGER on GET /v1/sale-attributions/{attribution_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/sale-attributions/{attribution_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | INVENTORY_PLANNER on GET /v1/sale-attributions/{attribution_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/sale-attributions/{attribution_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | CUSTOMER on GET /v1/sale-attributions/{attribution_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on GET /v1/sale-attributions/{attribution_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | SALES_ASSOCIATE on GET /v1/sale-attributions/{attribution_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/sale-attributions/{attribution_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | LOYALTY_OFFICER on GET /v1/sale-attributions/{attribution_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/sale-attributions/{attribution_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: experimentation | COMMERCIAL_SUPERVISOR on GET /v1/sale-attributions/{attribution_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | ANALYST on GET /v1/audit-entries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | ANALYST on GET /v1/audit-entries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | STORE_MANAGER on GET /v1/audit-entries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | STORE_MANAGER on GET /v1/audit-entries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | MARKETING on GET /v1/audit-entries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | MARKETING on GET /v1/audit-entries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | INVENTORY_PLANNER on GET /v1/audit-entries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | INVENTORY_PLANNER on GET /v1/audit-entries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | CUSTOMER on GET /v1/audit-entries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | CUSTOMER on GET /v1/audit-entries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | SALES_ASSOCIATE on GET /v1/audit-entries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | SALES_ASSOCIATE on GET /v1/audit-entries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | LOYALTY_OFFICER on GET /v1/audit-entries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | LOYALTY_OFFICER on GET /v1/audit-entries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | COMMERCIAL_SUPERVISOR on GET /v1/audit-entries | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | COMMERCIAL_SUPERVISOR on GET /v1/audit-entries, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | ANALYST on GET /v1/audit-entries/{audit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | ANALYST on GET /v1/audit-entries/{audit_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | STORE_MANAGER on GET /v1/audit-entries/{audit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | STORE_MANAGER on GET /v1/audit-entries/{audit_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | MARKETING on GET /v1/audit-entries/{audit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | MARKETING on GET /v1/audit-entries/{audit_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | INVENTORY_PLANNER on GET /v1/audit-entries/{audit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | INVENTORY_PLANNER on GET /v1/audit-entries/{audit_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | CUSTOMER on GET /v1/audit-entries/{audit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | CUSTOMER on GET /v1/audit-entries/{audit_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | SALES_ASSOCIATE on GET /v1/audit-entries/{audit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | SALES_ASSOCIATE on GET /v1/audit-entries/{audit_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | LOYALTY_OFFICER on GET /v1/audit-entries/{audit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | LOYALTY_OFFICER on GET /v1/audit-entries/{audit_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | COMMERCIAL_SUPERVISOR on GET /v1/audit-entries/{audit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | COMMERCIAL_SUPERVISOR on GET /v1/audit-entries/{audit_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | ANALYST on GET /v1/domain-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | ANALYST on GET /v1/domain-events, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | STORE_MANAGER on GET /v1/domain-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | STORE_MANAGER on GET /v1/domain-events, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | MARKETING on GET /v1/domain-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | MARKETING on GET /v1/domain-events, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | INVENTORY_PLANNER on GET /v1/domain-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | INVENTORY_PLANNER on GET /v1/domain-events, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | CUSTOMER on GET /v1/domain-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | CUSTOMER on GET /v1/domain-events, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | SALES_ASSOCIATE on GET /v1/domain-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | SALES_ASSOCIATE on GET /v1/domain-events, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | LOYALTY_OFFICER on GET /v1/domain-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | LOYALTY_OFFICER on GET /v1/domain-events, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | COMMERCIAL_SUPERVISOR on GET /v1/domain-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | COMMERCIAL_SUPERVISOR on GET /v1/domain-events, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | ANALYST on GET /v1/domain-events/verification | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | ANALYST on GET /v1/domain-events/verification, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | STORE_MANAGER on GET /v1/domain-events/verification | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | STORE_MANAGER on GET /v1/domain-events/verification, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | MARKETING on GET /v1/domain-events/verification | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | MARKETING on GET /v1/domain-events/verification, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | INVENTORY_PLANNER on GET /v1/domain-events/verification | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | INVENTORY_PLANNER on GET /v1/domain-events/verification, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | CUSTOMER on GET /v1/domain-events/verification | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | CUSTOMER on GET /v1/domain-events/verification, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | SALES_ASSOCIATE on GET /v1/domain-events/verification | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | SALES_ASSOCIATE on GET /v1/domain-events/verification, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | LOYALTY_OFFICER on GET /v1/domain-events/verification | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | LOYALTY_OFFICER on GET /v1/domain-events/verification, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | COMMERCIAL_SUPERVISOR on GET /v1/domain-events/verification | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | COMMERCIAL_SUPERVISOR on GET /v1/domain-events/verification, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | ANALYST on GET /v1/domain-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | ANALYST on GET /v1/domain-events/{event_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | STORE_MANAGER on GET /v1/domain-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | STORE_MANAGER on GET /v1/domain-events/{event_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | MARKETING on GET /v1/domain-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | MARKETING on GET /v1/domain-events/{event_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | INVENTORY_PLANNER on GET /v1/domain-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | INVENTORY_PLANNER on GET /v1/domain-events/{event_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | CUSTOMER on GET /v1/domain-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | CUSTOMER on GET /v1/domain-events/{event_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | SALES_ASSOCIATE on GET /v1/domain-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | SALES_ASSOCIATE on GET /v1/domain-events/{event_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | LOYALTY_OFFICER on GET /v1/domain-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | LOYALTY_OFFICER on GET /v1/domain-events/{event_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | COMMERCIAL_SUPERVISOR on GET /v1/domain-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: audit-trail | COMMERCIAL_SUPERVISOR on GET /v1/domain-events/{event_id}, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/net-sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/net-sales, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | CUSTOMER on GET /v1/kpi-reports/net-sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | CUSTOMER on GET /v1/kpi-reports/net-sales, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | SALES_ASSOCIATE on GET /v1/kpi-reports/net-sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | SALES_ASSOCIATE on GET /v1/kpi-reports/net-sales, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | LOYALTY_OFFICER on GET /v1/kpi-reports/net-sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | LOYALTY_OFFICER on GET /v1/kpi-reports/net-sales, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/spend | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/spend, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | CUSTOMER on GET /v1/kpi-reports/spend | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | CUSTOMER on GET /v1/kpi-reports/spend, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | SALES_ASSOCIATE on GET /v1/kpi-reports/spend | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | SALES_ASSOCIATE on GET /v1/kpi-reports/spend, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | LOYALTY_OFFICER on GET /v1/kpi-reports/spend | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | LOYALTY_OFFICER on GET /v1/kpi-reports/spend, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/retention | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/retention, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | CUSTOMER on GET /v1/kpi-reports/retention | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | CUSTOMER on GET /v1/kpi-reports/retention, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | SALES_ASSOCIATE on GET /v1/kpi-reports/retention | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | SALES_ASSOCIATE on GET /v1/kpi-reports/retention, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | LOYALTY_OFFICER on GET /v1/kpi-reports/retention | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | LOYALTY_OFFICER on GET /v1/kpi-reports/retention, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | STORE_MANAGER on GET /v1/kpi-reports/migrations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | STORE_MANAGER on GET /v1/kpi-reports/migrations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/migrations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/migrations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | CUSTOMER on GET /v1/kpi-reports/migrations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | CUSTOMER on GET /v1/kpi-reports/migrations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | SALES_ASSOCIATE on GET /v1/kpi-reports/migrations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | SALES_ASSOCIATE on GET /v1/kpi-reports/migrations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | LOYALTY_OFFICER on GET /v1/kpi-reports/migrations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | LOYALTY_OFFICER on GET /v1/kpi-reports/migrations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | COMMERCIAL_SUPERVISOR on GET /v1/kpi-reports/migrations | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | COMMERCIAL_SUPERVISOR on GET /v1/kpi-reports/migrations, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | STORE_MANAGER on GET /v1/kpi-reports/campaign-performance | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | STORE_MANAGER on GET /v1/kpi-reports/campaign-performance, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/campaign-performance | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/campaign-performance, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | CUSTOMER on GET /v1/kpi-reports/campaign-performance | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | CUSTOMER on GET /v1/kpi-reports/campaign-performance, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | SALES_ASSOCIATE on GET /v1/kpi-reports/campaign-performance | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | SALES_ASSOCIATE on GET /v1/kpi-reports/campaign-performance, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | LOYALTY_OFFICER on GET /v1/kpi-reports/campaign-performance | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | LOYALTY_OFFICER on GET /v1/kpi-reports/campaign-performance, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | COMMERCIAL_SUPERVISOR on GET /v1/kpi-reports/campaign-performance | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | COMMERCIAL_SUPERVISOR on GET /v1/kpi-reports/campaign-performance, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | MARKETING on GET /v1/kpi-reports/demand-coverage | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | MARKETING on GET /v1/kpi-reports/demand-coverage, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | CUSTOMER on GET /v1/kpi-reports/demand-coverage | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | CUSTOMER on GET /v1/kpi-reports/demand-coverage, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | SALES_ASSOCIATE on GET /v1/kpi-reports/demand-coverage | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | SALES_ASSOCIATE on GET /v1/kpi-reports/demand-coverage, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | LOYALTY_OFFICER on GET /v1/kpi-reports/demand-coverage | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | LOYALTY_OFFICER on GET /v1/kpi-reports/demand-coverage, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | COMMERCIAL_SUPERVISOR on GET /v1/kpi-reports/demand-coverage | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | COMMERCIAL_SUPERVISOR on GET /v1/kpi-reports/demand-coverage, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/definitions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/definitions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | CUSTOMER on GET /v1/kpi-reports/definitions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | CUSTOMER on GET /v1/kpi-reports/definitions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | SALES_ASSOCIATE on GET /v1/kpi-reports/definitions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | SALES_ASSOCIATE on GET /v1/kpi-reports/definitions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | LOYALTY_OFFICER on GET /v1/kpi-reports/definitions | 403 forbidden_role | 403 forbidden_role | ✓ |
| privacy: analytics-kpis | LOYALTY_OFFICER on GET /v1/kpi-reports/definitions, in XML | 403 forbidden_role | 403 forbidden_role | ✓ |
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
| rfm-engine | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| model-registry | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| experimentation | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
| analytics-kpis | Accept: text/csv | 406 not_acceptable | 406 not_acceptable | ✓ |
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
| stores | Content-Type: text/plain on PUT /v1/stores/{store_id}/channel-enablement | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| stores | an XML body with a DOCTYPE on PUT /v1/stores/{store_id}/channel-enablement | 400 malformed_body | 400 malformed_body | ✓ |
| stores | an XML body with an entity declaration on PUT /v1/stores/{store_id}/channel-enablement | 400 malformed_body | 400 malformed_body | ✓ |
| stores | 65 KiB on PUT /v1/stores/{store_id}/channel-enablement, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| stores | 65 KiB on PUT /v1/stores/{store_id}/channel-enablement, on 127.0.0.1:5005 | 413 payload_too_large | 413 payload_too_large | ✓ |
| stock | Content-Type: text/plain on POST /v1/stock-movements | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| stock | an XML body with a DOCTYPE on POST /v1/stock-movements | 400 malformed_body | 400 malformed_body | ✓ |
| stock | an XML body with an entity declaration on POST /v1/stock-movements | 400 malformed_body | 400 malformed_body | ✓ |
| stock | 65 KiB on POST /v1/stock-movements, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| stock | 65 KiB on POST /v1/stock-movements, on 127.0.0.1:5006 | 413 payload_too_large | 413 payload_too_large | ✓ |
| customers | Content-Type: text/plain on POST /v1/customers | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| customers | an XML body with a DOCTYPE on POST /v1/customers | 400 malformed_body | 400 malformed_body | ✓ |
| customers | an XML body with an entity declaration on POST /v1/customers | 400 malformed_body | 400 malformed_body | ✓ |
| customers | 65 KiB on POST /v1/customers, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| customers | 65 KiB on POST /v1/customers, on 127.0.0.1:5007 | 413 payload_too_large | 413 payload_too_large | ✓ |
| customer-preferences | Content-Type: text/plain on POST /v1/preference-profiles/{customer_id}/consent-decisions | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| customer-preferences | an XML body with a DOCTYPE on POST /v1/preference-profiles/{customer_id}/consent-decisions | 400 malformed_body | 400 malformed_body | ✓ |
| customer-preferences | an XML body with an entity declaration on POST /v1/preference-profiles/{customer_id}/consent-decisions | 400 malformed_body | 400 malformed_body | ✓ |
| customer-preferences | 65 KiB on POST /v1/preference-profiles/{customer_id}/consent-decisions, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| customer-preferences | 65 KiB on POST /v1/preference-profiles/{customer_id}/consent-decisions, on 127.0.0.1:5008 | 413 payload_too_large | 413 payload_too_large | ✓ |
| sales | Content-Type: text/plain on POST /v1/sale-imports | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| sales | an XML body with a DOCTYPE on POST /v1/sale-imports | 400 malformed_body | 400 malformed_body | ✓ |
| sales | an XML body with an entity declaration on POST /v1/sale-imports | 400 malformed_body | 400 malformed_body | ✓ |
| sales | 65 KiB on POST /v1/sale-imports, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| sales | 65 KiB on POST /v1/sale-imports, on 127.0.0.1:5009 | 413 payload_too_large | 413 payload_too_large | ✓ |
| engagement | Content-Type: text/plain on POST /v1/capture-incidents | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| engagement | an XML body with a DOCTYPE on POST /v1/capture-incidents | 400 malformed_body | 400 malformed_body | ✓ |
| engagement | an XML body with an entity declaration on POST /v1/capture-incidents | 400 malformed_body | 400 malformed_body | ✓ |
| engagement | 65 KiB on POST /v1/capture-incidents, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| engagement | 65 KiB on POST /v1/capture-incidents, on 127.0.0.1:5012 | 413 payload_too_large | 413 payload_too_large | ✓ |
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
| rfm-engine | Content-Type: text/plain on POST /v1/rfm-recalculations | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| rfm-engine | an XML body with a DOCTYPE on POST /v1/rfm-recalculations | 400 malformed_body | 400 malformed_body | ✓ |
| rfm-engine | an XML body with an entity declaration on POST /v1/rfm-recalculations | 400 malformed_body | 400 malformed_body | ✓ |
| rfm-engine | 65 KiB on POST /v1/rfm-recalculations, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| rfm-engine | 65 KiB on POST /v1/rfm-recalculations, on 127.0.0.1:5016 | 413 payload_too_large | 413 payload_too_large | ✓ |
| model-registry | Content-Type: text/plain on POST /v1/model-trainings | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| model-registry | an XML body with a DOCTYPE on POST /v1/model-trainings | 400 malformed_body | 400 malformed_body | ✓ |
| model-registry | an XML body with an entity declaration on POST /v1/model-trainings | 400 malformed_body | 400 malformed_body | ✓ |
| model-registry | 65 KiB on POST /v1/model-trainings, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| model-registry | 65 KiB on POST /v1/model-trainings, on 127.0.0.1:5019 | 413 payload_too_large | 413 payload_too_large | ✓ |
| experimentation | Content-Type: text/plain on POST /v1/experiment-assignments | 415 unsupported_media_type | 415 unsupported_media_type | ✓ |
| experimentation | an XML body with a DOCTYPE on POST /v1/experiment-assignments | 400 malformed_body | 400 malformed_body | ✓ |
| experimentation | an XML body with an entity declaration on POST /v1/experiment-assignments | 400 malformed_body | 400 malformed_body | ✓ |
| experimentation | 65 KiB on POST /v1/experiment-assignments, through the edge | 413 payload_too_large | 413 payload_too_large | ✓ |
| experimentation | 65 KiB on POST /v1/experiment-assignments, on 127.0.0.1:5020 | 413 payload_too_large | 413 payload_too_large | ✓ |
| users | page=0 on GET /v1/users | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| users | size=101 on GET /v1/users | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| products | page=0 on GET /v1/products | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| products | size=101 on GET /v1/products | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| products | page=0 on GET /v1/products/{product_id}/relations | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| products | size=101 on GET /v1/products/{product_id}/relations | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| categories | page=0 on GET /v1/categories | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| categories | size=101 on GET /v1/categories | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| categories | page=0 on GET /v1/categories/{category_id}/descendants | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| categories | size=101 on GET /v1/categories/{category_id}/descendants | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stores | page=0 on GET /v1/stores | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stores | size=101 on GET /v1/stores | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stock | page=0 on GET /v1/stock-levels | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stock | size=101 on GET /v1/stock-levels | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stock | page=0 on GET /v1/stock-movements | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stock | size=101 on GET /v1/stock-movements | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stock | page=0 on GET /v1/availabilities | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| stock | size=101 on GET /v1/availabilities | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customers | page=0 on GET /v1/customers | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customers | size=101 on GET /v1/customers | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customers | page=0 on GET /v1/customers/retention-candidates | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customers | size=101 on GET /v1/customers/retention-candidates | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customers | page=0 on GET /v1/customers/floor-search | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customers | size=101 on GET /v1/customers/floor-search | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customers | page=0 on GET /v1/loyalty-cards | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customers | size=101 on GET /v1/loyalty-cards | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customer-preferences | page=0 on GET /v1/preference-profiles/{customer_id}/consent-decisions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customer-preferences | size=101 on GET /v1/preference-profiles/{customer_id}/consent-decisions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customer-preferences | page=0 on GET /v1/preference-profiles/{customer_id}/restriction-decisions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customer-preferences | size=101 on GET /v1/preference-profiles/{customer_id}/restriction-decisions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customer-preferences | page=0 on GET /v1/preference-profiles/{customer_id}/preference-changes | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| customer-preferences | size=101 on GET /v1/preference-profiles/{customer_id}/preference-changes | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| sales | page=0 on GET /v1/sales | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| sales | size=101 on GET /v1/sales | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| sales | page=0 on GET /v1/sale-reversals | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| sales | size=101 on GET /v1/sale-reversals | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| sales | page=0 on GET /v1/sale-imports | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| sales | size=101 on GET /v1/sale-imports | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| sales | page=0 on GET /v1/sale-imports/{import_id}/rejections | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| sales | size=101 on GET /v1/sale-imports/{import_id}/rejections | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| sales | page=0 on GET /v1/sale-imports/{import_id}/reconciliation-cases | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| sales | size=101 on GET /v1/sale-imports/{import_id}/reconciliation-cases | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| segments | page=0 on GET /v1/segment-assignments | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| segments | size=101 on GET /v1/segment-assignments | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| segments | page=0 on GET /v1/segment-assignments/versioned | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| segments | size=101 on GET /v1/segment-assignments/versioned | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| segments | page=0 on GET /v1/segment-migrations | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| segments | size=101 on GET /v1/segment-migrations | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| recommendations | page=0 on GET /v2/recommendation-decisions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| recommendations | size=101 on GET /v2/recommendation-decisions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| engagement | page=0 on GET /v1/engagement-events | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| engagement | size=101 on GET /v1/engagement-events | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| engagement | page=0 on GET /v1/capture-incidents | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| engagement | size=101 on GET /v1/capture-incidents | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| engagement | page=0 on GET /v2/engagement-events | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| engagement | size=101 on GET /v2/engagement-events | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| check-ins | page=0 on GET /v1/check-ins | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| check-ins | size=101 on GET /v1/check-ins | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| check-ins | page=0 on GET /v1/customer-visits | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| check-ins | size=101 on GET /v1/customer-visits | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| campaigns | page=0 on GET /v1/campaigns | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| campaigns | size=101 on GET /v1/campaigns | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| campaigns | page=0 on GET /v1/campaigns/{campaign_id}/budget/spending | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| campaigns | size=101 on GET /v1/campaigns/{campaign_id}/budget/spending | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| campaigns | page=0 on GET /v1/promotions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| campaigns | size=101 on GET /v1/promotions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| audit-trail | page=0 on GET /v1/audit-entries | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| audit-trail | size=101 on GET /v1/audit-entries | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| audit-trail | page=0 on GET /v1/domain-events | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| audit-trail | size=101 on GET /v1/domain-events | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| rfm-engine | page=0 on GET /v1/rfm-profiles | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| rfm-engine | size=101 on GET /v1/rfm-profiles | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| rfm-engine | page=0 on GET /v1/rfm-rule-sets | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| rfm-engine | size=101 on GET /v1/rfm-rule-sets | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| rfm-engine | page=0 on GET /v1/rfm-recalculations | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| rfm-engine | size=101 on GET /v1/rfm-recalculations | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| rfm-engine | page=0 on GET /v1/rfm-recalculations/{recalculation_id}/differences | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| rfm-engine | size=101 on GET /v1/rfm-recalculations/{recalculation_id}/differences | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| model-registry | page=0 on GET /v1/model-versions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| model-registry | size=101 on GET /v1/model-versions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| model-registry | page=0 on GET /v1/model-versions/{version_id}/publications | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| model-registry | size=101 on GET /v1/model-versions/{version_id}/publications | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| model-registry | page=0 on GET /v1/model-trainings | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| model-registry | size=101 on GET /v1/model-trainings | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| model-registry | page=0 on GET /v1/drift-reports | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| model-registry | size=101 on GET /v1/drift-reports | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| model-registry | page=0 on GET /v1/drift-alerts | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| model-registry | size=101 on GET /v1/drift-alerts | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| experimentation | page=0 on GET /v1/experiments | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| experimentation | size=101 on GET /v1/experiments | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| experimentation | page=0 on GET /v1/experiments/{experiment_id}/conversions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| experimentation | size=101 on GET /v1/experiments/{experiment_id}/conversions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| experimentation | page=0 on GET /v1/experiment-assignments | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| experimentation | size=101 on GET /v1/experiment-assignments | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| experimentation | page=0 on GET /v1/sale-attributions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| experimentation | size=101 on GET /v1/sale-attributions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| analytics-kpis | page=0 on GET /v1/kpi-reports/net-sales | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| analytics-kpis | size=101 on GET /v1/kpi-reports/net-sales | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| analytics-kpis | page=0 on GET /v1/kpi-reports/spend | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| analytics-kpis | size=101 on GET /v1/kpi-reports/spend | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| analytics-kpis | page=0 on GET /v1/kpi-reports/retention | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| analytics-kpis | size=101 on GET /v1/kpi-reports/retention | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| analytics-kpis | page=0 on GET /v1/kpi-reports/migrations | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| analytics-kpis | size=101 on GET /v1/kpi-reports/migrations | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| analytics-kpis | page=0 on GET /v1/kpi-reports/campaign-performance | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| analytics-kpis | size=101 on GET /v1/kpi-reports/campaign-performance | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| analytics-kpis | page=0 on GET /v1/kpi-reports/demand-coverage | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| analytics-kpis | size=101 on GET /v1/kpi-reports/demand-coverage | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| analytics-kpis | page=0 on GET /v1/kpi-reports/definitions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
| analytics-kpis | size=101 on GET /v1/kpi-reports/definitions | 422 invalid_pagination | 422 invalid_pagination | ✓ |
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
| users | ANALYST on GET /v1/users | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on POST /v1/users | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on GET /v1/users/{user_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on PATCH /v1/users/{user_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on POST /v1/users/{user_id}/deactivation | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on DELETE /v1/users/{user_id}/deactivation | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on GET /v1/roles | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on GET /v1/roles/{role_code} | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on POST /v1/administrator-transfers | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on GET /v1/store-assignments/{user_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | ANALYST on PUT /v1/store-assignments/{user_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | CUSTOMER on GET /v1/products | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | ANALYST on POST /v1/products | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | CUSTOMER on GET /v1/products/{product_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | ANALYST on PATCH /v1/products/{product_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | CUSTOMER on GET /v1/products/{product_id}/details | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | ANALYST on PATCH /v1/products/{product_id}/details | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | CUSTOMER on GET /v1/products/{product_id}/relations | 403 forbidden_role | 403 forbidden_role | ✓ |
| products | ANALYST on PUT /v1/products/{product_id}/relations/{relation_kind} | 403 forbidden_role | 403 forbidden_role | ✓ |
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
| stores | CUSTOMER on GET /v1/stores/{store_id}/status | 403 forbidden_role | 403 forbidden_role | ✓ |
| stores | ANALYST on PUT /v1/stores/{store_id}/status | 403 forbidden_role | 403 forbidden_role | ✓ |
| stores | CUSTOMER on GET /v1/stores/{store_id}/hours | 403 forbidden_role | 403 forbidden_role | ✓ |
| stores | ANALYST on PUT /v1/stores/{store_id}/hours | 403 forbidden_role | 403 forbidden_role | ✓ |
| stores | CUSTOMER on GET /v1/stores/{store_id}/channel-enablement | 403 forbidden_role | 403 forbidden_role | ✓ |
| stores | ANALYST on PUT /v1/stores/{store_id}/channel-enablement | 403 forbidden_role | 403 forbidden_role | ✓ |
| stores | CUSTOMER on GET /v1/stores/{store_id}/commercial-rules | 403 forbidden_role | 403 forbidden_role | ✓ |
| stores | ANALYST on PUT /v1/stores/{store_id}/commercial-rules | 403 forbidden_role | 403 forbidden_role | ✓ |
| stock | CUSTOMER on GET /v1/stock-levels | 403 forbidden_role | 403 forbidden_role | ✓ |
| stock | CUSTOMER on GET /v1/stock-levels/{store_id}/{product_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| stock | ANALYST on GET /v1/stock-movements | 403 forbidden_role | 403 forbidden_role | ✓ |
| stock | ANALYST on POST /v1/stock-movements | 403 forbidden_role | 403 forbidden_role | ✓ |
| stock | ANALYST on GET /v1/stock-movements/{movement_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| stock | CUSTOMER on GET /v1/availabilities | 403 forbidden_role | 403 forbidden_role | ✓ |
| stock | CUSTOMER on GET /v1/availabilities/{store_id}/{product_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | INVENTORY_PLANNER on GET /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | ANALYST on POST /v1/customers | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | INVENTORY_PLANNER on GET /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | ANALYST on PATCH /v1/customers/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | ANALYST on PUT /v1/customers/{customer_id}/anonymization | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | ANALYST on GET /v1/customers/retention-candidates | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | ADMIN on GET /v1/customers/floor-search | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | ADMIN on GET /v1/customers/{customer_id}/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | ANALYST on GET /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | ANALYST on POST /v1/loyalty-cards | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | ANALYST on POST /v1/loyalty-cards/lookup | 403 forbidden_role | 403 forbidden_role | ✓ |
| customers | ANALYST on PUT /v1/loyalty-cards/{card_id}/revocation | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | ANALYST on PUT /v1/preference-profiles/{customer_id}/preferred-channels | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | ANALYST on PUT /v1/preference-profiles/{customer_id}/interest-categories | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id}/interests | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | INVENTORY_PLANNER on GET /v1/preference-profiles/{customer_id}/restrictions | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | ANALYST on GET /v1/preference-profiles/{customer_id}/consent | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | ANALYST on GET /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | ANALYST on POST /v1/preference-profiles/{customer_id}/consent-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | ANALYST on GET /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | ANALYST on POST /v1/preference-profiles/{customer_id}/restriction-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| customer-preferences | ANALYST on GET /v1/preference-profiles/{customer_id}/preference-changes | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | INVENTORY_PLANNER on GET /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | ANALYST on POST /v1/sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | INVENTORY_PLANNER on GET /v1/sales/{sale_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | INVENTORY_PLANNER on GET /v1/sale-reversals | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | ANALYST on POST /v1/sale-reversals | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | INVENTORY_PLANNER on GET /v1/sale-reversals/{reversal_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | ANALYST on GET /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | ANALYST on POST /v1/sale-imports | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | ANALYST on GET /v1/sale-imports/{import_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | ANALYST on PUT /v1/sale-imports/{import_id}/chunks/{sequence} | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | ANALYST on GET /v1/sale-imports/{import_id}/rejections | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | ANALYST on PUT /v1/sale-imports/{import_id}/status | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | STORE_MANAGER on POST /v1/sale-imports/{import_id}/reconciliation | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | STORE_MANAGER on GET /v1/sale-imports/{import_id}/reconciliation-cases | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | STORE_MANAGER on PUT /v1/sale-imports/{import_id}/reconciliation-cases/{case_id}/resolution | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | STORE_MANAGER on GET /v1/sale-imports/{import_id}/quality | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | MARKETING on GET /v1/sale-promotion-totals | 403 forbidden_role | 403 forbidden_role | ✓ |
| sales | CUSTOMER on GET /v1/channels | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segments | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segments/{label_code} | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segment-assignments/current-state | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | ADMIN on GET /v1/segment-assignments/floor-view | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segment-assignments/versioned | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segment-migrations/matrix | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segment-migrations | 403 forbidden_role | 403 forbidden_role | ✓ |
| segments | INVENTORY_PLANNER on GET /v1/segment-migrations/explanation | 403 forbidden_role | 403 forbidden_role | ✓ |
| recommendations | INVENTORY_PLANNER on GET /v1/recommendations | 403 forbidden_role | 403 forbidden_role | ✓ |
| recommendations | INVENTORY_PLANNER on GET /v2/recommendations | 403 forbidden_role | 403 forbidden_role | ✓ |
| recommendations | MARKETING on GET /v2/recommendation-decisions | 403 forbidden_role | 403 forbidden_role | ✓ |
| recommendations | MARKETING on GET /v2/recommendation-decisions/{decision_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | INVENTORY_PLANNER on GET /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | ANALYST on POST /v1/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | INVENTORY_PLANNER on GET /v1/engagement-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | ANALYST on POST /v1/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | CUSTOMER on GET /v1/engagement-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | ANALYST on GET /v1/capture-incidents | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | ANALYST on POST /v1/capture-incidents | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | ANALYST on GET /v1/capture-incidents/{incident_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | ANALYST on POST /v1/capture-incidents/{incident_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | ANALYST on GET /v1/capture-incident-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | INVENTORY_PLANNER on GET /v2/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | ANALYST on POST /v2/engagement-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | INVENTORY_PLANNER on GET /v2/engagement-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | ANALYST on POST /v2/engagement-events/{event_id}/void | 403 forbidden_role | 403 forbidden_role | ✓ |
| engagement | CUSTOMER on GET /v2/engagement-summaries | 403 forbidden_role | 403 forbidden_role | ✓ |
| check-ins | ANALYST on GET /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| check-ins | ANALYST on POST /v1/check-ins | 403 forbidden_role | 403 forbidden_role | ✓ |
| check-ins | ANALYST on GET /v1/check-ins/{check_in_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| check-ins | ANALYST on POST /v1/check-ins/{check_in_id}/reviews | 403 forbidden_role | 403 forbidden_role | ✓ |
| check-ins | ANALYST on GET /v1/customer-visits | 403 forbidden_role | 403 forbidden_role | ✓ |
| check-ins | ANALYST on POST /v1/customer-visits | 403 forbidden_role | 403 forbidden_role | ✓ |
| check-ins | ANALYST on GET /v1/customer-visits/{visit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | STORE_MANAGER on GET /v1/campaigns | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | ANALYST on POST /v1/campaigns | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | STORE_MANAGER on GET /v1/campaigns/{campaign_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | ANALYST on PATCH /v1/campaigns/{campaign_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | ANALYST on POST /v1/campaigns/{campaign_id}/transitions | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | STORE_MANAGER on GET /v1/campaigns/{campaign_id}/audience | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | ANALYST on PUT /v1/campaigns/{campaign_id}/audience | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | STORE_MANAGER on GET /v1/campaigns/{campaign_id}/audience/breakdown | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | STORE_MANAGER on GET /v1/campaigns/{campaign_id}/budget | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | ANALYST on PUT /v1/campaigns/{campaign_id}/budget | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | STORE_MANAGER on GET /v1/campaigns/{campaign_id}/budget/spending | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | STORE_MANAGER on GET /v1/campaigns/{campaign_id}/eligibility-rules | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | ANALYST on PUT /v1/campaigns/{campaign_id}/eligibility-rules | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | STORE_MANAGER on GET /v1/campaigns/{campaign_id}/exclusions | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | ANALYST on PUT /v1/campaigns/{campaign_id}/exclusions | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | INVENTORY_PLANNER on GET /v1/promotions | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | ANALYST on POST /v1/promotions | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | INVENTORY_PLANNER on GET /v1/promotions/{promotion_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| campaigns | ANALYST on PUT /v1/promotions/{promotion_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| audit-trail | ANALYST on GET /v1/audit-entries | 403 forbidden_role | 403 forbidden_role | ✓ |
| audit-trail | ANALYST on GET /v1/audit-entries/{audit_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| audit-trail | ANALYST on GET /v1/domain-events | 403 forbidden_role | 403 forbidden_role | ✓ |
| audit-trail | ANALYST on GET /v1/domain-events/verification | 403 forbidden_role | 403 forbidden_role | ✓ |
| audit-trail | ANALYST on GET /v1/domain-events/{event_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-profiles | 403 forbidden_role | 403 forbidden_role | ✓ |
| rfm-engine | INVENTORY_PLANNER on GET /v1/rfm-profiles/{customer_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| rfm-engine | STORE_MANAGER on GET /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| rfm-engine | ANALYST on POST /v1/rfm-rule-sets | 403 forbidden_role | 403 forbidden_role | ✓ |
| rfm-engine | STORE_MANAGER on GET /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| rfm-engine | ANALYST on PUT /v1/rfm-rule-sets/{rule_set_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| rfm-engine | ANALYST on POST /v1/rfm-rule-sets/{rule_set_id}/versions | 403 forbidden_role | 403 forbidden_role | ✓ |
| rfm-engine | ANALYST on POST /v1/rfm-rule-sets/{rule_set_id}/publication | 403 forbidden_role | 403 forbidden_role | ✓ |
| rfm-engine | STORE_MANAGER on GET /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| rfm-engine | ANALYST on POST /v1/rfm-recalculations | 403 forbidden_role | 403 forbidden_role | ✓ |
| rfm-engine | STORE_MANAGER on GET /v1/rfm-recalculations/{recalculation_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| rfm-engine | STORE_MANAGER on GET /v1/rfm-recalculations/{recalculation_id}/differences | 403 forbidden_role | 403 forbidden_role | ✓ |
| model-registry | STORE_MANAGER on GET /v1/model-versions | 403 forbidden_role | 403 forbidden_role | ✓ |
| model-registry | ANALYST on POST /v1/model-versions | 403 forbidden_role | 403 forbidden_role | ✓ |
| model-registry | STORE_MANAGER on GET /v1/model-versions/{version_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| model-registry | STORE_MANAGER on GET /v1/model-versions/{version_id}/publications | 403 forbidden_role | 403 forbidden_role | ✓ |
| model-registry | ANALYST on POST /v1/model-versions/{version_id}/publications | 403 forbidden_role | 403 forbidden_role | ✓ |
| model-registry | ANALYST on POST /v1/model-versions/{version_id}/rollbacks | 403 forbidden_role | 403 forbidden_role | ✓ |
| model-registry | STORE_MANAGER on GET /v1/model-trainings | 403 forbidden_role | 403 forbidden_role | ✓ |
| model-registry | ANALYST on POST /v1/model-trainings | 403 forbidden_role | 403 forbidden_role | ✓ |
| model-registry | STORE_MANAGER on GET /v1/model-trainings/{training_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| model-registry | STORE_MANAGER on GET /v1/drift-reports | 403 forbidden_role | 403 forbidden_role | ✓ |
| model-registry | STORE_MANAGER on GET /v1/drift-reports/{report_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| model-registry | STORE_MANAGER on GET /v1/drift-alerts | 403 forbidden_role | 403 forbidden_role | ✓ |
| model-registry | STORE_MANAGER on GET /v1/drift-alerts/{alert_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| experimentation | STORE_MANAGER on GET /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| experimentation | ANALYST on POST /v1/experiments | 403 forbidden_role | 403 forbidden_role | ✓ |
| experimentation | STORE_MANAGER on GET /v1/experiments/{experiment_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| experimentation | STORE_MANAGER on GET /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| experimentation | ANALYST on POST /v1/experiments/{experiment_id}/conversions | 403 forbidden_role | 403 forbidden_role | ✓ |
| experimentation | ANALYST on POST /v1/experiments/{experiment_id}/attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| experimentation | STORE_MANAGER on GET /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| experimentation | ANALYST on POST /v1/experiment-assignments | 403 forbidden_role | 403 forbidden_role | ✓ |
| experimentation | ANALYST on GET /v1/experiment-assignments/current | 403 forbidden_role | 403 forbidden_role | ✓ |
| experimentation | STORE_MANAGER on GET /v1/experiment-results/{experiment_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| experimentation | STORE_MANAGER on GET /v1/sale-attributions | 403 forbidden_role | 403 forbidden_role | ✓ |
| experimentation | STORE_MANAGER on GET /v1/sale-attributions/{attribution_id} | 403 forbidden_role | 403 forbidden_role | ✓ |
| analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/net-sales | 403 forbidden_role | 403 forbidden_role | ✓ |
| analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/spend | 403 forbidden_role | 403 forbidden_role | ✓ |
| analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/retention | 403 forbidden_role | 403 forbidden_role | ✓ |
| analytics-kpis | STORE_MANAGER on GET /v1/kpi-reports/migrations | 403 forbidden_role | 403 forbidden_role | ✓ |
| analytics-kpis | STORE_MANAGER on GET /v1/kpi-reports/campaign-performance | 403 forbidden_role | 403 forbidden_role | ✓ |
| analytics-kpis | MARKETING on GET /v1/kpi-reports/demand-coverage | 403 forbidden_role | 403 forbidden_role | ✓ |
| analytics-kpis | INVENTORY_PLANNER on GET /v1/kpi-reports/definitions | 403 forbidden_role | 403 forbidden_role | ✓ |
| users | POST /v1/users with role ADMIN | 409 single_administrator | 409 single_administrator | ✓ |
| users | PATCH an ANALYST to role ADMIN | 409 use_administrator_transfer | 409 use_administrator_transfer | ✓ |
| users | deactivating the only ADMIN | 409 administrator_required | 409 administrator_required | ✓ |
| sales | a line 1,000 over the stock on hand | 409 stock_unavailable | 422 discount_without_promotion | ✗ |
| sales | no Idempotency-Key | 400 idempotency_key_required | 400 idempotency_key_required | ✓ |
| store scope | POST /v1/sales (recordSale), another store, JSON | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | POST /v1/sales (recordSale), another store, XML | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | GET /v1/sales (listSales), another store's filter, JSON | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | GET /v1/sales (listSales), another store's filter, XML | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | GET /v1/sale-reversals (listSaleReversals), another store's filter, JSON | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | GET /v1/sale-reversals (listSaleReversals), another store's filter, XML | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | GET /v1/sale-promotion-totals (listSalePromotionTotals), another store, JSON | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | GET /v1/sale-promotion-totals (listSalePromotionTotals), another store, XML | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | POST /v1/engagement-events, another store, JSON | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | POST /v1/engagement-events, another store, XML | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | GET /v1/engagement-events, another store's filter, JSON | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | GET /v1/engagement-events, another store's filter, XML | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | GET /v1/engagement-summaries, another store's filter, JSON | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | GET /v1/engagement-summaries, another store's filter, XML | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | POST /v1/check-ins, another store, JSON | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | POST /v1/check-ins, another store, XML | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | GET /v1/check-ins, another store's filter, JSON | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | GET /v1/check-ins, another store's filter, XML | 403 forbidden_store | 403 forbidden_store | ✓ |
| store scope | GET sales/v1/sales, no store filter | only store 2 | stores [2] | ✓ |
| store scope | GET engagement/v1/engagement-events, no store filter | only store 2 | stores [] | ✓ |
| store scope | GET check-ins/v1/check-ins, no store filter | only store 2 | stores [2] | ✓ |
| store scope | ADMIN on GET /api/sales/v1/sales?store_id=1 | 200 | 200 | ✓ |
| store scope | ADMIN on GET /api/engagement/v1/engagement-events?store_id=1 | 200 | 200 | ✓ |
| store scope | ADMIN on GET /api/check-ins/v1/check-ins?store_id=1 | 200 | 200 | ✓ |
| store scope | an assigned store before the removal | 200 | 200 | ✓ |
| store scope | the same call after the assignment is removed | 403 forbidden_store | 403 forbidden_store | ✓ |
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
| rfm-engine | no token | 401 invalid_token | 401 invalid_token | ✓ |
| rfm-engine | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| rfm-engine | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| rfm-engine | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| model-registry | no token | 401 invalid_token | 401 invalid_token | ✓ |
| model-registry | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| model-registry | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| model-registry | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| experimentation | no token | 401 invalid_token | 401 invalid_token | ✓ |
| experimentation | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| experimentation | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| experimentation | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
| analytics-kpis | no token | 401 invalid_token | 401 invalid_token | ✓ |
| analytics-kpis | a tampered signature | 401 invalid_token | 401 invalid_token | ✓ |
| analytics-kpis | alg: none | 401 invalid_token | 401 invalid_token | ✓ |
| analytics-kpis | HS256 keyed with the public key | 401 invalid_token | 401 invalid_token | ✓ |
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
| rfm-engine | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| model-registry | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| experimentation | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
| analytics-kpis | a token auth signed, after its exp | 401 invalid_token | 401 invalid_token | ✓ |
