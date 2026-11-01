# ADR-0071 — Android raises local notifications from a periodic pull, and a refused write reaches a supervisor as a capture incident owned by engagement

**Status:** Proposed
**Owner:** Maximiliano
**Issue:** #864
**Supersedes:** — *(when accepted, it partially supersedes ADR-0038 for the number of collections only, and restates the engagement rule in `contracts/registry.yaml`; see "What must now be true elsewhere". Only status lines of accepted records are edited.)*
**Superseded by:** —

---

## Context

The instructor now requires that campaign alerts, capture errors and incidents reach the
Android user. `docs/scope-delivery-2-distributed.md` left push out because it needs
Firebase, ADR-0041 rejected "push notifications as one of Android's device capabilities"
for the same reason, and `docs/scope-delivery-3.md` §5 lists "Firebase push" as out of
scope "unless F27-10 decides otherwise". This record is that decision. Three questions
are open, and they have different answers.

What already holds:

- **ADR-0047 decides notifications for the web, not for the phone.** It says pull only:
  the data is in PostgreSQL, nothing is pushed, no timer or scheduler is added, and an
  event that no request causes is found by the next read. The phone cannot reuse its
  mechanism (the web reads PostgreSQL directly; Android calls services only, ADR-0041),
  but it can reuse its reasoning: no new transport, no new deployable, no new credential.
- **Android consumes JSON only and calls a resource only at the service that owns it**
  (ADR-0041, ADR-0035). It cannot read Redis Streams and no service calls another over
  HTTP (ADR-0044), so a service cannot "tell" the phone anything; the phone has to ask.
- **A session is at most 12 hours and an access token 10 minutes** (ADR-0036). The refresh
  token rotates, and two racing refreshes end the session, so any background request must
  share the app's single-flight `Authenticator`.
- **Floor writes go first to Room and a WorkManager job resends them** (ADR-0041). A write
  finally refused, such as a sale with 409 `stock_unavailable`, "stays visible in an outbox
  screen with its error code". That is the only place such a sale exists: the sales service
  refused it and stored nothing, so nobody but the person holding the phone can see it.
  The same record forbids generating a retry id inside a worker (`UUID.randomUUID` in
  `*Worker*.kt` fails its compliance grep).
- **ADR-0060 (accepted) gives `COMMERCIAL_SUPERVISOR` "capture incidents" and says they
  are narrowed to the supervisor's assigned stores.** It names the thing but not the
  resource, its owner, or how an incident is created.
- **The engagement service's rule is "recorded once per client event, attributed, voided
  rather than deleted, and summarized" (RN-55)**, over MongoDB, as `svc_engagement`
  (ADR-0038), and its contract already carries the shape of an idempotent client write
  (`client_event_id`, 200 for a replay, 409 `client_event_conflict`).

Three parts are judgement calls and the human reviewer should look at them first: that a
polled alert may arrive 15 minutes or more late, that folding incidents into engagement
is the weakest placement (section 3), and that a notification shows nothing about a
sale at all, not even a count of money.

## Decision

Android raises its own notifications. A WorkManager periodic job pulls the services'
ordinary JSON reads every 15 minutes, compares them with ids it has already seen in Room,
and posts a local notification for what is new. There is no Firebase, no push channel
and no new service for delivery. A capture error is raised locally and immediately, from
the outbox. A refused write is also reported once, by a queued write, to a new resource,
`capture-incidents`, owned by engagement-service, and the commercial supervisor learns of
it through the same pull. Every notification is built in two versions, and neither
contains personal data.

### 1. How things reach the phone

| What | Source | Raised by | Who sees it |
|---|---|---|---|
| Campaign alert | `GET /v1/campaigns?status=ACTIVE` at campaigns-service (the read ADR-0060 grants the floor roles; no new endpoint) | The poll | Every floor role allowed that read |
| Capture error | The Room outbox: a queued write has a final refusal | The outbox, immediately, with no network | The user whose session created the row |
| Capture incident | `GET /v1/capture-incidents?status=OPEN&recorded_since=` at engagement-service | The poll | `COMMERCIAL_SUPERVISOR` (and `ADMIN`) |
| Session ended | A 401 `session_expired` or a failed refresh in the poll | The poll, once | The signed-in user |

**The poll.**
- `PeriodicWorkRequest` named `mosaiq-notification-poll`, unique, repeat interval
  **15 minutes** (the platform minimum), flex interval 5 minutes, enqueued after sign-in
  and cancelled at sign-out. Constraints: network connected and battery not low. It does
  not require charging or idle, because a supervisor's phone is in use all shift.
- **Latency is a stated cost.** WorkManager and Doze may run it late, so the alert arrives
  within about 15 to 30 minutes of the fact, and later in deep Doze. A foreground launch
  and a pull-to-refresh on the notification inbox screen run the same poll at once. The
  campaign alerts it carries are measured in days; the incident alert is measured in the
  length of a shift. Neither needs seconds.
- **Cost.** A floor user makes one request per poll, a supervisor two, each with `size`
  of 50 or fewer: at most 192 requests per phone per day, within the session's 12 hours.
  F27-11's contract author checks this against the per-token rate limit of ADR-0037.
- **What it never does.** It never refreshes outside the app's single-flight path, and
  never extends a session (ADR-0036: refresh never extends the 12-hour lifetime), so the
  poll ends when the session does. On 401 it stops, posts one "Sign in to keep receiving
  updates" notification and does not retry. On 503 (Redis unreachable, auth fails closed)
  or a network failure it returns `retry` with exponential backoff from 5 minutes and posts
  nothing.
- **Baseline.** The first successful poll after sign-in records what is already active and
  open without notifying, so a user is not told about everything that existed.

**What counts as new.**
- Campaign: a `campaign_id` seen with status `ACTIVE` for the first time raises
  `CAMPAIGN_STARTED`. One whose `ends_on` is within 2 days of the device's date, once per
  campaign, raises `CAMPAIGN_ENDING`. Both are computed on the phone from fields the contract
  already has.
- Incident: an `incident_id` not yet in Room. The poll sends `recorded_since` set to the
  largest server `recorded_at` it has stored minus 5 minutes, so the device clock is never
  used as a watermark, and duplicates are removed by id.
- Room keeps `notified_item(user_id, kind, subject_id, notified_at)`: ids and a time, no
  text. Sign-out deletes the user's rows and cancels their notifications.

**Android specifics.** Channels `campaign_alerts` (default importance), `capture_incidents`
(default), `capture_errors` (default) and `session` (low). `POST_NOTIFICATIONS` is asked for
on Android 13 and later at the first moment a notification is due, not at launch. If the user
declines, the inbox screen and an app badge still show the same items from the same pull.
Nothing here needs the Play Services, a foreground service, an exact alarm or a wake lock.

### 2. A notification carries no personal data on a locked screen

Every notification is built through one function, `buildNotification(kind, count, …)`, and
has a private (full) version and a public version.

| Field | Public version (shown on a secure lock screen) | Full version (after unlock) |
|---|---|---|
| Channel lock-screen visibility | `VISIBILITY_PRIVATE` on the channel and on each notification | |
| Small icon | The app icon | The app icon |
| Title | `MOSAIQ` | `MOSAIQ` |
| Text | `New updates are available.` | `CAMPAIGN_STARTED`: `Campaign started: <campaign name>`. `CAMPAIGN_ENDING`: `Campaign ends soon: <campaign name>`. Incidents: `<n> new capture incidents`. Capture error: `<n> queued items were refused`. Session: `Sign in to keep receiving updates` |
| Large icon, picture, sub-text, ticker, inbox lines, reply actions | None | None |
| Content intent | Opens the app at the inbox, extra `kind` and one opaque id (`campaign_id` or `incident_id`) | Same |
| Wearables and car | `setLocalOnly(true)`; a group summary is also given a public version | Same |

Because `VISIBILITY_PRIVATE` shows the full version on a lock screen that is not secure, the
full version is **also** free of personal data. Neither version holds: a customer's name,
email, phone, loyalty state or `customer_id`; a staff user's name or email; a sale's
amount, lines or products; a store's coordinates; or any text typed by a user other than a
campaign name. A store name, a campaign name, a count and an opaque id are allowed, since
none identifies a person. Details (which sale, which code, which seller) are rendered only
inside the app, after sign-in, from the Room and service data the screen already uses.
The builder accepts a closed `kind`, an integer and at most one campaign name, so a caller
cannot pass a free-text field.

### 3. Capture incidents: the resource and its owner

A capture incident is a record that a floor write was finally refused and exists only on the
device. The phone reports it once, when the outbox row reaches a final refusal. The report is
itself a queued write in Room, resent by WorkManager like any other (ADR-0041).

**Owner and fit.** `capture-incidents` belongs to **engagement-service**, stored in a third
MongoDB collection, `capture_incident`, of the `engagement` database, written only by
`svc_engagement` (ADR-0038). The test of ADR-0043 is whether it is the same invariant on
more facts. It is, narrowly: a fact originated by a client, recorded once per client id,
attributed to the token's `sub`, never deleted, closed rather than erased, and summarized
by store and day. It is not about customers or sales, so it needs no other service's table;
it validates the store through `api_stores.store_directory_v1` as engagement already does,
and narrows by `api_users.store_assignment_v1` as ADR-0060 already requires of it. The
word "in-store engagement" in the registry's rule does not cover it, so the rule is
restated (below). **This is the weakest fold**, as ADR-0059 said of privacy-consent, and
the fallback is stated: if a reviewer holds the rule to scans and taps, incidents become a
twentieth service (section "Alternatives").

The resource names appear in no other service's registry entry, and no path contains one.

| Operation | Endpoint | Roles (`x-mosaiq-roles`) |
|---|---|---|
| Report | `POST /v1/capture-incidents` | `SALES_ASSOCIATE`, `LOYALTY_OFFICER`, `STORE_MANAGER`, `COMMERCIAL_SUPERVISOR`, `ADMIN` (the roles that queue a write) |
| List | `GET /v1/capture-incidents` (`status`, `store_id`, `source_type`, `recorded_since`, `page`, `size`) | `COMMERCIAL_SUPERVISOR`, `ADMIN` |
| Read | `GET /v1/capture-incidents/{incident_id}` | `COMMERCIAL_SUPERVISOR`, `ADMIN` |
| Review | `POST /v1/capture-incidents/{incident_id}/reviews` | `COMMERCIAL_SUPERVISOR`, `ADMIN` |
| Summary | `GET /v1/capture-incident-summaries` (`from`, `to`, `store_id`, `group_by=store\|day\|source_type\|refused_code`) | `COMMERCIAL_SUPERVISOR`, `ADMIN` |

Report, list, read and review are narrowed to the caller's assigned stores (403
`forbidden_store`, ADR-0060); `ADMIN` is not. The reporter does not read incidents back:
the device already holds the refusal.

**The report body** (JSON; the XML form mirrors it for Electron, which may read but does
not need to report):

| Field | Type | Meaning |
|---|---|---|
| `client_incident_id` | UUID | The idempotency key. Not random: it is the UUIDv5 of `source_operation_id` under one fixed namespace, computed when the outbox row is created, so a worker never generates an id and any retry sends the same one |
| `source_type` | `SALE`, `CHECK_IN`, `ENGAGEMENT_EVENT`, `CUSTOMER_WRITE` | Which queued write was refused |
| `source_operation_id` | UUID | The refused write's own key: the `Idempotency-Key`, `client_event_id` or `client_check_in_id` |
| `source_correlation_id` | string, `^[0-9A-Za-z-]{1,64}$` | The `X-Correlation-ID` of the refused attempt. The report sends the same value in its own header so both appear under one id in the logs |
| `store_id` | int | The store of the refused write |
| `refused_status` | int, 400 to 499 | The HTTP status of the final refusal |
| `refused_code` | string, pattern as the error `code` | The service's code, for example `stock_unavailable` |
| `attempts` | int, at least 1 | Tries before the final answer |
| `occurred_at` | timestamp | When the user acted, the device's time; 422 `occurred_in_future` beyond 5 minutes ahead |
| `line_count` | int, optional, `SALE` only | Lines in the refused sale |

There is no customer field and no free text. The server adds `incident_id` (UUID),
`reported_by` (the token's `sub`), `recorded_at` (server time), `status` (`OPEN`,
`ACKNOWLEDGED`, `RESOLVED`) and `history`.

**Idempotency.** `client_incident_id` has a unique index. A report arriving again with the same
`source_type` and `source_operation_id` answers 200 with the original and writes nothing; with
different ones, 409 `client_incident_conflict`. A second index, unique on
`(source_type, source_operation_id)`, makes two devices of the same user (or a restored
backup) one incident. The service does not verify the refusal: an incident is a claim by an
authenticated user, and is shown as reported, not confirmed.

**Review.** Each review appends `{status, note, by, at}` and moves the incident forward only
(`OPEN` to `ACKNOWLEDGED` to `RESOLVED`; `OPEN` to `RESOLVED` is allowed); a backward move is
409 `invalid_transition`. `note` is optional, at most 200 characters, and is shown to
supervisors only. No incident is deleted; the collection has no delete operation and
`readWrite` limits are as ADR-0038 already accepted.

**Retention and privacy.** An incident holds a staff `sub`, a store and codes, and nothing
about a customer, so it falls outside the anonymization of customers (ADR-0066, if accepted)
and is kept.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Firebase Cloud Messaging | The decision ADR-0041 already took, and nothing has changed except the requirement. It needs an external project and service account (a secret outside the repository), Google Play Services on every device, and a sender, which is a deployable unit of another kind (AGENTS.md) that every service would have to feed without calling another service. It would also make the phone's notifications depend on a third party for a demonstration of four alert kinds |
| A long-lived connection (SSE, WebSocket, long poll) held by a foreground service | A persistent notification, a foreground-service type on Android 14 and later, and a battery cost for the whole shift, to turn a 15-minute delay into seconds nobody asked for. It also needs a push endpoint in a service, and services cannot relay each other's events to a client (ADR-0044) |
| `AlarmManager` exact alarms, or a shorter interval through chained one-shot work | Exact alarms are restricted by permission on current Android and by Doze. Chained work below 15 minutes fights the scheduler and drains the battery; the platform minimum is the honest interval |
| In-app badge only, with no system notification | Cheap, but it fails the instructor's requirement: a supervisor with the app in a pocket would never see an incident. It stays as the fallback when the permission is declined |
| Show full content on the lock screen and rely on the user's "hide sensitive" setting | The default varies by device and a non-secure lock screen shows everything. Safety must not depend on a setting we do not control |
| Make the incident visible by the supervisor reading the seller's outbox | The outbox is in the seller's Room database; there is no device-to-device channel and none should be added |
| `capture-incidents` in sales-service | The sales rule is that a sale is recorded all-or-nothing. The refused sale is by definition not recorded, and a CHECK_IN or ENGAGEMENT_EVENT refusal is not a sale. It would put a record of a failure in the service whose rule is "nothing is stored on refusal", and widen the busiest service |
| `capture-incidents` in audit-trail | The audit trail records changes the system made, is readable by `ADMIN` and `AUDITOR` only (RN-28 to RN-30) and mirrors `audit_log`; a refused write changed nothing. The supervisor could not read it, and clients do not write into it |
| `capture-incidents` in the web and PostgreSQL | The phone calls services, not the web, and the web consumes no service. A table would also need a service-side writer, which is exactly this problem |
| A twentieth service, `incidents`, with a PostgreSQL schema | Cleanest on the one-rule test, and the fallback if engagement is held to scans and taps. It costs a contract, a role, a schema, a compose project and a deployment (ADR-0040). At 160 MB in Flask it takes the `mem_limit` sum from 3,376 MB to 3,536 MB, over the 3,500 MB gate ADR-0059 sets; in Rust (64 MB) it fits at 3,440 MB. It needs its own ADR and a Rust service for a single collection of simple records |
| Report an incident in the engagement event stream (`type: CAPTURE_ERROR` on `engagement-events`) | It mixes two shapes in one resource whose `$jsonSchema` is `oneOf` scan or click, and its summaries count customer-facing interactions. A refused sale counted as engagement would corrupt the meaning of `engagement-summaries` |
| The supervisor is told only through the existing 409 the seller sees | Nothing reaches the supervisor, which is the acceptance criterion this record exists for |

## Consequences

**What this makes easy.**
- No new dependency, credential or deployable. The phone needs WorkManager (already used
  for the outbox) and the platform notification API.
- A capture error is shown instantly and offline, because it is raised from Room.
- One id per refused write makes every retry and every second device the same incident.
- Alerts, the inbox and the badge read the same pull, so a declined permission loses only the
  system banner.

**What this makes hard.**
- **Late by design.** About 15 to 30 minutes, longer in Doze, and not at all while the phone
  is off or offline. An incident a seller never gets to report (phone lost or wiped) is lost.
- **The first poll is silent**, so a supervisor who signs in at 08:00 is not told of
  incidents from the night; they see them in the inbox.
- **The incident is a claim.** Nothing proves the sale was refused, and a modified client
  could report false incidents. The cost is bounded: an incident changes no data, and is
  attributed.
- **engagement grows a third collection and a fourth responsibility** in its rule. The rule
  sentence must stay one sentence, and the weakest-fold fallback is a real option.
- **A lock screen says almost nothing.** A supervisor sees `MOSAIQ: New updates are
  available.` and must unlock to learn what. That is the cost of the privacy rule.
- **Banner counts can lag** the inbox when the user opens the app between polls.

**What must now be true elsewhere.**
- `contracts/engagement/` gains the five operations above, a `CaptureIncident` schema in
  OpenAPI and XSD with examples in JSON and XML (including the replay, the conflict and the
  invalid transition), `x-mosaiq-roles` as above, and `contracts/registry.yaml` lists
  `capture-incidents` and `capture-incident-summaries` under engagement's `resources`.
  The registry rule is restated, for example: "A client-originated field event (`QR_SCAN`,
  `RECO_CLICK`, or a refused queued write reported as a capture incident) is recorded once
  per client id, attributed, closed or voided rather than deleted, and summarized (RN-55)".
  F27-11 (#865) writes them, and the RN-55 text is updated there.
- `services/engagement/mongo/init.js` creates `capture_incident` with a strict `$jsonSchema`,
  `additionalProperties: false`, and the two unique indexes and the `{store_id, status,
  recorded_at}` index. It is applied by an operator before the service version that needs it
  (ADR-0038). `svc_engagement` gains `SELECT` on `api_users.store_assignment_v1` (ADR-0060).
- `docs/scope-delivery-3.md` §5 changes "Firebase push" from out of scope to "decided no
  (ADR-0071)". `docs/datastores/` and `docs/requirements.md`'s service operation policy
  carry the new rows.
- Android (F27-10 and its client stories) adds the poll, the channels, the Room table
  `notified_item`, the outbox report and the inbox screen. `clients/android/check-adr41.sh`
  is extended with the checks below. No change to `web/` and none to the schema.

## Compliance

```bash
# No Firebase, no push SDK, no Play Services messaging in the client.
grep -rniE "firebase|FirebaseMessaging|google-services|com.google.android.gms:play-services-gcm" \
  clients/android/ --include='*.kt' --include='*.kts' --include='*.toml' --include='*.json' \
  && echo "push dependency found"
# One notification builder; nothing else touches NotificationCompat.Builder.
grep -rn "NotificationCompat.Builder" clients/android/ --include='*.kt' \
  | grep -v "NotificationFactory.kt" && echo "notification built outside the factory"
# The poll interval is the platform minimum and the id is never generated in a worker.
grep -rn "UUID.randomUUID" clients/android/ --include='*Worker*.kt' && echo "id generated in a worker"
```

- A unit test on the factory asserts, for every `kind`: `visibility == VISIBILITY_PRIVATE`, a
  public version exists, `isLocalOnly` is true, and neither version contains the test
  customer's name, email, phone or id, nor a staff user's name or email, nor an amount, when
  those are present in the same Room database.
- A WorkManager test asserts the unique periodic work has a 15-minute interval, `CONNECTED`
  and battery-not-low constraints, is cancelled at sign-out, stops on 401 and retries on 503.
- A test with two polls shows the first is silent and the second notifies exactly once per new
  id, and that a repeated `incident_id` or `campaign_id` never notifies twice.
- Contract tests (#865): a replayed report returns 200 with the original; a different
  `source_type` for the same `client_incident_id` returns 409 `client_incident_conflict`;
  a seller reading `GET /v1/capture-incidents` returns 403; a supervisor reading another
  store's incident returns 403 `forbidden_store`; a backward review returns 409; the
  report carries no `customer_id`, and `contracts/check.sh` finds each resource has one owner.
