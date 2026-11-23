# Distributed demonstration: sale to measured effect

This is the rehearsal script for F18-05 (#462) and the script portion of
F29-10 (#896). It is written for the deployed instance at
`https://mosaiq.maxthecoder.online/`. A person who did not build the system can
run it in order. Do not replace a `‹fill: …›` value with an estimate: capture
the value from this run.

All service paths below are contract paths. Through the gateway, prefix them
with `/api/<service>`. Android sends and accepts JSON. Electron sends and
accepts XML and validates it against that service's XSD. Save every response's
`X-Correlation-ID`; the Android error detail and Electron error panel display
it when a request fails.

## Run record

| Item | Value |
|---|---|
| Deployed commit or release | `‹fill: commit or release›` |
| Run start, UTC | `‹fill: RFC 3339 instant›` |
| Floor account email / user id | `‹fill: email›` / `‹fill: UUID›` |
| Marketing account email / user id | `‹fill: email›` / `‹fill: UUID›` |
| Store | `‹fill: id and name›` |
| Customer | `‹fill: id and name›` |
| Product | `‹fill: id, SKU and name›` |
| Campaign / promotion / experiment | `‹fill: ids›` |
| Phone session id (`sid`, never the token) | `‹fill: sid›` |

## Preparation

Do this once before rehearsals. Repeat the checks explicitly marked “before
each recording”. Store passwords in the team password manager, never here.

### P1. Identify every deployed service version

For every implemented name in `contracts/registry.yaml`, read
`GET /api/<name>/openapi.yaml` without authentication and record its
`info.version`. Compare it with the same contract in the checked-out commit.
The one-command, read-only check in P8 does this for all services and writes the
exchanges. Also record the Android `versionName` and Electron package version.

Check: no served contract version differs from the checkout, and
`deploy/check-api-edge.sh` prints nothing. Record the version table at
`‹fill: evidence location›`.

### P2. Create and assign the demonstration accounts

Reuse the users-service procedure in
[F20-01 §1](evidence/f20-01-android-sale.md#1-create-the-account) for an active
`STORE_MANAGER`, including its store assignment, and
[F20-02 §1](evidence/f20-02-electron-sale-campaign.md#1-the-accounts) for an
active `MARKETING` account. Seeded accounts are deactivated on the instance and
must not be reactivated for the demo.

The relevant operations are:

- `POST /v1/users`, role `ADMIN`, once per account;
- `PUT /v1/store-assignments/{user_id}`, role `ADMIN`, body
  `{"store_ids":[‹store_id›]}` for the floor account;
- `GET /v1/users/me`, with each new account, to prove `role_code`, `is_active`
  and the separate principal.

Check: `GET /v1/store-assignments/{floor_user_id}`, role `STORE_MANAGER`, lists
the demonstration store. Record correlation ids `‹fill: create floor›`,
`‹fill: create marketing›`, and `‹fill: assign store›`.

### P3. Set the store and emulator location

Use Store 17 for the fresh-seed primary customer. An `ADMIN` sends
`PUT /api/stores/v1/stores/17/location`:

```json
{
  "latitude": 19.432608,
  "longitude": -99.133209,
  "geofence_radius_m": 150
}
```

Check with `GET /v1/stores/17`, role `STORE_MANAGER`: `location` contains the
same three values. In Android Studio Extended controls → Location, set the mock
fix to those exact coordinates. The emulator must grant precise location and
report an accuracy no greater than the radius. Record the PUT correlation id
`‹fill: correlation id›`.

If Store 17 cannot be used, choose the store returned as `usual_store_id` by
the selected customer's recommendation, set that store's location, update the
floor assignment, and use it consistently throughout this document.

### P4. Choose the customer and product

On a fresh reload on 2026-11-23, the primary is **Demo Customer 16**,
`00000000-0000-0000-0000-000000000016`: it was `assigned`, returned
`recommended`, had `PERSONALIZATION` and `MARKETING_CONTACT` granted, and its
usual store was 17. Product 3 was recommended there and had positive stock.
These are candidate facts, not a promise about the live instance.

Before each recording, run P8. On the phone's **Sale** screen, searching by
name uses `GET /v1/customers?q=Demo+Customer+16&size=100`; the two required
checks are `GET /v1/segment-assignments/current?customer_id=<id>` with
`state: assigned` and `GET /v1/recommendations?customer_id=<id>` with
`status: recommended`. Confirm the recommendation's `usual_store_id` is the
demo store and its items include an active, stocked product.

Fallback: if any check fails, do not force the seed row or edit the database.
From the treatment assignments of P7, test seeded customers in ascending id
with the same two GETs and choose the first one that is `assigned` and
`recommended`, has both consents granted, and whose usual store can be used.
Write that name and id into the Run record before Part 1. If none qualifies,
stop: the preparation is not valid.

Check product detail with `GET /v1/products/{product_id}` and stock with
`GET /v1/stock-levels/{store_id}/{product_id}`. It must be active and
`quantity_on_hand >= 1`. Save the quantity as `‹fill: before quantity›`.

### P5. Create the campaign and promotion through services

The 30 seeded promotions are not active today and the seeded experiments have
no service plan, so none may be reused. Sign in as `MARKETING` and create a
campaign in Electron **Campaigns → New campaign**, or send XML/JSON directly
to `POST /api/campaigns/v1/campaigns`. Its `label_code` must be the customer's
current label, its dates must include today and the experiment window, and it
must remain `DRAFT`:

```json
{
  "name": "MOSAIQ complete-loop rehearsal ‹fill: suffix›",
  "label_code": "‹fill: current label›",
  "starts_on": "‹fill: today›",
  "ends_on": "‹fill: date after analysis_at›"
}
```

Then, still while the campaign is `DRAFT`, call
`POST /api/campaigns/v1/promotions`, role `MARKETING`:

```json
{
  "campaign_id": ‹campaign_id›,
  "name": "Complete-loop treatment",
  "discount_percent": "10.00",
  "starts_on": "‹fill: today›",
  "ends_on": "‹fill: within campaign dates›",
  "store_ids": [‹store_id›],
  "channel_ids": [‹physical_store channel_id›]
}
```

Find the channel id with `GET /api/sales/v1/channels`; match the name
`physical_store`. Check the campaign is `DRAFT`, the promotion has the intended
store/channel, and record correlation ids `‹fill: campaign›` and
`‹fill: promotion›`. Electron has no promotion-write screen; use the documented
API call for that operation.

### P6. Plan the frozen experiment

In Electron **Marketing → A/B experiments → Plan an experiment**, choose the
draft campaign, metric `CONVERSION`, today as the start, a conversion window
that fits the rehearsal plan, one `CONTROL` and one `TREATMENT`, and submit
**Plan the experiment → Freeze the plan**. This calls
`POST /api/experimentation/v1/experiments`, role `MARKETING`.

The plan transaction freezes the frame, criteria, `frame_taken_at`, digest,
baseline, required customers per arm, and a random salt. It cannot be edited or
deleted. Point out `frame.size`, the exclusions, salt, `required_per_arm`, and
the `UNDERPOWERED` flag that is expected with seed-sized data. Do not claim
that this small frame can measure a reliable effect.

Check with `GET /v1/experiments/{experiment_id}`: state `PLANNED`, the campaign
id matches, `design_kind` is `CONTROLLED`, and the frozen frame is nonempty.
The read exposes the frame's size and digest, not its member ids; the selected
customer is proved by the assignment read in P7. Record correlation id
`‹fill: plan›`.

### P7. Assign, select treatment, then activate

In **A/B experiments**, open the plan and use **Assign the frame**. If it is
underpowered, tick the explicit acknowledgement. This calls
`POST /v1/experiment-assignments`, role `MARKETING`, body
`{"experiment_id":‹id›,"acknowledge_underpowered":true}`. Assignment is
immutable.

Read
`GET /v1/experiment-assignments?experiment_id=<id>&customer_id=<customer>`.
The Part 1 customer must have exactly one `TREATMENT` assignment. Assignment
uses the frozen salt and always deals the control arm first, so the primary is
not guaranteed to be treated; apply P4's fallback instead of retrying or
rewriting an assignment.

Now activate the campaign with Electron **Campaigns** or
`POST /v1/campaigns/{campaign_id}/transitions`, role `MARKETING`, body
`{"to":"ACTIVE","expected_status":"DRAFT"}`. Check today lies in both
campaign and promotion dates. A floor read of
`GET /v1/promotions?store_id=<store>&channel_id=<channel>&active=true&size=100`
must contain it, and
`GET /v1/experiment-assignments/current?customer_id=<customer>&campaign_id=<campaign>`
must answer `TREATMENT`. Record correlation ids `‹fill: assign›` and
`‹fill: activate›`.

### P8. Run the read-only precondition collector

Set secrets only in the environment. The output directory must be outside any
location used for raw token capture.

```sh
export MOSAIQ_BASE_URL=https://mosaiq.maxthecoder.online
export MOSAIQ_DEMO_FLOOR_EMAIL='‹fill›'
export MOSAIQ_DEMO_FLOOR_PASSWORD='‹read from password manager›'
export MOSAIQ_DEMO_MARKETING_EMAIL='‹fill›'
export MOSAIQ_DEMO_MARKETING_PASSWORD='‹read from password manager›'
python -m tests.acceptance.demo \
  --out "docs/evidence/f18-05-preflight-‹fill: run id›" \
  --customer '‹customer UUID›' --store ‹store id› --product ‹product id› \
  --campaign ‹campaign id› --experiment ‹experiment id›
unset MOSAIQ_DEMO_FLOOR_PASSWORD MOSAIQ_DEMO_MARKETING_PASSWORD
```

It performs only GETs plus its own sign-ins/sign-outs. Proceed only on exit 0.

### P9. Reset between rehearsals

Never delete or edit recorded sales, events, assignments, frames, conversions,
or results. For a new rehearsal, re-run P4 and choose a newly stocked product;
record new client ids and a new run start. If the old campaign or promotion is
outside its dates, create a new draft campaign, promotion and experiment. If
the experiment has a stored primary result, `getCurrentAssignment` correctly
returns 404, so create a new non-overlapping plan rather than reusing it.
Stock decreases, RFM/profile/history and recommendation decisions persist.

## Part 1 — F18-05

Each numbered step is one evidence checkpoint. Capture the named file only
after the screen and response agree.

### 1. Sign in on Android

The demonstrator acts on Android **MOSAIQ → Sign in** as the created
`STORE_MANAGER`. The app sends `POST /v1/sessions`, then
`GET /v1/users/me` (all active roles). Point out `role_code: STORE_MANAGER`,
`is_active: true`, the account's user id, and that this is a fresh phone
session. Correlation id: `‹fill: …›`.

Evidence: [f18-05-01-sign-in.png](evidence/f18-05-01-sign-in.png).

### 2. Check in with GPS

The same person opens Android **Check-in**, selects the assigned store, and
taps **Check in here**. Android sends `POST /v1/check-ins`, role
`STORE_MANAGER`, with a UUID `client_check_in_id`, store, current
`occurred_at`, mock latitude/longitude and accuracy. Point out `verified: true`,
`reason: within_geofence`, `distance_m`, and `check_in_id`. Correlation id:
`‹fill: …›`; check-in id: `‹fill: …›`.

Evidence: [f18-05-02-gps-check-in.png](evidence/f18-05-02-gps-check-in.png).

### 3. Scan the product

On Android **Scan**, scan the product QR/barcode. The app reads
`GET /v1/products/{product_id}` and
`GET /v1/stock-levels/{store_id}/{product_id}`, both allowing
`STORE_MANAGER`, then sends one `POST /v1/engagement-events` with type
`QR_SCAN`, role `STORE_MANAGER`. Point out the product detail, **In stock**,
and the exact `client_event_id` `‹fill: …›`.

In the operator's `mongosh`, use a value parameter, not pasted JavaScript:

```javascript
const id = process.env.CLIENT_EVENT_ID;
db.getSiblingDB("engagement").engagement_event.find(
  {client_event_id: id},
  {_id: 0, client_event_id: 1, type: 1, store_id: 1, product_id: 1, occurred_at: 1}
).toArray()
```

Exactly one row must have type `QR_SCAN`. Correlation ids: product
`‹fill: …›`, stock `‹fill: …›`, event `‹fill: …›`.

Evidence: [f18-05-03-product-scan.png](evidence/f18-05-03-product-scan.png).

### 4. Find the named customer and capture an interest

On Android **Sale → Customer**, search the Run record's customer. Android uses
`GET /v1/customers?q=<name>&size=100`, role `STORE_MANAGER`. Select the exact
id. Show the current assignment
`GET /v1/segment-assignments/current?customer_id=<id>` and recommendation
`GET /v1/recommendations?customer_id=<id>`; point out `assigned` and
`recommended` again.

Open **Customer → Consent, benefits and preferences → Interests**. The save is
the whole-set `PUT /v1/preference-profiles/{customer_id}/interest-categories`,
role `STORE_MANAGER`, body `{"category_ids":[…]}`. Include every interest that
must remain, plus one category chosen for this run; omission removes an old
interest. Correlation ids: search `‹fill: …›`, segment `‹fill: …›`,
recommendations `‹fill: …›`, interest PUT `‹fill: …›`.

Evidence: [f18-05-04-customer.png](evidence/f18-05-04-customer.png).

### 5. Show recommendations and tap one

On Android **Sale → Customer**, point out the **Segment** and
**Recommendations** cards. Refresh the same segment and recommendation GETs.
Tap exactly one recommendation with **Open … in Scan**. Android queues
`POST /v2/engagement-events`, role `STORE_MANAGER`, type `RECO_CLICK`, and its
one-based rank. Wait for **Outbox** to show it sent. Record
`client_event_id` `‹fill: …›` and correlation ids `‹fill: reads and write›`.

Verify with
`GET /v2/engagement-events?type=RECO_CLICK&customer_id=<id>&from=<run-start>&size=100`;
exactly one item in this run has that client id.

Evidence: [f18-05-05-recommendations.png](evidence/f18-05-05-recommendations.png).

### 6. Record the sale offline, then reconnect

On Android **Sale**, add the scanned/recommended product and open **Review
sale**. If Part 2 uses the promotion, select it; the discounted unit price must
not exceed its `discount_percent`. Disable network, tap **Record sale**, show
**Sale kept as a draft on this phone, pending**, then reconnect and use
**Outbox → Send now**. Android sends `POST /v1/sales`, role `STORE_MANAGER`,
JSON plus `Idempotency-Key: <phone UUID>`; a promoted line also carries
`promotion_id`. Point out the 201 `sale_id`, line, total and replay-safe key.

The service pins RS256 and checks `iss`, `aud`, `exp`. With the ops read-only
Redis user, extract only the JWT's `sid` during the live capture and show:

```sh
redis-cli --user ops --askpass EXISTS "pub:auth:v1:live:‹sid›"
```

The result is `1`; never print the access token. Correlation id:
`‹fill: …›`; sale id `‹fill: …›`; Idempotency-Key `‹fill: …›`.

Evidence: [f18-05-06-offline-sale.png](evidence/f18-05-06-offline-sale.png).

### 7. Show the PostgreSQL rows

The instance operator uses `psql` read-only. Every runtime value is bound:

```sql
BEGIN READ ONLY;
SELECT t.transaction_id, t.source_transaction_id,
       t.source_transaction_id = 'svc-sales:' || lower($2) AS key_matches,
       t.customer_id, t.store_id, t.channel_id, t.occurred_at, t.total
FROM transaction AS t WHERE t.transaction_id = $1::bigint;
\bind :sale_id :idempotency_key
\g
SELECT transaction_id, product_id, quantity, unit_price
FROM transaction_line WHERE transaction_id = $1::bigint ORDER BY product_id;
\bind :sale_id
\g
SELECT transaction_id, recorded_by, captured_at
FROM sales.sale_capture WHERE transaction_id = $1::bigint;
\bind :sale_id
\g
SELECT i.store_id, i.product_id, l.quantity AS sold, i.quantity_on_hand
FROM transaction t JOIN transaction_line l USING (transaction_id)
JOIN inventory i ON i.store_id=t.store_id AND i.product_id=l.product_id
WHERE t.transaction_id=$1::bigint ORDER BY i.product_id;
\bind :sale_id
\g
ROLLBACK;
```

Point out `source_transaction_id = svc-sales:<Idempotency-Key>`, every line,
the capture principal, and before minus sold equals after. SQL has no request
correlation id; refer back to step 6's `‹fill: …›`.

Evidence: [f18-05-07-postgresql.png](evidence/f18-05-07-postgresql.png).

### 8. Show the unchanged web

The web demonstrator opens `/reports/` and `/catalog/stock` at 375 px, then
1440 px. Point out the sale in the revenue summary and the decremented stock.
The web reads PostgreSQL directly; it calls no service. Show
`git diff --stat <stage-start-commit> -- web/` is empty. There is no
`X-Correlation-ID` for server-rendered web navigation; record `not applicable`
and the sale correlation id `‹fill: …›` that created the rows.

Evidence: [f18-05-08-web.png](evidence/f18-05-08-web.png).

### 9. Read the run in Electron XML

In Electron, sign in as the same `STORE_MANAGER` in a distinct desktop
session (`POST /v1/sessions`). In **Sales**, open the sale:
`GET /v1/sales/{sale_id}` and `GET /v1/products?ids=<comma-separated ids>`.
In **Check-ins**, open `GET /v1/check-ins/{check_in_id}`. In **Engagement**,
list
`GET /v1/engagement-events?type=RECO_CLICK&customer_id=<id>&from=<run-start>`
and identify the step 5 `client_event_id`. These operations all allow
`STORE_MANAGER`.

Point out that each response is XML and was accepted only after validation by
`sales.xsd`, `products.xsd`, `check-ins.xsd`, or `engagement.xsd`. Record
correlation ids: session `‹fill›`, sale `‹fill›`, products `‹fill›`, check-in
`‹fill›`, engagement `‹fill›`.

Evidence: [f18-05-09-electron-xml.png](evidence/f18-05-09-electron-xml.png).

### 10. Sign out and prove revocation

Keep Electron signed in. On Android use **Account menu → Sign out**; this sends
`DELETE /v1/sessions/current`. Replay the old phone token only from the secure
capture against `GET /v1/sales/{sale_id}` and
`GET /v1/stores/{store_id}`. Both must answer 401 `session_revoked`; the
desktop session still works because it has another `sid`. Record correlation
ids: sign-out `‹fill›`, sales refusal `‹fill›`, stores refusal `‹fill›`.

Evidence: [f18-05-10-session-revoked.png](evidence/f18-05-10-session-revoked.png).

## Part 2 — F29-10, the complete loop

Continue with Part 1's customer and sale. Use a fresh phone session after step
10.

### 11. Profile change in rfm-engine

Before the sale, save
`GET /v1/rfm-profiles/{customer_id}`, role `STORE_MANAGER`: `r_days`,
`f_count`, `m_total`, the three scores, label and `computed_at`. After the sale,
poll the same GET for at most the stream's five-second default maximum and
compare. The sale makes recency zero (for a current sale), increases frequency
by one, and increases monetary value by the accepted net line total; a score
changes only when a boundary is crossed. Record both correlation ids and
[f29-10-11-rfm-profile.png](evidence/f29-10-11-rfm-profile.png).

rfm-engine consumes `stream:sales.v1.recorded`, re-reads the sale and
recomputes only this customer. If no profile appears, stop and diagnose the
consumer; do not substitute the web's seeded assignment for this proof.

### 12. Segment migration and retained history

Read the current assignment, then
`GET /v1/segment-assignments?customer_id=<id>&size=100`. If the RFM label crossed
a boundary, the current row is new and the preceding row remains closed in the
history. In Electron **Analysis → Migration matrix**, use dates spanning the
sale, open the matching cell and **Why this customer moved**; the APIs are
`GET /v1/segment-migrations?...` and
`GET /v1/segment-migrations/explanation?customer_id=<id>&from=<before>&to=<after>`.
Point out previous/new labels, model versions, cause, and changed measures.

If the label did not change, say exactly that: the live sale changed F/M but
did not cross a band. F29-10's migration criterion is then not demonstrated;
choose a different prechecked boundary customer for the recorded run rather
than claiming migration. Correlation ids `‹fill›`; evidence
[f29-10-12-segment-migration.png](evidence/f29-10-12-segment-migration.png).

### 13. Recommendations follow segment and stock

After segment propagation, read
`GET /v2/recommendations?customer_id=<id>&store_id=<store>&channel_id=<channel>&limit=10`.
In Electron **Analysis → Recommendations → Inspect a recommendation**, point
out offered products, `quantity_on_hand`, alternatives, exclusions, and every
reason (`segment`, declared interest, purchase history, or relation). Open the
stored decision in **Stored decisions** to prove the explanation was retained.
The sold product is excluded when now bought or out of stock; no out-of-stock
item is offered. Correlation ids `‹fill›`; evidence
[f29-10-13-recommendation-decision.png](evidence/f29-10-13-recommendation-decision.png).

### 14. Assignment and live exposure from Android

On Android **Sale**, select the customer and scroll to **Promotions**. The app calls
`GET /v1/promotions?store_id=<store>&channel_id=<channel>&active=true` and, for
the campaign, `GET /v1/experiment-assignments/current?customer_id=<id>&campaign_id=<id>`.
Point out the `TREATMENT` arm and promotion. Tap **Shown to customer** exactly
once. Android queues `POST /v2/engagement-events` type
`CAMPAIGN_EXPOSURE`; wait for **Shown and recorded**. The experimentation
consumer projects that event within five seconds. Read the assignment list and
point out `exposure_count`/first exposure. Correlation ids and client event id
`‹fill›`; evidence
[f29-10-14-campaign-exposure.png](evidence/f29-10-14-campaign-exposure.png).

### 15. Conversion and attribution

The Part 1 sale is a conversion only if it occurs after assignment, within the
conversion window, and for the assigned customer. If exposure is required for
the intended attribution, record the exposure before the promoted sale; if
Part 1 was already sold first, record a second, clearly labelled conversion
sale after exposure and do not call the first one attributed.

`POST /v1/experiments/{experiment_id}/conversions`, role `MARKETING`, performs
an on-demand reconciliation; `GET` on that path lists conversions.
`POST /v1/experiments/{experiment_id}/attributions` evaluates attribution only
after the conversion window closes; `GET /v1/sale-attributions?...` reads it.
Electron currently has no buttons for these two POST operations, so use the
contracted API and then show the read in **Conversion analysis**. Record ids
`‹fill›`; evidence
[f29-10-15-conversion-attribution.png](evidence/f29-10-15-conversion-attribution.png).

### 16. Effect and uncertainty in Electron

In Electron **Analysis → Conversion analysis**, choose the experiment and
**Stored result (computed once, at the analysis instant)**, then **Open result**.
This calls `GET /v1/experiment-results/{id}?basis=PRIMARY`.

Before `analysis_at` (last assignment + conversion-window days + seven settle
days), the service returns `PENDING`: observed arm counts and elapsed share,
but no interval, p-value or direction. The live sale changes those counts and
may become a conversion; it does not create a measured effect. At or after
`analysis_at`, the first PRIMARY read evaluates missing conversions, computes
and stores the result once; later PRIMARY reads never rewrite it. `RE_READ`
recomputes as of now without replacing the stored result. Attribution is also
written only after the conversion window closes.

Point out **Effect against the control**, its difference and lower/upper 95%
interval only if the result is `COMPLETE`. With this seed-sized,
underpowered frame, expect `PENDING`, `INCONCLUSIVE`, or a withheld small-arm
comparison. One demonstration sale cannot by itself produce a credible
incremental effect. Show the planned sample per arm, analysed counts and every
warning. Correlation id `‹fill›`; evidence
[f29-10-16-effect-interval.png](evidence/f29-10-16-effect-interval.png).

### Android services required by AR-1

| Required service | Demonstration step |
|---|---|
| auth | 1 and 10 — sign in and revoke the phone session |
| customers | 4 — search and select the named customer |
| products | 3 — scan and read the product |
| stock | 3 and 7 — read then prove the decrement |
| sales | 6 — queue offline and record on reconnect |
| recommendations | 5 and 13 — show and explain stock-bound recommendations |
| campaigns | 14 — list the active promotion on Android |

## Rehearsal record

| Date | Ran by | Observed by | Step-4 customer check (`assigned` / `recommended`) | Result |
|---|---|---|---|---|
| `‹fill: date›` | `‹fill: name›` | `‹fill: name›` | `‹fill: customer, states, correlation ids›` | `‹fill: pass/fail and failed step›` |

## If something fails

| Step | Likely cause | Where to look |
|---|---|---|
| P1 / any 404 | Contract and deployed image differ, or gateway include is stale | Served OpenAPI, ops **Services**, gateway access/error log by correlation id |
| P2 / 1 | Seeded account used, inactive account, wrong role/store assignment | users-service log; `GET users/me`; Users screen; auth log |
| P3 / 2 | Missing store location, stale assignment, permission/accuracy or mock fix outside radius | Stores response; Android permission; check-ins log and response reason |
| 3 / 5 / 14 | Outbox pending, Mongo unavailable, duplicate client id, wrong event version | Android **Outbox**; engagement log/Mongo; gateway log; correlation id |
| 4 | Customer changed since preparation, consent withdrawn, recommendation cache not invalidated | Re-run P8; segments/recommendations logs; `segments.assignment_changed` bound (5 s) |
| 6 / 7 | Token/session invalid, stock race, promotion invalid, duplicate key with different body | sales log; gateway log; stock row; `sales.sale_capture`; phone Outbox detail |
| 6 authentication | Redis unavailable or `pub:auth:v1:live:{sid}` missing | ops Redis panel and read-only EXISTS; auth and sales logs; a Redis outage must yield 503 |
| 8 | Wrong sale window, report cache, or viewport | PostgreSQL rows first; web log; browser network; do not blame a service |
| 9 | Desktop base URL/role wrong or XML fails XSD | Electron error panel/correlation id; gateway log; response bytes and named XSD |
| 10 | The desktop token was replayed instead of the phone token | Compare `sid` only; auth log; liveness key before/after sign-out |
| 11 | rfm consumer lagged or its published rule set/profile is absent | ops stream lag; rfm log; `sales.recorded.v1` default 5 s bound and pending entries |
| 12 | Scores changed without crossing a band, or assignment apply failed | RFM profile/rule boundaries; segments log; assignment history; do not invent migration |
| 13 | Cache still holds old label/stock or no eligible item remains | recommendations log/decision; `segments.assignment_changed` and `stock.changed` 5 s bounds |
| 14 | Customer is control/404, campaign inactive, or engagement notice has not projected | P7 checks; campaigns/experimentation/engagement logs; interaction stream 5 s bound |
| 15 | Sale preceded assignment/exposure, window still open, or reconciliation not run | conversion list, exposure count, sale time; experimentation log; contracted evaluation POSTs |
| 16 | Analysis instant not reached, small arm, failed ratio check, or result already frozen | Experiment design `analysis_at`; status/assumptions; result rows; never manufacture a second look |

## Known gaps

- Step 11 reads `getRfmProfile`, and Electron's exploration and RFM modules read
  `listRfmProfiles`/`getRfmProfile`; rfm-engine still marks both `planned` until
  #1175 (F22-19) merges and deploys. Until then step 11 cannot be shown through the
  service.
- Step 13's **Stored decisions** reads `/v2/recommendation-decisions`, which
  recommendations-service still marks `planned` until #1176 (F24-09) merges and
  deploys.

- Electron has no served promotion-create screen, although campaigns-service
  serves `createPromotion`; preparation therefore uses the contracted API.
- Electron **Conversion analysis** reads results but has no controls for
  `evaluateConversions` or `evaluateAttributions`; the complete rehearsal uses
  those contracted POST operations directly.
- Assignment is immutable and salt-based. The service cannot guarantee that a
  customer named before planning lands in treatment. This script uses the
  primary only when its stored assignment is treatment and otherwise applies
  the documented fallback; it never retries by mutating an assignment.
- A live sale before `analysis_at` changes counts, not an effect estimate. The
  recorded demonstration may honestly show a pending or small-arm-withheld
  result; a complete interval requires enough customers and elapsed time.
