# ADR-0041 — The Android and Electron clients run different processes over different formats

**Status:** Partially superseded by ADR-0060 (the floor roles)
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** [ADR-0060](0060-four-store-profiles-map-to-four-roles-and-services-read-each-users-stores-per-request.md), in part

---

## Context

The second delivery requires two clients:

| Client | Format | Requirement |
|---|---|---|
| Android (Kotlin) | JSON only | At least four microservices and two device capabilities |
| Desktop (Electron) | XML only, validated against an XSD | At least four microservices, in a **different** process from the mobile one |

The instructor's reference shows a public Electron client that reads one service and validates
nothing. Our scope asks for more: authentication, four or more services, and XSD validation.

Clients now face **fifteen narrow services** ([ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)); **no composition by services**
([ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)), so a recommendation gives a `product_id`, not a product; and **rotating refresh
tokens** in a session of at most 12 hours ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)), so two racing refreshes end the session.

Four decisions are open:
- **Who carries the phone.** The seven roles of `docs/requirements.md` §3 hold no in-store
  associate, and a new role is a business decision. On 2026-10-13 the team decided that the floor
  role is `STORE_MANAGER`, following [ADR-0023](0023-store-managers-read-segments-and-customers.md).
- **Which process each client runs,** visibly different in the demonstration, not just claimed.
- **What each client may assume about the services,** which must hold when one is redeployed alone.
- **Which security baseline each starts from,** given `nodeIntegration` and backed-up tokens.

## Decision

The two clients run different processes for different users.
- **Android, the floor process.** It runs the store floor for `STORE_MANAGER` and `ADMIN`, in JSON,
  against thirteen services. Its device capabilities are camera/QR, GPS and Room.
- **Electron, the back-office process.** It runs maintenance, review and monitoring for the staff
  roles, in XML validated against each service's own XSD, against all fifteen services.

Each client calls a resource only at the service that `contracts/registry.yaml` registers it to. It
composes related data itself from ids, and never assumes that one service returns another's data.
Each client generates its retry ids once, at the moment of the action, so a retry never creates a
second sale, click or check-in. Each refreshes its tokens through one single-flight path.

**Android: the floor process.**
- **Stack.** Kotlin, Jetpack Compose, Retrofit, OkHttp and kotlinx.serialization with
  `ignoreUnknownKeys`. It sends `Accept: application/json` and never parses XML.
- **Tokens** are encrypted with an Android Keystore key, excluded from backup, and refreshed
  single-flight by one OkHttp `Authenticator`.
- **Users.** `STORE_MANAGER` and `ADMIN`. Other roles are turned away after sign-in with a plain
  message, a usability gate only: the services refuse them anyway (`x-mosaiq-roles`).
- **Process.** Sign in; check in at a store by GPS; scan a QR code and see availability; find or
  register the customer, capture interests; see segment and recommendations; record the sale.

| Service | What the phone does there |
|---|---|
| auth | Sign in, refresh and sign out (`/v1/sessions`) |
| users | Show who is signed in (`GET /v1/users/me`) |
| stores | Store picker (`GET /v1/stores`) |
| check-ins | GPS check-in (`POST /v1/check-ins` with `client_check_in_id`). The server computes the distance and decides whether it is verified |
| products | QR detail (`GET /v1/products/{product_id}`; the QR payload is `product_id`) and one batch lookup (`GET /v1/products?ids=`) per recommendation list |
| stock | Availability at the checked-in store (`GET /v1/stock-levels/{store_id}/{product_id}`) |
| engagement | `QR_SCAN` after each decoded scan. Exactly one `RECO_CLICK` per tap on a recommendation, with `label_code` and `rank` (`POST /v1/engagement-events`) |
| customers | Search and register at the counter (`GET` and `POST /v1/customers`) |
| categories | Category names for the interest picker (`GET /v1/categories`) |
| customer-preferences | Interest capture (`PUT /v1/preference-profiles/{customer_id}/interest-categories`) |
| sales | Channel list (`GET /v1/channels`), with `physical_store` preselected and `channel_id` sent. `POST /v1/sales` with a client UUID in `Idempotency-Key`. `GET /v1/sales/{sale_id}` for status |
| segments | The current assignment (`GET /v1/segment-assignments/current?customer_id=`), with the `no_open_assignment` and `unassigned_result` states shown as they are |
| recommendations | `GET /v1/recommendations?customer_id=` |

- **Device capabilities.** Camera/QR (CameraX, ML Kit barcode scanning), GPS
  (`FusedLocationProvider`, sending the fix's accuracy) and Room; each permission is asked for at
  first use.
- **Floor writes.** Sales, engagement events and check-ins go first to Room with their client id
  (`Idempotency-Key`, `client_event_id` or `client_check_in_id`), generated once, at the moment of
  the tap. A WorkManager job resends each row with the same id until a final answer arrives, so a
  tap produces exactly one `RECO_CLICK` and a sale replayed after a timeout returns the original
  ([ADR-0039](0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md)). A queued row is resent only under the session of the user who
  created it, because a write must be attributed to the user who acted, and each of the three services records that user from
  the token's `sub`. Under another user's token, sales and check-ins also refuse a replay of a write that already landed: sales
  answers 409 `idempotency_key_reused`, since its request hash includes the `sub` (ADR-0039), and the check-ins contract answers
  409 `client_check_in_conflict`, since the `user_id` differs. A write finally refused, such as a sale with 409 `stock_unavailable`, stays visible
  in an outbox screen with its error code, never dropped. Reads are not queued.

**Electron: the back-office process.**
- **Stack.** TypeScript. Every `BrowserWindow` has `contextIsolation: true`,
  `nodeIntegration: false` and `sandbox: true`. A strict CSP allows no remote and no inline scripts,
  navigation and new windows are blocked, and the renderer reaches the network only through a typed
  preload bridge of named operations, never a generic `fetch`.
- **Tokens** stay in the main process, encrypted with `safeStorage`, refreshed single-flight.
- **XML both ways.** Requests send `Accept: application/xml` and XML bodies. The main process
  bundles the XSDs of `contracts/<service>/` and `contracts/common/v1/`, keyed by namespace. Before
  parsing a response it checks that the root element's namespace is the called service's, or
  `common/v1` for a `common:Error` (ADR-0035), and that the document validates (`xmllint-wasm`).
  Drifted XML is refused as a contract defect naming the service, never rendered. Response types
  are frozen within a major (ADR-0035).
- **Users.** The staff roles. Screens follow the bundled `x-mosaiq-roles`; the services still
  enforce every call, and a 403 is shown, not hidden.
- **Services.** It uses all fifteen services: user administration and the administrator transfer;
  catalog, category and store-location maintenance (`ADMIN`); stock below a threshold; customers and
  both preference sets (`PUT /v1/preference-profiles/{customer_id}/preferred-channels` and
  `/interest-categories`); sales read-back, with product names from one `GET /v1/products?ids=` per
  page; segments and recommendations; engagement summaries and voids; check-in review; and, in the
  second wave, campaigns and the audit trail.

**Both clients** take the base URL from configuration, call `/api/<service>/v1/...`, send
`X-Correlation-ID` and show it with any error.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Both clients run the same catalog-and-sales process, in different formats | It fails "a different process". The demonstration would show one flow twice |
| A public Electron client with no authentication, as in the reference | Every service is authenticated ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)), and the back office administers users and the catalog. A client with no sign-in could do neither |
| Push notifications as one of Android's device capabilities | They need Firebase Cloud Messaging, which is an external project and service with its own credentials. Camera/QR, GPS and Room need nothing outside the device |
| A new `STORE_ASSOCIATE` role for the phone | Rejected by the team on 2026-10-13. A role is a business decision: a row in the seed-exempt `role` table and a row in the permission matrix. The web's matrix lives in `web/middleware/authz.py`, where an unknown role code holds no permission (`permissions_for`). Because the web is frozen, an account with the new role would sign into the web and reach nothing, and nothing could change that. [ADR-0023](0023-store-managers-read-segments-and-customers.md) already puts `STORE_MANAGER` at the counter |
| Let services embed related data (product names in recommendations, store names in summaries) to save round trips | It is the re-serving that [ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) forbids, and it ties one service's representation to another's release |
| An online-only phone, with no local queue | A dropped connection at the counter loses the sale, or tempts a second tap that records two. Room is also one of the device capabilities |
| Validate XML only in tests, not at runtime | The XSD exists so that the desktop refuses a response that drifted from its contract. A test only checks the examples |

## Consequences

**What this makes easy.**
- **The end-to-end demonstration closes:** the phone records a sale in JSON and Electron reads it
  back in XML, as success criterion 8 of [`scope-delivery-2-distributed.md`](../scope-delivery-2-distributed.md) lists.
- **Coverage:** every service has at least one client, thirteen have both; floor writes retry safely.

**What this makes hard.**
- **Composition costs round trips:** one batch product lookup (`GET /v1/products?ids=`, at most 50
  ids) per recommendation list or sales page. The phone caches product details for the session.
- **Floor writes for `STORE_MANAGER`** are new grants, recorded in the [service operation
  policy](../requirements.md#service-operation-policy-distributed-stage). `app_user` has no store
  (ADR-0023), so a check-in confines nothing: a store manager can record a sale at any store.
- **The device clock is the client's:** a fast phone clock gets 422 `future_dated` for its sales.
- **Refused queued writes are a user-facing case:** an offline sale can be refused hours later.
- **Two more toolchains** (Gradle with the Android SDK, Node with Electron), each with its own CI
  (#402, #403), and **unsigned Electron packages**, whose warnings the demonstration must explain.

**What must now be true elsewhere.**
- `docs/requirements.md`'s Service operation policy (distributed stage) records the floor writes,
  and the web's §3 matrix is unchanged; services copy them into `x-mosaiq-roles` (ADR-0036).
- No client story starts before the contracts it consumes have merged (ADR-0035).
- engagement and sales treat a repeated id as the original ([ADR-0038](0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md), ADR-0039).
- `stores.store_location` is seeded for every seeded store, so a GPS check-in can be verified.

## Compliance

```bash
# Android never touches XML; Electron never asks a service for JSON.
grep -rnE "application/xml|XmlPullParser|SimpleXml" clients/android/ --include='*.kt' && echo "Android touches XML"
grep -rn "application/json" clients/desktop/src/ && echo "desktop requests JSON"
# Electron security defaults, and no token handling in the renderer.
grep -rnE "nodeIntegration:\s*true|contextIsolation:\s*false|sandbox:\s*false|webSecurity:\s*false" \
  clients/desktop/src/ && echo "insecure BrowserWindow"
grep -rlE "safeStorage|refresh_token" clients/desktop/src/ | grep -i renderer && echo "token handling in renderer"
# Every /api/<name>/ in client code is a registry entry.
for p in $(grep -rhoE "/api/[a-z-]+/v[0-9]+" clients/android/ clients/desktop/src/ | cut -d/ -f3 | sort -u); do
  yq -e ".services[] | select(.name == \"$p\")" contracts/registry.yaml >/dev/null || echo "client calls unregistered service: $p"; done
# Retry ids are generated once, never per attempt: no UUID generation in WorkManager workers.
grep -rn "UUID.randomUUID" clients/android/ --include='*Worker*.kt' && echo "retry id generated per attempt"
```

The client test suites also assert that:
- a tap retried through two network failures leaves exactly one `RECO_CLICK` event;
- a replayed queued sale returns 200 with `Idempotent-Replayed: true`;
- Electron refuses, and never renders, XML with a foreign namespace or an extra element;
- `typeof require` and `typeof process` are `undefined` in Electron's renderer.
