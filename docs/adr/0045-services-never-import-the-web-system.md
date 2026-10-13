# ADR-0045 — Services never import the web system

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The first draft of the distributed decision set told services to reuse the
web's transport-free modules in `web/services/` and `web/db/` instead of
reimplementing a business rule. `AGENTS.md` still says so, and so does C-4 in
[`scope-delivery-2-distributed.md`](../scope-delivery-2-distributed.md), which
has users-service reuse `web/services/users.py`. The aim was sound: one
implementation of each rule. It does not survive contact with the code or with
the stage's requirements.

**Independence.** The instructor requires each service to be built, versioned,
deployed and rolled back on its own. A service that imports `web/` changes
whenever the web changes. It cannot pin a version of the web, either, because
the web has none: `pyproject.toml` has no `[project]` table ("The application
is not packaged"), so `web/` is a directory, not a release.

**What an import actually pulls in.**
- `web/db/__init__.py` imports `flask` and `web.config` at package import, so
  `from web.db.users import ...` brings Flask and the web's configuration into
  a service that uses neither.
- `web/services/users.py` imports `web.services.auth` and
  `web.db.sessions.revoke_user_sessions`. Its `set_active` calls that function,
  which runs `UPDATE app_session`. `app_session` is a web-only table on which no
  `svc_*` role holds any privilege
  ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)).
  Reusing the module would mean granting a service a web-only table, or a
  runtime failure on the first deactivation.

**The web is frozen.** It cannot be refactored into a library first. It may
receive bug fixes and the analytics gaps of epic #405, nothing that reshapes it
for a second consumer.

**Language.** Six of the fifteen services are Rust: stores, stock,
customer-preferences, segments, check-ins and audit-trail
([ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)).
They could never import Python, so for them the reuse rule offered nothing.

Every service still needs the same mechanics: RS256 verification, the liveness
check, Accept negotiation, JSON and XML, the error envelope, pagination and
health. Fifteen hand-written copies of the token check would drift into fifteen
security postures.

The real uncertainty is the cost. Some rules the web already implements are
ported rather than shared, and then two implementations exist. This record
cannot make that cost go away. It can only make drift visible.

## Decision

No service imports, copies or builds from `web/`. Each service is built from its
own directory, with its own lock file and `VERSION`. A rule that services share
with the web is carried either by the database, which both writers pass
through, or by a port in the service, pinned to the web's behaviour by golden
fixtures captured once from the web on the seed database. Mechanics common to
all services come from two mechanism-only kits, released by tag and pinned per
service. The tables below are the detail.

### How a shared rule is carried

**By the database.** These need no port, because every writer, web or service,
passes through them:
- the CHECKs in `sql/01_schema.sql`;
- `ux_app_user_single_administrator` and `ux_app_user_email_lower`;
- `trg_role_code_immutable` and `trg_category_no_cycle`, plus
  `trg_category_delete_guard`, which this stage adds;
- the foreign keys with `ON DELETE RESTRICT`;
- `quantity_on_hand >= 0`;
- `api_stock.consume_v1`;
- `fn_audit`.

**By a port pinned with golden fixtures:**

| Port | Web source captured | What the fixtures pin | Service |
|---|---|---|---|
| Sales line rejection outcomes | `ingest_row` in `web/services/ingestion.py` | The outcome for each of a fixed set of lines: accepted, or the rejection reason. Covers future-dated, before registration, inactive product, non-positive quantity, non-positive price, more than two decimals, unknown references and insufficient stock | sales |
| RN-40 recommendation lists | `recommend` in `web/services/recommendations.py`, with the `web/db/` and `web/services/consumption_profile.py` reads it uses | The status, items, nearest category and ordered reasons for **every seeded customer** | recommendations |
| Users outcomes | `create_user`, `update_user`, `change_role`, `set_active`, `transfer_administrator` and `validate_user` in `web/services/users.py` | The accept or refuse outcome for each scenario: a second administrator, removing the only one, a transfer, a case-variant duplicate email, a malformed email | users |
| Campaign transitions | `TRANSITIONS`, `transition`, `update_draft` and `validate_campaign` in `web/services/campaigns.py` | Every (from, to) pair, the draft-only edit rule and the end-before-start refusal | campaigns |

**How fixtures are captured and kept:**
- A capture script runs the web code against a fresh database built by the
  three scripts. It lives in `tests/golden/`, outside `services/`, `packages/`
  and `bench/`, because it is the one place that may import `web/`.
- It writes JSON under `services/<name>/tests/golden/`. Each file records the
  web commit, the capture date, and the SHA-256 of `02_seed_30_per_table.sql`.
- Service CI runs the port against the fixtures. It never runs web code.
- Where a service deliberately differs from the web, the fixture records the
  difference with its reason instead of hiding it. For example, the service
  answers with an error code where the web has a message.

### The kits

| | `packages/kit-py` (`mosaiq-kit`) | `packages/kit-rs` |
|---|---|---|
| Packaging | Its own `pyproject.toml` with a `[project]` table and a semver version | Its own crate and `Cargo.lock` |
| Released as | Tag `kit-py/vX.Y.Z`, with a changelog | Tag `kit-rs/vX.Y.Z`, with a changelog |
| Pinned by a service as | A git dependency at an exact tag, locked in its `uv.lock` | `tag = "kit-rs/vX.Y.Z"` in its `Cargo.toml` |

Both kits contain only:
- RS256 verification with JWKS file reload;
- the liveness check;
- Accept negotiation;
- JSON and XML rendering against an XSD the service supplies;
- the error envelope;
- pagination, with bounds the service supplies;
- health handlers;
- the correlation id;
- a rate limiter;
- the outbox relay and the stream consumer helpers;
- the transaction-local audit-actor helper (`set_config('mosaiq.user_id', ...)`).

They contain:
- no role or permission names;
- no error catalogue;
- no business rule;
- no Redis key except the published liveness family
  `pub:auth:v1:live:{sid}` and the `stream:` naming convention.

A service upgrades a kit in its own pull request, with its own `VERSION` bump.
Path dependencies on `packages/` are rejected.

### One service, one build

- Each `services/<name>/` has its own `VERSION` (semver) and its own lock file:
  `uv.lock`, or `Cargo.lock`.
- There is no Cargo workspace spanning services. A workspace would share one
  `Cargo.lock` across them.
- The Docker build context is `services/<name>/`. A Dockerfile never `COPY`s
  or `ADD`s from `web/`, or from outside its directory.
- Repository-wide `black`, `ruff` and `cargo fmt` remain the global style
  gates. They read code; they do not link it.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Services import `web/services/` and `web/db/` (the earlier draft, and the current `AGENTS.md` rule) | It drags Flask and `web.config` into every service. It makes users-service write `app_session` through `set_active`. It ties every service's build and rollback to an unversioned web. And it offers nothing to the six Rust services |
| Extract a shared domain library under `packages/` that both the web and the services use | The web is frozen and cannot be rewired to depend on it. Rust could not use it either. A domain library shared by fifteen services is the release coupling the instructor ruled out, under another name |
| Copy (vendor) the web modules into each service | The copies inherit the Flask imports and the `app_session` write. They then drift silently, because nothing records what they were copied from or checks them against it |
| Move every shared rule into PostgreSQL functions and triggers | The database already carries the invariants that fit there. RN-40 ranking or the users workflow rewritten in plpgsql would still be a port, without a fixture to check it, and it would make the one shared DDL file the place every boundary's logic changes |
| Let services call the web over HTTP for a decision | The web serves HTML, has no JSON API and is frozen. Services never call anything over HTTP |
| One kit carrying helpers for business rules too | It becomes the shared domain library under a different name. Every rule change would release a kit that fifteen services must consider |
| No kits: each service writes its own mechanics | Fifteen copies of RS256 verification and the liveness check would drift into different security behaviour. A pinned, tested kit keeps one implementation, and still lets each service upgrade on its own |

## Consequences

**What this makes easy.**
- Each service builds from one directory, versions on its own, and rolls back
  to its previous tag without touching the web or another service.
- A web bug fix cannot break a service build.
- A service carries only its own dependencies: no Flask in a service that does
  not use it, and no web configuration.
- Python and Rust services stand on the same footing. Each owns its rule's
  code.
- The token check is written twice, once per language, not fifteen times.

**What this makes hard.**
- **Ported rules have two implementations.** The web keeps its legacy code for
  sales validation, RN-40, users and campaigns, and the service has its own.
  They can diverge, and nothing prevents it. Three things catch it:
  1. **Golden tests.** In service CI, they hold each port to the web's
     behaviour *as of the capture date*.
  2. **A drift job in the web's CI.** It runs nightly, and on any pull request
     that touches a captured web file or `02_seed_30_per_table.sql`. It
     re-runs the capture on a fresh database and diffs the result against the
     committed fixtures. A difference fails the job until an issue is opened
     to regenerate the fixtures and update the port. The regeneration is its
     own pull request, because one pull request never changes `web/` and
     `services/` together.
  3. **Database invariants.** Where both writers meet the same constraint,
     the database refuses whichever one is wrong.

  What none of these catch is a difference on an input the fixtures do not
  contain. Fixtures are built from the seed database and a fixed scenario set.
  An edge case outside both can differ unnoticed. The fixtures are evidence of
  parity, not proof of it.
- **Recommendations is the largest port.** RN-40 rests on the RN-34 and RN-35
  consumption profile, and the fixtures cover every seeded customer. A change
  to the seed data makes them stale, and regenerating them is part of that
  change.
- **The kits are shared code.** A bug in a kit affects every service that
  upgrades to it. Pinning by tag means it spreads one pull request at a time,
  and each service can stay on, or roll back to, the previous tag.
- **Builds fetch the kit from the repository at its tag.** If the repository
  is private, CI passes a read-only token as a build secret. The image never
  carries it.
- **Fifteen lock files,** not one. A dependency upgrade, such as a PyJWT
  security fix, is fifteen small pull requests, or two kit releases followed
  by fifteen pins.

**What must now be true elsewhere.**
- `AGENTS.md` replaces "A service reuses the web's transport-free modules in
  `web/services/` and `web/db/`" with this record. Its single-administrator
  paragraph stops saying users-service reuses `web/services/users.py`.
- C-4 in [`scope-delivery-2-distributed.md`](../scope-delivery-2-distributed.md)
  says the same.
- [ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md):
  the kits carry the verification steps and the liveness check, and no role
  names.
- [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md):
  the kits carry the outbox relay and the stream consumer helpers, and each
  owner supplies its own stream names.
- [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md):
  each image is built from `services/<name>/`. A changed `services/<name>/`
  requires a bumped `VERSION`.
- [ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md):
  the XSD a kit renders against is the service's own.
- [ADR-0042](0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md):
  the bench twin follows the same rules, from `bench/products-service-rs/`.
- sales, recommendations, users and campaigns ship `tests/golden/` before
  their first release. `tests/golden/` holds the capture script, and the web's
  CI holds the drift job.

## Compliance

CI runs these from the repository root, and each must print nothing:

```bash
# No service, kit or bench crate imports the web.
grep -rnE '^\s*(from|import)\s+web(\.|\s|$)' services/ packages/ bench/

# Dockerfile lint: no COPY or ADD from web/, or from outside the build context.
grep -rniE '^\s*(COPY|ADD)\s.*(\bweb/|\.\./)' services/*/Dockerfile bench/*/Dockerfile

# Every compose build context is the service's own directory.
for f in services/*/compose.yaml; do
  yq -r '.services[] | select(has("build")) | (.build.context // .build)' "$f" \
    | grep -vxE '\.|\./' | sed "s|^|$f: |"
done

# Kits are pinned by exact tag. No path dependency on packages/.
grep -rnE 'path\s*=\s*"[^"]*packages/|file://|\.\./packages' \
  services/*/pyproject.toml services/*/Cargo.toml bench/*/Cargo.toml
grep -LE 'kit-(py|rs)/v[0-9]+\.[0-9]+\.[0-9]+' services/*/pyproject.toml services/*/Cargo.toml

# No Cargo workspace spans services.
grep -lE '^\[workspace\]' Cargo.toml services/*/Cargo.toml 2>/dev/null

# Each service has its own VERSION and exactly one lock file.
for d in services/*/; do
  [ -f "$d/VERSION" ] || echo "$d: no VERSION"
  n=$(ls "$d"/uv.lock "$d"/Cargo.lock 2>/dev/null | wc -l)
  [ "$n" -eq 1 ] || echo "$d: $n lock files"
done

# The kits stay mechanism-only: no role codes, and no Redis keys but the two allowed families.
grep -rnwE 'ADMIN|ANALYST|STORE_MANAGER|MARKETING|INVENTORY_PLANNER|AUDITOR|CUSTOMER' packages/
grep -rnoE "[\"'][a-z_]+:[a-z_]+:[^\"']*" packages/ | grep -vE "pub:auth:v1:live:|[\"']stream:"

# Ported rules ship their fixtures.
for s in sales recommendations users campaigns; do
  [ -d "services/$s" ] && ! ls "services/$s/tests/golden/"*.json >/dev/null 2>&1 \
    && echo "services/$s: no golden fixtures"
done
```

The drift job runs the capture in `tests/golden/` against a fresh database,
into a scratch directory. It then compares each outcome with the committed
fixture, ignoring the header fields (web commit, capture date, seed hash). Any
difference fails the job, and the failure names the fixture, the case and the
web commit the fixture was captured from.
