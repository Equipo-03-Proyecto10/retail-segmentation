# ADR-0035 — Each service publishes its own versioned contract, and no two services share a resource

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The course scope puts contracts first (*"antes de desarrollar las aplicaciones cliente, el equipo
deberá definir los contratos de integración"*) and asks for versioned routes, OpenAPI, and JSON
**and** XML: JSON for Android, XSD-validated XML for Electron. The instructor also requires **total
independence**, each service built, versioned and rolled back alone ([ADR-0043](0043-at-least-ten-microservices-each-implementing-one-business-rule.md)), so no contract
may change for all services at once; and **never mix endpoints**: no service exposes, duplicates,
proxies or nests another's resources.

Non-HTTP contracts, the database objects and streams of [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) and the liveness key of
[ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md), need owners and versions too. Judgement calls: how keying by another boundary's id
differs from nesting, and treating an additive XML response change as breaking.

## Decision

Each service owns `contracts/<service>/`: `openapi.yaml` (own `info.version`), `<service>.xsd` in
namespace `https://mosaiq.maxthecoder.online/xml/<service>/v1`, and `examples/`. The only shared
file is the frozen `contracts/common/v1/common.xsd` (`Error`, `PageInfo`), via `xs:import`. Every
top-level resource, the first path segment after `/v1`, is registered to exactly one service in
`contracts/registry.yaml`, which serves only its own and never nests, embeds or re-serves another's.
A path never contains another service's registered resource name. Another boundary's id may key a resource only when it is that resource's own identity (`/v1/preference-profiles/{customer_id}`, `/v1/stock-levels/{store_id}/{product_id}`); otherwise foreign ids are query or body fields.
Each fact has one write endpoint. Breaking changes ship as `/v2` beside `/v1`. Database, stream and
liveness contracts live in `contracts/db/`, `contracts/streams/` and `contracts/auth/`, owned by
their producer. A contract merges, passing its checks, before its implementation and client stories.

**The resource registry.** `contracts/registry.yaml` holds `services:` entries with `name`, `port`,
`rule` (one sentence), `rn` (plain RN ids), `wave` (`core` or `second`; ADR-0043) and `resources`.
`name` is also the contract directory, the NGINX prefix `/api/<name>/` ([ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)) and the XML
namespace segment. The fifteen entries and their resources are the registry table of [ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md).
Every `services/<name>/` has an entry, which may precede the directory: contracts merge first.

**Routing rules.**
- **Foreign ids** otherwise travel as query or body fields: `/v1/stock-levels?product_id=`,
  `/v1/segment-assignments/current?customer_id=`, `/v1/recommendations?customer_id=`. Names are
  compared as whole path segments: `interest-categories` and `preferred-channels` are not
  `categories` or `channels`. Forbidden: `/v1/customers/{id}/sales`.
- **No re-serving:** ids, never representations. A reason may quote a figure the service used to
  decide, such as recommendations' quantity held, but never another service's representation.
  `GET /v1/products?ids=<comma list, max 50>` resolves a list of product ids in one call.
- **One write endpoint per fact** (an endpoint is one resource URI; its methods are listed). Segment
  assignments and audit entries have none: the web's administrator run and `fn_audit` write them.

  | Fact | Its only write endpoint |
  |---|---|
  | Purchase | `POST /v1/sales` |
  | Recommendation click | `POST /v1/engagement-events` with type `RECO_CLICK` |
  | Check-in | `POST /v1/check-ins` |
  | Store coordinates | `PUT` and `DELETE /v1/stores/{store_id}/location` |
  | Administrator role | `POST /v1/administrator-transfers` |
  | A user's password | `PUT /v1/users/{user_id}/password`: the caller's own, with the current password, or an `ADMIN` reset |
  | Session revocation | `DELETE /v1/sessions/current`. Access changes revoke through `stream:users.v1.access_changed`, never through an endpoint in another service |
- **Meta routes** `/health/live`, `/health/ready`, `/openapi.yaml` and `/schema.xsd` sit outside
  `/v1` and the registry and need no token. The last two serve the contract files byte for byte; CI
  stages the contract files into each image (ADR-0040).
- **Authentication.** Every other operation declares Bearer security and `x-mosaiq-roles`
  (ADR-0036), except `POST /v1/sessions`, `POST /v1/sessions/refresh` and `GET /v1/jwks.json`, the
  one JSON-only operation: it answers `application/jwk-set+json` (RFC 7517 has no XML form), no
  client consumes it, and an `Accept` that excludes JSON gets 406.
- **The Rust bench build** ([ADR-0042](0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md)) is never routed, so it has no registry entry.

**Formats** are the same in every service; the kits ([ADR-0045](0045-services-never-import-the-web-system.md)) carry no service's error codes.

| Concern | Convention |
|---|---|
| Negotiation | A missing `Accept`, `*/*` or `application/json` gets JSON, and `application/xml` gets XML. Any other `Accept` gets 406. Request bodies follow `Content-Type` in the same two formats. Any other `Content-Type` gets 415. A body that does not parse gets 400 `malformed_body`; a body that parses but breaks the schema gets 422 `invalid_body` with `details`. Request XML is parsed by the kit only: a DOCTYPE or entity declaration gets 400 `malformed_body`, and there is no external entity resolution and no network access. A body over 64 KiB gets 413 `payload_too_large`, from NGINX (`client_max_body_size 64k`) and from the kit |
| XML | One root element per representation, in the service's namespace. A collection is a plural element that wraps singular elements plus a `common:PageInfo`. An absent value is an omitted element in XML and `null` in JSON. Every XML response validates against the service's XSD |
| Values | Timestamps are RFC 3339 with an offset, and are `Z` in responses. Dates are RFC 3339 `full-date`. Money is a decimal string with two places in both formats. An id keeps its owner's type: integer ids are numbers, and UUIDs and ObjectIds are strings |
| Errors | One envelope in both formats, `common:Error`: `{code, message, correlation_id, details[]}`. Each `details` item names a `field` and a `code`. `code` is stable snake_case. Each operation lists its own codes in that service's OpenAPI. The only shared codes are the mechanism codes: 400 `malformed_body`, 401 `invalid_token`, 401 `session_revoked`, 403 `forbidden_role`, 404 `not_found` (no route matches), 405 `method_not_allowed`, 406 `not_acceptable`, 413 `payload_too_large`, 415 `unsupported_media_type`, 422 `invalid_body` (the body parses but breaks the schema; `details` name the fields), 422 `invalid_pagination`, 429 `rate_limited`, 503 `auth_unavailable` and 503 `schema_not_ready` (from `/health/ready` only) |
| Status codes | 200, 201, 204, 400, 401, 403, 404, 405, 406, 409, 413, 415, 422, 429 and 503. A 500 is a defect, never part of a contract |
| Pagination | `page` (≥ 1, default 1) and `size` (1–100, default 20). The response carries the items and `PageInfo` (`page`, `size`, `total`). Out-of-range values get 422 `invalid_pagination` |
| Headers | `X-Correlation-ID` is accepted, or generated when absent, and is echoed back. A 429 carries `Retry-After`. A retried mobile write carries its id as the operation declares: `Idempotency-Key` for sales, `client_event_id` for engagement, and `client_check_in_id` for check-ins |

**Versioning.** Every service versions alone: a change under `contracts/<service>/` bumps its semver
`info.version` (major = route major), one under `services/<service>/` its separate `VERSION`. Not
breaking: a new operation, optional query parameter, optional request element, or error code on an
existing status. Breaking: whatever `oasdiff` reports, and any element added to an existing XML
response type: response types are closed, so an old-XSD client would refuse it. Breaking changes
ship `/v2` routes beside `/v1` in the same `openapi.yaml`, with `<service>.v2.xsd` (namespace
`.../xml/<service>/v2`) at `/schema.v2.xsd`. Retiring a `/v1` needs its own record. `common/v1`
never changes; a shared-type change is `common/v2`, adopted in each service's own v2.

**Non-HTTP contracts** are Markdown files owned by their producer, each stating what breaks it.

| Kind | Location | Example | Breaking change |
|---|---|---|---|
| Database view or function | `contracts/db/<schema>.<object>.v1.md` | `contracts/db/api_products.product_ref.v1.md` for `api_products.product_ref_v1` | Ships `_v2` beside `_v1`. Consumers name their columns, so a new column is not breaking |
| Stream | `contracts/streams/<producer>.<event>.v1.md` | `contracts/streams/users.access_changed.v1.md` for `stream:users.v1.access_changed` | A new stream `stream:<producer>.v2.<event>`, written beside v1 until its consumers move. The fields are keys only: `event_id`, `event_type`, `entity_id`, `occurred_at` and `v` |
| Liveness key | `contracts/auth/redis-liveness.v1.md` | `pub:auth:v1:live:{sid}`: a STRING whose TTL is the session's remaining absolute lifetime (at most 12 h, ADR-0036); EXISTS means live | A new key family `pub:auth:v2:live:*` |

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
- A service ships its contract alone; its only shared edit is its own registry line.
- An XSD failure in Electron names its service; clients build in parallel from validated examples.

**What this makes hard.**
- **Three descriptions per resource** (OpenAPI, XSD, examples), kept in step only by CI.
- **Clients compose.** With no embedding, more calls cross the network: a recommendation list or a
  page of sales lines needs one `GET /v1/products?ids=` (at most 50 ids) besides the list itself.
- **XML responses are frozen within a major.** A new response field costs a `/v2` route, a `.v2.xsd`
  and a second handler: fine for a course, expensive beyond it.
- **Two namespaces in every XML document,** so every producer and consumer must be namespace-aware.
- **Keying versus nesting is a review call:** no check tells an own-identity key from a foreign id.
- **One JSON-only operation,** `GET /v1/jwks.json`, which the format checks must exempt.

**What must now be true elsewhere.**
- ADR-0040 runs the contracts job, stages `contracts/<name>/` and `contracts/common/v1/` into each
  build context, and checks each service's committed `nginx/<name>.conf` against its registry port.
- ADR-0036 defines `x-mosaiq-roles` and the liveness key; ADR-0044 the `api_*` objects and streams.
- [ADR-0041](0041-android-and-electron-clients-run-different-processes-over-different-formats.md): each client calls only registered services, and Electron validates by namespace.
- Phase 14 stories (#399) wait for their contracts; client stories (#402, #403) for those they use.

## Compliance

The CI contracts job runs at the repository root with `BASE=$(git merge-base origin/develop HEAD)`
and fails on any output or non-zero exit. Inline: each XSD compiles standalone (xmllint exits 3:
schema compiles, probe invalid), and each contract predates its code:

```bash
probe=$(mktemp); echo '<compile-probe/>' > "$probe"; for x in contracts/common/v1/common.xsd contracts/*/*.xsd; do
  xmllint --noout --nonet --schema "$x" "$probe" 2>/dev/null; [ $? -eq 3 ] || echo "does not compile standalone: $x"; done
for s in $(yq -r '.services[].name' contracts/registry.yaml); do
  c=$(git log --diff-filter=A --format=%ct -- "contracts/$s/openapi.yaml" | tail -1)
  i=$(git log --diff-filter=A --format=%ct -- "services/$s/" | tail -1)
  [ -n "$i" ] && { [ -z "$c" ] || [ "$i" -lt "$c" ]; } && echo "implemented before its contract: $s"; done
```

`contracts/check.sh` (the full draft's code) fails on any output:
- every resource is registered once;
- every `services/<dir>` has an entry;
- each path's first segment after `/vN` is registered to its own service;
- no later path segment equals a resource registered to another service;
- every path is versioned or a meta route;
- each XSD declares its own namespace and imports only `common/v1`;
- XML examples validate, and `*.invalid.xml` examples are refused;
- `openapi-spec-validator` passes;
- `oasdiff` finds nothing breaking within a major, and the base XSD accepts its old XML examples;
- `contracts/common/v1` is unchanged;
- `info.version` and `VERSION` move with their changes.

At runtime, the shared black-box suite asserts against each built image:
- `/openapi.yaml` and `/schema.xsd` equal the contract files byte for byte;
- `Accept: text/csv` gets 406, and `Content-Type: text/csv` gets 415;
- a 65 KiB body gets 413 `payload_too_large`;
- a DOCTYPE body, a billion-laughs body and an external-entity body each get 400 `malformed_body`;
- `size=101` gets 422 `invalid_pagination`;
- the error envelope comes back in both formats, and every XML response validates.
