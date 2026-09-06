# Requirements

What MOSAIQ must do, and what must be true of how it does it. Deliverable 1 in
[`scope.md`](scope.md) §5.

Every requirement here is inside the boundary in [`scope.md`](scope.md). Where a
requirement is a deliberately small slice of something larger that is deferred,
it says so and names what was left out — a requirement that quietly promises
less than its title suggests is worse than one that is honestly narrow.

Ids are stable. `RF` functional, `RNF` non-functional. Stories that build them
are in [`user-stories.md`](user-stories.md); invariants they rely on are in
[`business-rules.md`](business-rules.md).

---

## 1. Functional requirements

| Id | Requirement | Demonstration item | Story |
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
| RF-12 | The administrator runs a segment recalculation: Recency, Frequency and Monetary are scored per customer over a window of recorded sales, matched against the bands in `segment_rule`, and the resulting segment is written to the customer | Ejecución de un proceso principal | F3-10 (#102) |
| RF-13 | A user consults which customers are in a segment, and which segment a customer is in | Consulta de información | F3-05 (#65) |
| RF-14 | The administrator and the auditor read the log of changes to catalogs and business rules, filtered by entity and date, with before and after values | Registro de auditoría | F3-11 (#103) |
| RF-15 | A failure the user caused shows a page explaining what to do; a failure they did not shows a controlled error page and is logged with enough detail to diagnose. Neither shows a stack trace | — | F4-05 (#73) |

**RF-12 is a deliberately narrow slice.** [`roadmap.md`](roadmap.md) defers RFM
computation, clustering, segment history and migration reporting. What this
delivery promises is quintile scoring over a configurable window and a match
against the rule bands already in the schema. What it does not promise: k-means,
history, migration reports, dashboards. See
[ADR-0004](adr/0004-model-ahead-of-the-deferred-segmentation-modules.md).

## 2. Non-functional requirements

Each states how it is checked. A quality nobody can verify is a wish.

| Id | Requirement | How it is verified |
|---|---|---|
| RNF-01 | The browser receives server-rendered HTML. No JSON or XML is exchanged between internal components (`scope.md` C-2) | No serialization endpoint exists; every route returns a rendered template. Reviewed per pull request |
| RNF-02 | One deployable unit. No microservices, no external REST, GraphQL or SOAP API (C-1, C-3, C-7) | One application package, one process. A second deployable unit needs an ADR |
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

## 3. Permission matrix

The canonical statement of who may do what. [`data-model.md`](data-model.md)
links here rather than repeating it, because two copies of a matrix drift.

`full` create, read, update and delete · `read` read only · `own` only their own
row · `—` no access.

| Role | Catalogs | Users | Segments and rules | Campaigns | Segment run | Reports | Audit log |
|---|---|---|---|---|---|---|---|
| `ADMIN` | full | full | full | full | run | read | read |
| `ANALYST` | read | — | read | read | — | read | — |
| `STORE_MANAGER` | read | — | — | — | — | read, own store | — |
| `MARKETING` | read | — | read | full | — | read | — |
| `INVENTORY_PLANNER` | read, `inventory` write | — | — | — | — | read | — |
| `AUDITOR` | read | read | read | read | — | read | read |
| `CUSTOMER` | — | own | — | — | — | — | — |

Three things this matrix is deliberately strict about:

- **Only `ADMIN` runs the segment recalculation.** It rewrites a column on every
  customer, and an analyst who can trigger it can change what every report says.
- **`AUDITOR` reads the audit log and cannot write anywhere.** An auditor who can
  edit the thing they audit is not an auditor.
- **`CUSTOMER` reaches their own row and nothing else.** It is the role a loyalty
  customer signs in with, not a staff role.

Enforced by the authorization middleware at the route level — F4-01 (#69),
which transcribes this table into `web/middleware/authz.py` and refuses any
route that declares nothing. Where a cell says `own` or `own store`, the gate
decides that the page may be reached at all; narrowing the query to the
caller's own rows belongs to the story that writes the query, F3-05 (#65) and
F3-11 (#103). Why the matrix is code rather than two more tables:
[ADR-0007](adr/0007-permissions-in-code-with-a-default-deny-middleware.md).

## 4. Traceability

Demonstration item → requirement → story → issue. A row with nothing behind it
is a gap, and the point of the table is that the gap is visible.

| Demonstration item | Requirements | Story | Issue | State |
|---|---|---|---|---|
| Inicio de sesión | RF-01, RF-02 | F3-03 | #63 | Open, not started |
| Acceso diferenciado por perfil | RF-03, RF-04, RF-05 | F4-01, F4-02 | #69, #70 | RF-03 and RF-04 built (#69); RF-05 open (#70) |
| Operación de catálogos | RF-06, RF-07, RF-08, RF-09 | F3-04, F3-06, F3-07 | #64, #66, #67 | Open, not started |
| Ejecución de un proceso principal | RF-12 | F3-10 | #102 | Open, just written |
| Almacenamiento en PostgreSQL | RNF-08, RNF-09, RNF-10 | F2-04, F2-05, F2-06 | #57, #58, #59 | In review (#98) |
| Consulta de información | RF-10, RF-11, RF-13 | F3-05 | #65 | Open, not started |
| Registro de auditoría | RF-14, RNF-17 | F3-11 | #103 | Open, just written |
| Ejecución mediante contenedores | RNF-13, RNF-14 | F3-09 | #89 | Open, rewritten |

**Everything except the database is unbuilt, and all of it is behind F3-02
(#62)** — the database connection. That story is closed on the board with only a
Node.js file behind it, and nothing else can start until it is real.

Four stories carry no demonstration item of their own but every item passes
through them:

| Story | Issue | Why it is here |
|---|---|---|
| F3-12 | #106 | The shell a signed-in user lands on. Every item above is reached through its navigation, and nothing else builds it |
| F4-06 | #107 | The deployed instance must not be reachable with the password this repository publishes for the seeded administrator |
| F5-04 | #108 | Deliverable 10 — screenshots. F5-01 captures test output, which is not the same thing |
| F6-07 | #109 | Deliverable 13 — proof of deployment. F6-02 makes it survive a reboot; nothing recorded that it does |
| F6-08 | #110 | The demonstration itself, rehearsed in sequence on the instance. It is the one deliverable that cannot be corrected after submission |
