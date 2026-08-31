# ADR-0001 — Flask monolith on a single Compute Engine instance

**Status:** Accepted
**Owner:** Marcelo
**Supersedes:** the four-component architecture and ADRs 0001–0004 of the previous scope

---

## Context

The exercise scope was replaced. The project is now a single monolithic web
application on one GCP Compute Engine instance running CentOS 10 Stream, with
PostgreSQL installed on the same machine, published on an assigned host.
External APIs, microservices, JSON/XML exchange between internal components, and
managed cloud databases are all prohibited.

The previous architecture was a monorepo of four deployable components — a Flask
web system, thirteen Flask microservices, an Android client and a desktop
client — over PostgreSQL, MongoDB and Redis, with JWT authentication shared
through `services/_shared/` and content negotiation between JSON and XML. Every
one of those elements is now either out of scope or explicitly prohibited.

The scope statement illustrates the target with Node.js and Express, and lists
`express`, `ejs`, `pg`, `bcrypt`, `express-session`, `multer` and `morgan` as the
dependencies. It also uses an online bookstore as its worked example. The
bookstore is plainly an illustration rather than a requirement, which leaves the
question of whether the runtime is a requirement or part of the same
illustration.

## Decision

We build the monolith in **Python 3.12 with Flask and Jinja2**, server-rendered,
on one Compute Engine instance with PostgreSQL installed locally. The four
extra components, the two extra data stores, and the JWT/content-negotiation
contracts are retired rather than deferred.

The scope constrains the *architecture* — one deployable unit, no external APIs,
no internal JSON exchange, one database engine, one administrator. Flask
satisfies every one of those constraints exactly as Express would. What the
scope names as technology is the illustration; what it names as architecture is
the requirement.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Node.js + Express + EJS, exactly as the statement lists | The team's existing Python experience is the deciding factor. A stack switch would spend the first phase relearning tooling that has nothing to do with what the exercise grades: the 4NF model, the roles, and the deployment. Nothing in the scope depends on the runtime |
| Keep the four-component architecture and treat the monolith as one part of it | Directly prohibited. Constraints C-1, C-2, C-3 and C-7 rule out external APIs, internal JSON exchange, multiple applications and microservices |
| Keep the retired documentation in place, marked as deferred | The four-component design was roughly 4,000 lines describing components that no longer exist. Documentation describing a system nobody is building is read as the current design and misleads anyone who joins later. Git history preserves it at no cost |

## Consequences

**What this makes easy.** One deployable unit, one database engine, one
authentication mechanism, one place where a request is handled. The team keeps
its Python tooling — `black`, `ruff`, `pytest` — and the standards already
agreed.

**What this makes hard.** The deferred analytics modules re-enter as modules of
this monolith rather than as services, so they inherit its process model. If a
clustering run turns out to be long enough to block a request, it needs a
background worker inside the same application rather than a separate service.

**What must now be true elsewhere.** Schema changes ship as edits to
`sql/01_schema.sql` rather than as Alembic migrations, and the three SQL scripts
must run clean in order against an empty database. The Definition of Done in
[`../process.md`](../process.md) §6 already reflects this.

## Compliance

- The repository contains exactly one deployable application directory, `web/`.
- No route returns JSON to another internal component; the browser receives HTML.
- `sql/00_create_database.sql`, `01_schema.sql` and `02_seed_30_per_table.sql`
  run in order against an empty PostgreSQL and produce a working database.
- CI runs `black --check .`, `ruff check .` and `pytest`.
