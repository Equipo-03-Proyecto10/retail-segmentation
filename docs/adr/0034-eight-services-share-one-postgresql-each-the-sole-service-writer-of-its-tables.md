# ADR-0034 — Eight services share one PostgreSQL database, each the only service that writes its tables

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

[ADR-0016](0016-the-second-delivery-reinstates-the-distributed-architecture.md)
requires six to ten microservices. Each must have one responsibility, its own
container, versioned routes, authentication, JSON and XML, a health endpoint and
OpenAPI. It left the boundaries undecided. Two clients will consume them: an
Android app (JSON) and an Electron app (XML), each using at least four services.

Three facts constrain where the boundaries can fall:

1. **The web system keeps working and is not rebuilt.** It reads and writes
   PostgreSQL directly, connects to no Redis and consumes no service. The
   end-to-end demonstration requires that a sale recorded from the phone
   appear in the web system without changing it. A database per service would
   hide that sale from the web unless the web were rewritten to ask a service.
2. **Business rules already live in transport-free modules.** Every module in
   `web/services/` and `web/db/` takes a psycopg `Connection` rather than a
   request. ADR-0020 states that a later entry point "calls the same row-level
   service" and "may not create a second implementation of the same rules".
   The single-administrator rule is in `web/services/users.py` and in a
   partial unique index.
3. **The instructor's reference architecture** has services over one
   centralized PostgreSQL schema, and a monolith that keeps reading it directly.

What remains a judgement call is how much isolation a shared database can
still provide. The answer here is database roles: a service cannot write what
it does not own, because PostgreSQL refuses it, not because the code is careful.

## Decision

The distributed stage has **eight service boundaries**. All of them share the
existing PostgreSQL database `retail`. Each service connects as its own role,
granted exactly the privileges in the matrix below, and is the only *service*
that writes the tables it owns.

Services do not call one another over HTTP. They share reads through grants
and announce changes on Redis Streams (ADR-0037). Where the web already
implements a rule, a Flask service imports that transport-free module from
`web/services` or `web/db` instead of reimplementing it. Any service that
writes an audited table sets `mosaiq.user_id` on its connection (`web/db/audit.py`).

| Service | Lang | Port | Role | Writes | Reads (SELECT) |
|---|---|---|---|---|---|
| auth-service | Flask | 5001 | `svc_auth` | — | `app_user`, `role` |
| users-service | Flask | 5002 | `svc_users` | `app_user`; DELETE on `app_session` (revocation) | `role`, `app_session` |
| catalog-service | Flask | 5003 | `svc_catalog` | `product`, `category` | — |
| catalog-service-rs | Rust | 5103 | `svc_catalog_rs` | — | `product`, `category` |
| customers-service | Flask | 5004 | `svc_customers` | `customer`, `customer_preferred_channel`, `customer_interest_category` | `channel`, `category` |
| inventory-service | Rust | 5005 | `svc_inventory` | — | `inventory`, `store`, `product` |
| sales-service | Flask | 5006 | `svc_sales` | `transaction`, `transaction_line`; UPDATE on `inventory` (decrement) | `product`, `store`, `channel`, `customer`, `inventory` |
| segments-service | Rust | 5007 | `svc_segments` | — | `segment`, `segment_label`, `segmentation_run`, `customer_segment_history`, `product`, `inventory`, `transaction`, `transaction_line` |
| activity-service | Flask + MongoDB | 5008 | `svc_activity` | — (MongoDB, ADR-0038) | `store`, `customer` |

Additional rules for the matrix:

- **Audit log.** Every role that writes an audited table also holds INSERT on
  `audit_log`, which the triggers need, and never UPDATE or DELETE on it.
- **Sequences.** Each writing role holds USAGE on the sequences behind the
  tables it writes.
- **The twin.** catalog-service-rs is a second implementation of the catalog
  boundary, kept for the comparison in ADR-0042. It counts as one service, not
  two.

Two permissions join the vocabulary in `web/middleware/authz.py`, both granted
to `ADMIN` and `STORE_MANAGER`:

- `sale.record`, which sales-service requires;
- `activity.record`, which activity-service requires.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| One database per service | The web system could not see a sale recorded from the phone without being rewritten to call a service, which contradicts the boundary in #398. The cross-database joins that segmentation and recommendations need would become event-driven copies, a large amount of work the course does not ask for |
| One shared role for every service, as `retail_app` is for the web | "One writer per table" would be a convention only. A bug in the read-only segments-service could write history, and nothing would stop it |
| The reference slide's domain (login, users, authors, books, orders, payments) mapped name for name | Our domain has no payments, and its distinctive data (segments, stock, activity) would have no service. The mapping that fits is: login→auth, users→users, books→catalog, authors→customers, orders→sales, plus inventory, segments and activity |
| Services call each other synchronously over HTTP (sales asks catalog for the price, inventory for stock) | It adds a service-to-service token flow, a chain of failures, and latency for data the shared database already provides under grants. ADR-0039 also requires the stock decrement to be in the sale's own transaction (ADR-0031), which an HTTP call cannot join |
| Thirteen services, as in the pre-reset design | Over the course's ten-service ceiling, and never named even then |

## Consequences

**What this makes easy.**
- The end-to-end flow works with no change to the web.
- Each rule has one implementation. Python services import it; the Rust
  services are read-only, apart from the twin's parity tests.
- Grants make ownership checkable by a query.

**What this makes hard.**
- **A shared database couples schemas.** A column change in `sql/01_schema.sql`
  can break several services and the web at once, so CI must build and test
  all of them on every schema change.
- **The web is a second writer.** It remains a writer of `app_user`, `product`,
  `customer`, `transaction` and `inventory` (as `retail_app`). "Only service
  writer" therefore does not mean "only writer". Concurrent edits are arbitrated
  by PostgreSQL constraints and by both writers using the same service-layer
  code, not by the boundary.
- **Importing `web.db` pulls Flask and `web.config`** into each Flask service,
  and service images copy `web/` read-only.
- **The Rust recommendation port is a second implementation** of a business
  rule. Only F14-14's parity test keeps it honest.

**What must now be true elsewhere.**
- `sql/00_create_database.sql` creates the roles, and `sql/01_schema.sql`
  grants the matrix (F13-07).
- `AGENTS.md` scopes "One application" to the web system.
- `docs/requirements.md` scopes RNF-01 and RNF-02 the same way.
- ADR-0036 carries the permissions in the token.
- ADR-0037 gives each service a Redis user mirroring this table.

## Compliance

```sql
-- Every svc_* privilege, to compare with the matrix above; CI fails on any
-- difference (F13-07).
SELECT grantee, table_name, string_agg(privilege_type, ',' ORDER BY privilege_type)
FROM information_schema.role_table_grants
WHERE grantee LIKE 'svc\_%'
GROUP BY grantee, table_name
ORDER BY grantee, table_name;
```

```bash
# Services never call each other over HTTP: no service references another
# service's port or hostname.
grep -rnE '(localhost|127\.0\.0\.1|[a-z]+-service):5[01][0-9]{2}' services/ --include='*.py' --include='*.rs' \
  | grep -v '/tests/' || echo "no service-to-service HTTP"
```
