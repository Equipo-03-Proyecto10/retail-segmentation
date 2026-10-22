# MOSAIQ back office (desktop client)

Electron and TypeScript. It talks to every service only in XML, validates each request and each
response against that service's own XSD, and signs in through auth-service
([ADR-0041](../../docs/adr/0041-android-and-electron-clients-run-different-processes-over-different-formats.md),
[ADR-0035](../../docs/adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md),
[ADR-0036](../../docs/adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)).
F16-01 is the foundation: shell, secure window, preload bridge, XML transport, sign-in and
role-gated screens. F16-02 adds Products and Categories, F16-04 adds Stock, Sales, Engagement and
Check-ins, F16-06 adds Users, Transfer administrator and the account menu, F16-07 adds Stores
with their locations, F16-08 adds Campaigns and their transitions, and F16-09 adds the Audit trail
(see below).

## Run it

Needs Node 20.19 or later.

```sh
cd clients/desktop
npm ci
npm start        # bundles the contracts, builds, launches Electron
```

`npm start` copies `contracts/<name>/<name>.xsd` for every entry of `contracts/registry.yaml`, plus
`contracts/common/v1/common.xsd`, into `bundled/schemas/`, and writes every operation's
`x-mosaiq-roles` to `bundled/operations.json` (`scripts/bundle-contracts.mjs`). The build fails when
a service has an `openapi.yaml` but no XSD. A registry entry whose contract has not merged yet is
skipped, listed in the output, and its screens are not offered. `bundled/` and `dist/` are generated
and git-ignored.

### Against the local stack

Start the stack and the gateway (F13-08). The default base URL is the gateway,
`http://127.0.0.1:8080`, so no setting is needed. Calls go to `<base URL>/api/<service>/v1/...`.

### Against the instance

Create `settings.json` in the configuration directory (Linux `~/.config/mosaiq-desktop/`, macOS
`~/Library/Application Support/mosaiq-desktop/`, Windows `%APPDATA%\mosaiq-desktop\`):

```json
{ "baseUrl": "https://<instance host>" }
```

The base URL is never hard-coded. A malformed settings file stops the app with an error rather than
being ignored. `MOSAIQ_DESKTOP_CONFIG_DIR` overrides the directory (the window tests use it).

## Security model

- Every window: `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, `webSecurity` left
  on; a CSP with no remote and no inline script; `will-navigate` cancelled; every new window denied;
  every permission request denied. The one window factory is `src/main/window.ts`.
- The screens reach the network only through the named operations of `src/preload/preload.ts`:
  - session: `getState`, `signIn`, `signOut`, `probeService`, `onStateChange`;
  - products: `listProducts`, `getProduct`, `createProduct`, `updateProduct`, `deactivateProduct`;
  - categories: `categoryTree`, `listCategories`, `categoryDescendants`, `createCategory`,
    `updateCategory`, `deleteCategory`.
  - floor activity: `stores`, `stockLevels`, `listSales`, `getSale`, `engagementSummary`,
    `listEngagementEvents`, `voidEngagementEvent`, `listCheckIns`, `getCheckIn`, `reviewCheckIn`.
  - users: `listUsers`, `getUser`, `listRoles`, `createUser`, `updateUser`, `deactivateUser`,
    `reactivateUser`, `resetUserPassword`, `changeOwnPassword`, `transferAdministrator`.
  - store locations: `listStores`, `getStore`, `setStoreLocation`, `removeStoreLocation`.
  - campaigns: `listCampaigns`, `getCampaign`, `createCampaign`, `updateCampaign`, `transitionCampaign`,
    `listCampaignLabels`.
  - audit trail: `listAuditEntries`, `getAuditEntry` (read only).

  There is no generic fetch and no `ipcRenderer`. The main process checks every value a screen sends
  (`src/main/checks.ts`, used by `catalog.ts` and `floor.ts`) before it builds a path or a body from it.

- Tokens live in the main process only, and only as sealed bytes (see Token storage below).
- A response is checked in the main process before it is parsed: its root namespace must be the called
  service's (or `common/v1` for an `Error`) and it must validate against the bundled XSD
  (`xmllint-wasm`). Otherwise it is a contract defect naming the service and the correlation id, and
  nothing of it is rendered.
- Access tokens are refreshed through one single-flight path; waiting calls are retried once.
- The client never calls `GET /v1/jwks.json`.

## Token storage

The access and refresh tokens are never held as plain strings between calls. Each is sealed as soon as
it arrives and opened into a local variable only to build the request that needs it (the
`Authorization` header, or the body of `POST /v1/sessions/refresh`).

| Mode          | When                                                                                 | Sealed with                                     | Kept across restarts                                        |
| ------------- | ------------------------------------------------------------------------------------ | ----------------------------------------------- | ----------------------------------------------------------- |
| `os_keyring`  | macOS Keychain, Windows DPAPI, or a Linux keyring (GNOME Keyring, KWallet)           | `safeStorage`                                   | Yes, in `session.bin` (mode 0600)                           |
| `memory_only` | No keyring, including Linux's `basic_text` backend, whose key is built into Chromium | AES-256-GCM under a random key made at start-up | No; nothing is written, and an old `session.bin` is deleted |

In `memory_only` mode every screen shows: "This computer has no secure keyring. Your session is kept in
memory only and ends when the app closes."

Limits: a token is plain text for the moment a request is built and sent, since the server needs the
real value, and JavaScript cannot wipe a string. In `memory_only` mode the key sits in the same process,
so sealing keeps tokens out of casual memory dumps and swap scans, not away from code running inside the
app.

## Products and categories (F16-02)

- **Products** lists `GET /v1/products` with its filters (`q`, `sku`, `category_id`, `active`) and
  paging, and shows one product. Each category name is read from categories-service, never from
  products-service. An `ADMIN` creates (`POST`), edits (`PATCH` with only the changed fields) and
  deactivates (`PATCH` of `is_active` to false). There is no delete: products are retired (RN-11).
- **Categories** builds the tree from every page of `GET /v1/categories` and shows a category's
  subtree from `.../descendants`. An `ADMIN` creates, edits (choosing "No parent" sends
  `xsi:nil`) and deletes.
- A write control is drawn only when the signed-in role may call that operation, per the
  contracts' `x-mosaiq-roles` (`operations` in the signed-in state). Every refusal is shown as
  returned: code, message, correlation id, and each `details` entry next to its field. After a
  refused category change the tree is reloaded, so what is shown is what the service still has.
- The window tests (`tests/window/catalog.spec.ts`) answer with the recorded examples in
  `contracts/products/examples/` and `contracts/categories/examples/`, read from disk.

## Stock, sales, engagement and check-ins (F16-04)

A stock level, a sale or an event carries ids only (ADR-0035). `src/main/names.ts` reads the names
from their owners within each call: products through `GET /v1/products?ids=`, 50 ids per call;
stores through every page of `GET /v1/stores`; channels through `GET /v1/channels`. A name that
cannot be read shows as unavailable, and the screen still loads.

- **Stock**: levels from `GET /v1/stock-levels`, by store and below a threshold.
- **Sales**: `GET /v1/sales` with customer, store and a UTC time range, and one sale with its lines,
  line totals and the total as returned. **Refresh** reads the list again, so a sale the phone
  recorded in JSON shows here, read in XML and validated against `sales.xsd`.
- **Engagement**: the summary grouped by store, day or type, then the event list for roles that may
  read events (not `INVENTORY_PLANNER`). An `ADMIN` voids an event with a required reason: it stays
  listed, marked voided, with its history, and the summary is read again. There is no delete.
- **Check-ins** (`ADMIN`, `STORE_MANAGER`, `AUDITOR`): each check-in with its `verified` flag and its
  reason in plain words, and one check-in with its reviews in order. An `ADMIN` appends a review with
  a verdict and a required reason; nothing edits or deletes a check-in or a review.

The navigation shows a screen only when the role may call its read operation. The window tests
(`tests/window/floor.spec.ts`) answer with the recorded examples of the six contracts involved.

## Users, account menu and administrator transfer (F16-06)

All of it goes through users-service (`contracts/users/`). The single-administrator rule is the
service's and the schema's: the client only reflects the answers, so `single_administrator`,
`use_administrator_transfer`, `administrator_required`, `email_taken`, `successor_inactive` and
`invalid_successor` are shown as returned (code, message, correlation id, each `details` entry next to
its field).

- **Users** (`ADMIN` and `AUDITOR`; hidden from every other role): `GET /v1/users` with `role`,
  `active`, `q` and paging, one user from `GET /v1/users/{user_id}`, and the role names from
  `GET /v1/roles`. `AUDITOR` reads only. An `ADMIN` creates (the role picker offers every role except
  `ADMIN`), edits name, email and a non-`ADMIN` role, deactivates (after a confirmation) and
  reactivates, and resets another user's password. There is no delete control: accounts are
  deactivated, never deleted. A `use_administrator_transfer` refusal adds a pointer to the transfer
  screen.
- **Passwords** are 12 characters or more (the XSD facet, checked before sending, so a short one is
  refused as `invalid_body` with `min_length` on its field) and are never shown back: the field is
  emptied on submit and no response carries one.
- **Account menu** (every staff role): changes the caller's own password with
  `PUT /v1/users/{user_id}/password`, using the id from the session claims and the current password. A
  wrong current password and a 429 `rate_limited` (with its `Retry-After`) are shown as returned. A
  success ends every session of the user, the caller's own included, so the app returns to sign-in with
  "Password changed, sign in again."
- **Transfer administrator** (`ADMIN`): pick an active successor and confirm a dialog that warns that
  the change ends the sessions of both accounts, then `POST /v1/administrator-transfers`. After a
  success the screen makes one more call; users-service refuses it with 401 `session_revoked`, and so
  does any call after a deactivation or a password change. The app treats that 401 on any call as the
  end of the session and returns to sign-in.

The window tests (`tests/window/users.spec.ts`) answer with the recorded examples in
`contracts/users/examples/`, read from disk, and keep the users they create, change and hand over.
The transfer test signs in again as the successor and reads `GET /v1/users?role=ADMIN`.

## Store locations and geofences (F16-07)

Through stores-service (`contracts/stores/`). The **Stores** screen is open to every staff role:

- The directory comes from `GET /v1/stores` (`active`, `city`, paging); a store opens from
  `GET /v1/stores/{store_id}`. A store without a location reads "no location: check-ins here are
  recorded as store_location_unknown".
- An `ADMIN` sets a location with `PUT /v1/stores/{store_id}/location` (`latitude`, `longitude`,
  `geofence_radius_m`) and removes it with `DELETE` after a confirmation. The screen then shows what
  the service stored: the directory is read again. These are the only calls that write coordinates
  (ADR-0035). Other roles see no control, and a forced write gets 403 `forbidden_role`, shown as
  returned.
- The bounds are the XSD's facets: latitude -90 to 90, longitude -180 to 180, radius 25 to 2000 m.
  The main process checks them first so the refusal names its field (`out_of_range`,
  `invalid_format`, `required`), nothing is sent, and the XSD check in the transport stays the last
  gate. A forced request that the service refuses with 422 `invalid_body` shows its `details` next to
  the field; an unknown store shows `store_not_found` as returned.
- No map is drawn: coordinates are plain numbers, and the CSP of F16-01 (no remote script, tile, image
  or font) is unchanged. The window test records every request and finds none outside the app.

The window tests (`tests/window/stores.spec.ts`) answer with the recorded examples in
`contracts/stores/examples/`, read from disk, and keep the locations they are given.

## Campaigns and their transitions (F16-08)

Through campaigns-service (`contracts/campaigns/`). The **Campaigns** screen is shown to `ADMIN`,
`ANALYST`, `MARKETING` and `AUDITOR` and hidden from every other role, by the `x-mosaiq-roles` of
`listCampaigns`:

- The list comes from `GET /v1/campaigns` (`status`, `label_code`, paging); a campaign opens from
  `GET /v1/campaigns/{campaign_id}`. A campaign carries only a label code, so the label names come from
  segments-service (`GET /v1/segments`, `listCampaignLabels`), also for the label pickers. A name that
  cannot be read leaves the code on screen and shows the error.
- `ADMIN` and `MARKETING` create a campaign (`POST /v1/campaigns`; it is shown as `DRAFT`) and edit one
  (`PATCH`, only the changed fields). `ANALYST` and `AUDITOR` see no control. Outside `DRAFT` the edit
  controls are drawn disabled, and a forced edit gets 409 `campaign_not_draft`, shown as returned.
- A transition is `POST /v1/campaigns/{campaign_id}/transitions` with `to` and the `expected_status`
  the screen displayed. The buttons are the moves the service lists in `allowed_transitions`
  (`DRAFT` to `ACTIVE` or `CANCELLED`, `ACTIVE` to `FINISHED` or `CANCELLED`); `FINISHED` and
  `CANCELLED` offer none. The client does not decide whether a move is legal, so a forced one is
  answered by the service.
- The lifecycle and the dates are the service's: the client checks only types and lengths. Refusals are
  shown as returned (code, message, correlation id): `ends_before_start` with its `ends_on` detail next
  to the dates, `duplicate_draft` and `unknown_label`, `illegal_transition`, `status_changed`,
  `campaign_ended` and `experiment_not_ready`. After `status_changed` the campaign is read again and
  the move is not retried.

The window tests (`tests/window/campaigns.spec.ts`) answer with the recorded examples in
`contracts/campaigns/examples/` and `contracts/segments/examples/`, read from disk, and keep the status
of the campaign they transition.

## Audit trail (F16-09)

Through audit-trail (`contracts/audit-trail/`). The **Audit trail** screen is offered only to `ADMIN`
and `AUDITOR`, by the `x-mosaiq-roles` of `listAuditEntries`. Any other role has no entry in the
navigation, and a forced call gets 403 `forbidden_role`, shown as returned.

- Entries come from `GET /v1/audit-entries` with `entity`, `entity_pk`, `user_id`, `action`, `from`, `to`
  and paging; one opens from `GET /v1/audit-entries/{audit_id}`. The main process checks every filter
  first (the audited table names, `INSERT`/`UPDATE`/`DELETE`, a UUID, RFC 3339 timestamps with an
  offset, a key of at most 80 characters, and the audit id as a positive 64-bit integer kept as text).
  `invalid_query` (with its `details`) and `audit_entry_not_found` are shown as returned.
- The acting user is shown by its `user_id`; an entry with no known actor says so. The screen does not
  resolve names, so it needs no call to users-service.
- `data_before` and `data_after` are JSON text in the XML. They are formatted when they parse and set
  with `textContent` inside a `<pre>`; nothing is rendered as HTML. audit-trail strips credentials, and
  the client adds none: no password or hash is requested or shown.
- The trail is append-only: the screen has no create, edit or delete control and the bridge has no
  operation for one.

The window tests (`tests/window/audit.spec.ts`) answer with the recorded examples in
`contracts/audit-trail/examples/`. The fake gateway records a product edit and then answers the audit
query with an entry for that entity, key and the signed-in user.

## Checks

```sh
npm run lint
npm run format:check
npm run typecheck        # tsc --noEmit
npm test                 # unit tests (Vitest)
npm run test:window      # Playwright Electron tests; uses xvfb-run on a headless Linux
```

Compliance greps (both print nothing):

```sh
grep -rn "application/json" src/
grep -rlE "safeStorage|refresh_token" src/ | grep -i renderer
```
