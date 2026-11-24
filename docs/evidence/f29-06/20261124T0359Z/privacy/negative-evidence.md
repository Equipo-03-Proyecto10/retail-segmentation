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

Run at 2026-11-24 04:01 UTC against `https://127.0.0.1`: 1369 cases, 1369 held, 0 did not.

- **store scope, reads and reversals by id of another store's rows:** not run here. They need a sale, a reversal, an event and a check-in in another store, which persist; the local stack's run covers them.

| Area | Case | Expected | Actual | |
|---|---|---|---|---|
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
