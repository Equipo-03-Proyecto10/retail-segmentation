# Agent Instructions

Retail segmentation platform on a single GCP Compute Engine instance, backed by
PostgreSQL on the same machine. Course project, Team 03.

There are four components:
- **The web system:** a monolithic Flask application.
- **Fifteen microservices:** Flask and Rust, with Redis and MongoDB.
- **An Android client** in Kotlin.
- **An Electron desktop client.**

Read the boundary of the work you are touching before starting:
- [`docs/scope.md`](docs/scope.md): the first delivery, the web system.
- [`docs/scope-delivery-2-analytics.md`](docs/scope-delivery-2-analytics.md):
  the web system's analytics.
- [`docs/scope-delivery-2-distributed.md`](docs/scope-delivery-2-distributed.md):
  services and clients.

## Language

Every artifact is written in English: code, comments, commit messages, branch
names, issue titles, documentation. Spoken meetings are in Spanish; nothing
written is.

## Hard rules

**The web system is one application, and stays one.** Its pages are
server-rendered with Jinja2, and the browser receives HTML. It connects to no
Redis and consumes no microservice. It reads and writes PostgreSQL directly.

**Services live in `services/`, under ADR-0034 to ADR-0046, and only after those
records are accepted.**
- There are at least ten microservices (ADR-0043), and fifteen are planned
  (ADR-0034). Each implements exactly one business rule, never mixed, and is
  registered in `contracts/registry.yaml` with that rule, its RN ids and its
  top-level resources. A new service needs an ADR naming its single rule.
  A deployable unit of any other kind needs an ADR too.
- Never mix endpoints. Each top-level resource, the first path segment after
  `/v1`, belongs to exactly one service. A foreign id appears only as a query
  parameter or a body field. No service exposes, nests, proxies, duplicates or
  re-serves another service's resource (ADR-0035).
- Services never import or copy `web/`. A service's build context is its own
  directory. A rule shared with the web is carried by the database, or by a
  port pinned with golden fixtures (ADR-0045).
- Services never touch another boundary's tables. Each connects as its own
  `svc_*` role, which holds privileges on its own tables only, and reads
  another boundary only through that producer's versioned
  `api_<boundary>.*_v1` views and functions (ADR-0034, ADR-0044). New
  service-owned tables go in service schemas, never in `public` (ADR-0046).
  Services never call each other over HTTP.
- Events between services travel only on Redis Streams, relayed by the owner
  from its own transactional outbox. `pg_notify` is only an owner's private
  wake-up for its relay; no service listens on another boundary's channel
  (ADR-0044).
- Each service builds, versions and deploys alone: its own lock file
  (`uv.lock` or `Cargo.lock`), a semver `VERSION` file, no shared Cargo
  workspace, and its own compose project `mosaiq-<name>` with no `depends_on`
  on another service (ADR-0040).

**Contracts come before code.** A service's OpenAPI, XSD and examples in
`contracts/` merge before its implementation, and before any client story that
consumes them (ADR-0035). Every service answers in JSON **and** XML. Android
consumes JSON only. Electron consumes XML only, validated against the XSD.

**Schema changes go in `sql/01_schema.sql`.** Never alter a table by hand on the
server and never write DDL anywhere else. The three scripts —
`00_create_database.sql`, `01_schema.sql`, `02_seed_30_per_table.sql` — must run
clean in that order against an empty PostgreSQL. A change that breaks a clean
run is a broken change, not a migration problem.

**The model is normalized to 4NF, and each normalization decision is written
down.** The justification is a graded deliverable, not a comment.

**Seed data keeps at least 30 rows per table.** A new table ships with its seed
rows in the same pull request. A table whose real domain is smaller — `role`
has seven, `channel` five — is exempted by naming it in `sql/seed-exempt.txt`
with the reason. CI reads that file (RNF-09). Padding a catalog with filler to
reach the minimum is the thing the exemption exists to prevent.

**There is exactly one administrator.** Enforced twice: refused by the
application, and refused by a partial unique index in the schema. The
application check alone is bypassed by a direct `INSERT`; the index alone
surfaces as an unexplained database error. Both, or neither counts. This holds for
users-service too: it enforces never-two and never-zero in its own code, moves
the role only through an explicit administrator transfer, and maps the index's
refusal to a 409, never a 500.

**Every SQL statement is parameterized.** No string interpolation into SQL, ever,
including in scripts, one-off queries and Rust services.

**Tokens are RS256, and only auth-service signs them.** They carry the role
code and no permissions; each service owns its own role-to-operation policy.
Every other service verifies them with the public key, pins `RS256`, checks
`iss`, `aud` and `exp`, and checks that the session's liveness key exists in
Redis. When Redis is unreachable, authentication fails closed with a 503
(ADR-0036, ADR-0037).

**No secrets in source.** New configuration goes in `.env.example` with a safe
default.

**Never commit datasets or uploaded files.** `data/`, `*.csv` and `web/uploads/`
are gitignored.

## Conventions

- Python: PEP 8, `black`, `ruff`. Tables are snake_case and singular.
- Rust: `cargo fmt`, and `cargo clippy --all-targets -- -D warnings`.
  `Cargo.lock` is committed.
- Kotlin (Android): ktlint. Versions are pinned in the Gradle version catalog.
- TypeScript (Electron): ESLint and Prettier. Every `BrowserWindow` keeps
  `contextIsolation: true`, `nodeIntegration: false` and `sandbox: true`.
- Commits: Conventional Commits, `type(scope): subject (#issue)`.
  Example: `feat(auth): add password reset (#42)`
- Branches: `feature/<issue-number>-kebab-slug`, also `fix/`, `chore/`, `docs/`.
- `main` is protected. `develop` is the integration branch and also requires a
  reviewed pull request.

## Before reporting a task complete

1. `pytest` passes.
2. `black --check .` and `ruff check .` pass.
   - A Rust crate also needs `cargo fmt --check`, `cargo clippy --all-targets
     -- -D warnings` and `cargo test` to pass.
   - The Android client: `./gradlew ktlintCheck lint testDebugUnitTest`.
   - The Electron client: its lint, type-check and tests.
   - A contract: its OpenAPI validates and its XML examples validate against
     their XSD.
3. Any schema change is reflected in `sql/01_schema.sql`, and the three scripts
   still run clean in order from an empty database.
4. New configuration is documented in `.env.example`.
5. The Definition of Done in [`docs/process.md`](docs/process.md) §6 is met.

## Where decisions live

Architectural decisions go in [`docs/adr/`](docs/adr/) as numbered ADRs. ADRs
are immutable once accepted — supersede, never edit. Do not make an
architectural decision silently in code; write the ADR or ask.

Work deferred to a later delivery is recorded in
[`docs/roadmap.md`](docs/roadmap.md). Do not build it early.
