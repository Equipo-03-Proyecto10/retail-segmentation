# AN-04 — In-app notifications

Evidence for AN-04 (#419) and [ADR-0047](../adr/0047-notifications-are-pulled-from-postgresql-and-an-experiments-end-is-announced-by-the-next-page-read.md):
a signed-in user is told, inside the web system, when something that concerns
their role happens. Delivery is by pull from PostgreSQL; the web connects to no
Redis and calls no service.

The work is six stacked pull requests, each of which passes the suite on its own.
This record is extended by each one; what a section covers is named in its
heading.

| PR | Adds |
|---|---|
| 1 | ADR-0047 |
| 2 | The two tables, their seed, the 4NF argument, table count 27 to 29 |
| 3 | The inbox, the navigation entry and its unread badge |
| 4 | The detail page, mark one and mark all read, the 404 rule |
| 5 | The three events a request causes, written in their own transaction |
| 6 | An experiment's end, announced when a page is next read (the closing version of this record) |

The screens, at 375 px and 1440 px, are under [Captures](#captures) at the end.

## How it was checked

PostgreSQL 16.14 (`postgres:16`, a container with a Unix socket and no published
port), built from the three scripts in order, with `psql -v ON_ERROR_STOP=1`.
The test client of the Flask application runs the pages, and the real application
is driven through it where a section says so. The screens were then checked in a
browser, behind NGINX, as [Captures](#captures) records.

## The scripts run clean, in order, from empty

```text
initdb: 00_create_database.sql
initdb: 01_schema.sql
initdb: 02_seed_30_per_table.sql
initdb: done — retail is created, schemad and seeded.
```

The `database` job of `.github/workflows/ci.yml` has six steps that run SQL. The
three scripts are the first; each of the other five was run against the same
database and exited 0: the seed's ADR compliance queries, the application-role
verification block (run as `retail_app`: `PASS: SELECT, INSERT, UPDATE, DELETE
and audit sequence access`), the 30-row loop, the boundary sections re-applied,
and the grants and boundaries checks. `contracts/db/check_grants.sh` prints
nothing and exits 0, with `notification` and `notification_recipient` named among
the web-only tables no `svc_*` role may reach.

`python docs/check.py`:

```text
Business-rule ids: 65 headings, 65 unique
Schema tables: 29
Data-model entity tables: 29
Documented table counts: docs/user-stories.md:188=29, docs/infra.md:190=29, docs/demo-system-guide.md:527=29, docs/architecture/application.d2:78=29
```

## The seeded database, read through the real queries

The seed writes one event for each of the thirty runs and thirty experiments, to
the active users whose role holds the delivery permission (1110 rows), and marks
what is older than seven days read. The inbox was then read as `retail_app`, the
application's own role, through `web.services.notifications.read_inbox`, for the
first seeded user of each role:

| Role | User | Notifications | Unread | Pages | Badge |
|---|---|---|---|---|---|
| `ADMIN` | 1 | 60 | 8 | 3 | `8` |
| `ANALYST` | 2 | 60 | 8 | 3 | `8` |
| `STORE_MANAGER` | 3 | 30 (runs only) | 8 | 2 | `8` |
| `MARKETING` | 4 | 60 | 8 | 3 | `8` |
| `INVENTORY_PLANNER` | 5 | 0 | 0 | 1 | none |
| `AUDITOR` | 6 | 60 | 8 | 3 | `8` |
| `CUSTOMER` | 7 | 0 | 0 | 1 | none |

The first row of every inbox that has one is `Segmentation run #30 (RFM_RULES)
completed for 30 customers.`, the newest event; the third page of the
administrator's inbox ends on the oldest experiment's end. The store manager
holds `segment.read` and not `campaign.read` (ADR-0023), so the thirty experiment
events are absent from their inbox and from their count, which is the read-time
re-check of the delivery permission. `INVENTORY_PLANNER` and `CUSTOMER` hold
neither, and their inbox says that nothing is sent to their role instead of
promising that something will come.

## What the page does

Checked with the Flask test client, the database replaced by a mock and the
data-access functions by fakes, so each assertion states what the page was given
(`tests/test_notification_routes.py`):

- The navigation of every signed-in role has a "Notifications" entry after
  "Home", in the Workspace section, and it is current on the inbox. A visitor who
  is not signed in has none.
- The badge shows the unread count: `1`, `42` and `99` as they are, `100` and
  `12345` as `99+`, and nothing at 0. It carries a visually hidden word, so its
  accessible name is "7 unread", not a bare number.
- The inbox is twenty to a page; `?page=2` reads offset 20; a page past the last
  redirects to the last (`/notifications/?page=9` to `?page=3` with 45 rows);
  `0`, `-1`, `abc`, `٢`, a number past the integer range and an empty value are
  the first page.
- An unread row is marked in words ("Unread") and by a rule and a tint, not by
  colour alone. A subject's name, here a file called `<script>alert(1)</script>.csv`,
  is escaped.
- A count that cannot be read, because the query raises `OperationalError` or the
  connection cannot be opened, hides the badge, logs
  `notification_count_failed reason=OperationalError` without the driver's text,
  and the page is still served: a 200 for the page, a 404 still a 404.
- A notification opens at `/notifications/<id>` with its sentence, its facts and a
  link to the page it is about. Opening it, or a `HEAD` request, does not mark it
  read: that is a `POST` to `/notifications/<id>/read`, with a CSRF token, which
  returns to the page it came from (a same-site path only: `https://evil.example/`,
  `//evil.example/`, `/\evil.example` and `javascript:alert(1)` all fall back to
  the inbox). "Mark all read" is a `POST` to `/notifications/read-all` and says how
  many it marked (`7 notifications marked read.`, `1 notification marked read.`,
  `No unread notifications.`).
- An id the column cannot hold (`0`, `-1`, `9223372036854775808`) matches no route
  and is a 404 before any query runs; `9223372036854775807`, the largest it can,
  is looked up.

## Privacy: the same 404 for every notification that is not yours (RN-62)

The application was run against the database above as `retail_app`, with the real
sign-in, the real CSRF check and the real session lookup, and each account signed
in through `/login`. Notification 60 is the newest run's, written for every holder
of `segment.read`; notification 1 is an experiment's end, written for every holder
of `campaign.read`. The badge is the number the page shows.

```text
badge before: analyst 8 | store manager 8 | inventory planner none

analyst (ANALYST)      GET  /notifications/60                  -> 200
store manager          GET  /notifications/60                  -> 200
inventory planner      GET  /notifications/60                  -> 404
admin                  GET  /notifications/60                  -> 200
-- an event the role may not read (STORE_MANAGER holds segment.read, not campaign.read)
store manager          GET  /notifications/1                   -> 404
analyst                GET  /notifications/1                   -> 200
-- an id that does not exist, and ids the column cannot hold
analyst                GET  /notifications/999999              -> 404
analyst                GET  /notifications/9223372036854775807 -> 404
analyst                GET  /notifications/9223372036854775808 -> 404
analyst                GET  /notifications/0                   -> 404
-- marking read
inventory planner      POST /notifications/60/read             -> 404
analyst, no CSRF token POST /notifications/60/read             -> 403
analyst                POST /notifications/60/read             -> 303
badge after analyst read one: analyst 7 | admin 8 (admin's own row is untouched)
analyst                POST /notifications/read-all            -> 303
flash: 7 notifications marked read.
badge after mark all read: analyst none | store manager 8

-- the 404 body is the same page for a foreign id and a missing one
identical apart from the reference: True | contains 'Forbidden': False
```

Reading it: a role that was never written for an event (`INVENTORY_PLANNER`), and a
role that was written for it but no longer holds the permission that delivered it
(`STORE_MANAGER` and the experiment), both get the 404 that a number that does not
exist gets. The 404 page of a foreign id and of a missing one are the same page
apart from the request reference, and none of them says "Forbidden". Marking read
changes only the reader's own row: the administrator's badge stayed at 8 while the
analyst's went to 7, and "mark all read" left the store manager's untouched.

The 403 is the CSRF check, which runs before the route and is the same refusal
every other form gets; it is not a notification being refused. The statement-level
checks, including a kind the role lost while its recipient row remains, are
`tests/test_notification_state_db.py`.

## The three events a request causes (RN-61)

The three producers were run as `retail_app` against the same database, with the
real services, a real connection and the administrator as the acting user. The
connection's `commit()` was switched off so that the script could roll everything
back, which leaves a `rollback()` the service itself asks for reaching the
database. A three-row CSV (one row malformed) was loaded with the real adapter,
the rows themselves stubbed so that no sale was written:

```text
events written in this transaction:
   SALES_LOAD_FINISHED    1 recipient
   SEGMENT_RUN_COMPLETED  21 recipients
   USER_DEACTIVATED       1 recipient
ADMIN: total=63 unread=11
   - Demo User 2 was deactivated.
   - Segmentation run #52 (RFM_RULES) completed for 30 customers.
   - The sales load “may.csv” finished: 2 rows accepted, 1 rejected.
STORE_MANAGER: total=31 unread=9
   - Segmentation run #52 (RFM_RULES) completed for 30 customers.
   - Segmentation run #30 (RFM_RULES) completed for 30 customers.
   - Segmentation run #29 (RFM_RULES) completed for 30 customers.
INVENTORY_PLANNER: total=0 unread=0
run with failing notification -> boom
segmentation_run rows after the failed run and its rollback: 30
```

The load goes to the one account that holds `sales_ingest.execute`; the run to the
21 active users whose role holds `segment.read`, the administrator who ran it
among them; the deactivation to the administrator alone. The store manager reads
the run and neither of the other two. An account that is already inactive when
the event happens is not written to: `tests/test_notification_producers_db.py` runs
the same run after deactivating one analyst and counts 20. When the notification's
own insert is made to fail, the run is rolled back with it (30 runs, the seeded
thirty, remain; the rollback also took this script's earlier run, which shared
its transaction).

## An experiment's end, announced when a page is next read (RN-64)

Six experiments were added to the database above, with the dates relative to the
database's own `CURRENT_DATE`: ended yesterday, ended 14 days ago, ending today,
ended 15 days ago, no end date, and ending in three days. The real application
was then driven as `retail_app` with the real sign-in, CSRF and sessions. Each line
is the set of experiments announced after the request named:

```text
announced before any request: []
after an anonymous visit and signing in: []
HEAD /notifications/ -> 200 announced: []
POST /notifications/999999999/read -> 404 announced: []
GET /notifications/ -> 200 announced: [yesterday, 14 days ago]
badge on that page: 10 | first rows:
   - The experiment “ended yesterday” reached its end date, 2026-10-18.
   - The experiment “ended 14 days ago” reached its end date, 2026-10-05.
after two more GETs: [yesterday, 14 days ago] | events: 2 | recipients: 32 (2 x 16)
store manager (no campaign.read) inbox mentions an experiment: False | badge: 8
```

An experiment ending today is still running; one that ended 15 days ago is past the
lookback; one with no end date and one ending in three days have not ended. The
first signed-in `GET` announced the two that qualify, and the page that request
rendered already listed them and counted them in its badge (analyst: 8 unread
before, 10 after). A `HEAD`, a `POST` and an anonymous visit announced nothing,
nor did signing in, and further reads wrote nothing. The store manager, whose role
does not hold `campaign.read`, was not written to and reads no experiment.

**Two sessions at once.** `tests/test_notification_announcer_db.py` makes the race
happen deterministically. Session A runs the statement and holds its insert
uncommitted; session B runs it while A is open, finds the experiment unannounced
too, tries to insert, and is seen in `pg_stat_activity` waiting on A's transaction
(`wait_event = 'transactionid'`). A commits; B's `ON CONFLICT DO NOTHING` inserts
nothing and writes no recipients: one event and 16 recipients remain. When A rolls
back instead, B announces the experiment itself. With the `ON CONFLICT` clause
removed the first of those tests fails; with `<=` in place of `<` the "ends today"
case fails; with the `ORDER BY` removed the test that lists a batch most recently
ended first fails. (Removing the `NOT EXISTS` filter changes no result, as expected:
it saves the speculative insert for an experiment that already has its event, and
`ON CONFLICT` is what makes the statement safe.)

## Tests

| File | Covers |
|---|---|
| `tests/test_notification_schema.py` | PostgreSQL: the arc CHECK, the three `UNIQUE` subjects, the cascades, the application role's privileges, no service role, the seed's recipients against the permission matrix |
| `tests/test_notification_reads_db.py` | PostgreSQL: the read queries, per role, newest first, pages that do not overlap, the unread count, a demoted role, the four kinds' facts |
| `tests/test_notification_announcer.py` | The hook: a signed-in `GET` announces before the view, and a `HEAD`, any other method, an anonymous visit, a refused request, an unknown route and a static file do not; a database failure is logged and the page served, any other failure surfaces; the service's recipients, lookback, commit, rollback and log line; the statement's rules |
| `tests/test_notification_announcer_db.py` | PostgreSQL: the rules on real dates (today, yesterday, 14 and 15 days, no end date, the future, the seed), recipients, an inactive account, idempotence, batch order, the deterministic two-session race and the rollback variant, and the real application through the real sign-in |
| `tests/test_notification_producers.py` | The statements, who is told of each event, the order and the commit, the rollback when a notification fails, and what announces nothing: a refused file, a failed run, a refused or repeated deactivation, an activation, the bulk command-line deactivation, and any trigger on `app_user` |
| `tests/test_notification_producers_db.py` | PostgreSQL, as `retail_app`: each producer's event, recipients and sentence; the actor included; an inactive account excluded; the snapshot; and the operation rolled back when its notification fails |
| `tests/test_notification_state_db.py` | PostgreSQL: one notification found only by its recipient and only among the kinds the role reads, marking one (idempotent, keeps the first time, never another user's), marking all (counts what it marked, leaves hidden kinds) |
| `tests/test_notification_service.py` | The kinds a role reads, `roles_holding`, the badge cap, the four sentences and links, the inbox's paging, one notification and marking read as one committed operation |
| `tests/test_notification_routes.py` | The inbox, the navigation entry, the badge and its fail-soft behaviour; the detail page, the 404 for missing, foreign and out-of-range ids, the three `POST`s, the return address, and the real CSRF check |
| `tests/conftest.py`, `pyproject.toml` | The unread count and the announcement are each stubbed out for every other route test, with the `real_notification_count` and `real_experiment_announcer` markers to opt out |
| `tests/test_segmentation_pipeline.py`, `tests/test_kmeans_run.py`, `tests/test_kmeans_adapter.py`, `tests/test_segment_run.py`, `tests/test_event_logging.py`, `tests/test_sales_csv.py`, `tests/test_single_administrator.py` | Their patch lists gain the notification call, and the two ordering assertions state that it is the last write before the commit |
| `tests/test_analytics_authz.py`, `tests/test_authz.py`, `tests/test_negative_flows.py` | The `notifications` blueprint is classified as not analytics; every role's menu gains the entry; the anonymous refusal matrix covers the four routes |

The full suite on this branch: 3,316 passed, 1 skipped, and 2 failed, all with
PostgreSQL available. The 2 are `tests/test_create_database_script.py`'s two
scratch-cluster tests, which fail here only because the portable `initdb` this
machine offers has no `pg_trgm`; they fail the same way on `develop`. Without a
database, 3,230 pass and 89 skip, none fail.

## Captures

Taken from the tip of the last branch (`feature/419-notification-experiment-end`),
run with `docker compose -f compose.yaml -f compose.proxy.yaml up --build`:
PostgreSQL 16 built from the three scripts, NGINX with the production CSP, and
Chromium through Playwright. Every account signed in through the real form.
Full-page captures are exactly 375 or 1440 CSS px wide (no horizontal scroll). The
browser console showed no CSP violation, and only the two deliberate 404s as errors.

| Page | Account | What it shows | Captures |
|---|---|---|---|
| `/notifications/` | `admin@mosaiq-demo.com` | The seeded inbox, newest first. The 8 unread rows are tinted and labelled "Unread", with "Mark read". The badge reads 8, and the list is page 1 of 3 | [1440](an-04-inbox-1440.png) · [375](an-04-inbox-375.png) |
| `/notifications/?page=3` | `admin@mosaiq-demo.com` | The last page: the experiments that ended | [1440](an-04-inbox-last-page-1440.png) |
| `/notifications/` | `admin@mosaiq-demo.com` | The badge with the navigation collapsed, and in the 375 px tab bar | [1440, collapsed](an-04-badge-collapsed-1440.png) · [375](an-04-badge-375.png) |
| `/notifications/` | `user3@mosaiq-demo.com` (store manager) | Runs only, 30 notifications. `segment.read` and not `campaign.read`, so no experiment-ended rows | [375](an-04-inbox-store-manager-375.png) |
| `/notifications/` | `user5@mosaiq-demo.com` (inventory planner) | "Nothing is sent to your role", and no badge | [375](an-04-inbox-no-delivery-375.png) |
| `/notifications/60` | `user5@mosaiq-demo.com` (inventory planner) | 404 for a notification not written for them, never 403 (RN-62) | [1440](an-04-not-found-1440.png) · [375](an-04-not-found-375.png) |
| `/notifications/60` | `user2@mosaiq-demo.com` (analyst) | An unread notification, with "Open the run" and "Mark read" | [1440](an-04-detail-unread-1440.png) · [375](an-04-detail-unread-375.png) |
| `/notifications/60` | `user2@mosaiq-demo.com` | After "Mark read": "Read" with its time, and the badge down from 8 to 7 | [1440](an-04-detail-read-1440.png) |
| `/notifications/` | `user2@mosaiq-demo.com` | After "Mark all read": "7 notifications marked read.", no badge, and no "Mark all read" button | [1440](an-04-mark-all-read-1440.png) |

## What was not run

Nothing was run on the Compute Engine instance. The seed's recipients are those of
the seeded users only. The instance database must be rebuilt from the three
scripts before the release that contains the inbox, because the page reads tables
the old database does not have.
