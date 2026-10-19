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
| 3 | The inbox, the navigation entry and its unread badge (this version of the record) |
| 4 | The detail page, mark one and mark all read, the 404 rule |
| 5 | The three events a request causes, written in their own transaction |
| 6 | An experiment's end, announced when a page is next read |

TODO: the 375 px and 1440 px captures of the inbox, and the others listed under
"Captures still to take" below, are not in this record yet. They need a browser
session and a signed-in account on a running instance; the files will be added as
`an-04-*.png` and linked from this record, and none is referenced before it
exists.

## How it was checked

PostgreSQL 16.14 (`postgres:16`, a container with a Unix socket and no published
port), built from the three scripts in order, with `psql -v ON_ERROR_STOP=1`.
The test client of the Flask application runs the pages; no browser was used for
this version.

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
Business-rule ids: 62 headings, 62 unique
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

## Tests

| File | Covers |
|---|---|
| `tests/test_notification_schema.py` | PostgreSQL: the arc CHECK, the three `UNIQUE` subjects, the cascades, the application role's privileges, no service role, the seed's recipients against the permission matrix |
| `tests/test_notification_reads_db.py` | PostgreSQL: the read queries, per role, newest first, pages that do not overlap, the unread count, a demoted role, the four kinds' facts |
| `tests/test_notification_service.py` | The kinds a role reads, `roles_holding`, the badge cap, the four sentences and links, the inbox's paging |
| `tests/test_notification_routes.py` | The inbox, the navigation entry, the badge and its fail-soft behaviour |
| `tests/conftest.py`, `pyproject.toml` | The unread count is stubbed to none for every other route test, with the `real_notification_count` marker to opt out |
| `tests/test_analytics_authz.py`, `tests/test_authz.py`, `tests/test_negative_flows.py` | The `notifications` blueprint is classified as not analytics; every role's menu gains the entry; the anonymous refusal matrix covers the inbox |

The full suite on this branch: 3,181 passed, 1 skipped, and 2 failed, all with
PostgreSQL available. The 2 are `tests/test_create_database_script.py`'s two
scratch-cluster tests, which fail here only because the portable `initdb` this
machine offers has no `pg_trgm`; they fail the same way on `develop`. Without a
database, 3,136 pass and 48 skip, none fail.

## Captures still to take

None of these exists yet. Each is for a browser session; the file name is the one
the finished record will link.

TODO: capture these, signed in as the account named, at both widths, and link them
here.

| Page | Account | State | Files |
|---|---|---|---|
| `/notifications/` | `admin@mosaiq-demo.com` | the seeded inbox: unread and read rows, the badge `8`, page 1 of 3 | `an-04-inbox-1440.png`, `an-04-inbox-375.png` |
| `/notifications/?page=3` | `admin@mosaiq-demo.com` | the last page, previous link only | `an-04-inbox-last-page-1440.png` |
| `/notifications/` | `user3@mosaiq-demo.com` (store manager) | runs only, 30 rows, 2 pages | `an-04-inbox-store-manager-375.png` |
| `/notifications/` | `user5@mosaiq-demo.com` (inventory planner) | "Nothing is sent to your role" | `an-04-inbox-no-delivery-375.png` |
| Any page | `admin@mosaiq-demo.com` | the badge with the navigation collapsed, and in the 375 px tab bar | `an-04-badge-collapsed-1440.png`, `an-04-badge-375.png` |

## What was not run

Nothing was run on the Compute Engine instance, and no browser was used. The
seed's recipients are those of the seeded users only. The instance database must
be rebuilt from the three scripts before the release that contains the inbox,
because the page reads tables the old database does not have.
