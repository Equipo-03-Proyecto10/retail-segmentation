# ADR-0060 — Four store profiles map to four roles, and services read each user's stores per request

**Status:** Accepted (2026-10-31)
**Owner:** Marcelo
**Issue:** #753
**Supersedes:** in part, [ADR-0041](0041-android-and-electron-clients-run-different-processes-over-different-formats.md) (`STORE_MANAGER` as the only floor role) and [ADR-0048](0048-store-managers-read-every-stores-reports.md) (no user-to-store association, for the services only)
**Superseded by:** —

---

## Context

The instructor's brief asks for an Android app with four separated profiles: store
seller, store manager, loyalty officer and commercial supervisor. It warns against
mixing experiences without clearly separating roles, and asks users-service for
assigned stores and the manager for local indicators.

ADR-0041 settled on `STORE_MANAGER` as the floor role because the seven roles hold no
in-store associate and a new role is a business decision. The instructor has now
made that decision. ADR-0048 rejected confining a manager to a store, and said that
if confinement were ever required it would need the association, its seed, its
administration, and the same narrowing on every surface the role reads, plus an
answer on how a fixed store relates to the per-visit check-in.

Tokens carry only the role code (ADR-0036), and an access token lives 10 minutes.

The judgement call is the web. It cannot read `users.*` (ADR-0046), so this record
narrows the services and leaves a gap there, stated below.

## Decision

Three role codes are added, for ten in all, and each profile is exactly one role.
Each of the four floor roles works only in the stores assigned to it. Services read
those stores from `api_users.store_assignment_v1` on each narrowed request, so tokens
stay role-only and a removed store stops working on the next request.

| Profile | Role | `role_id` | Works on |
|---|---|---|---|
| Store seller | `SALES_ASSOCIATE` (new) | 8 | Identifying customers, the permitted profile, catalog and stock, recommendations, carts and sales |
| Store manager | `STORE_MANAGER` | 3 | What it does today, plus local indicators and active campaigns |
| Loyalty officer | `LOYALTY_OFFICER` (new) | 9 | Registering customers, consent, benefits and preferences |
| Commercial supervisor | `COMMERCIAL_SUPERVISOR` (new) | 10 | Capture incidents, synchronization and promotions applied, in aggregate |

`ADMIN` stays a floor role and is not confined. `ANALYST`, `MARKETING`,
`INVENTORY_PLANNER`, `AUDITOR` and `CUSTOMER` are not assigned to stores. A floor role
with no assignment can work in no store.

### What is narrowed

An assignment says where a user may work. A check-in says where they are today, and
it is accepted only at an assigned store. A narrowed operation refuses another store
with 403 `forbidden_store`, in JSON and XML. A list with no store filter returns only
the caller's stores.

| Narrowed to the assigned stores | Not narrowed, and why |
|---|---|
| `POST` and `GET` of sales, and reversals | Products, categories, stores: catalogs |
| `POST` and `GET` of check-ins and customer visits | Stock levels: a seller may offer what another store holds |
| `POST` and `GET` of engagement events, and engagement summaries | Customers, segments, recommendations: a customer belongs to no store, so these follow the field table below |
| KPI reports and capture incidents | Active promotions: already filtered by store |

### Who reads what of a customer

Each role gets its own representation. An excluded field is absent, not null or
masked, so every such element is optional in the XSD.

| Field | `SALES_ASSOCIATE` | `LOYALTY_OFFICER` | `STORE_MANAGER` | `COMMERCIAL_SUPERVISOR` |
|---|---|---|---|---|
| Name, loyalty state | Yes | Yes | Yes | No |
| Email, phone | No | Yes | Yes | No |
| Segment label and `since` | Yes | Yes | Yes | No |
| R, F and M scores | No | No | Yes | No |
| Interest categories, restrictions | Yes | Yes | Yes | No |
| Preferred channels, consent record | No | Yes | Read | No |

### Rows added to the service operation policy

| Operations | Added to `x-mosaiq-roles` |
|---|---|
| Reads of products, categories, stores, stock levels and channels | `SALES_ASSOCIATE`, `LOYALTY_OFFICER`, `COMMERCIAL_SUPERVISOR` |
| Reads of customers, preference profiles, segments, segment assignments and recommendations, in the representation above | `SALES_ASSOCIATE`, `LOYALTY_OFFICER` |
| Reads of sales and engagement events, narrowed | `SALES_ASSOCIATE`, `COMMERCIAL_SUPERVISOR` |
| `POST /v1/sales` and its reversals, `POST /v1/check-ins`, `POST /v1/engagement-events` | `SALES_ASSOCIATE` |
| `POST /v1/check-ins`, `POST` and `PATCH` of customers, `PUT` of preferences and consent | `LOYALTY_OFFICER` |
| `POST /v1/check-ins` | `COMMERCIAL_SUPERVISOR` |
| Reads of active promotions | The four floor roles. Other campaign reads keep their roles |
| KPI reports, narrowed | `STORE_MANAGER`, `COMMERCIAL_SUPERVISOR` |

### The web

The new roles get an empty row in `PERMISSIONS`: they hold nothing there, the safe
direction ADR-0007 expects, and `web/` gains no route. `STORE_MANAGER` keeps reading
every store in the web, as ADR-0048 decided.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep `STORE_MANAGER` as the only floor role, with modes in the app | Permissions could not differ: a seller would hold a manager's reads, and the services could not refuse it. It is the mixing the instructor warns against |
| Carry the stores in the token | ADR-0036 keeps tokens role-only. A removed store would keep working for up to 10 minutes, and every service would trust a stale claim |
| Confine by check-in alone, with no assignment | A user could check in anywhere, and the brief asks for assigned stores |
| Narrow the web too | `retail_app` has no privilege on service schemas (ADR-0046), and every report query would change, which goes well past what the brief asks |
| Cache assignments in Redis | It saves one indexed read per narrowed request, at the cost of a staleness bound and an invalidation path. The narrowed operations already hit PostgreSQL |

## Consequences

**What this makes easy.** A new floor screen follows one table. The negative suite
covers the new roles on its own, since it reads each contract.

**What this makes hard.** Five services gain a read on every narrowed request and a
grant on the view. Contracts must make role-excluded elements optional, which F21-13
states for customers.

**A gap this leaves open.** A store manager confined on the phone can still read every
store in the web. Confinement here is a rule of the services, not a property of the
account across the whole system. Closing it needs its own record.

**What must now be true elsewhere.**
- F19-04 adds the codes to the seed, `sql/seed-exempt.txt`, `PERMISSIONS` and every contract's `x-mosaiq-roles`.
- F19-05 and F19-06 deliver the association, and F19-07 the narrowing, after which `docs/requirements.md` §3 stops saying no operation is narrowed to a store.
- `FLOOR_ROLES` in `SessionRepository.kt` and `clients/android/check-adr41.sh` follow this table (F27-01).
- The single-administrator rule is unchanged: C-4 still holds, enforced twice.

## Compliance

`tests/negative/test_roles.py` runs against every role in each contract. F19-07 adds
a negative case per narrowed operation, and a test with real time that a removed
store is refused on the next request. The existing test comparing `PERMISSIONS` with
the seed fails if a new role has no row there.
