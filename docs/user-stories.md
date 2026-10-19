# User stories

What the people who use MOSAIQ need from it, in their words.

**These are not the stories on the board.** [`backlog.md`](backlog.md) holds the
*delivery* stories — engineering work like "install PostgreSQL on the instance"
— because that is what the team schedules and estimates. The stories here are
*product* stories: what a role wants the application to do for them. The two
are traced to each other in the table at the end, and conflating them is what
makes a story list unreadable.

Ids are stable. Each story names the requirement it satisfies in
[`requirements.md`](requirements.md) and the delivery story that builds it.

The web system is the subject of every story here. The distributed stage gives
it no Redis connection and it consumes no service: it keeps reading and writing
PostgreSQL directly and returning server-rendered HTML.

---

## Signing in

### HU-01 — Sign in
**As a** member of staff, **I want** to sign in with my email and password, **so
that** the system knows who I am and what I am allowed to do.

- Given valid credentials, when I submit them, then I reach the home page signed in
- Given a wrong password, when I submit it, then I am told the credentials are invalid — without being told which half was wrong
- Given a deactivated account, when I sign in with correct credentials, then I am refused and told the account is inactive
- Given five consecutive failures, when I try again, then the attempt is refused for a cool-off period

`RF-01` · F3-03 (#63)

### HU-02 — Sign out
**As a** signed-in user, **I want** to sign out, **so that** nobody using this
machine after me inherits my session.

- Given I am signed in, when I sign out, then I am returned to the sign-in page
- Given I have signed out, when I press the browser's back button, then I see the sign-in page and not the cached page
- Given I have signed out, when the old session cookie is replayed, then it is not accepted

`RF-02` · F3-03 (#63)

### HU-03 — Be sent where I was going
**As a** user who followed a link while signed out, **I want** to land on the
page I asked for after signing in, **so that** I do not have to find it again.

- Given I request a protected page while signed out, when I sign in, then I arrive at that page
- Given no page was requested, when I sign in, then I arrive at the home page

`RF-03` · F4-01 (#69)

## Access by role

### HU-04 — Be refused what I may not see
**As the** organization, **we want** every page to check the signed-in user's
role before it runs, **so that** knowing a URL is never enough to reach it.

- Given a role without permission, when it requests the page, then the request is refused before the view runs and nothing about the content is revealed
- Given the refusal, when it is shown, then it explains that access is denied rather than showing a stack trace or a blank page
- Given the refusal, when it happens, then it is logged with the user, the route and the time

`RF-04` · F4-01 (#69)

### HU-05 — Keep exactly one administrator
**As the** organization, **we want** the system to hold one administrator and
only one, **so that** accountability for privileged changes is never ambiguous.

- Given an administrator exists, when a second is created through the application, then it is refused with a message explaining why
- Given an administrator exists, when a second is inserted directly into the database, then the database refuses it
- Given the sole administrator, when an attempt is made to demote or deactivate them, then it is refused — the system is never left without one

`RF-05` · F4-02 (#70)

## Catalogs

### HU-06 — Maintain the catalogs
**As the** administrator, **I want** to create, edit and remove categories,
products, stores, channels and roles, **so that** the data the business runs on
stays current without a developer.

- Given a catalog, when I open it, then I see its rows paginated with a search box
- Given a new row, when I save it with valid values, then it is stored and appears in the list
- Given an edit, when I save it, then the change is stored and recorded in the audit log
- Given invalid values, when I save, then each invalid field is marked with what is wrong and nothing is stored

`RF-06` · F3-04 (#64)

### HU-07 — Be stopped from breaking references
**As the** administrator, **I want** a deletion that would orphan data to be
refused in plain language, **so that** I understand the consequence instead of
seeing a database error.

- Given a category with products, when I delete it, then it is refused and I am told how many products still reference it
- Given a customer with sales, when I delete them, then it is refused and I am told sales exist
- Given a row nothing references, when I delete it, then it is removed and the deletion is recorded in the audit log

`RF-07` · F3-04 (#64)

### HU-08 — Put a picture on a product
**As the** administrator, **I want** to attach an image to a product, **so that**
staff recognise it in a list rather than reading SKUs.

- Given a JPEG, PNG or WebP under 5 MB, when I upload it, then it is stored and shown on the product
- Given a file of another type or over the limit, when I upload it, then it is refused with the reason and nothing is stored
- Given a product with an image, when I replace it, then the new file is stored and the old path no longer resolves from the product
- Given a product without an image, when it is listed, then a placeholder is shown rather than a broken image

`RF-08` · F3-07 (#67)

### HU-09 — Manage who has access
**As the** administrator, **I want** to create users, change their role and
deactivate them, **so that** access matches who actually works here.

- Given a new user, when I create them with an email and a role, then the account exists and can sign in
- Given an existing email, when I reuse it, then creation is refused
- Given a user who has left, when I deactivate them, then they can no longer sign in and their name still appears against their past actions in the audit log
- Given any of these, when it happens, then it is recorded in the audit log without the password hash

`RF-09` · F3-06 (#66)

## Consulting information

### HU-10 — Find a customer or a product
**As an** analyst, **I want** to search and filter customers and products, **so
that** I can answer a question without writing SQL.

- Given a list, when I search by name or SKU, then only matching rows are shown
- Given a result, when I open it, then I see its detail including its category, its channel and its current segment
- Given no matches, when the search returns, then I am told so rather than shown an empty table with no explanation
- Given more results than a page, when I page through them, then the search stays applied

`RF-10`, `RF-13` · F3-05 (#65)

### HU-11 — Check stock
**As an** inventory planner, **I want** to see stock by store and product, **so
that** I can spot what needs replenishing.

- Given the inventory view, when I open it, then I see quantity on hand per store and product with its last update time
- Given a store, when I filter by it, then only its stock is shown
- Given a quantity below a threshold, when it is listed, then it is visually distinguishable from healthy stock

`RF-11` · F3-05 (#65)

## The main process

### HU-12 — Recalculate segments
**As the** administrator, **I want** to recalculate every customer's segment
from the sales actually recorded, by rules or by K-means, **so that** segments
reflect behaviour rather than a manual guess and every earlier result stays on
record.

- Given the Segment run page, when I choose a method (RFM rules or K-means) and a window of 1 to 3650 days and press Review recalculation, then nothing is written until I confirm on the next page
- Given a confirmed run, when it commits, then each customer with sales in the window carries a stable label derived from raw recency, frequency and monetary values: RFM rules score quintiles and match them against the segment bands, and K-means pairs its clusters with the labels by rank, best to worst
- Given equal raw R, F or M values in an RFM-rules run, when quintile scores are assigned, then the tied customers receive the same score for that measure
- Given a K-means run, when I enter K, a seed, an iteration limit and a tolerance, then they are recorded with the window and the quality measures so the run can be reproduced; a K below 2 is refused before any sale is read
- Given a successful recalculation, when it commits, then one run and one assignment-history row per customer are stored together; each customer's earlier open row is closed and nothing else in it changes, so no earlier assignment is overwritten
- Given a customer's segment, when it is read anywhere in the application, then it is the one open assignment-history row, not a field on the customer
- Given a customer with no sales in the window, when the recalculation commits, then the new open row records the customer as unassigned rather than retaining a stale label
- Given any part of the recalculation fails, when the transaction rolls back, then no run, no closed row and no new row remains
- Given the run finishes, when the result is shown, then it reports customers processed, segments assigned, customers with no sales in the window, customers who matched no rule, and the time taken
- Given a run commits, when I read the audit log for `customer_segment_history`, then the rows it closed and opened are there
- Given I am not the administrator, when I try to run it, then it is refused

`RF-12` · F3-10 (#102), F7-02, F9-01–F9-03, F9-05 (#336), #335, #352, #519

## Audit

### HU-13 — Trace a change
**As an** auditor, **I want** to read what changed, when, and who changed it,
**so that** I can answer a compliance question without database access.

- Given entries exist, when I open the audit view, then they are listed newest first with entity, action, actor and time
- Given a specific entity or date range, when I filter by it, then only matching entries are shown
- Given one entry, when I open it, then I see the values before and after, with the changed fields distinguishable
- Given an entry the seed produced, when I open it, then the actor is shown as unattributed rather than invented
- Given I am not an administrator or auditor, when I request the view, then it is refused

`RF-14` · F3-11 (#103)

### HU-14 — Never find a credential in the log
**As the** organization, **we want** the audit log to carry no password hashes,
**so that** a trail kept for years is not a second place credentials can leak
from.

- Given a user is created or edited, when the audit entry is written, then it contains no `password_hash` field
- Given the audit view, when an `app_user` entry is opened, then no credential is displayed from any source

`RNF-17` · already enforced by `fn_audit()`; verified as case P3 in [`evidence/f2-07-integrity-verification.md`](evidence/f2-07-integrity-verification.md)

## Running it

### HU-15 — Bring the whole thing up with one command
**As a** developer joining the team, **I want** one command to start a working
environment, **so that** my first day is not spent installing PostgreSQL and
ordering scripts by hand.

- Given a clean clone and a copied `.env`, when I run `docker compose up`, then the database is created, schemad, seeded, and the application answers
- Given the stack is up, when I query the database, then all 27 tables exist and exactly one administrator does
- Given I bring it down and up again, when it starts, then it reaches the same state with no manual step

`RNF-13`, `RNF-15` · F3-09 (#89)

## Handling failures

### HU-16 — Receive a controlled error
**As a** user, **I want** a failure to be explained without revealing
internals, **so that** I can recover or report it safely.

- Given I request a page that does not exist, when the application answers, then I see the application's own 404 page saying the link may have moved or been mistyped, with a way back to the start
- Given I request a page my role may not reach, when it is refused, then I see a 403 page that says so and tells me to ask the administrator to review my role
- Given an unexpected failure, when the error page is shown, then it says the problem was recorded and that nothing I did caused it, and it contains no stack trace, exception name, SQL or secret
- Given an unexpected failure, when it is handled, then the log holds one entry with the method, path and client, and the reference shown on the page

`RF-15` · F4-05 (#73)

## Segmentation history and migration

### HU-17 — Inspect assignment history
**As an** analyst, **I want** to inspect completed segmentation runs and a
customer's assignment timeline, **so that** I can trace the current label back
to the run that produced it.

- Given completed runs, when I open Run history, then each run is listed, most recent first, with its method, window, customer count, executor and time, and its K and seed when it has them
- Given a run, when I open it, then I see its parameters as entered, a K-means run's quality measures with their values, and every customer it scored with label, R/F/M scores, last purchase, orders and spend, unassigned customers included
- Given a customer with several assignments, when I open the customer, then the Segment timeline lists every assignment, newest first, with the date the customer entered the label it holds and its stored values
- Given the timeline, when I look for the current segment, then it is the one assignment whose validity is still open
- Given a date, when I ask which segment the customer held on it, then I am shown the assignment open at the end of that day, or told that no run had scored the customer yet
- Given an assignment that follows an earlier one, when I open the change, then I see the sales that entered and left the calculation between the two runs
- Given a role without `segment.read`, when it requests any of these pages, then it is refused before the view runs

`RF-16` · F7-02, F7-03, F7-07 (#337), #447

### HU-18 — Explain a segment migration
**As an** analyst, **I want** to compare two completed runs and open the
customers behind a matrix cell, **so that** I can explain who moved and why.

- Given two selected runs in either order, when I compare them, then the earlier run is the before state, the later run is the after state, and every matrix count reconciles to its customer list
- Given a customer absent from one run or unassigned in a run, when the matrix is built, then those states stay distinct and selectable
- Given a cell, when I open it, then I see its customers with both labels and whether each improved or declined, and a link to each customer's explanation
- Given a changed assignment, when I open its explanation, then it states the recency, frequency and monetary values of both runs and judges each changed or stable by RN-60's thresholds, from stored values and without rescoring
- Given only a quintile score changed while the raw value stayed stable, when I read the explanation, then it says that other customers moved the cut point

`RF-17` · F7-04–F7-06, F7-08 (#338), #339

## Sales and consumption

### HU-19 — Import sales with a reconciled report
**As the** administrator, **I want** to upload the versioned sales CSV and keep
its rejection report, **so that** accepted sales are usable without hiding bad
rows or duplicating retries.

- Given the Sales import page, when I choose a CSV that follows the contract version shown and press Upload, then each valid row is accepted and each invalid row is rejected without stopping the remaining rows
- Given a file with an unsupported header or contract version, when I upload it, then it is refused as a whole with the reason and no row is loaded
- Given a completed load, when I open its report, then received equals accepted plus rejected and every rejection names its source line and reason
- Given a row for a source transaction id and product that already have an accepted line, when it appears again, then it is rejected as a duplicate and no second line is written
- Given an accepted row, when it is stored, then the store's stock of that product falls by its quantity in the same transaction; a row the stock cannot cover is rejected and leaves nothing behind
- Given a row dated in the future, with a price that is not positive or has more than two decimals, or with a spreadsheet-formula-like transaction id, when it is read, then it is rejected with that reason
- Given a past load, when I return later, then its report is still there and its rejections download as a CSV
- Given a role without `sales_ingest.execute`, when it requests an import page, then it is refused

`RF-18` · F8-01, F8-02, #334, #355, #356, #362

### HU-20 — Understand a customer's consumption
**As an** analyst, **I want** a consumption profile with before-and-now
behaviour, **so that** I can distinguish a real shift from ordinary variation.

- Given accepted sales in the stated window, when I open a profile, then it shows total spend, purchase frequency, average ticket, last purchase, average discount, dominant channel and store, favourite categories, frequent products, R/F/M and the current and previous segment
- Given no accepted sale in the window, when I open the profile, then its sales measures are reported as unavailable rather than as zero, and no leader is invented
- Given a window outside 1 to 3650 days, when I submit it, then the page explains the limits and computes nothing
- Given the window's two halves, when their dominant channel, store or leading category differs, then Before and now claims a shift only if each half has at least three purchases and a clear leader, and otherwise says why
- Given a tie, when a ranking is shown, then its documented tie-break makes the result deterministic

`RF-19` · F8-03–F8-06 (#341)

## Segmentation models and recommendations

### HU-21 — Compare the two segmentation models
**As an** analyst, **I want** to compare an RFM-rules run with a K-means run,
**so that** I can see where their stable labels agree without treating one as
the truth.

- Given one run of each method, when I compare them, then each run's parameters and quality measures are shown and every label appears in vocabulary order with its population in each run
- Given customers scored by both runs, when the comparison is built, then each is counted once as agreeing or disagreeing by label
- Given a customer scored by only one run, when the comparison is built, then that customer is counted separately rather than as a disagreement
- Given the cross-tabulation, when its cells are totalled, then they reconcile to the customers scored by both runs and its diagonal to the agreements

`RF-20` · F9-04

### HU-22 — Receive explainable recommendations
**As a** store manager, **I want** a customer's in-stock recommendations and
the reason for each, **so that** I can make a relevant suggestion at the usual
store.

- Given a customer with an open label and a usual store, when I open their recommendations, then every result is active, not bought by them in the window and in positive stock at that store
- Given a result, when I read its reason, then it names each matching segment, preferred-category or purchase-history signal and the figures behind it
- Given a preferred or purchased parent category, when a product in a category below it qualifies, then the nearest matching category is named in the reason
- Given no open label or no usual store, when I open the page, then it says which prerequisite is absent and invents no fallback
- Given a product's stock falls to zero, when I reload, then it no longer appears

`RF-21` · F10-01, F10-02, #277, #280

## Campaigns and experiments

### HU-23 — Move a campaign through its lifecycle
**As a** marketing user, **I want** a campaign to target a stable segment label
and follow explicit states, **so that** recalculation does not change its
meaning and completed work cannot be reopened accidentally.

- Given the Campaigns page, when I create or edit a draft, then its name, dates and target label are stored, and an end date before the start date or an unknown label is refused
- Given a draft whose experiments all have their arms, when I activate it, then its status becomes active; a campaign that ended before today, or whose experiment lacks its arms, is refused with the reason
- Given an active campaign, when I complete or cancel it, then the new state is recorded in the audit log and no later transition is allowed
- Given an illegal transition or an edit outside a draft, when I submit it, then it is refused with the campaign's current state and what it may become

`RF-22` · F11-01, F11-02

### HU-24 — Fix an experiment before assignment
**As a** marketing user, **I want** to define the experiment arms and
measurement frame before assignment, **so that** outcomes cannot influence the
design later.

- Given an experiment setup, when I save it, then it has at most one optional control, named treatment arms, a target metric, a positive conversion window and a data origin
- Given a no-control design, when I save it, then it has exactly two treatment arms
- Given the target metric Average ticket, when I choose it, then it is labelled as not measured yet
- Given the first assignment exists, when I try to change the campaign, dates, target metric, conversion window or arm definitions, then the change is refused; only the name may change
- Given a `SEEDED` or `INJECTED` experiment, when any setup or result surface shows it, then that surface says `Synthetic`

`RF-23` · F11-01, F11-03, #342, #427

### HU-25 — Keep assignment, exposure and conversion separate
**As a** marketing user, **I want** experiment participation recorded as
separate durable events, **so that** an unexposed or non-converting customer
stays in the population originally assigned.

- Given an active campaign, when I assign its experiment, then each customer holding the target label enters exactly one arm, the arms differ in size by at most one, and the whole assignment commits or rolls back together
- Given assignments already exist, when assignment is requested again or a row is moved or deleted, then it is refused
- Given a treatment assignment inside the experiment's dates, when I record exposure for one customer or a whole arm, then each exposure is appended separately from the assignment
- Given a control assignment, an unassigned customer or a date outside the experiment, when exposure is attempted, then it is refused
- Given a qualifying sale inside an assignment's fixed window, when I evaluate conversion, then it is linked once to that assignment without changing assignment or exposure

`RF-24` · F11-04–F11-06, #357

### HU-26 — Measure experiment outcomes
**As an** analyst, **I want** conversion and uplift calculated over the declared
population, **so that** the result does not exclude people after their outcome
is known.

- Given evaluated assignments, when I open the result, then every arm shows conversion over all assigned customers and every treatment arm also shows conversion over exposed customers
- Given a control and non-empty arms, when uplift is calculated, then it uses only the assigned-customer rate and shows its 95% interval
- Given an open conversion window, when the result is shown, then it is marked preliminary and the still-open assignments remain in the denominator
- Given no control, an empty arm, a metric other than conversion or unrecorded qualifying sales, when uplift is requested, then it is refused with the reason
- Given the validation fixtures, when the A/A and fixed injected-uplift checks run, then the A/A difference is not significant and the injected effect is recovered within its stated tolerance
- Given a non-`OBSERVED` experiment, when its result is shown, then it says `Synthetic`

`RF-25` · F11-07, #343

## Analytics dashboards and reports

### HU-27 — See one segmentation run as a dashboard
**As an** analyst, **I want** a dashboard for one completed run, **so that** I
can read its population, behaviour, movement and revenue together.

- Given a selected run, when I open the Segmentation dashboard, then it shows Segment sizes, Revenue by label over the run's window, the RFM distribution and the Migration flow from the immediately previous run, all by stable label
- Given the same page, when I read below the charts, then I find average R, F and M per label, the experiments running today with each arm's conversion, and the most recommended products with their stock
- Given equal raw R, F or M values, when quintile bins are built, then tied customers remain in the same bin
- Given either segmentation method, when the charts are built, then they read stable labels and stored raw values without interpreting a cluster id or method-specific score
- Given the page is loaded, when its assets and data are inspected, then Highcharts is served by the application and chart data is embedded in the server-rendered HTML rather than fetched from an endpoint

`RF-26` · F12-01, #340, #423

### HU-28 — Filter segment history
**As an** auditor, **I want** to filter stored assignment history and expand a
row's explanation, **so that** I can answer a specific segment-history question
without rerunning a model.

- Given any combination of run, label (or Unassigned) and run-date filters, when I apply them, then every selected condition combines with AND in the count and the rows
- Given no matching rows, when the report returns, then it shows a valid empty state
- Given a row with an immediately previous run, when I expand it, then its explanation compares that customer's two stored history rows
- Given a customer's first row, when I expand it, then the report says there is no earlier run rather than inventing a comparison

`RF-27` · F12-02

### HU-29 — Filter consumption and recommendation reports
**As a** store manager, **I want** consumption shifts and recommendations under
the same filters, **so that** I can focus on the store, channel, category and
period I am investigating.

- Given store, channel, category and as-of-and-window filters, when I apply them, then every selected filter must match and an omitted filter excludes nothing
- Given the two report sections, when one has no rows, then only that section shows its empty state
- Given more than one page in either section, when I page through it, then the other section's page and all filters are retained
- Given a recommendation whose stock reaches zero, when I regenerate the report, then that row is absent

`RF-28` · F12-03

### HU-30 — Export experiment results with provenance
**As a** marketing user, **I want** a filtered experiment report and matching
CSV, **so that** counts, uplift and data origin remain explicit when shared.

- Given optional campaign and data-origin filters, when I apply them, then they combine in both the rows and the total
- Given an experiment arm, when it is listed, then assignment, exposure and conversion are separate labelled counts beside intent-to-treat uplift and its interval
- Given a filtered report, when I choose Export CSV, then the file contains the same rows and repeats the data origin and the `Synthetic` label on every applicable line
- Given a filter value the report does not offer, when it is submitted, then it is refused rather than used in a query

`RF-29` · F12-04, #358

### HU-31 — See experiment results as charts
**As a** marketing user, **I want** an experiment dashboard with a named
baseline, **so that** I can see arm performance and its development through the
conversion window without changing the measurement rule.

- Given an experiment with assignments, when I open the dashboard, then it shows assigned and exposed conversion rates per arm
- Given a control, when differences are charted, then each treatment is compared with that control using the intent-to-treat rate and its 95% interval
- Given no control, when differences are charted, then the first treatment arm is named as the reference and the chart calls the result a difference, not uplift
- Given conversions through the fixed window, when the cumulative series is drawn, then it never falls and its final point equals the assigned-customer rate
- Given a non-`OBSERVED` experiment, when any chart is read visually or by assistive technology, then it says `Synthetic`

`RF-30` · AN-03 (#415)

### HU-32 — Open the analytics reports from one page
**As a** member of staff, **I want** one Reports page that shows recent revenue
and links to the analytics reports I may open, **so that** I do not need to know
their addresses.

- Given sales on record, when I open Reports, then I see the revenue and the sales count of the newest 90 days, split by channel and by store
- Given no sales on record, when I open Reports, then the page says so instead of showing zeros
- Given my role, when the page lists analytics reports, then it links only those my role can open
- Given the deployment declares demonstration data, when the page loads, then it carries the `Synthetic` badge
- Given a role without `report.read`, when it requests the page, then it is refused

`RF-31` · #344

---

## Traceability

| Story | Requirement | Delivery story | Issue |
|---|---|---|---|
| HU-01, HU-02 | RF-01, RF-02 | F3-03 | #63 |
| HU-03, HU-04 | RF-03, RF-04 | F4-01 | #69 |
| HU-05 | RF-05 | F4-02 | #70 |
| HU-06, HU-07 | RF-06, RF-07 | F3-04 | #64 |
| HU-08 | RF-08 | F3-07 | #67 |
| HU-09 | RF-09 | F3-06 | #66 |
| HU-10, HU-11 | RF-10, RF-11, RF-13 | F3-05 | #65 |
| HU-12 | RF-12 | F3-10, F7-01, F7-02, F9-01–F9-03, F9-05 | #102, #203, #204, #214–#216, #336, #335, #352, #519 |
| HU-13 | RF-14 | F3-11 | #103 |
| HU-14 | RNF-17 | F2-05 | #58 — done, in review |
| HU-15 | RNF-13, RNF-15 | F3-09 | #89 |
| HU-16 | RF-15 | F4-05 | #73 |
| HU-17 | RF-16 | F7-02, F7-03, F7-07 | #204, #205, #337, #447 |
| HU-18 | RF-17 | F7-04–F7-06, F7-08 | #206–#208, #338, #339 |
| HU-19 | RF-18 | F8-01, F8-02 | #209, #210, #334, #355, #356, #362 |
| HU-20 | RF-19 | F8-03–F8-06 | #211–#213, #341 |
| HU-21 | RF-20 | F9-04 | #217 |
| HU-22 | RF-21 | F10-01, F10-02 | #218, #219, #277, #280 |
| HU-23 | RF-22 | F11-01, F11-02 | #220, #221 |
| HU-24 | RF-23 | F11-01, F11-03 | #220, #222, #342, #427 |
| HU-25 | RF-24 | F11-04–F11-06 | #223–#225, #357 |
| HU-26 | RF-25 | F11-07 | #226, #343 |
| HU-27 | RF-26 | F12-01 | #227, #340, #423 |
| HU-28 | RF-27 | F12-02 | #228 |
| HU-29 | RF-28 | F12-03 | #229 |
| HU-30 | RF-29 | F12-04 | #230, #358 |
| HU-31 | RF-30 | AN-03 | #415 |
| HU-32 | RF-31 | — | #344 |

Thirty-two product stories cover every functional requirement, RF-01 to RF-31.
HU-14 and HU-15 cover the non-functional requirements they name, and the
first-delivery stories other than HU-14 need F3-02 (#62), the database
connection, before they can start. The tests and the evidence behind RF-12 and
RF-16 to RF-31, and the gaps #443 will fill, are in
[`requirements.md`](requirements.md) §4.
