# MOSAIQ back office (desktop client)

Electron and TypeScript. It talks to every service only in XML, validates each request and each
response against that service's own XSD, and signs in through auth-service
([ADR-0041](../../docs/adr/0041-android-and-electron-clients-run-different-processes-over-different-formats.md),
[ADR-0035](../../docs/adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md),
[ADR-0036](../../docs/adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)).
F16-01 is the foundation: shell, secure window, preload bridge, XML transport, sign-in and
role-gated screens. F16-02 adds Products and Categories, F16-04 adds Stock, Sales, Engagement and
Check-ins, F16-06 adds Users, Transfer administrator and the account menu, F16-07 adds Stores
with their locations, F16-08 adds Campaigns and their transitions, F16-09 adds the Audit trail, and
F16-03 adds Customers (see below).

## Run it

Needs Node 22.12 or later.

```sh
cd clients/desktop
npm ci
npm start        # bundles the contracts, builds, launches Electron
```

`npm start` copies `contracts/<name>/<name>.xsd` for every entry of `contracts/registry.yaml`, plus
`contracts/common/v1/common.xsd`, into `bundled/schemas/`, together with each later major a service
publishes beside `/v1` as `contracts/<name>/<name>.v<N>.xsd` under its own namespace (ADR-0035), and writes every operation's
`x-mosaiq-roles` to `bundled/operations.json` (`scripts/bundle-contracts.mjs`). The build fails when
a service has an `openapi.yaml` but no XSD, or declares `/v<N>` operations without a `<name>.v<N>.xsd`. A
call's schema follows the major in its path: `/v2/...` is checked against the `v2` namespace and a `/v1`
answer on it is refused as a contract defect, so `/v1` callers keep the schema they validate against. A registry entry whose contract has not merged yet is
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
  - drift analysis: `listDriftReports`, `getDriftReport`, `listDriftAlerts`, `getDriftAlert` (read only).
  - migration matrix: `migrationMatrix`, `listMigrations`, `explainMigration` (read only).
  - A/B experiments: `listExperiments`, `getExperiment`, `planExperiment`, `assignExperiment`.
  - conversion analysis: `getExperimentResult` (read only).
  - K-means models: `listModelVersions`, `getModelVersion`, `listTrainings`, `getTraining`,
    `startTraining`, `publishModelVersion`.
  - recommendations: `inspectRecommendation`, `listRecommendationDecisions`, `openRecommendationDecision`
    (read only).

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

## A/B experiments (F28-10)

Through experimentation-service (`contracts/experimentation/`, ADR-0068). The **A/B experiments** screen is
shown to `ADMIN`, `ANALYST`, `MARKETING` and `AUDITOR`, by the `x-mosaiq-roles` of `listExperiments`; `ADMIN`
and `MARKETING` also plan and assign. The main process checks the operation before every call, so a forced
call from another role is refused as `forbidden_role` and nothing is sent. The service's operations are
published ahead of their implementation (ADR-0073); the screen shows whatever the service answers.

- **List and detail.** `GET /v1/experiments` (`state`, `data_origin`, `campaign_id`, paging) and
  `GET /v1/experiments/{experiment_id}`: arms, design values, frame with each exclusion's count, the published
  salt, flags and the assignment. An experiment of assignment rule V0 is readable and says it is not verified.
  Figures from a `SEEDED` or `INJECTED` experiment are labelled Synthetic.
- **Plan** (`POST /v1/experiments`). The form sends only the inputs of the contract: name, campaign, primary
  metric, start, conversion window, minimum detectable effect, optional alpha and power, and two to five
  arms. The baseline, the sample size, the frame and the salt are never inputs: the service takes them in
  the plan's transaction. A plan is frozen, so a confirmation says it cannot be edited or deleted. While arms
  are entered the screen reads the design (one control and treatments; two treatments and no control;
  or one the service would refuse) and disables the button for the last. The arm rules themselves stay the
  service's: `control_count` and `treatment_count` come back as returned.
- **Sample size.** Right after the plan, and on every later read, the screen shows the customers each arm
  needs (`design.required_per_arm`, computed by the service from the plan, never by the client), the frame
  and what it supplies to each arm, and the **Underpowered** flag the service stored, with the smallest
  effect the frame can detect. A result that is not significant is said to be inconclusive, never "no
  effect".
- **No control.** A design with no control arm says, while it is entered and wherever it is read, that no
  effect of the campaign against no treatment can be claimed: it compares two offers.
- **Assign** (`POST /v1/experiment-assignments`). Offered for a `PLANNED` experiment after a confirmation
  that the assignment is written once. An underpowered plan needs the acknowledgement box ticked, and the
  acknowledgement is sent only then (`underpowered_not_acknowledged` is the service's answer when it is
  missing). The assignment's recomputation, **sample ratio check** and **balance checks** (standardized
  difference per measure against the reference arm, the threshold, and whether it is flagged) are shown from
  the response and again whenever the experiment is opened.
- A `<name>` or description from the service is set with `textContent`; entities are not decoded by the
  client's XML reader, so a name that holds markup is shown as typed.

Not in this screen: the conversion results (the conversion analysis module, F28-11, #880), the list of conversions
and `POST /v1/experiments/{experiment_id}/conversions`, the whole-assignment list (it names every customer),
the floor's `GET /v1/experiment-assignments/current`, and the sale attributions.

The window tests (`tests/window/experiments.spec.ts`) answer with the recorded examples of
`contracts/experimentation/` and `contracts/campaigns/`, read from disk, and keep whether the plan was
assigned, so `underpowered_not_acknowledged` arises from what is stored.

## Conversion analysis (F28-11)

Through experimentation-service (`contracts/experimentation/`, ADR-0068 and ADR-0069). The **Conversion
analysis** screen is shown to `ADMIN`, `ANALYST`, `MARKETING` and `AUDITOR`, by the `x-mosaiq-roles` of
`getExperimentResult`; the main process checks the operation before the call, so a forced read from another
role is `forbidden_role` and nothing is sent. Nothing is written: the module has no write control and the
bridge has no write for it. The operation is published ahead of its implementation (ADR-0073).

- **Open a result.** The picker lists the experiments that are `ASSIGNED` or `ANALYZED` (one call each,
  `GET /v1/experiments?state=`): a `PLANNED` experiment has no result. The reading is the stored primary
  result, or "Re-read as of now" (`basis=RE_READ`), which says it replaces nothing. Both are
  `GET /v1/experiment-results/{experiment_id}`.
- **Each arm, and the pending customers.** A table of each arm's assigned, analysed, exposed, converted,
  **pending** and not-converted customers, its conversion rate (the exposed rate is labelled descriptive and
  not causal), net revenue and revenue per customer. A sentence states the pending customers of each arm:
  they have not bought yet, their window is open, and they are counted in the arm's customers. Every
  figure is as the service sent it; a fraction is written as a percentage by moving its decimal point.
- **The difference and its interval.** Each comparison shows the difference, its interval at the service's
  confidence level, the p-value, the test (with the bootstrap beside a Welch interval, and a warning when it
  disagrees) and the conclusion. A rate difference is also written in percentage points. A result that is
  not read yet (`PENDING`) shows counts and the share of the window elapsed and no interval, p-value or
  direction; one whose sample ratio check failed (`INVALID`) says its estimate is withheld.
- **An effect only against a control.** A comparison is an **Effect against the control** (and money of
  that effect, "Incremental revenue", with its interval) only when the design is controlled, its arms hold
  exactly one control and the comparison is a treatment against it. Two treatments with no control show a
  **Difference between treatments**, never the words effect, incremental or uplift, and say that no effect
  can be claimed, with the service's own refusal. An `EFFECT` the service sends without a control is not
  shown and is reported as a contract problem. A non-significant result of an underpowered plan reads
  "inconclusive", never "no effect".
- **Attribution apart.** Attribution is a section of its own, every figure labelled Attribution, saying it
  is commercial credit and not a measure of what the campaign changed. It never uses the words incremental,
  uplift or caused, and its figures are not in the effect table.
- **The warnings of the design stay next to the result.** A bar under the title lists them (what the design
  flagged, no control, not read yet, withheld, inconclusive, Synthetic, re-read) and stays in view while the
  result scrolls; each is written out below the facts, repeated above each comparison table, and carried on
  every arm row. The design values and the checks (sample ratio and balance) the result was planned and read
  under are on the same screen.
- **Export.** The shared CSV export (ADR-0074) writes the first table, the arms table. Each of its rows
  carries the data origin (Synthetic or Observed), the instant the net sales were read and the design
  warnings, so the file says what it is: observed counts, neither an effect nor attribution. The
  comparisons and attribution are not exported; ADR-0074 leaves the labelled experiment report to the web.

The window tests (`tests/window/conversion.spec.ts`) answer with the recorded results of
`contracts/experimentation/` (complete, revenue, inconclusive, pending, invalid and offer comparison), read
from disk.

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

## Customers, preferences, segments and recommendations (F16-03)

The Customers screen is offered to the roles whose `x-mosaiq-roles` allow `searchCustomers`: every
staff role except `INVENTORY_PLANNER`. Each panel shows what its own service returned, and the client
composes names from ids (ADR-0035). Each panel keeps its own `latestRequest()`, so a slow answer for a
customer opened earlier is dropped.

- **Customer** (customers-service). Search with `q` and an exact `email`, with paging; a customer opens
  from `GET /v1/customers/{customer_id}`. `ADMIN` and `STORE_MANAGER` correct it with `PATCH`; only the
  fields that changed are sent, because the contract has no way to clear a field, and an empty value is
  refused in the main process, naming its field. The registration channel is named from
  `GET /v1/channels` (sales-service). `email_taken`, `unknown_channel` and `invalid_body` are shown as
  returned.
- **Preferences** (customer-preferences). Channel names come from sales-service and category names from
  categories-service (every page, once). `ADMIN` and `STORE_MANAGER` edit a set with checkboxes and the
  app sends the whole set with `PUT .../preferred-channels` or `PUT .../interest-categories`. Unchecking
  everything sends an empty body, which clears the set. A chosen id whose service cannot be read is
  shown as "Channel 7 (unavailable)", never guessed. `unknown_channel` and `unknown_category` are shown
  as returned. Other roles see no control.
- **Segment** (segments-service). `GET /v1/segment-assignments/current?customer_id=` is printed as
  returned: `assigned` with the label (named from `GET /v1/segments`), the RFM scores, `since` and
  `run_id`, or `unassigned_result` and `no_open_assignment` with only the fields the service sent.
  "Show history" pages through `GET /v1/segment-assignments?customer_id=`. The label view lists the
  labels in `ordinal_position` order with their sizes; a label lists its members through
  `?label_code=` (current members by default, all rows with the box unchecked), and a member opens as
  a customer.
- **Recommendations** (recommendations-service). Each item shows its product name, category, the stock
  held at the usual store and its reasons, with the nearest matching category named. The names of a
  list come from exactly one `GET /v1/products?ids=` (a list holds at most 50 items). When nothing
  qualifies, the status is shown as returned: `none_match`, `no_open_assignment`, `unassigned_result`
  or `no_usual_store`.

The window tests (`tests/window/customers.spec.ts`) answer with the recorded examples of the seven
contracts, read from disk, including the `unassigned_result` and `no_open_assignment` examples that no
seeded customer has.

## Drift analysis (F28-07)

Through model-registry (`contracts/model-registry/`, version 1.1.0). The **Drift** screen, in the Analysis
group, is offered to the roles whose `x-mosaiq-roles` allow `listDriftReports`: `ADMIN`, `ANALYST`,
`MARKETING` and `AUDITOR`. Any other role has no entry in the navigation, and the main process refuses a
forced call with `forbidden_role` before sending anything. Each of the four reads is checked against its own
operation (`DRIFT_OPERATIONS`). The four operations are published ahead of their implementation and marked
planned (ADR-0073), so the window and unit tests answer with the recorded examples of
`contracts/model-registry/examples/`; a service that does not answer yet is shown as it answers. The screen
writes nothing: there is no operation that starts a run or acknowledges an alert.

- **Reports.** A list (`GET /v1/drift-reports`: kind, model version and comparison filters, twenty to a
  page), and a report opens from it (`GET /v1/drift-reports/{report_id}`). A `DATA_DRIFT` report charts, for
  each of the segment shares and R, F and M, the reference and the current share of every bin side by side,
  with the statistic (PSI, and the chi-square on the segment shares) beside the thresholds it is judged by
  (0.10 and 0.25; p below 0.05 and 0.01), the variable's state (`NORMAL`, `WATCH`, `ALARM_PENDING`,
  `ALARM`), a meter of each PSI against its two thresholds, and a table of every bin with its PSI
  contribution. An `OUTCOME_TEST` report names its relation, horizon and cohort, and charts each segment's
  reference and current repurchase rate with the two-proportion z, the Holm-adjusted p-value and the result.
- **Alerts.** A list (`GET /v1/drift-alerts`: kind, model version and variable filters) and an alert opens
  with its explanation first: what changed, the variable, the model version, and the suggested review
  (the service's text, shown as sent, with "A suggestion to review, not a verdict."), then how much: the
  statistics against their thresholds and the three bins that moved most, or the segments that persisted.
  An alert opens the report that raised it and the report before it, and a report opens its alerts.
- **No number, no guess.** An `INSUFFICIENT_DATA` report says that the window holds fewer than 200
  customers (or that no segment has 100 on both sides) and shows no statistic. A `NOT_COMPARED` report says
  that the window crosses a change of model version, that this is a methodological difference, and that
  nothing in it was compared (ADR-0064, ADR-0065). The screen never fills the gap.
- **The wording of F23-07.** A finding about a distribution is a "distribution change". The phrase of a
  change in the relation between a segment and an outcome is written in one function
  (`claimText` in `src/shared/drift.ts`), only for an outcome-test result that persisted, with the
  relation, the horizon and the cohort beside it; no other screen, list, chart or message says it, and no
  sentence about a window that crosses a version uses a word of behaviour. The main process also refuses an
  answer that says more than its own evidence supports (a claim without a persisted outcome result, a
  statistic in a report that holds no number, distribution evidence in an outcome-test alert, the phrase in
  a distribution alert's review): a contract defect, nothing of it used (`reportFault` and `alertFault` in
  `src/main/drift.ts`). `src/shared/drift-wording.ts` holds the rules, `tests/unit/drift.test.ts` scans the
  module's strings against them, and `tests/window/drift.spec.ts` reads back the text of every screen.
- **Charts** are SVG built element by element (`src/renderer/drift-chart.ts`): no markup string, no
  `innerHTML`, no chart library and nothing loaded from outside, so they run under the CSP as it is
  (`script-src 'self'`, `style-src 'self'`). The reference and the current are the first two categorical
  colours of the data-visualisation palette, in light and dark, with a legend; every pair also gives its
  values on hover and on keyboard focus and in the table beside the chart.
- Export is the shell's: the first table on the screen (the list of reports or alerts), as CSV, for the
  roles that may export (ADR-0074).

## Migration matrix (F28-06)

Through segments-service (`contracts/segments/`). The **Migration matrix** screen, in the Analysis group,
is offered to the roles whose `x-mosaiq-roles` allow `getSegmentMigrationMatrix`: `ADMIN`, `ANALYST`,
`STORE_MANAGER`, `MARKETING` and `AUDITOR`. Any other role has no entry in the navigation, and the main
process refuses a forced call with `forbidden_role` before sending anything. Each of the three reads is
checked against its own operation (`MIGRATION_OPERATIONS`). The three operations are published ahead of
their implementation and marked planned (ADR-0073), so the window and unit tests answer with the recorded
examples of `contracts/segments/examples/`; a service that does not answer yet is shown as it answers.

- **Two dates.** The screen compares the segment each customer held at two instants
  (`GET /v1/segment-migrations/matrix`, `from` and `to`, entered in UTC). The earlier date is always the
  before state, whatever order they are entered in (RN-59); two equal dates are refused first.
- **The grid.** Rows are the state before and columns the state after. The axes come from the label
  vocabulary (`GET /v1/segments`) in business order, then Unassigned and New (New is "Not in later run" on
  the after side), so a pair with no customers, which the service leaves out, is still a cell that can be
  opened. Each cell shows its count and the service's `movement`; the client never classifies a customer.
- **Totals reconcile.** The grid adds a row total and a column total from the cells, and the screen checks
  them against the service's figures: each row against its before count, each column against its after
  count, the customers who entered a state against its column less those who stayed, those who left against
  its row less the same, and all of them against the customers compared. It says "Totals reconcile", or
  lists every figure that does not (`reconcile` in `src/shared/migration-matrix.ts`).
- **Each cell opens its customers** (`GET /v1/segment-migrations` with the cell's two states and label
  codes), twenty to a page, and so does a label's entries or exits (`direction` and `label_code`). The list
  is compared with the count the matrix gave for it. "Behaviour only" and "Methodological only" send
  `methodological`.
- **Two versions are methodological.** When the service marks the matrix methodological, a banner says that
  a difference may come from the model and is not shown as behaviour, the versions are named (or said to be
  unknown), the grid is drawn with a dashed outline, and each customer carries "Methodological: may come
  from the model" (ADR-0064). The mark is the service's; the screen never derives or clears it.
- **A move names its responsible variables.** Each customer lists the variables the service judged changed;
  **Explain** opens `GET /v1/segment-migrations/explanation` with each variable's values on both sides, its
  judgement and the service's sentence (RN-60), "Only the rank moved" when the label changed and no variable
  did, and the reason when a variable is not compared.
- Export is the shell's: the first table, the grid, as CSV, for the roles that may export (ADR-0074).

The window tests (`tests/window/migrations.spec.ts`) answer with `tests/support/segments.ts`, which serves
the recorded matrices and explanations and generates the customers behind a cell from the matrix's own
cells, so a list holds exactly as many customers as its cell counted.

## K-means models (F28-05)

Through model-registry (`contracts/model-registry/`, ADR-0064). The **K-means** screen, in the Analysis
group, is offered to `ADMIN`, `ANALYST`, `MARKETING` and `AUDITOR` by the `x-mosaiq-roles` of
`listModelVersions`, and to no other role. Starting a training and publishing a version are `ADMIN` only
in the contract (RN-05), so the other three roles read and compare and are drawn no write control. The
main process refuses a write from a role the contract does not allow before anything is sent, and a
forced one gets `forbidden_role`.

- **Launch.** An `ADMIN` chooses the variables (R, F, M), the normalization (`MIN_MAX` or `NONE`), k (2 to
  20), the seed, the window in days, the iteration limit and the tolerance, and names the draft version.
  An empty seed is left out of the request: the service chooses one and records it (RN-58), and the
  screen shows the seed the training ran with. `POST /v1/model-trainings` answers RUNNING. The main
  process checks every value first against the contract's facets, so a refusal names its field and
  nothing is sent; the service's own refusals (`training_in_progress`, `model_version_name_taken`,
  `invalid_body` with its details) are shown as returned.
- **Progress.** The contract has no percentage, so the screen shows the state of the run (an indeterminate
  progress bar and the seconds it has run, polled once a second with `GET /v1/model-trainings/{id}`),
  and for a finished run its iterations, whether it converged and its inertia and silhouette. A training
  already running when the screen opens, started by anyone, is followed too. A COMPLETED training
  registered a DRAFT version: the screen says so, lists it and opens it. A FAILED one says it
  registered nothing.
- **Versions.** `GET /v1/model-versions` filtered by method (K-means by default) and state, with
  paging; the export button writes the table on screen (ADR-0074). A version opens with its parameters,
  its metrics, its data origin (`Synthetic` when it was fitted on seeded data, ADR-0052), its centroids
  in raw units and its size under each label. A version is immutable: there is no edit or delete
  control.
- **Raw units.** A centroid is stored in the normalized space of the fit. Under `MIN_MAX` the screen puts
  it back with the bounds the version froze, `min + coordinate * (max - min)`: recency in days since
  the last purchase, frequency in net purchases, monetary value as a net amount. Under `NONE` the
  coordinates already are raw. A variable the fit did not use, or a bound the service did not send, shows
  as a dash rather than as a guess.
- **Sizes.** A version carries a share per label and the number of customers it was fitted on, and no
  count per cluster, so a size is the label's: `share * customers fitted`, to the nearest customer.
- **Compare.** Tick two versions, or compare a version with the published one. Inertia, silhouette and
  stability, the parameters, the size under each label and the centroids in raw units sit side by side,
  and what differs is marked. Centroids are lined up by label, never by cluster number (ADR-0018). The
  comparison is marked methodological (ADR-0064), says that inertia and silhouette do not compare across
  different data, k, variables or normalization, and never says that one version is better (RN-39).
- **Publish.** `POST /v1/model-versions/{id}/publications` for a DRAFT, from a dialog that first reads the
  version published now and states what changes for customers: which version it replaces and retires,
  that every customer is scored again in a full run that does not finish before the answer, the labels
  customers will hold, that a change caused by it is recorded as `VERSION_CHANGE` and never counted as
  behaviour, and that segments already written are not rewritten. The reason (1 to 500 characters) is
  required and is kept in the append-only publication. If the published version cannot be read, nothing
  can be confirmed. `version_not_draft` and `publication_conflict` are shown as returned and the version
  is read again. Rollback is in the contract and is not offered here.

model-registry is not implemented yet (F22-11). The unit tests (`tests/unit/models.test.ts`,
`tests/unit/kmeans.test.ts`) and the window tests (`tests/window/kmeans.spec.ts`) answer with the
recorded examples in `contracts/model-registry/examples/`, read from disk.

## Packages and CI (F16-05)

`.github/workflows/desktop.yml` runs on a pull request that touches `clients/desktop/**`,
`contracts/**` or the workflow: `npm ci`, ESLint, `prettier --check`, `tsc --noEmit`, the unit tests
(which first bundle the schemas from the contracts in the pull request, so a contract change that the
client no longer validates fails), the Playwright Electron tests, the ADR-0041 checks
(`npm run check:adr41`, which print nothing), and then build a Linux AppImage uploaded as the workflow
artifact `mosaiq-desktop-appimage`. It is separate from the services workflow.

```sh
npm run package:linux    # release/mosaiq-desktop-<version>-linux-<arch>.AppImage
npm run package:win      # release/mosaiq-desktop-<version>-win-x64.exe (NSIS); built by CI
```

Pushing a tag `desktop/vX.Y.Z` (it must equal `version` in `package.json`) builds the Windows (NSIS) and
Linux (AppImage) packages and attaches them to a GitHub release. `release/` is git-ignored.

### The packages are unsigned

There is no code-signing certificate, so the operating system warns on first run:

- **Windows (SmartScreen):** "Windows protected your PC". Choose **More info**, then **Run anyway**.
- **Linux:** an AppImage must be marked executable: `chmod +x mosaiq-desktop-*.AppImage`, then run it.
  On a system without FUSE 2 (Ubuntu 22.04 and later), run it with `--appimage-extract-and-run`. If the
  sandbox is refused on a hardened kernel, the warning names the missing user-namespace setting; the
  `sandbox: true` window setting is never switched off to get past it.

For the demonstration, download the package before the session, open it once beforehand to accept the
warning, and have the base URL set in `settings.json` (see above).

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

## Recommendations inspection (F28-08)

Through recommendations-service (`contracts/recommendations/`, `/v2`, ADR-0067). The **Recommendations**
screen, in the Analysis group, is offered to the roles whose `x-mosaiq-roles` allow
`getRecommendationsV2`. Stored decisions are open to fewer roles (`listRecommendationDecisions` and
`getRecommendationDecision`: `ADMIN`, `ANALYST`, `AUDITOR` and `STORE_MANAGER`, the last only at its assigned
stores, which the service enforces). A role that may inspect but not read decisions sees the inspection and a
note that the decisions are closed to it. The main process checks each of the three reads against its own
operation (`RECOMMENDATION_OPERATIONS`) and refuses a forced call with `forbidden_role` before sending
anything. The operations are published ahead of their implementation and marked planned (ADR-0073), so the
tests answer with the recorded examples of `contracts/recommendations/examples/v2/`, validated against
`recommendations.v2.xsd`; a service that does not answer yet is shown as it answers.

- **Inspect.** A customer (chosen from the customer search for roles that may search, or typed as an id), a
  store from the stores read and a channel from the channels read, and a limit of 1 to 50. With no store the
  service uses the customer's usual store, and with no channel any channel the store enables: the screen
  never fills either in (`GET /v2/recommendations`, `store_id` and `channel_id` are sent only when chosen).
- **Each item** shows its position, product, kind, factors (signals matched, segment buyers and category share,
  the figures RN-40 orders by), availability (the quantity on hand and the store and channel it was judged at)
  and the reasons that matched, each in words with its figures. Product, category, store and channel names are
  read from their owners within the call (ADR-0035); an id whose name cannot be read is shown with its id and
  "name unavailable".
- **Alternatives are marked.** A `kind` of `alternative` reads "Alternative for <product>, which is unavailable
  here (<reason>)", has no factors ("Not scored") and is drawn with a solid left rule. A `kind` of `elsewhere`
  reads "Another store: unavailable at <store>, so not recommended there", names the other store that holds it
  and is drawn in italics with a dashed rule. The words carry the mark, not only the styling.
- **Excluded.** The exclusion counts of the answer, each with the reason in words. Products are counted and
  never listed, so the screen lists none. A status with no items (`no_usual_store`, `no_open_assignment`,
  `unassigned_result`, `PERSONALIZATION_WITHDRAWN`) says in plain words why nothing was offered, and `none_match`
  says that no product qualified here.
- **Stored decisions.** The list (`GET /v2/recommendation-decisions`) filters by customer, store, status and a
  UTC day range (the last day included), twenty to a page. A decision opens from the list, from an inspection
  ("Open the stored decision") or by its id, and shows what was offered and excluded as the answer does. A
  decision whose customer was anonymized reads "Anonymized". It keeps no window or usual store, so the screen
  does not show them.
- Export is the shell's: the first table on screen as CSV, for the roles that may export (ADR-0074).

The unit tests (`tests/unit/recommendations.test.ts`) and the window tests
(`tests/window/recommendations.spec.ts`) answer with the recorded v2 examples and with pages of products,
categories, stores, channels and customers built in `tests/support/recommendations.ts`.
