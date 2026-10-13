# ADR-0035 — Integration contracts are written before the code that serves or consumes them

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The course scope says it outright: *"antes de desarrollar las aplicaciones
cliente, el equipo deberá definir los contratos de integración"*. It names the
contents: JSON shapes, HTTP codes, pagination and versioning; XML elements,
hierarchy, XSD and namespaces. Every service must answer in JSON **and** XML and
publish OpenAPI. The Android client consumes JSON only; the Electron client
consumes XML only and validates it against an XSD.

That puts two formats, eight services and two clients over one set of shapes.
Without one convention, each service invents its own error body, its own
pagination parameters and its own date format. Each client then carries eight
special cases, and the XSDs drift from what the services actually emit.

The judgement call is how much to fix centrally. If too little is fixed, the
contracts disagree. If too much is fixed, a service cannot express its own
resource.

## Decision

Every service exposes versioned routes `/v1/...`. On the instance they sit
behind NGINX under `/api/<service>/`. Each service has:

- one OpenAPI 3.1 file, `contracts/openapi/<service>.yaml`;
- one XSD, `contracts/xsd/<service>.xsd`, in the namespace
  `https://mosaiq.maxthecoder.online/xml/v1`;
- JSON and XML examples in `contracts/examples/<service>/`.

All of them build on `_common.yaml` and `common.xsd`, which define the shared
pieces once. A service's contract merges, and passes CI validation, **before**
its implementation starts and before any client story that consumes it.

The shared conventions:

| Concern | Convention |
|---|---|
| Format selection | `Accept: application/json` or `application/xml`. A missing `Accept` or `*/*` gets JSON. Anything else gets 406. Request bodies follow `Content-Type` in the same two formats, and anything else gets 415 |
| JSON ↔ XML mapping | One root element named after the resource. A collection is a plural element wrapping singular elements. A null is an omitted element. Timestamps are ISO-8601 UTC. Money is a decimal string with two places. Identifiers are strings in both formats |
| Errors | One envelope in both formats: `code` (stable, snake_case), `message` (human), `details` (field-level list), `correlation_id` |
| Status codes | 200, 201, 204, 400, 401, 403, 404, 406, 409, 415, 422, 429, 503. A 500 is a defect, never a contract |
| Pagination | `page` (≥ 1) and `size` (1–100, default 20). Responses carry `page`, `size`, `total` and the items. Invalid values get 422 `invalid_pagination` |
| Tracing | `X-Correlation-ID` is accepted, or generated when absent, echoed in the response and logged |
| Rate limit | A 429 carries `Retry-After`. Responses carry `RateLimit-Limit` and `RateLimit-Remaining` |
| Health | `GET /health/live` (the process is up, no dependencies checked) and `GET /health/ready` (PostgreSQL, Redis and, where used, MongoDB reachable). Neither requires a token |
| Versioning | A breaking change creates `/v2` alongside `/v1`. Additive fields do not |

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Generate the contracts from code (Flask-Smorest, utoipa) after implementing | It inverts the course's required order. It also produces two generators' dialects for one system, and generated XSDs are not something either generator supports |
| Format by path suffix (`/products.xml`) instead of `Accept` | Doubles every route in OpenAPI and lets clients bypass negotiation. `Accept` is what both Retrofit and Electron's `fetch` set naturally |
| Cursor pagination | Better at scale, but the desktop client's paged tables and the web's existing pagination are page-numbered, and no list here exceeds tens of thousands of rows |
| Per-service error bodies | Each client would need eight parsers. The reference slide's services each return their own shapes, which is the problem this record exists to prevent |
| XML without a namespace | The course requires namespaces, and validation against the XSD is how the desktop client refuses a drifted response |

## Consequences

**What this makes easy.**
- Clients can be built in parallel with services, from examples that CI has
  already validated.
- The shared kits (F13-03, F13-04) implement negotiation, errors, pagination
  and health once per language.
- An XSD failure on the desktop is a contract defect with an obvious owner.

**What this makes hard.**
- Every resource is described three times (OpenAPI, XSD, examples), and keeping
  them in step is real work. CI validation (F13-09) is the only thing that
  stops drift.
- The JSON ↔ XML mapping rules must be followed by hand in the Rust and Python
  serializers.

**What must now be true elsewhere.**
- Every Phase 14 implementation story is blocked by its contract story.
- Every client story is blocked by the contracts it consumes.
- The Phase 18 negative tests check the codes listed above.

## Compliance

```bash
# Every service has a contract, a schema and examples, and they validate.
for s in auth users catalog customers inventory sales segments activity; do
  test -f contracts/openapi/$s.yaml && test -f contracts/xsd/$s.xsd \
    && test -d contracts/examples/$s || echo "missing contract for $s"
done
for f in contracts/examples/*/*.xml; do
  s=$(basename "$(dirname "$f")")
  case "$f" in *.invalid.xml) continue;; esac
  xmllint --noout --schema "contracts/xsd/$s.xsd" "$f" || echo "invalid: $f"
done
```

Every service also passes the shared negative tests: 406 for `text/csv`, 422
for `size=101`, and an error envelope in both formats.
