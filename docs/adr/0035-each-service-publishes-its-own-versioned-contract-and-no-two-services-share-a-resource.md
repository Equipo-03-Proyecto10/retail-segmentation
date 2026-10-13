# ADR-0035 — Each service publishes its own versioned contract, and no two services share a resource

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The course scope puts contracts first: *"antes de desarrollar las aplicaciones
cliente, el equipo deberá definir los contratos de integración"*. Every service
needs versioned routes, OpenAPI, and answers in JSON **and** XML. Android
consumes JSON only. Electron consumes XML only and validates it against an XSD.

Two of the instructor's requirements, confirmed for this stage, constrain how
those contracts may be shaped:

- **Total independence.** Each service is built, versioned, deployed and
  rolled back on its own
  ([ADR-0043](0043-at-least-ten-microservices-each-implementing-one-business-rule.md)).
  A contract shared by several services cannot change for one of them without
  changing for all.
- **Never mix endpoints.** No service exposes, duplicates, proxies or nests
  another service's resources.

The first draft of this record, written for eight services, failed both. It
kept every XSD in one namespace (`.../xml/v1`) and every OpenAPI file on a
shared `_common.yaml`, so no service could version its XML alone. It also said
nothing about which service owns which path, so two services could both serve
`/v1/products`.

With fifteen services
([ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md))
there are also contracts that are not HTTP:

- the database views and functions one boundary publishes to another
  ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md));
- the Redis Streams events relayed from each owner's outbox;
- the session liveness key that every service checks
  ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)).

They need an owner and a version as much as the routes do.

Two parts of this record are judgement calls, and are stated as such below.
The first is how a resource keyed by another boundary's id differs from
nesting. The second is that additive change to an XML response is treated as
breaking.

## Decision

Each service owns one contract directory, `contracts/<service>/`. It holds:

- `openapi.yaml`, with its own `info.version`;
- `<service>.xsd`, in the namespace
  `https://mosaiq.maxthecoder.online/xml/<service>/v1`;
- `examples/`, with JSON and XML examples.

The only thing shared between contracts is `contracts/common/v1/common.xsd`,
in the namespace `.../xml/common/v1`. It holds the `Error` and `PageInfo` types,
is frozen, and is brought in with `xs:import`. Every top-level resource,
meaning the first path segment after `/v1`, is registered to exactly one
service in `contracts/registry.yaml`. A service serves only its registered
resources. It never nests under, embeds or re-serves another service's
resource, and a foreign id reaches it only as a query parameter or a body
field. Each fact has one write endpoint. A breaking change ships as `/v2`
beside `/v1`. Database, stream and liveness contracts live under
`contracts/db/`, `contracts/streams/` and `contracts/auth/`, owned by their
producer. A contract merges before its implementation, and before any client
story that consumes it.

**The resource registry.** `contracts/registry.yaml` has one entry per
`services/<name>/` directory. The entry's name is used three times: as the
contract directory `contracts/<name>/`, as the NGINX prefix `/api/<name>/`
([ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)),
and as the XML namespace segment. Each entry also carries the service's port,
its one rule with its RN ids, and its resources:

| Service | Port | Registered resources |
|---|---|---|
| auth | 5001 | `sessions`, `jwks.json` |
| users | 5002 | `users`, `roles`, `administrator-transfers` |
| products | 5003 | `products` |
| categories | 5004 | `categories` |
| stores | 5005 | `stores` |
| stock | 5006 | `stock-levels` |
| customers | 5007 | `customers` |
| customer-preferences | 5008 | `preference-profiles` |
| sales | 5009 | `sales`, `channels` |
| segments | 5010 | `segments`, `segment-assignments` |
| recommendations | 5011 | `recommendations` |
| engagement | 5012 | `engagement-events`, `engagement-summaries` |
| check-ins | 5013 | `check-ins` |
| campaigns | 5014 | `campaigns` |
| audit-trail | 5015 | `audit-entries` |

**Routing rules.**

- **Foreign ids.** A foreign id travels only as a query parameter or a body
  field, for example `/v1/stock-levels?product_id=`,
  `/v1/segment-assignments/current?customer_id=` and
  `/v1/recommendations?customer_id=`.
- **Resources keyed by another boundary's id.** A resource may be keyed by
  another boundary's id when that id *is* its own identity. Examples are
  `/v1/preference-profiles/{customer_id}`, a 1:1 profile;
  `/v1/stock-levels/{store_id}/{product_id}`, the primary key of `inventory`;
  and `/v1/stores/{store_id}/location`. What is forbidden is a later path
  segment that names a resource registered to another service, such as
  `/v1/customers/{id}/sales`.
- **No re-serving.** A representation carries other boundaries' ids, never
  their representations. A recommendation item carries `product_id`, not a
  product. A client that needs the product's name asks products-service.
- **One write endpoint per fact.**

  | Fact | Its only write endpoint |
  |---|---|
  | Recommendation click | `POST /v1/engagement-events` with type `RECO_CLICK` |
  | Purchase | `POST /v1/sales` |
  | Check-in | `POST /v1/check-ins` |
  | Store coordinates | `PUT /v1/stores/{store_id}/location` |
  | Administrator role | `POST /v1/administrator-transfers` |
  | Session revocation | auth only |
  | Own profile | `/v1/users/me` |

  Segment assignments and audit entries have no write endpoint at all.
  Their only writers are the web's administrator run and `fn_audit`.
- **Meta routes** sit outside `/v1` and outside the registry: `/health/live`,
  `/health/ready`, `/openapi.yaml` and `/schema.xsd`. None of them requires a
  token. `/openapi.yaml` and `/schema.xsd` serve the service's contract files
  byte for byte.
- **Authentication.** Every other operation declares Bearer security and
  `x-mosaiq-roles`
  ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)).
  The exceptions are `POST /v1/sessions`, `POST /v1/sessions/refresh` and
  `GET /v1/jwks.json`.
- **The Rust bench build** of the products reads implements
  `contracts/products/openapi.yaml`. It is not a registry entry, because it is
  never routed
  ([ADR-0042](0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md)).

**Formats.** These are the same in every service. The kits
([ADR-0045](0045-services-never-import-the-web-system.md)) implement the
mechanism. They carry no service's error codes.

| Concern | Convention |
|---|---|
| Negotiation | A missing `Accept`, `*/*` or `application/json` gets JSON, and `application/xml` gets XML. Any other `Accept` gets 406. Request bodies follow `Content-Type` in the same two formats. Any other `Content-Type` gets 415. A body that does not parse gets 400. A body that parses but breaks the schema gets 422 with `details` |
| XML | One root element per representation, in the service's namespace. A collection is a plural element that wraps singular elements plus a `common:PageInfo`. An absent value is an omitted element in XML and `null` in JSON. Every XML response validates against the service's XSD |
| Values | Timestamps are RFC 3339 with an offset, and are `Z` in responses. Dates are RFC 3339 `full-date`. Money is a decimal string with two places in both formats. An id keeps its owner's type: integer ids are numbers, and UUIDs and ObjectIds are strings |
| Errors | One envelope in both formats, `common:Error`: `{code, message, correlation_id, details[]}`. Each `details` item names a `field` and a `code`. `code` is stable snake_case. Each operation lists its own codes in that service's OpenAPI. There is no shared error catalogue beyond the mechanism codes: `not_acceptable`, `unsupported_media_type`, `invalid_pagination`, `session_revoked`, `auth_unavailable`, `forbidden_role` and `rate_limited` |
| Status codes | 200, 201, 204, 400, 401, 403, 404, 406, 409, 415, 422, 429 and 503. A 500 is a defect, never part of a contract |
| Pagination | `page` (≥ 1, default 1) and `size` (1–100, default 20). The response carries the items and `PageInfo` (`page`, `size`, `total`). Out-of-range values get 422 `invalid_pagination` |
| Headers | `X-Correlation-ID` is accepted, or generated when absent, and is echoed back. A 429 carries `Retry-After`. A retried mobile write carries its id as the operation declares: `Idempotency-Key` for sales, `client_event_id` for engagement, and `client_check_in_id` for check-ins |

**Versioning.** Every service versions alone.

- **`info.version`.** Each contract has its own semver `info.version`, and its
  major equals the route major. A change under `contracts/<service>/` bumps
  it.
- **`VERSION`.** A change under `services/<service>/` bumps the service's
  `VERSION`, which is a separate semver for the implementation.
- **What is not breaking.** A new operation, a new optional query parameter, a
  new optional request element, or a new error code on an existing status.
- **What is breaking.** Anything that `oasdiff` reports as breaking.
  Additionally, any element added to an existing XML response type: response
  types are closed, so a validating client built against the old XSD would
  refuse the new element.
- **How a breaking change ships.** It ships as `/v2` routes beside `/v1`, in
  the same `openapi.yaml`, with `<service>.v2.xsd` in namespace
  `.../xml/<service>/v2`, served at `/schema.v2.xsd`. No `/v1` is retired in
  this delivery. Retiring one would need its own record.
- **`common/v1`.** It never changes. A change to the shared types is
  `common/v2`, adopted by each service in its own v2.

**Non-HTTP contracts.** Each is a Markdown file owned by its producer. It
names the object, its fields with types and meaning, its consumers, and what
counts as breaking.

| Kind | Location | Example | Breaking change |
|---|---|---|---|
| Database view or function | `contracts/db/<schema>.<object>.v1.md` | `contracts/db/api_products.product_ref.v1.md` for `api_products.product_ref_v1` | Ships `_v2` beside `_v1`. Consumers name their columns, so a new column is not breaking |
| Stream | `contracts/streams/<producer>.<event>.v1.md` | `contracts/streams/users.access_changed.v1.md` for `stream:users.v1.access_changed` | A new stream `stream:<producer>.v2.<event>`, written beside v1 until its consumers move. The fields are keys only: `event_id`, `event_type`, `entity_id`, `occurred_at` and `v` |
| Liveness key | `contracts/auth/redis-liveness.v1.md` | `pub:auth:v1:live:{sid}`: a STRING with TTL equal to the refresh lifetime, where EXISTS means live | A new key family `pub:auth:v2:live:*` |

The web's campaign-id lock key 221001 (`web/db/campaigns.py`) is also recorded
under `contracts/db/`. The second-wave campaigns-service shares that key with
the web.

**Order.** A service's contract merges, and passes the checks below, before its
implementation merges. A client story is not pulled before the contracts it
consumes have merged.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| One shared namespace and one shared `_common.yaml` for every service, as first drafted | Any XML change is a change for all fifteen services, and no service can reach v2 alone. That contradicts total independence. It also left path ownership unstated, so nothing stopped two services from serving one resource |
| Copy `Error` and `PageInfo` into each service's XSD instead of importing them | It removes the import, but leaves fifteen definitions of the envelope that Electron parses in one place, and they would drift. A frozen import gives one definition that never moves. A same-namespace `xs:include` is not an option either, because each service has its own namespace |
| An API gateway or backend-for-frontend that composes resources for the clients | It re-serves other services' representations, which the instructor forbids, and adds a sixteenth deployable that every client change touches. NGINX only maps a prefix to one port and composes nothing |
| Nest dependent resources under their parent (`/v1/customers/{id}/preferences`, `/v1/customers/{id}/recommendations`) | It puts one service's resource under another's prefix, so either the parent's service serves it (mixing endpoints) or NGINX has to route on a later segment. The id moves to the query or to the resource's own key instead |
| Open content (`xs:any`) at the end of every response type, so v1 responses can grow | It weakens validation exactly where Electron relies on it. Under XSD 1.0's unique particle attribution rule, a trailing wildcard after optional same-namespace elements is ambiguous unless it is restricted to `##other`, and then it does not admit same-namespace additions anyway |
| Generate the contracts from code (Flask-Smorest, utoipa) after implementing | It inverts the order the course requires, produces two generator dialects, and neither generator produces an XSD |

## Consequences

**What this makes easy.**
- A service changes, versions and ships its contract without touching any
  other service's files. The only shared edit is its own line in
  `contracts/registry.yaml`.
- The registry answers "which service serves this?" in one place. It also
  generates the NGINX map and the CI matrix
  ([ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)).
- An XSD failure in Electron points at one service, because the response's
  namespace names it.
- Clients are built in parallel with services, from examples that CI has
  already validated.

**What this makes hard.**
- **Three descriptions per resource.** Every resource is described in
  OpenAPI, the XSD and the examples, fifteen times over. CI is the only thing
  that keeps them in step.
- **Clients compose.** With no embedding, more calls cross the network. A
  recommendation list gives product ids, and the phone then asks
  products-service for each product. The catalogue lists no "many ids" filter
  on `GET /v1/products`. If measurement shows the round trips hurt, adding one
  is a non-breaking contract change in products alone.
- **XML responses are frozen within a major.** A new field in an existing
  response costs a `/v2` route, a `.v2.xsd` and a second handler. That is
  acceptable on a course horizon, and expensive beyond it.
- **Two namespaces in every XML document.** The service's own types and the
  imported `common:*` types appear in the same document, so every XML
  producer and consumer must be namespace-aware.
- **Keying versus nesting is a review call.** The registry check catches a
  registered segment in the wrong service. It cannot tell a resource keyed by
  its own 1:1 identity from one that is merely filed under a foreign id.
- **Images need their contract files.** A service image needs
  `contracts/<service>/` and `contracts/common/v1/` to render XML and to serve
  `/openapi.yaml`. The build context is `services/<service>/`, so CI has to
  copy those files in before the build.

**What must now be true elsewhere.**
- [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md):
  - A contracts job runs the checks below.
  - Each service build copies its contract files into its context.
  - NGINX includes are generated from the registry.
- [ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)
  defines `x-mosaiq-roles` and the liveness key that `contracts/auth/`
  documents.
- [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)
  names the `api_*_v1` objects and the streams that `contracts/db/` and
  `contracts/streams/` document.
- [ADR-0041](0041-android-and-electron-clients-run-different-processes-over-different-formats.md):
  each client calls only a resource's registered service, and Electron
  validates each response against that service's namespace.
- `AGENTS.md` ("Contracts come before code") and
  `docs/scope-delivery-2-distributed.md` describe per-service contracts, not
  a shared set.
- Every Phase 14 implementation story (epic #399) is blocked by its contract.
  Every client story (#402, #403) is blocked by the contracts it consumes.

## Compliance

All checks run from the repository root in the CI contracts job.
`BASE=$(git merge-base origin/develop HEAD)`.

```bash
# 1. Registry: every first segment after /vN is registered once, to the
#    service that serves it; no path nests another service's resource; every
#    services/<dir> has an entry with a rule.
python3 - <<'PY'
import pathlib, re, sys, yaml
META = {"/health/live", "/health/ready", "/openapi.yaml", "/schema.xsd"}
reg = yaml.safe_load(open("contracts/registry.yaml"))["services"]
owner, errors = {}, []
for s in reg:
    if not s.get("rule") or not s.get("resources"):
        errors.append(f"{s['name']}: entry without a rule or resources")
    for r in s.get("resources", []):
        if r in owner:
            errors.append(f"/v1/{r}: registered to {owner[r]} and {s['name']}")
        owner.setdefault(r, s["name"])
names = {s["name"] for s in reg}
for d in sorted(pathlib.Path("services").iterdir()):
    if d.is_dir() and d.name not in names:
        errors.append(f"services/{d.name}: no registry entry")
for s in reg:
    for path in yaml.safe_load(open(f"contracts/{s['name']}/openapi.yaml"))["paths"]:
        if path in META or re.fullmatch(r"/schema\.v[2-9]\d*\.xsd", path):
            continue
        m = re.fullmatch(r"/v[1-9]\d*/([^/]+)((?:/[^/]+)*)", path)
        if not m:
            errors.append(f"{s['name']}: {path} is neither versioned nor a meta route")
            continue
        if owner.get(m.group(1)) != s["name"]:
            errors.append(f"{s['name']}: {path} starts with a segment not registered to it")
        for seg in m.group(2).split("/")[1:]:
            if owner.get(seg, s["name"]) != s["name"]:
                errors.append(f"{s['name']}: {path} nests {seg}, registered to {owner[seg]}")
print("\n".join(errors)); sys.exit(1 if errors else 0)
PY

# 2. Each XSD compiles standalone (only itself plus common/v1, no network),
#    declares its own namespace, and imports nothing but common/v1.
#    xmllint exits 3 when the schema compiles and the probe document is
#    invalid, and 4 or 5 when the schema itself fails.
probe=$(mktemp); echo '<compile-probe/>' > "$probe"
for x in contracts/common/v1/common.xsd contracts/*/*.xsd; do
  xmllint --noout --nonet --schema "$x" "$probe" 2>/dev/null; [ $? -eq 3 ] \
    || echo "does not compile standalone: $x"
done
for s in $(yq -r '.services[].name' contracts/registry.yaml); do
  grep -q "targetNamespace=\"https://mosaiq.maxthecoder.online/xml/$s/v1\"" \
    "contracts/$s/$s.xsd" || echo "wrong namespace: contracts/$s/$s.xsd"
  grep -ho 'schemaLocation="[^"]*"' contracts/$s/*.xsd \
    | grep -v '"../common/v1/common.xsd"' && echo "imports more than common/v1: $s"
done

# 3. Examples: every XML example validates against its own service's XSD,
#    and every *.invalid.xml is refused. OpenAPI documents validate.
for f in contracts/*/examples/*.xml; do
  s=$(basename "$(dirname "$(dirname "$f")")")
  if [[ $f == *.invalid.xml ]]; then
    xmllint --noout --nonet --schema "contracts/$s/$s.xsd" "$f" 2>/dev/null \
      && echo "invalid example accepted: $f"
  else
    xmllint --noout --nonet --schema "contracts/$s/$s.xsd" "$f" || echo "invalid: $f"
  fi
done
for s in $(yq -r '.services[].name' contracts/registry.yaml); do
  openapi-spec-validator "contracts/$s/openapi.yaml"
done

# 4. No breaking change within a major: oasdiff for OpenAPI, and the base
#    branch's XSD must still accept the new XML examples it already had.
base=$(mktemp -d); git archive "$BASE" contracts | tar -x -C "$base"
for s in $(yq -r '.services[].name' contracts/registry.yaml); do
  [ -f "$base/contracts/$s/openapi.yaml" ] || continue   # a new service
  oasdiff breaking --fail-on ERR "$base/contracts/$s/openapi.yaml" "contracts/$s/openapi.yaml"
  for f in "$base"/contracts/$s/examples/*.xml; do
    [[ $f == *.invalid.xml ]] && continue
    xmllint --noout --nonet --schema "$base/contracts/$s/$s.xsd" \
      "contracts/$s/examples/$(basename "$f")" || echo "XML response changed in v1: $f"
  done
done
git diff --quiet "$BASE" -- contracts/common/v1/ || echo "contracts/common/v1 is frozen"

# 5. Versions move with changes: info.version for a contract, VERSION for a service.
for s in $(git diff --name-only "$BASE" -- contracts/ | cut -d/ -f2 | sort -u); do
  [ -f "contracts/$s/openapi.yaml" ] || continue           # db, streams, common
  git cat-file -e "$BASE:contracts/$s/openapi.yaml" 2>/dev/null || continue
  [ "$(git show "$BASE:contracts/$s/openapi.yaml" | yq -r .info.version)" != \
    "$(yq -r .info.version "contracts/$s/openapi.yaml")" ] || echo "info.version not bumped: $s"
done
for s in $(git diff --name-only "$BASE" -- services/ | cut -d/ -f2 | sort -u); do
  git diff --quiet "$BASE" -- "services/$s/VERSION" && echo "VERSION not bumped: $s"
done

# 6. Contract before implementation: a service's openapi.yaml entered the
#    history before any file under services/<name>/ did.
for s in $(yq -r '.services[].name' contracts/registry.yaml); do
  c=$(git log --diff-filter=A --format=%ct -- "contracts/$s/openapi.yaml" | tail -1)
  i=$(git log --diff-filter=A --format=%ct -- "services/$s/" | tail -1)
  [ -n "$i" ] && { [ -z "$c" ] || [ "$i" -lt "$c" ]; } && echo "implemented before its contract: $s"
done
```

Every check prints what it finds, and the job fails on any output or non-zero
exit. At runtime, the shared black-box suite runs against each built image and
asserts:

- `/openapi.yaml` and `/schema.xsd` equal the contract files byte for byte;
- `Accept: text/csv` gets 406, and `Content-Type: text/csv` gets 415;
- `size=101` gets 422 `invalid_pagination`;
- the error envelope is returned in both formats;
- every XML response validates against the service's XSD.
