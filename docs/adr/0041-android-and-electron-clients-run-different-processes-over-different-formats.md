# ADR-0041 — The Android and Electron clients run different processes over different formats

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The second delivery requires two clients:

| Client | Format | Requirement |
|---|---|---|
| Android (Kotlin) | JSON only | At least four microservices and two device capabilities |
| Desktop (Electron) | XML only, validated against an XSD | At least four microservices, in a **different** process from the mobile one |

The instructor's reference shows a public Electron client that reads one
service and validates nothing. Our scope asks for more: authentication, four or
more services, and XSD validation.

The redesign changes what the clients face:

- **More, narrower services.** There are fifteen single-rule services instead
  of eight
  ([ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)).
- **No composition by services.** No service embeds or re-serves another
  service's resource
  ([ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)).
  A recommendation gives a `product_id`, not a product, and a sale line gives
  a `product_id`, not a name. The clients do the composing.
- **Rotating refresh tokens.** A refresh token rotates on every use, and
  reusing one revokes the session
  ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)).
  Two refreshes racing each other sign the user out.

Four decisions are open.

**Who carries the phone.** The first draft of this record called it "the
in-store associate", but the permission matrix in `docs/requirements.md` §3
has no such role. Its seven roles are `ADMIN`, `ANALYST`, `STORE_MANAGER`,
`MARKETING`, `INVENTORY_PLANNER`, `AUDITOR` and `CUSTOMER`, and `role` is
seed-exempt because a new role is a business decision. On 2026-10-13 the user
decided that the floor role is `STORE_MANAGER`, following
[ADR-0023](0023-store-managers-read-segments-and-customers.md), which already
gave that role customers, segments and recommendations at the counter.

**Which process each client runs.** "Different" must be visible in the
demonstration, not just claimed.

**What each client may assume about the services.** The answer must hold
when a service is redeployed or versioned on its own.

**What security baseline each client starts from.** An Electron window with
`nodeIntegration` turns any injected script into code execution on the
desktop. A phone that keeps tokens in plain preferences leaks them to any
backup.

## Decision

The two clients run different processes for different users.

- **Android, the floor process.** It runs the store floor for `STORE_MANAGER`
  and `ADMIN`, in JSON, against twelve services. Its device capabilities are
  camera/QR, GPS and Room.
- **Electron, the back-office process.** It runs maintenance, review and
  monitoring for the staff roles, in XML validated against each service's own
  XSD, against all fifteen services.

Each client calls a resource only at the service that
`contracts/registry.yaml` registers it to. It composes related data itself
from ids, and never assumes that one service returns another's data. Each
client generates its retry ids once, when the user acts, so a retry never
creates a second sale, click or check-in. Each refreshes its tokens through
one single-flight path.

**Android: the floor process.**

- **Stack.**
  - Kotlin, Jetpack Compose, Retrofit, OkHttp and kotlinx.serialization with
    `ignoreUnknownKeys`.
  - It sends `Accept: application/json` and never parses XML.
- **Tokens.**
  - Stored encrypted with an Android Keystore key, and excluded from backup.
  - One OkHttp `Authenticator` refreshes them single-flight.
- **Users.** `STORE_MANAGER` and `ADMIN`. The app turns any other role away
  after sign-in with a plain message. The services refuse those roles anyway
  through their `x-mosaiq-roles`, so this gate is only for usability.
- **Process.**
  1. Sign in.
  2. Pick the store and check in by GPS.
  3. Scan a product's QR code.
  4. See the product and its availability at that store.
  5. Find or register the customer, and capture their interests.
  6. See their current segment and recommendations.
  7. Open a recommendation.
  8. Record the sale.

| Service | What the phone does there |
|---|---|
| auth | Sign in, refresh and sign out (`/v1/sessions`) |
| users | Show who is signed in (`GET /v1/users/me`) |
| stores | Store picker (`GET /v1/stores`) |
| check-ins | GPS check-in (`POST /v1/check-ins` with `client_check_in_id`). The server computes the distance and decides whether it is verified |
| products | QR detail: the QR payload is `product_id` (`GET /v1/products/{product_id}`), plus name lookups for recommended items |
| stock | Availability at the checked-in store (`GET /v1/stock-levels/{store_id}/{product_id}`) |
| engagement | `QR_SCAN` after each decoded scan. Exactly one `RECO_CLICK` per tap on a recommendation, with `label_code` and `rank` (`POST /v1/engagement-events`) |
| customers | Search and register at the counter (`GET` and `POST /v1/customers`) |
| customer-preferences | Interest capture (`PUT /v1/preference-profiles/{customer_id}/interest-categories`) |
| sales | Channel list (`GET /v1/channels`), with `physical_store` preselected and `channel_id` sent. `POST /v1/sales` with a client UUID in `Idempotency-Key`. `GET /v1/sales/{sale_id}` for status |
| segments | The customer's current assignment (`GET /v1/segment-assignments/current?customer_id=`), including the `no_open_assignment` and `unassigned_result` states as they are |
| recommendations | `GET /v1/recommendations?customer_id=` |

- **Device capabilities.**
  - **Camera/QR**: CameraX with ML Kit barcode scanning.
  - **GPS**: `FusedLocationProvider`. The fix's accuracy is sent with it.
  - **Local storage**: Room.
  - Each permission is asked for when it is first needed.
- **Writes made on the floor.** Sales, engagement events and check-ins are
  first written to a Room table together with their client id: the
  `Idempotency-Key`, `client_event_id` or `client_check_in_id`.
  - The id is generated once, at the moment of the tap.
  - A WorkManager job sends each row, and resends it until a final answer
    arrives, always with the same id.
  - A tap therefore produces exactly one `RECO_CLICK`. A sale replayed after
    a timeout returns the original sale, not a second one
    ([ADR-0039](0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md)).
  - A queued write that is finally refused, for example a sale that gets 409
    `stock_unavailable`, stays visible in an outbox screen with its error code.
    It is never dropped silently.
  - Reads are not queued.

**Electron: the back-office process.**

- **Stack.** TypeScript.
  - Every `BrowserWindow` has `contextIsolation: true`,
    `nodeIntegration: false` and `sandbox: true`.
  - A strict CSP: no remote scripts and no inline scripts.
  - Navigation and new windows are blocked.
  - The renderer reaches the network only through a typed preload bridge that
    exposes named operations, never a generic `fetch`.
- **Tokens.** They live in the main process, encrypted with `safeStorage`,
  and never reach the renderer. The main process refreshes them single-flight.
- **XML both ways.**
  - Requests send `Accept: application/xml`, and write bodies are XML.
  - The main process holds the XSDs from `contracts/<service>/` and
    `contracts/common/v1/`, bundled at build time and keyed by namespace.
  - Before parsing a response, it checks two things: the root element's
    namespace must be the namespace of the service that was called, and the
    document must validate against that XSD (`xmllint-wasm`).
  - A response that fails either check is reported as a contract defect
    naming the service. It is never rendered.
  - Within a major version an XML response type never changes
    ([ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)),
    so the bundled schemas stay valid until a service ships `/v2`.
- **Users.** The staff roles. Which screens a role sees comes from the
  `x-mosaiq-roles` in the bundled contracts, and the services still enforce
  every call. A 403 is shown, not hidden.

| Service | What the back office does there |
|---|---|
| auth | Sign in, refresh and sign out |
| users | User administration, deactivation, password reset and the administrator transfer (`POST /v1/administrator-transfers`) |
| products | Catalog maintenance (ADMIN) |
| categories | Category tree maintenance (ADMIN), with the delete refusals shown as returned |
| stores | Store location and geofence editor (`PUT /v1/stores/{store_id}/location`, ADMIN) |
| stock | Levels below a threshold (`GET /v1/stock-levels?below=`) |
| customers | The customer screen |
| customer-preferences | A customer's channels and interests |
| sales | Read-back of sales, including phone sales, with product names resolved through products |
| segments | The label vocabulary, and the assignments per label or per customer |
| recommendations | A customer's recommendations, with their reasons |
| engagement | Summaries by store, day or type, and voids with a reason (ADMIN) |
| check-ins | Check-in list and append-only reviews (ADMIN) |
| campaigns *(second wave)* | Campaign list, editing in DRAFT, and transitions (MARKETING, ADMIN) |
| audit-trail *(second wave)* | Audit entries (ADMIN, AUDITOR) |

**Both clients.**

- They take their base URL from configuration and call
  `/api/<service>/v1/...`.
- They send `X-Correlation-ID` and show it with any error.
- They run against the local stack or the instance alike.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Both clients run the same catalog-and-sales process, in different formats | It fails "a different process". The demonstration would show one flow twice |
| A public Electron client with no authentication, as in the reference | Every service is authenticated ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)), and the back office administers users and the catalog. A client with no sign-in could do neither |
| Push notifications as one of Android's device capabilities | They need Firebase Cloud Messaging, which is an external project and service with its own credentials. Camera/QR, GPS and Room need nothing outside the device |
| A new `STORE_ASSOCIATE` role for the phone | Rejected by the user on 2026-10-13. A role is a business decision: a row in the seed-exempt `role` table and a row in the permission matrix. The web's matrix lives in `web/middleware/authz.py`, where an unknown role code holds no permission (`permissions_for`). Because the web is frozen, an account with the new role would sign into the web and reach nothing, and nothing could change that. [ADR-0023](0023-store-managers-read-segments-and-customers.md) already puts `STORE_MANAGER` at the counter |
| Let services embed related data (product names in recommendations, store names in summaries) to save round trips | It is the re-serving that [ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) forbids, and it ties one service's representation to another's release |
| An online-only phone, with no local queue | A dropped connection at the counter loses the sale, or tempts a second tap that records two. Room is also one of the device capabilities |
| Validate XML only in tests, not at runtime | The XSD exists so that the desktop refuses a response that drifted from its contract. A test only checks the examples |

## Consequences

**What this makes easy.**
- **The end-to-end demonstration closes.**
  1. The phone records a sale in JSON.
  2. sales-service verifies the token, and Redis confirms the session is live.
  3. The sale lands in PostgreSQL.
  4. The web shows it with no web change.
  5. Electron reads it back in XML.
- **Coverage.** Every service is exercised by at least one client, and twelve
  of them by both.
- **Floor writes are retry-safe end to end.** That covers sales, clicks and
  check-ins, and none of it depends on Redis.

**What this makes hard.**
- **Composition costs round trips.**
  - Ten recommendations mean ten product lookups on the phone, because
    `GET /v1/products` has no filter for many ids.
  - The phone caches product details for the session.
  - Electron resolves names in sales read-back and engagement summaries the
    same way.
- **Floor writes for `STORE_MANAGER`.** Recording sales and registering
  customers, preferences, check-ins and engagement events are new grants for
  that role, beyond what `docs/requirements.md` §3 shows today.
  - `app_user` has no store, as
    [ADR-0023](0023-store-managers-read-segments-and-customers.md) found.
  - A check-in records where a manager is, but confines nothing: a store
    manager can record a sale at any store.
- **The device clock is the client's.** `occurred_at` comes from the device.
  A phone whose clock runs ahead gets 422 `future_dated` for its sales.
- **Refused queued writes are a user-facing case.** A sale queued offline can
  be refused hours later, for example with `stock_unavailable`, and someone
  has to act on it.
- **Two more toolchains.** Gradle with the Android SDK, and Node with
  Electron, each needing its own CI (epics #402 and #403).
- **Unsigned Electron packages.** They trigger operating-system warnings that
  the demonstration has to explain.

**What must now be true elsewhere.**
- `docs/requirements.md` §3 records the floor writes granted to
  `STORE_MANAGER`. Each service transcribes them into its `x-mosaiq-roles`
  ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)).
- No client story starts before the contracts it consumes have merged
  ([ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)).
- engagement-service treats a repeated `client_event_id` as the original
  event
  ([ADR-0038](0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md)).
  sales-service treats a repeated `Idempotency-Key` as the original sale
  ([ADR-0039](0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md)).
- `stores.store_location` is seeded for every seeded store, so a GPS check-in
  can be verified in the demonstration.
- `AGENTS.md` lists the client conventions: ktlint, ESLint and Prettier, and
  the three `BrowserWindow` settings.
- `docs/scope-delivery-2-distributed.md` describes both processes and the
  floor role.

## Compliance

```bash
# Android never asks for or parses XML; Electron never asks a service for JSON.
grep -rnE "application/xml|XmlPullParser|SimpleXml" clients/android/ --include='*.kt' \
  && echo "Android touches XML"
grep -rn "application/json" clients/desktop/src/ && echo "desktop requests JSON"

# Electron security defaults, and no token outside the main process.
grep -rnE "nodeIntegration:\s*true|contextIsolation:\s*false|sandbox:\s*false|webSecurity:\s*false" \
  clients/desktop/src/ && echo "insecure BrowserWindow"
grep -rlE "safeStorage|refresh_token" clients/desktop/src/ | grep -i renderer && echo "token handling in renderer"

# Each client calls only registered prefixes. Every /api/<name>/ in client
# code must be a registry entry.
for p in $(grep -rhoE "/api/[a-z-]+/v[0-9]+" clients/android/ clients/desktop/src/ \
           | cut -d/ -f3 | sort -u); do
  yq -e ".services[] | select(.name == \"$p\")" contracts/registry.yaml >/dev/null \
    || echo "client calls unregistered service: $p"
done

# Retry ids are generated once, at creation, never per attempt: no UUID
# generation inside the WorkManager workers.
grep -rn "UUID.randomUUID" clients/android/ --include='*Worker*.kt' \
  && echo "retry id generated per attempt"
```

The client test suites also assert the following:

- **Exactly one click per tap.** An Android instrumented test taps one
  recommendation, forces two network failures and a success, then asserts
  through `GET /v1/engagement-events?type=RECO_CLICK` that exactly one event
  exists.
- **A replayed sale stays one sale.** Replaying a queued sale returns 200
  with `Idempotent-Replayed: true`.
- **Drifted XML is refused.** An Electron test feeds the main process an XML
  response with a foreign namespace and another with an extra element, and
  asserts that both are refused and neither is rendered.
- **The renderer has no Node.** An Electron test asserts that
  `typeof require` and `typeof process` are `undefined` in the renderer.
