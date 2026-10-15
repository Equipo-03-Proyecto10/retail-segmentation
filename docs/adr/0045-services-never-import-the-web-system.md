# ADR-0045 — Services never import the web system

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The first draft of the distributed decision set, and the `AGENTS.md` and C-4 text it produced, told services to reuse the web's
transport-free modules in `web/services/` and `web/db/`. The aim was sound: one implementation of each rule. It does not survive
contact with the code or with the stage's requirements.

- **Independence.** Each service must be built, versioned, deployed and rolled back on its own. A service that imports `web/`
  changes whenever the web changes, and cannot pin a version of it: `pyproject.toml` has no `[project]` table ("The application
  is not packaged"), so `web/` is a directory, not a release.
- **What an import pulls in.** `web/db/__init__.py` imports `flask` and `web.config` at package import, so
  `from web.db.users import ...` brings Flask and the web's configuration into a service that uses neither. `web/services/users.py`
  imports `web.db.sessions.revoke_user_sessions`; its `set_active` runs `UPDATE app_session`, a web-only table no `svc_*` role may
  touch ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)), so reuse would mean
  granting a service that table, or a runtime failure on the first deactivation.
- **The web is frozen.** It cannot be refactored into a library first; it takes bug fixes and the analytics gaps of epic #405,
  nothing that reshapes it for a second consumer.
- **Language.** Six of the fifteen services are Rust
  ([ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)); they could never import Python.

Every service still needs the same mechanics: RS256 verification, the liveness check, Accept negotiation, JSON and XML, the
error envelope, pagination and health. Fifteen hand-written copies of the token check would drift into fifteen security
postures. The real uncertainty is the cost: rules the web already implements are ported, and then two implementations exist.
This record cannot remove that cost; it can only make drift visible.

## Decision

No service imports, copies or builds from `web/`. Each service is built from its own directory, with its own lock file and
`VERSION`. A rule that services share with the web is carried either by the database, for each writer its constraint or guard applies to, or by
a port in the service, pinned to the web's behaviour by golden fixtures captured once from the web on the seed database.
Mechanics common to all services come from two mechanism-only kits, released by tag and pinned per service.

### How a shared rule is carried

**By the database.** These need no port, because every writer they apply to passes through them:
- the CHECKs in `sql/01_schema.sql`, including `quantity_on_hand >= 0`;
- `ux_app_user_single_administrator` and `ux_app_user_email_lower`;
- `trg_role_code_immutable`, `trg_category_no_cycle`, and `trg_category_delete_guard`, which this stage adds;
- `trg_campaign_activation_guard`, which this stage adds for `svc_campaigns` sessions only (the web refuses the same
  move in its own code): it carries the web's
  `activation_refusal` test (RN-24), since `experiment` and `experiment_group` are web-only tables the service cannot read;
- the foreign keys with `ON DELETE RESTRICT`;
- `api_stock.consume_v1` and `fn_audit`.

**By a port pinned with golden fixtures:**

| Port | Web source captured | What the fixtures pin | Service |
|---|---|---|---|
| Sales line rejection outcomes | `ingest_row` in `web/services/ingestion.py` | The outcome for each of a fixed set of lines: accepted, or the rejection reason. Covers future-dated, before registration, inactive product, non-positive quantity, non-positive price, more than two decimals, unknown references and insufficient stock | sales |
| RN-40 recommendation lists | `recommend` in `web/services/recommendations.py`, with the `web/db/` and `web/services/consumption_profile.py` reads it uses | The status, items, nearest category and ordered reasons for **every seeded customer** | recommendations |
| Users outcomes | `create_user`, `update_user`, `change_role`, `set_active`, `transfer_administrator` and `validate_user` in `web/services/users.py` | The accept or refuse outcome for each scenario: a second administrator, removing the only one, a transfer, a case-variant duplicate email, a malformed email | users |
| Campaign transitions | `TRANSITIONS`, `transition`, `update_draft` and `validate_campaign` in `web/services/campaigns.py` | Every (from, to) pair, the draft-only edit rule, the end-before-start refusal, and the two activation refusals: an ended campaign (RN-49) and an experiment not ready (RN-24) | campaigns |

**How fixtures are captured and kept.** The capture script lives in `tests/golden/`, the one place that may import `web/`.
It writes JSON under `services/<name>/tests/golden/`, recording the web commit, the capture date and the seed hash. Service CI
runs the port against the fixtures, never web code. Where a service deliberately differs from the web, the fixture records the
difference with its reason.

### The kits

`packages/kit-py` (`mosaiq-kit`) and `packages/kit-rs` are released by tag (`kit-py/vX.Y.Z`, `kit-rs/vX.Y.Z`, with a changelog).
Each service pins an exact tag (a git dependency locked in `uv.lock`, or `tag = "kit-rs/vX.Y.Z"` in `Cargo.toml`); path dependencies are rejected.
Both contain only: RS256 verification with JWKS reload, the liveness check, Accept negotiation, JSON and XML rendering against a
service-supplied XSD, the error envelope, pagination, health, the correlation id, a rate limiter, the PostgreSQL connection pool
(at most 3 connections, over PostgreSQL's mounted Unix socket,
[ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)), which maps a connection it cannot get or
loses mid-request to 503 `database_unavailable`, the outbox relay and stream consumer helpers, and the audit-actor helper.
They contain no role or permission names, no error catalogue, no business rule, and no Redis key except `pub:auth:v1:live:{sid}` and the `stream:` naming convention.
A kit upgrade is the service's own pull request, with its own `VERSION` bump.

### One service, one build

- Each `services/<name>/` has its own `VERSION` (semver) and its own lock file: `uv.lock`, or `Cargo.lock`.
- There is no Cargo workspace spanning services; it would share one `Cargo.lock` across them.
- The Docker build context is `services/<name>/`. A Dockerfile never `COPY`s or `ADD`s from `web/`, or from outside its directory.
- CI stages `contracts/<name>/` and `contracts/common/v1/` into `services/<name>/contracts/` before the build
  ([ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)); nothing else from outside the directory enters the image.
- Repository-wide `black`, `ruff` and `cargo fmt` remain the global style gates. They read code; they do not link it.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Services import `web/services/` and `web/db/` (the earlier draft) | It drags Flask and `web.config` into every service. It makes users-service write `app_session` through `set_active`. It ties every service's build and rollback to an unversioned web. And it offers nothing to the six Rust services |
| Extract a shared domain library under `packages/` that both the web and the services use | The web is frozen and cannot be rewired to depend on it. Rust could not use it either. A domain library shared by fifteen services is the release coupling the instructor ruled out, under another name |
| Copy (vendor) the web modules into each service | The copies inherit the Flask imports and the `app_session` write. They then drift silently, because nothing records what they were copied from or checks them against it |
| Move every shared rule into PostgreSQL functions and triggers | The database already carries the invariants that fit there. RN-40 ranking or the users workflow rewritten in plpgsql would still be a port, without a fixture to check it, and it would make the one shared DDL file the place every boundary's logic changes |
| Let services call the web over HTTP for a decision | The web serves HTML, has no JSON API and is frozen. Services never call anything over HTTP |
| One kit carrying helpers for business rules too | It becomes the shared domain library under a different name. Every rule change would release a kit that fifteen services must consider |
| No kits: each service writes its own mechanics | Fifteen copies of RS256 verification and the liveness check would drift into different security behaviour. A pinned, tested kit keeps one implementation, and still lets each service upgrade on its own |

## Consequences

**What this makes easy.**
- Each service builds from one directory, versions on its own, and rolls back to its previous tag without touching the web or another service.
- A web bug fix cannot break a service build, and no service carries Flask or the web's configuration unless it uses them.
- Python and Rust services stand on the same footing; each owns its rule's code.
- The token check is written twice, once per language, not fifteen times.

**What this makes hard.**
- **Ported rules have two implementations.** The web keeps its legacy code for sales validation, RN-40, users and campaigns, and
  the service has its own. They can diverge, and nothing prevents it. Three things catch it:
  1. **Golden tests** in service CI hold each port to the web's behaviour *as of the capture date*.
  2. **A drift job in the web's CI**, nightly and on any pull request touching a captured web file or the seed script, re-runs the
     capture and fails on a difference until an issue regenerates the fixtures and updates the port, in its own pull request,
     because one pull request never changes `web/` and `services/` together.
  3. **Database invariants.** Where both writers meet the same constraint, the database refuses whichever one is wrong.

  None of these catch a difference on an input the fixtures do not contain. The fixtures are evidence of parity, not proof.
- **Recommendations is the largest port.** RN-40 rests on the RN-34 and RN-35 consumption profile; a seed change makes its
  fixtures stale, and regenerating them is part of that change.
- **The kits are shared code.** A kit bug spreads one pull request at a time; each service can stay on, or roll back to, the previous tag.
- **Builds fetch the kit at its tag.** If the repository is private, CI passes a read-only build secret the image never carries.
- **Fifteen lock files.** A dependency fix such as PyJWT is two kit releases followed by fifteen pins.

**What must now be true elsewhere.**
- `AGENTS.md` and C-4 in the scope doc state this rule (this pull request).
- [ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md): the kits carry the verification steps and the liveness check, and no role names.
- [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md): the kits carry the relay and consumer helpers; each owner supplies its own stream names.
- [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md): each image is built from `services/<name>/`, and a changed `services/<name>/` requires a bumped `VERSION`.
- [ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md): the XSD a kit renders against is the service's own.
- [ADR-0042](0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md): the bench twin follows the same rules, from `bench/products-service-rs/`.
- sales, recommendations, users and campaigns ship `tests/golden/` before their first release; the web's CI holds the drift job.

## Compliance

CI runs these from the repository root, and each must print nothing:

```bash
grep -rnE '^\s*(from|import)\s+web(\.|\s|$)' services/ packages/ bench/            # no import of the web
grep -rniE '^\s*(COPY|ADD)\s.*(\bweb/|\.\./)' services/*/Dockerfile bench/*/Dockerfile   # Dockerfile lint
for f in services/*/compose.yaml; do                                               # build context is the service's own directory
  yq -r '.services[] | select(has("build")) | (.build.context // .build)' "$f" \
    | grep -vxE '\.|\./' | sed "s|^|$f: |"
done
grep -rnE 'path\s*=\s*"[^"]*packages/|file://|\.\./packages' \
  services/*/pyproject.toml services/*/Cargo.toml bench/*/Cargo.toml               # no path dependency on packages/
grep -LE 'kit-(py|rs)/v[0-9]+\.[0-9]+\.[0-9]+' services/*/pyproject.toml services/*/Cargo.toml   # kits pinned by exact tag
grep -lE '^\[workspace\]' Cargo.toml services/*/Cargo.toml 2>/dev/null            # no Cargo workspace
for d in services/*/; do                                                           # own VERSION, exactly one lock file
  [ -f "$d/VERSION" ] || echo "$d: no VERSION"
  n=$(ls "$d"/uv.lock "$d"/Cargo.lock 2>/dev/null | wc -l)
  [ "$n" -eq 1 ] || echo "$d: $n lock files"
done
# The kits stay mechanism-only: no role codes, and no Redis keys but the two allowed families.
grep -rnwE 'ADMIN|ANALYST|STORE_MANAGER|MARKETING|INVENTORY_PLANNER|AUDITOR|CUSTOMER' packages/
grep -rnoE "[\"'][a-z_]+:[a-z_]+:[^\"']*" packages/ | grep -vE "pub:auth:v1:live:|[\"']stream:"
for s in sales recommendations users campaigns; do                                 # ported rules ship their fixtures
  [ -d "services/$s" ] && ! ls "services/$s/tests/golden/"*.json >/dev/null 2>&1 \
    && echo "services/$s: no golden fixtures"
done
```

The drift job re-runs the capture in `tests/golden/` on a fresh database into a scratch directory and compares each outcome
with the committed fixture, ignoring the header fields. Any difference fails the job, naming the fixture, the case and the
web commit it was captured from.
