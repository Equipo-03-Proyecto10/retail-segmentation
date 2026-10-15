# Service contract conventions

This directory is the single reference for every MOSAIQ service contract. A
contract is merged before its implementation and before any client story that
consumes it. Each service owns and versions its contract independently.

## Layout

For a service named `<name>`, the v1 contract contains:

- `contracts/<name>/openapi.yaml`, an OpenAPI 3.1 document with its own semantic
  `info.version`. The version's major number equals the route major.
- `contracts/<name>/<name>.xsd`, whose target namespace is
  `https://mosaiq.maxthecoder.online/xml/<name>/v1`.
- `contracts/<name>/examples/`, containing JSON examples named `*.json`, XML
  examples named `*.xml`, and examples that must be refused named
  `*.invalid.xml`.

Request examples are named `*.request.json` and `*.request.xml`. Every other
non-invalid XML example is a response example. This distinction is used by the
compatibility check: the base XSD must accept the head response examples, and
the head XSD must accept the base request examples.

The only shared XSD is the frozen `contracts/common/v1/common.xsd`, imported as
`../common/v1/common.xsd`. It defines `Error` and `PageInfo`. A shared-type
change is made as `common/v2`, never by changing `common/v1`.

## HTTP and representation conventions

When `Accept` is absent, `*/*`, or `application/json`, a service responds with
JSON. `application/xml` selects XML, and any other value gets 406. Request
bodies use the same two formats according to `Content-Type`; any other content
type gets 415. An unparsable body gets 400 `malformed_body`. A body that parses
but violates its schema gets 422 `invalid_body` with field details. XML parsing
does not resolve external entities or use the network, and a DOCTYPE or entity
declaration is malformed. A body over 64 KiB gets 413 `payload_too_large`.

Each XML representation has one root element in the service namespace. A
collection is a plural element containing singular elements and a
`common:PageInfo`. An absent value is omitted in XML and is `null` in JSON.
Every XML response validates against the service XSD.

Timestamps are RFC 3339 with an offset and use `Z` in responses. Dates are RFC
3339 `full-date`. Money is a decimal string with two places in both formats.
Integer identifiers remain numbers; UUID and ObjectId identifiers are strings.

The error envelope is `{code, message, correlation_id, details[]}` in both
formats and is `common:Error` in XML. Each detail has `field` and `code`; error
codes are stable snake_case. Operations document their domain codes. The only
shared codes are:

| Status | Code | Meaning |
|---:|---|---|
| 400 | `malformed_body` | The body cannot be parsed safely. |
| 401 | `invalid_token` | The bearer token is invalid. |
| 401 | `session_revoked` | The token's session is no longer live. |
| 403 | `forbidden_role` | The authenticated role cannot perform the operation. |
| 404 | `not_found` | No route matches. |
| 405 | `method_not_allowed` | The route does not support the method. |
| 406 | `not_acceptable` | The requested response format is unsupported. |
| 413 | `payload_too_large` | The body exceeds 64 KiB. |
| 415 | `unsupported_media_type` | The request format is unsupported. |
| 422 | `invalid_body` | A parsed body violates its schema. |
| 422 | `invalid_pagination` | A pagination value is out of range. |
| 429 | `rate_limited` | The request limit was exceeded. |
| 503 | `auth_unavailable` | Session state cannot be checked. |
| 503 | `database_unavailable` | The service's own database is unreachable. |
| 503 | `schema_not_ready` | `/health/ready` found an unapplied schema. |

`database_unavailable` means the kit's PostgreSQL pool cannot provide a
connection, a connection fails during a request, or engagement's MongoDB
connection fails. It never replaces `/health/ready`'s answer.

The allowed status codes are 200, 201, 204, 400, 401, 403, 404, 405, 406, 409,
413, 415, 422, 429, and 503. A 500 is a defect and is never contracted.

Pagination uses `page` (at least 1, default 1) and `size` (1 through 100,
default 20). A response carries its items and `PageInfo` with `page`, `size`,
and `total`. Invalid values get 422 `invalid_pagination`.

`X-Correlation-ID` is accepted when it matches `^[0-9A-Za-z-]{1,64}$`; otherwise
the service generates one, and always echoes it. A 429 carries `Retry-After`.
Retried mobile writes use the identifier declared by their operation:
`Idempotency-Key` for sales, `client_event_id` for engagement, and
`client_check_in_id` for check-ins.

## Authentication and routes

Every operation that declares Bearer security also declares
`x-mosaiq-roles`, an array of role codes transcribed from the
[service operation policy](../docs/requirements.md#service-operation-policy-distributed-stage).
The token-free operations are `POST /v1/sessions`,
`POST /v1/sessions/refresh`, `GET /v1/jwks.json`, and the meta routes.

The meta routes are `/health/live`, `/health/ready`, `/openapi.yaml`, and
`/schema.xsd`. They sit outside `/v1`, do not appear in the registry, and need
no token. Versioned schemas add `/schema.vN.xsd`.

Each first path segment after `/vN` belongs to exactly one service in
`registry.yaml`. Names are compared as whole path segments.

> A path never contains another service's registered resource name. Another
> boundary's id may key a resource only when it is that resource's own identity
> (`/v1/preference-profiles/{customer_id}`,
> `/v1/stock-levels/{store_id}/{product_id}`); otherwise foreign ids are query
> or body fields.

## Document structure

Fifteen contracts are written by different people, so the shape of the
documents is fixed here, not left to each author.

**OpenAPI.**
- `openapi: 3.1.0`, `info.title` `<name>-service`, and a semver `info.version`.
- Security:
  - `components.securitySchemes.bearer` is `type: http`, `scheme: bearer`,
    `bearerFormat: JWT`, and the document has a root
    `security: [{bearer: []}]`.
  - A token-free operation says `security: []`. Every meta route does.
- Every operation has a lowerCamelCase `operationId`.
- Correlation ids:
  - Every operation takes `X-Correlation-ID` through
    `components.parameters.CorrelationId`.
  - Every response returns it through `components.headers.CorrelationId`.
  - A 429 also returns `Retry-After`.
- Bodies:
  - Request and response bodies list `application/json` and `application/xml`
    against the same `components.schemas` entry.
  - The XSD is authoritative for XML.
  - The only JSON-only body is auth's JWK Set (E-04).
- Errors:
  - `components.schemas.Error` mirrors `common:Error`.
  - Each error status is one response whose description names every code it
    can carry.
  - That response holds one named example per code, keyed by the code.

**Meta routes.**
- `GET /health/live` answers 204.
- `GET /health/ready` answers 204, or 503 `schema_not_ready`.
- Neither has a body to negotiate.
- `GET /openapi.yaml` answers 200 `application/yaml`.
- `GET /schema.xsd` answers 200 `application/xml`.

**XML.**
- A root element is the PascalCase name of its representation, as `Error` and
  `PageInfo` are in `common/v1`.
- A child element carries the JSON member's snake_case name.
- An array is the member's element repeated, as `details` is.
- A collection root is the plural (`Users`) holding singular items (`User`)
  and one `common:PageInfo`.

**Examples.**
- Files are kebab-case and named after what they show: `sign-in.request.json`,
  `sign-in.json`, `invalid-credentials.xml`.
- An error example is named after its code.

## Versioning

Any change under `contracts/<name>/` moves that contract's semantic
`info.version`. Anything reported by `oasdiff` is breaking. Adding an element
to an existing XML response type is also breaking because response types are
closed to additions within a major. A new operation, optional query parameter,
optional request element, or error code on an existing status is not breaking.

A breaking version ships `/v2` beside `/v1` in the same OpenAPI document. Its
schema is `<name>.v2.xsd`, uses namespace
`https://mosaiq.maxthecoder.online/xml/<name>/v2`, and is served at
`/schema.v2.xsd`. Retiring a major requires its own architectural record.
