# Requirements

What MOSAIQ must do, and what must be true of how it does it. Deliverable 1 in
[`scope.md`](scope.md) §5.

The first-delivery requirements are inside [`scope.md`](scope.md); RF-12, RF-16
to RF-31 and the analytics permissions reflect the later monolith phase bounded
by [`scope-delivery-2-analytics.md`](scope-delivery-2-analytics.md). Where a
requirement is a deliberately small slice of something larger that is deferred,
it says so and names what was left out — a requirement that quietly promises
less than its title suggests is worse than one that is honestly narrow.

Ids are stable. `RF` functional, `RNF` non-functional. Stories that build them
are in [`user-stories.md`](user-stories.md); invariants they rely on are in
[`business-rules.md`](business-rules.md).

**The distributed stage does not change the web system.** It receives no Redis
connection and consumes no service ([RNF-02](#2-non-functional-requirements)).
It keeps reading and writing PostgreSQL directly and rendering HTML on the
server, which is also how it shows what the services record. RF-16 to RF-31
below are all web-system requirements.

---

## 1. Functional requirements

| Id | Requirement | Demonstration item or phase | Story |
|---|---|---|---|
| RF-01 | A user signs in with an email address and a password. Credentials are verified against an argon2id hash; the password is never stored, logged, or written to the audit log | Inicio de sesión | F3-03 (#63) |
| RF-02 | A signed-in user signs out, and the session is invalidated server-side rather than only cleared in the browser | Inicio de sesión | F3-03 (#63) |
| RF-03 | An unauthenticated request to any protected page is redirected to sign-in, and returns there after signing in | Acceso diferenciado | F4-01 (#69) |
| RF-04 | Every route declares the roles that may reach it, and the authorization middleware refuses the rest at the route level, before the view runs | Acceso diferenciado | F4-01 (#69) |
| RF-05 | The system holds exactly one administrator. Creating or promoting a second is refused by the application and, independently, by the database | Acceso diferenciado | F4-02 (#70) |
| RF-06 | The administrator creates, reads, updates and deletes rows in every catalog: category, product, store, channel and role | Operación de catálogos | F3-04 (#64) |
| RF-07 | A deletion that would orphan existing data is refused with a message naming what still references the row, not with a database error | Operación de catálogos | F3-04 (#64) |
| RF-08 | The administrator attaches an image to a product. The file is stored under `UPLOAD_DIR` and the database keeps its path, never its bytes | Operación de catálogos | F3-07 (#67) |
| RF-09 | The administrator creates users, edits them, and deactivates them. Deactivation, not deletion, is how access is removed, so history keeps its actor | Operación de catálogos | F3-06 (#66) |
| RF-10 | A signed-in user lists, searches and opens the detail of customers and products, within what their role permits | Consulta de información | F3-05 (#65) |
| RF-11 | A user consults stock per store and product | Consulta de información | F3-05 (#65) |
| RF-12 | The administrator runs an atomic segment recalculation with either `RFM_RULES` or `KMEANS` over a closed sales window, after confirming it. RFM rules give tied measures equal quintile scores; K-means takes a `k` of at least 2 and records its reproducibility parameters and quality measures. Both methods map every scored customer to a stable label and append the run and one assignment-history row per customer; the earlier open row is closed, never rewritten (RN-05, RN-20, RN-21, RN-38, RN-46, RN-51, RN-58) | Ejecución de un proceso principal | F3-10 (#102), F7-01, F7-02, F9-01–F9-03, F9-05 (#336), #335, #352, #519 |
| RF-13 | A user consults which customers are in a segment, and which segment a customer is in | Consulta de información | F3-05 (#65) |
| RF-14 | The administrator and the auditor read the log of changes to catalogs and business rules, filtered by entity and date, with before and after values | Registro de auditoría | F3-11 (#103) |
| RF-15 | A failure the user caused shows a page explaining what to do; a failure they did not shows a controlled error page and is logged with enough detail to diagnose. Neither shows a stack trace | — | F4-05 (#73) |
| RF-16 | A user holding `segment.read` lists every completed segmentation run with its method, window, parameters as entered, executor and customer count, and opens one to read its quality measures with their values (K-means) and every customer it scored, unassigned ones included. On a customer's page the same user reads the append-only assignment timeline, newest first, can ask which segment the customer held on a given date, and sees the sales that entered and left the calculation between two runs. The current segment is the timeline's one open row. A run's page, list row and timeline row say `Synthetic` when the run is `SEEDED` (RN-20, RN-21, RN-47, RN-60) | Phase 7 — segmentation traceability | F7-02, F7-03, F7-07 (#337), #447 |
| RF-17 | A user holding `segment.read` compares any two completed runs by stable label. The earlier run is the before state whatever order they are chosen in; every customer lands in one migration-matrix state (a label, Unassigned, new, or not in the later run); each cell lists its customers; and each customer's change is explained in plain language from the stored R/F/M values, with RN-60's changed/stable thresholds, without recomputing any assignment. The matrix and each explanation say `Synthetic` when either run is `SEEDED` (RN-59, RN-60) | Phase 7 — migration | F7-04–F7-06, F7-08 (#338), #339 |
| RF-18 | The administrator uploads a versioned sales CSV through the web page. Each row is accepted or rejected on its own; a row repeating an accepted source transaction id and product is rejected as a duplicate, so a retry adds no second sale; an accepted line consumes the store's stock in the same transaction. The stored report reconciles received, accepted and rejected rows, names the source line and reason of every rejection, stays available afterwards and downloads as a rejections CSV. CSV through this page is the web system's only way to ingest sales (RN-14, RN-37, ADR-0020, ADR-0031) | Phase 8 — sales ingestion | F8-01, F8-02, #334, #355, #356, #362 |
| RF-19 | A user holding `segment.read` opens a customer's consumption profile over a window of 1 to 3650 days: total spend, purchase frequency, average ticket, last purchase, average discount, dominant channel and store, the three favourite categories, the five most frequent products, R/F/M, and the current and previous segment. A "Before and now" section compares the window's two halves and claims a shift of channel, store or category only when each half has at least three purchases and a clear leader. A customer with no accepted sale in the window gets a profile that says so, not zeros. Sales measures say `Synthetic` when a sale they read is `SEEDED`, and the segment panel when its run is (RN-34–RN-36, RN-50) | Phase 8 — consumption profile | F8-03–F8-06 (#341) |
| RF-20 | A user holding `segment.read` compares one `RFM_RULES` run with one `KMEANS` run on label codes alone. The page shows each run's parameters and quality measures and every label's population in each run, counts each customer scored by both runs once as agreeing or disagreeing, counts a customer only one run scored separately, and gives a cross-tabulation whose cells reconcile. It never declares either method better, and says `Synthetic` when either run is `SEEDED` (RN-39) | Phase 9 — model comparison | F9-04 |
| RF-21 | A user holding `segment.read` opens a customer's recommendations: by default at most ten products drawn from the customer's open stable label, preferred categories (and the categories below them) and purchase history. Each is active, not bought by the customer in the window, in positive stock at the customer's usual store, and states the signals and figures behind it. A customer with no open label or no usual store is told which, and nothing is substituted (RN-40) | Phase 10 — recommendations | F10-01, F10-02, #277, #280 |
| RF-22 | A user holding `campaign.write` creates a campaign for a stable label and edits it only while it is a draft, with its end date not before its start. A campaign moves only from DRAFT to ACTIVE or CANCELLED and from ACTIVE to FINISHED or CANCELLED; an illegal move is refused naming the status and the moves allowed, and a campaign that ended before today, or whose experiment lacks its arms, is not activated. Users holding `campaign.read` list the campaigns (RN-15, RN-16, RN-22, RN-24, RN-31, RN-32, RN-49) | Phase 11 — campaigns | F11-01, F11-02 |
| RF-23 | A user holding `campaign.write` sets up an experiment on a campaign with zero or one control arm, named treatment arms (exactly two when there is no control), a target metric, a positive conversion window in days and a data origin. Conversion rate is the only target metric that can be chosen, and the one measured ([ADR-0053](adr/0053-experiments-are-measured-on-conversion-only.md)). The frame (campaign, dates, target metric, window, arm definitions) is locked by the first assignment, the origin never changes, and a `SEEDED` or `INJECTED` experiment is labelled `Synthetic` (RN-19, RN-24–RN-26, RN-49) | Phase 11 — experiment setup | F11-01, F11-03, #342, #427, #457 |
| RF-24 | A user holding `campaign.write` assigns the target population of an active campaign (the customers holding its label) to the experiment's arms once, in one transaction, in reproducible arms whose sizes differ by at most one. Exposure is then recorded as its own event for one customer or for a whole treatment arm, and conversion as its own event by linking an assignment to a qualifying sale inside the fixed window. Assignments are never repeated, moved or deleted; exposing a control customer, an unassigned customer, or anyone outside the experiment's dates is refused (RN-23, RN-27, RN-42, RN-49) | Phase 11 — assignment, exposure and conversion | F11-04–F11-06, #357 |
| RF-25 | A user holding `campaign.read` reads, per experiment, conversion over all assigned customers (intent to treat) and, separately, over exposed treatment customers. A treatment arm's uplift against the control, with its 95% interval, uses the intent-to-treat rate only. The result is preliminary while any window is open; an experiment with no control, an empty arm, a metric other than conversion or unrecorded qualifying sales is refused with the reason. An A/A split and a fixed injected uplift validate the calculation, and a result says `Synthetic` when the experiment is not `OBSERVED` or a conversion it counts links a `SEEDED` sale (RN-26, RN-27, RN-48) | Phase 11 — experiment measurement | F11-07, #343 |
| RF-26 | A user holding `segment.read` opens a dashboard for one segmentation run: segment sizes, revenue over the run's window, the raw recency and frequency distribution and the migration flow from the previous run, all by stable label, plus average R/F/M per label, the experiments running today with each arm's conversion, and the most recommended products with their stock. Customers with tied measures share a quintile. Chart data is embedded in the server-rendered page and Highcharts is served by the application, not an external host; each panel and chart, its `aria-label` included, says `Synthetic` when the run (or, for the migration flow, either run) is `SEEDED`, with a banner when any panel does (RN-41, RN-51) | Phase 12 — segmentation dashboard | F12-01, #340, #423 |
| RF-27 | A user holding `segment.read` filters the stored segment history by run, label (or unassigned) and run date, and expands a row to compare that customer's stored R/F/M with their row in the immediately previous run, or to read that there is none. All filters combine, and no match is an empty report, not an error. A row says `Synthetic` when its run or the run it is explained against is `SEEDED` (RN-43) | Phase 12 — segment history report | F12-02 |
| RF-28 | A user holding `segment.read` filters two reports, consumption shifts and recommendations, by store, channel, category and an as-of date with a window in days. Each report filters what the profile and recommendation rules already compute, every set filter must match, each section pages on its own and keeps every filter, and each shows its own empty state. The shift report says `Synthetic` when either period holds a `SEEDED` sale; a recommendation when RN-40's marker rule applies (RN-40, RN-44) | Phase 12 — consumption reports | F12-03 |
| RF-29 | A user holding `campaign.read` filters the experiment report by campaign and data origin. Per arm it shows assignment, exposure and conversion as three separately labelled counts beside intent-to-treat uplift and its 95% interval, and it exports exactly the filtered rows as CSV that repeats the data origin and the `Synthetic` label on every line, an `OBSERVED` experiment whose conversions link a `SEEDED` sale included (RN-26, RN-45) | Phase 12 — experiment report | F12-04, #358 |
| RF-30 | A user holding `campaign.read` opens a server-rendered dashboard for one experiment: the conversion rate per arm over the assigned and over the exposed customers, the difference from a named baseline (the control, or the first treatment arm when there is none) with its 95% interval, and the cumulative conversions through the fixed window. Every chart says `Synthetic` when the experiment is not `OBSERVED` or a conversion it counts links a `SEEDED` sale (RN-26, RN-57) | Phase 12 — experiment dashboard | AN-03 (#415) |
| RF-31 | A user holding `report.read` opens Reports: the revenue, sales count and split by channel and by store of the newest 90 days of sales on record (or a notice when there are none), and a link to each analytics report the user's role can open, never one that would answer 403. The page says `Synthetic` when a sale in the window is `SEEDED` | Phase 12 — reports index | #344 |

**RF-12 grew in Delivery 2.** The first delivery implemented the original
rule-band slice, which overwrote each customer's segment in place. The monolith
analytics phase replaced that with append-only runs and assignment-history rows
whose open row is the current segment
([ADR-0017](adr/0017-segment-assignment-history-replaces-the-mutable-current-segment.md)),
added `KMEANS` with a variable `k`, and kept both methods behind one stable-label
pipeline. RF-16 to RF-31 state the other analytics capabilities delivered around
that process, one requirement each; the permission each needs is in
[`analytics-permission-map.md`](analytics-permission-map.md). Their boundary and
success criteria are in
[`scope-delivery-2-analytics.md`](scope-delivery-2-analytics.md); the phase
ordering remains in [`roadmap.md`](roadmap.md). ADR-0017 through ADR-0021
govern the later implementation of the work ADR-0004 originally deferred.

**Not claimed above, because it is not built.** A revenue target metric
(#457, decided by ADR-0052 and ADR-0053, removed average ticket because it
counts only customers who bought; revenue per assigned customer would arrive as
a new story under its own rule), an HTTP
sales-ingestion endpoint in the web system (ADR-0020),
stock receipts, transfers and adjustments (`inventory.write`), editing the label
vocabulary or the rule catalog (`segment.write`) and a customer self-service
view (`user.self`) are open decisions or deferred work in
[`roadmap.md`](roadmap.md) and [`backlog.md`](backlog.md), not requirements met.

## 2. Non-functional requirements

Each states how it is checked. A quality nobody can verify is a wish.

| Id | Requirement | How it is verified |
|---|---|---|
| RNF-01 | The web system's browser receives server-rendered HTML. The web system exchanges no JSON or XML with another component (`scope.md` C-2). The microservices of the second delivery are not bound by this, and answer in JSON and XML under ADR-0035 | No serialization endpoint exists in `web/`; every web route returns a rendered template. Reviewed per pull request |
| RNF-02 | The web system is one deployable unit, exposes no REST, GraphQL or SOAP API, connects to no Redis and consumes no service (C-1, C-3, C-7). The second delivery adds at least ten microservices, fifteen planned, each implementing one business rule in its own container, under ADR-0034, ADR-0040 and ADR-0043; any further service or deployable unit needs an ADR | `web/` is one application package and one process; its configuration and imports contain no Redis or service client. Each service is registered in `contracts/registry.yaml` and runs as its own compose project from `services/<name>/compose.yaml` |
| RNF-03 | Every SQL statement is parameterized. No string interpolation into SQL anywhere, including scripts and one-off queries | `grep` for f-strings and concatenation in `web/db/`; reviewed per pull request |
| RNF-04 | Passwords are hashed with argon2id. The database never hashes and never sees a plaintext password | `web/requirements.txt` pins `argon2-cffi`; seeded hashes are argon2id |
| RNF-05 | Session cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` wherever the deployment terminates TLS. The secret key comes from the environment | Inspect response headers; `FLASK_SECRET_KEY` is absent from source |
| RNF-06 | No secrets in source. New configuration is documented in `.env.example` with a safe local default | `.env` is gitignored; CodeQL and review. `sql/00_create_database.sql` takes the role password as a psql variable |
| RNF-07 | Input is validated and sanitized before it reaches the database or a template | Negative tests in F5-02 (#75) |
| RNF-08 | The relational model is in fourth normal form, with each normalization decision written down | [`data-model.md`](data-model.md) §2; the justification is the graded artifact |
| RNF-09 | Every table carries at least 30 seed rows, or an exemption recorded with its reason | CI counts rows and reads `sql/seed-exempt.txt` |
| RNF-10 | The three SQL scripts run clean, in order, against an empty PostgreSQL | CI `database` job; evidence in [`evidence/f2-07-integrity-verification.md`](evidence/f2-07-integrity-verification.md) |
| RNF-11 | Screens are usable at 375 px and at 1440 px | Definition of Done item 10, checked per pull request |
| RNF-12 | The interface is in English, like every other written artifact | `CONTRIBUTING.md`; templates already carry `lang="en"` |
| RNF-13 | The application runs under both systemd and Docker Compose, reading identical configuration from the environment. systemd is the default on the instance | [ADR-0006](adr/0006-run-under-both-systemd-and-docker-compose.md); F3-09 (#89) and F6-02 (#78) |
| RNF-14 | The application restarts automatically after a crash or a reboot | `Restart=always` in the systemd unit; `restart:` policy in Compose |
| RNF-15 | The application runs from a clean clone by following the README, with no undocumented manual step | Definition of Done item 5 |
| RNF-16 | Uploaded images are limited to 5 MB and to JPEG, PNG and WebP. A file outside those limits is refused with a message | `MAX_UPLOAD_BYTES` and `ALLOWED_IMAGE_TYPES` in `.env.example`; negative tests in F5-02 (#75) |
| RNF-17 | The audit log is append-only. Nothing in the application updates or deletes an entry, and no entry carries a credential | `fn_audit()` strips `password_hash`; verified as case P3 in the integrity evidence |
| RNF-18 | A list page over seed-sized data renders in under one second on the instance | Measured at F5-01 (#74) against the 30-row catalogs and 300 transactions |
| RNF-19 | Server-side sessions expire after 30 minutes without activity and after 8 hours absolutely; responses carry the application security-header baseline and HTTPS responses carry one-year HSTS with `includeSubDomains` | `tests/test_server_side_sessions.py`, `tests/test_security.py`, `tests/test_nginx_security_headers.py` |

## 3. Permission matrix

The canonical statement of who may do what in the web system. The distributed
stage's services follow the
[service operation policy](#service-operation-policy-distributed-stage) below,
and this matrix does not change for them. [`data-model.md`](data-model.md)
links here rather than repeating it, because two copies of a matrix drift.

`full` create, read, update and delete · `read` read only · `own` only their own
row · `—` no access.

| Role | Catalogs | Users | Segments and rules | Campaigns | Segment run | Sales ingestion | Reports | Audit log |
|---|---|---|---|---|---|---|---|---|
| `ADMIN` | full | full | full | full | run | ingest | read | read |
| `ANALYST` | read | — | read | read | — | — | read | — |
| `STORE_MANAGER` | read | — | read | — | — | — | read | — |
| `MARKETING` | read | — | read | full | — | — | read | — |
| `INVENTORY_PLANNER` | read, `inventory` write | — | — | — | — | — | read | — |
| `AUDITOR` | read | read | read | read | — | — | read | read |
| `CUSTOMER` | — | own | — | — | — | — | — | — |

Three things this matrix is deliberately strict about:

- **Only `ADMIN` runs the segment recalculation.** It rewrites a column on every
  customer, and an analyst who can trigger it can change what every report says.
- **`AUDITOR` reads the audit log and cannot write anywhere.** An auditor who can
  edit the thing they audit is not an auditor.
- **`CUSTOMER` has only a future own-row capability.** The role is for a
  loyalty customer rather than staff, but no self-service route exists until a
  lifecycle populates and maintains the schema's optional
  `customer.user_id` ownership link. The seed and current user-management
  workflow leave that link empty, so the dormant `user.self` permission grants
  no current route or access.

Enforced by the authorization middleware at the route level — F4-01 (#69),
which transcribes this table into `web/middleware/authz.py` and refuses any
route that declares nothing. A matrix cell does not invent a route: the
dormant `inventory.write`, `segment.write` and `user.self` declarations and
their prerequisites are accounted for in
[`analytics-permission-map.md`](analytics-permission-map.md). Where a cell
says `own`, the gate decides that the page may be reached at all; narrowing a
query to the caller's own rows belongs to the surface that eventually claims
that permission. `STORE_MANAGER` reads every store's reports, with no
per-store narrowing
([ADR-0048](adr/0048-store-managers-read-every-stores-reports.md)). Why the matrix is code rather than two more tables:
[ADR-0007](adr/0007-permissions-in-code-with-a-default-deny-middleware.md).

**Customers and stock are not columns above.** The consultation module (F3-05,
#65) gives every `catalog` reader a read-only view of stock per store and
product, and every `segment` reader a read-only view of the customer directory
and segment membership — customer data is the substrate of segmentation, so it
follows `segment.read` rather than `catalog.read`. This keeps
`INVENTORY_PLANNER` out of the customer directory.
[ADR-0010](adr/0010-the-consultation-module-is-a-separate-read-only-blueprint.md)
records why. `STORE_MANAGER` holds
`segment.read` since F10-02, so a store manager can act on a customer's
recommendations at the counter, and reads the whole directory:
[ADR-0023](adr/0023-store-managers-read-segments-and-customers.md).

### Service operation policy (distributed stage)

The services of the distributed stage do not use the matrix above. Their tokens
carry only the role code, and each service enforces this policy per operation,
as `x-mosaiq-roles` in its own contract
([ADR-0036](adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)).
The web's matrix does not change.

| Operations | `x-mosaiq-roles` | Basis |
|---|---|---|
| Reads of products (including `GET /v1/products?ids=`), categories, stores, stock levels and channels | `ADMIN`, `ANALYST`, `STORE_MANAGER`, `MARKETING`, `INVENTORY_PLANNER`, `AUDITOR` | Catalogs: read. Stock follows the catalog (ADR-0010) |
| Writes of products, categories and store locations | `ADMIN` | Catalogs: full for `ADMIN` only |
| Reads of customers, preference profiles, segments, segment assignments and recommendations | `ADMIN`, `ANALYST`, `STORE_MANAGER`, `MARKETING`, `AUDITOR` | Segments and rules: read. Customers follow `segment.read` (ADR-0010, ADR-0023) |
| Reads of sales, sale reversals and engagement events (each row names a customer) | `ADMIN`, `ANALYST`, `STORE_MANAGER`, `MARKETING`, `AUDITOR` | Reports: read, narrowed to `segment.read` holders, which keeps `INVENTORY_PLANNER` out of customer data |
| Engagement summaries (aggregates, no customer) | `ADMIN`, `ANALYST`, `STORE_MANAGER`, `MARKETING`, `INVENTORY_PLANNER`, `AUDITOR` | Reports: read |
| Reads of check-ins (where a staff member was) | `ADMIN`, `STORE_MANAGER`, `AUDITOR` | New, and narrowed: staff location data |
| Floor writes: `POST /v1/sales`; `POST /v1/customers` and `PATCH /v1/customers/{customer_id}`; `PUT /v1/preference-profiles/{customer_id}/preferred-channels` and `/interest-categories`; `POST /v1/check-ins`; `POST /v1/engagement-events` | `ADMIN`, `STORE_MANAGER` | New. `STORE_MANAGER` is the floor role (ADR-0041, following ADR-0023). The web still refuses these to `STORE_MANAGER` |
| `POST /v1/sale-reversals` | `ADMIN`, `STORE_MANAGER` for a cancellation or a return; `SALES_ASSOCIATE` (ADR-0060) for a cancellation only | ADR-0062. A return is refused to the seller with `forbidden_role` |
| Check-in reviews and engagement voids | `ADMIN` | Administrative correction |
| Reads of users and roles | `ADMIN`, `AUDITOR` | Users: full for `ADMIN`, read for `AUDITOR` |
| Writes of users, password resets of another user, and `POST /v1/administrator-transfers` | `ADMIN` | Users: full |
| `GET /v1/users/me`; `PUT /v1/users/{user_id}/password` for the caller's own id, with the current password | Every authenticated role, `CUSTOMER` included | Users: own |
| `GET` and `DELETE /v1/sessions/current` | Every authenticated role | — |
| Reads of campaigns | `ADMIN`, `ANALYST`, `MARKETING`, `AUDITOR` | Campaigns: read |
| Writes and transitions of campaigns | `ADMIN`, `MARKETING` | Campaigns: full |
| `GET /v1/audit-entries` | `ADMIN`, `AUDITOR` | Audit log: read |
| `POST /v1/sessions`, `POST /v1/sessions/refresh`, `GET /v1/jwks.json`, meta routes | No token | — |

- `CUSTOMER` reaches only sign-in, refresh, its own session, `GET /v1/users/me`
  and its own password. The web's dormant `user.self` is unchanged.
- The floor writes are new for `STORE_MANAGER`, the Android floor role
  ([ADR-0041](adr/0041-android-and-electron-clients-run-different-processes-over-different-formats.md)).
  The web still refuses them to that role.
- Two reads are narrower than the Reports column above. Rows that name a
  customer (sales and engagement events) follow `segment.read`, which keeps
  `INVENTORY_PLANNER` out of customer data. Check-ins, which record where a
  staff member was, are read by `ADMIN`, `STORE_MANAGER` and `AUDITOR` only.
- No operation is narrowed to a store: `app_user` has no store (ADR-0010).
- `INVENTORY_PLANNER`'s `inventory` write stays deferred
  ([`roadmap.md`](roadmap.md)), and no operation lists it.

## 4. Traceability

Demonstration item → requirement → story → issue. A row with nothing behind it
is a gap, and the point of the table is that the gap is visible.

| Demonstration item | Requirements | Story | Issue | State |
|---|---|---|---|---|
| Inicio de sesión | RF-01, RF-02 | F3-03 | #63, #251 | Built — login, logout and argon2 password hashing; server-side sessions revoked on sign-out (ADR-0022, #251) |
| Acceso diferenciado por perfil | RF-03, RF-04, RF-05 | F4-01, F4-02 | #69, #70 | Built — the middleware (#69) and the single administrator (#70) |
| Operación de catálogos | RF-06, RF-07, RF-08, RF-09 | F3-04, F3-06, F3-07 | #64, #66, #67, #251, #289 | Built — CRUD over every catalog (#64) and image upload (#67); user management (#66) shipped create/deactivate/reactivate only, RF-09's edit was missing until #289 closed it; deactivation ends open sessions (#251) |
| Ejecución de un proceso principal | RF-12 | F3-10 | #102 | Built — quintile R/F/M over a configurable window; widened in Delivery 2 to `RFM_RULES` or `KMEANS` with append-only assignment history (see the analytics traceability below) |
| Almacenamiento en PostgreSQL | RNF-08, RNF-09, RNF-10 | F2-04, F2-05, F2-06 | #57, #58, #59 | Built — the three scripts, verified in [`evidence/f1-04-f1-05-postgresql-access.md`](evidence/f1-04-f1-05-postgresql-access.md) |
| Consulta de información | RF-10, RF-11, RF-13 | F3-05 | #65 | Built — the `/catalog` consultation module: products, customers, stock and segment membership, read-only |
| Registro de auditoría | RF-14, RNF-17 | F3-11 | #103 | Built — the log is readable from the application |
| Ejecución mediante contenedores | RNF-13, RNF-14 | F3-09 | #89 | Built — `compose.yaml`, with `compose.proxy.yaml` for the NGINX path |

Every demonstration item above is built. What is not settled is the evidence
for two of them, and that is the row to read below.

Five stories carry no demonstration item of their own but every item passes
through them:

| Story | Issue | Why it is here |
|---|---|---|
| F3-12 | #106 | The shell a signed-in user lands on. Every item above is reached through its navigation, and nothing else builds it |
| F4-06 | #107 | The deployed instance must not be reachable with the password this repository publishes for the seeded administrator |
| F5-04 | #108 | Deliverable 10 — screenshots. F5-01 captures test output, which is not the same thing. Done: [`evidence/f5-04-key-functionality.md`](evidence/f5-04-key-functionality.md) |
| F6-07 | #109 | Deliverable 13 — proof of deployment. [`evidence/f6-07-proof-of-deployment.md`](evidence/f6-07-proof-of-deployment.md) records all five acceptance criteria; container execution was captured on a developer machine per [ADR-0015](adr/0015-containers-are-a-development-path-only.md) |
| F6-08 | #110 | The demonstration itself, rehearsed in sequence on the instance. It is the one deliverable that cannot be corrected after submission |

### Analytics traceability (Delivery 2, phases 7–12)

The table above is the first delivery's demonstration list. The analytics
capabilities have no demonstration item of their own, so each is traced from its
requirement: requirement → product story → delivery story or issue → automated
tests → observed evidence. Every route is also checked against the permission
map ([`analytics-permission-map.md`](analytics-permission-map.md), F4-07 #202)
by `tests/test_analytics_authz.py` (F4-08 #231), which fails the build when an
analytics route is missing from it.

An evidence cell says what exists. Where a capability has no evidence document of
its own, the cell says so and names #443 (AN-10), the issue that adds it, rather
than citing a file that does not exist. The AN-02 proof of deployment
([`evidence/an-02-proof-of-deployment.md`](evidence/an-02-proof-of-deployment.md))
shows only that each page answered 200 on the published host.

| Requirement | Story | Delivery story or issue | Automated tests | Evidence |
|---|---|---|---|---|
| RF-12 | HU-12 | F3-10 (#102), F7-01 (#203), F7-02 (#204), F9-01–F9-03 (#214–#216), F9-05 (#336), #335, #352, #519 | `tests/test_segment_run.py`, `tests/test_segment_run_kmeans.py`, `tests/test_segmentation_pipeline.py`, `tests/test_segments_pipeline_db.py`, `tests/test_kmeans.py`, `tests/test_kmeans_adapter.py`, `tests/test_kmeans_run.py`, `tests/test_cluster_labels.py`, `tests/test_seed_segment_rules.py` | [`f3-10-segment-run.md`](evidence/f3-10-segment-run.md) (the first-delivery slice, before assignment history), [`f9-01-rfm-rules-adapter.md`](evidence/f9-01-rfm-rules-adapter.md), [`f9-02-kmeans-fit.md`](evidence/f9-02-kmeans-fit.md), [`f9-03-cluster-labels.md`](evidence/f9-03-cluster-labels.md), [`f9-05-kmeans-variable-k.md`](evidence/f9-05-kmeans-variable-k.md), [`f352-quintile-ties.md`](evidence/f352-quintile-ties.md), [`f345-segment-rule-bands.md`](evidence/f345-segment-rule-bands.md) |
| RF-16 | HU-17 | F7-02 (#204), F7-03 (#205), F7-07 (#337), #447 | `tests/test_run_history.py`, `tests/test_customer_timeline.py`, `tests/test_customer_timeline_db.py`, `tests/test_customer_timeline_route.py`, `tests/test_segments_db.py`, `tests/test_history_protection.py` | Partial: [`f350-history-protection.md`](evidence/f350-history-protection.md) (history closed and never rewritten), [`f7-08-migration-explanation-links.md`](evidence/f7-08-migration-explanation-links.md) (the customer timeline's change links) and [`f9-05-kmeans-variable-k.md`](evidence/f9-05-kmeans-variable-k.md) (a K-means run's detail). The run-history list and the timeline's as-of lookup have none of their own — #443 |
| RF-17 | HU-18 | F7-04–F7-06 (#206–#208), F7-08 (#338), #339 | `tests/test_segment_migration.py`, `tests/test_migration_matrix.py`, `tests/test_migration_matrix_route.py`, `tests/test_migration_explanation.py`, `tests/test_migration_explanation_route.py` | Partial: [`f7-08-migration-explanation-links.md`](evidence/f7-08-migration-explanation-links.md) (the explanation, reached from the matrix and the timeline). The matrix and the migration classification (F7-04, F7-05) have none of their own — #443 |
| RF-18 | HU-19 | F8-01 (#209), F8-02 (#210), #334, #355, #356, #362 | `tests/test_sales_csv.py`, `tests/test_ingestion_service.py`, `tests/test_sales_import_route.py`, `tests/test_sales_loads_db.py`, `tests/test_sales_db.py` | Partial: [`f355-f356-sales-ingestion-guards.md`](evidence/f355-f356-sales-ingestion-guards.md) (the ingestion guards). No capture of an import or its rejection report exists — #443 |
| RF-19 | HU-20 | F8-03–F8-05 (#211–#213), F8-06 (#341) | `tests/test_consumption_profile.py`, `tests/test_consumption_db.py`, `tests/test_consumption_shift.py`, `tests/test_consumption_shift_db.py`, `tests/test_customer_profile_route.py`, `tests/test_consumption_profile_shifts_route.py` | [`f8-03-consumption-profile.md`](evidence/f8-03-consumption-profile.md), [`f8-04-consumption-profile-view.md`](evidence/f8-04-consumption-profile-view.md), [`f8-05-consumption-shifts.md`](evidence/f8-05-consumption-shifts.md), [`f8-06-profile-shifts.md`](evidence/f8-06-profile-shifts.md) |
| RF-20 | HU-21 | F9-04 (#217) | `tests/test_model_comparison.py`, `tests/test_model_comparison_route.py` | [`f9-04-model-comparison.md`](evidence/f9-04-model-comparison.md) |
| RF-21 | HU-22 | F10-01 (#218), F10-02 (#219), #277, #280 | `tests/test_recommendations.py`, `tests/test_recommendations_db.py`, `tests/test_customer_recommendations_route.py` | [`f10-01-recommendations.md`](evidence/f10-01-recommendations.md), [`f10-02-customer-recommendations.md`](evidence/f10-02-customer-recommendations.md), [`recommendations-category-subtree.md`](evidence/recommendations-category-subtree.md), [`store-manager-segment-read.md`](evidence/store-manager-segment-read.md) |
| RF-22 | HU-23 | F11-01 (#220), F11-02 (#221) | `tests/test_campaigns.py`, `tests/test_experiments.py` | Partial: [`f11-03-experiment-setup.md`](evidence/f11-03-experiment-setup.md) shows a campaign refused activation while an attached experiment lacks a group. The campaign list, form and transitions have none of their own — #443 |
| RF-23 | HU-24 | F11-01 (#220), F11-03 (#222), #342, #427 | `tests/test_experiments.py`, `tests/test_experiment_treatment_arms.py`, `tests/test_experiment_frame.py` | [`f11-03-experiment-setup.md`](evidence/f11-03-experiment-setup.md), [`f357-experiment-frame-and-exposure.md`](evidence/f357-experiment-frame-and-exposure.md) |
| RF-24 | HU-25 | F11-04–F11-06 (#223–#225), #357 | `tests/test_experiment_assignment.py`, `tests/test_experiment_exposure.py`, `tests/test_experiment_conversion.py`, `tests/test_experiment_frame.py` | [`f11-04-group-assignment.md`](evidence/f11-04-group-assignment.md), [`f11-05-experiment-exposure.md`](evidence/f11-05-experiment-exposure.md), [`f11-06-experiment-conversion.md`](evidence/f11-06-experiment-conversion.md), [`f357-experiment-frame-and-exposure.md`](evidence/f357-experiment-frame-and-exposure.md) |
| RF-25 | HU-26 | F11-07 (#226), #343 | `tests/test_experiment_uplift.py`, `tests/test_exposed_conversion.py` | [`f11-07-experiment-uplift.md`](evidence/f11-07-experiment-uplift.md), [`f343-exposed-conversion.md`](evidence/f343-exposed-conversion.md) |
| RF-26 | HU-27 | F12-01 (#227), #340, #423, AN-20 (#560) | `tests/test_segmentation_dashboard.py`, `tests/test_segmentation_dashboard_db.py`, `tests/test_segmentation_dashboard_route.py`, `tests/test_dashboard_kpis.py` | [`f12-01-segmentation-dashboard.md`](evidence/f12-01-segmentation-dashboard.md), [`f340-dashboard-kpis.md`](evidence/f340-dashboard-kpis.md), [`f352-quintile-ties.md`](evidence/f352-quintile-ties.md) |
| RF-27 | HU-28 | F12-02 (#228) | `tests/test_segment_history_report.py`, `tests/test_segment_history_report_db.py`, `tests/test_segment_history_report_route.py` | [`f12-02-segment-history-report.md`](evidence/f12-02-segment-history-report.md) |
| RF-28 | HU-29 | F12-03 (#229) | `tests/test_consumption_reports.py`, `tests/test_consumption_reports_db.py`, `tests/test_consumption_reports_route.py` | [`f12-03-consumption-reports.md`](evidence/f12-03-consumption-reports.md) |
| RF-29 | HU-30 | F12-04 (#230), #358 | `tests/test_experiment_report.py` | [`f12-04-experiment-report.md`](evidence/f12-04-experiment-report.md) |
| RF-30 | HU-31 | AN-03 (#415) | `tests/test_experiment_dashboard.py`, `tests/test_experiment_dashboard_route.py` | [`an-03-experiment-dashboard.md`](evidence/an-03-experiment-dashboard.md) |
| RF-31 | HU-32 | #344, AN-20 (#560) | `tests/test_reports_index.py` | None of its own — #443 (the AN-02 proof shows only that `/reports/` answered 200) |

The web system's boundary holds across all of this: it reads and writes
PostgreSQL directly, connects to no Redis and consumes no service (RNF-02). The
distributed stage's services run beside it and are specified in
[`scope-delivery-2-distributed.md`](scope-delivery-2-distributed.md); they are
not part of this table.
