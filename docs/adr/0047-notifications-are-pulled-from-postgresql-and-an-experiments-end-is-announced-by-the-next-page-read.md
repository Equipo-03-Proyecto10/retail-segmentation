# ADR-0047 — Notifications are pulled from PostgreSQL, and an experiment's end is announced by the next signed-in page read

**Status:** Accepted
**Owner:** Marcelo
**Issue:** #419
**Supersedes:** —
**Superseded by:** —

---

## Context

AN-04 (#419) gives the web system internal notifications. A user whose role holds the relevant permission is told
when a sales CSV load finishes, when a segmentation run completes, when an experiment reaches its end date, and
(administrator only) when a user is deactivated. Q-14 (2026-10-15) limits the last one to deactivations made in the
web: a deactivation through users-service creates none, and no trigger on `app_user` is added.

What already holds, and shapes the answer:

- **The web is one Flask application serving server-rendered HTML.** It connects to no Redis and consumes no
  microservice ([ADR-0022](0022-sessions-are-resolved-server-side-on-every-request.md), Q-12). A notification is
  therefore data in PostgreSQL, produced by the web's own service layer.
- **Nothing runs between requests.** The deployment installs `mosaiq.service`, two Gunicorn workers, and PostgreSQL. No
  timer unit, cron entry, queue or `pg_cron` extension is installed or documented anywhere in the repository.
- **A service owns its transaction** ([ADR-0014](0014-service-owned-transactions-and-typed-write-failures.md)), and
  permissions live in code, not in a table ([ADR-0007](0007-permissions-in-code-with-a-default-deny-middleware.md)).

Three of the four events happen inside a request this application is serving, so they can be written in that
request's own transaction. The fourth cannot: no request causes a date to arrive. What is genuinely uncertain is that
one: when an experiment "reaches its end date" is announced, to whom, and what happens if nobody opens a page for a
while. The answer below is a judgement, not the only reasonable one.

## Decision

Notifications are delivered by pull only: the inbox page and the navigation badge read PostgreSQL when a page renders,
and nothing is pushed or polled by script. The three events a request causes are written in the transaction of the
service that causes them. The one event no request causes, an experiment's end, is materialized lazily: a single
idempotent statement announces every experiment that has ended and has no notification yet, and it runs on signed-in
`GET` requests only, never on `HEAD` or `POST`.

### Storage

Two tables in `public`, both web-only (no `svc_*` role holds a privilege on them, [ADR-0046](0046-service-owned-data-lives-in-service-schemas.md)):

- `notification` is the event. Its subject is an exclusive arc of four foreign keys, `sales_load_id`, `run_id`,
  `experiment_id` and `subject_user_id`, of which exactly one is set (a CHECK). The first three are each `UNIQUE`, so an
  event is announced once; `subject_user_id` is not, because a user can be deactivated more than once. `kind` is a
  generated column derived from whichever subject is set.
- `notification_recipient` is the read state: `(notification_id, user_id)` is the key and `read_at` is the only other
  column.

No message text is stored. The sentence is built when it is read, from the subject row, so a corrected fact is never
contradicted by an old sentence. Both tables take default DML for `retail_app`, are not audited (they are runtime
state, like `app_session`), and are not seed-exempt.

### Who is told

| Event | Written by | Delivery permission |
|---|---|---|
| A sales CSV load finishes | `load_and_record_sales_csv` | `sales_ingest.execute` |
| A segmentation run completes | `_record` in the segmentation pipeline | `segment.read` |
| A user is deactivated, from the web | `set_active` | `user.write` |
| An experiment has ended | the lazy announcer | `campaign.read` |

Recipients are the **active** users whose role holds that permission, **at the moment of the event**, and the user who
caused it is one of them. The list is a snapshot: a role change later neither adds nor removes recipients. At read time
the permission is checked again against the reader's current role, so a user whose role lost it no longer sees the
notification in the inbox or the count, and asking for it by id answers 404. A notification is private: another
user's id answers 404, never 403.

### Lazy announcement of an experiment's end

- **Ended** means `ends_on < CURRENT_DATE`: an experiment is still running on its end date. A `NULL` `ends_on` is never
  announced.
- **Lookback.** Only experiments that ended within the last 14 days are considered, so a database restored from an old
  backup does not announce every experiment it ever held.
- **Idempotent and conflict-safe.** One data-modifying CTE inserts the missing events with
  `ON CONFLICT (experiment_id) DO NOTHING` and their recipients from the rows it actually inserted. Two Gunicorn
  workers racing on the same experiment leave one event and one set of recipients.
- **Never on `HEAD` or `POST`.** A `HEAD` request must not write, and a `POST` has its own business operation.
- **A failure is logged, not shown.** If the announcer fails, the page is served and the next read tries again.

### Failure behaviour

A notification that cannot be written rolls the business operation back with it (ADR-0014): the three synchronous events
are part of the operation, not a side effect of it. The unread count in the navigation fails soft: if it cannot be read,
the error is logged and the badge is hidden, because a page must not be lost to a counter.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| A cron job or systemd timer that announces ended experiments | The instance has none, and a timer is a deployable unit of another kind that needs its own ADR and an operator step on every rebuild. It would also run when nobody could read the result, and it adds nothing a page read cannot do |
| An in-process scheduler (a thread or APScheduler in the Gunicorn worker) | Two workers would each run one, so the idempotent write would be needed anyway. Worker restarts lose the schedule, and a thread in a request-serving process is a second runtime to reason about |
| `pg_cron` | An extension that neither `00_create_database.sql` nor the runbook installs. It would be one more thing every environment, and every rebuild of the instance, must provide |
| Database triggers on the source tables | A trigger on `app_user` would fire for a users-service deactivation, which Q-14 excludes, and for the CLI's bulk deactivation. It cannot compute recipients, because the role-to-permission map is in code (ADR-0007). It would also fire during the seed load |
| Push (server-sent events, WebSocket) or script polling | The pages are server-rendered and the CSP allows `script-src 'self'` only; a push channel is a new transport for four low-frequency events, and it would need Redis or a long-lived process, which the web does not have |
| One row per user per event, holding the message | The event's facts (which load, how many rows) would be repeated once per recipient, and a correction would have to touch every copy. The event/recipient split keeps each fact once, and the recipient row only the one thing that differs per user |
| A polymorphic subject (`subject_type`, `subject_id`) | It cannot be a foreign key, so a deleted load or run would leave an orphan, and nothing would stop an unknown type. The exclusive arc costs three nullable columns and gets the foreign keys and the `UNIQUE` guards |
| Storing the message text | A message stored at the time says what was true then. Built from the subject row it follows the data, and the table holds no free text to leak or to localize |

## Consequences

**What this makes easy.**
- Notifications need no new process, no new port and no new credential. They use the connection the page already has.
- The three synchronous events can never disagree with their operation: both commit or neither does.
- The announcer is safe to run as often as it likes, from either worker, and it stays out of the way of `HEAD` and `POST`.
- A change to a message's wording, or a corrected load count, shows immediately, because nothing was copied.

**What this makes hard.**
- **An experiment's end is announced late, or not at all.** It appears when the next signed-in `GET` arrives. If nobody
  opens a page within 14 days of the end date, it is never announced. That bound is the price of not having a scheduler.
- **A `GET` writes.** It is idempotent and invisible to the reader, but it is a write on a safe method, and every
  signed-in `GET` pays one extra statement and a commit. A cache in front of the application would defeat it; none
  exists, and the application sends `Cache-Control: no-store` on every page that is not static.
- **A failed notification blocks the operation.** A segmentation run that cannot record its notification is rolled back,
  which is what ADR-0014 means by a service owning its transaction, and it is also stricter than a log line would be.
  For a sales load the rows were already committed one at a time; only the load record and its notification are lost
  together.
- **The delivery permission is read from code.** Which roles hold it comes from the matrix in
  `web/middleware/authz.py`, so adding a permission to a role does not back-fill notifications it missed.
- **Two tables, where the issue and ADR-0046 expected "a notification table".** The split is the 4NF argument in
  `docs/data-model.md` §2.4, and ADR-0046's `public` allowance covers both.

**What must now be true elsewhere.**
- `sql/01_schema.sql` creates `notification` and `notification_recipient`; `sql/02_seed_30_per_table.sql` seeds both
  with at least 30 rows; neither is named in `sql/seed-exempt.txt`.
- `docs/data-model.md` records the entities, the relationships, the dictionary, the indexes and the 4NF decision, and the
  table count becomes 29 everywhere `docs/check.py` reads it.
- `contracts/db/check_grants.sh` lists both tables among those no service may reach.
- `docs/business-rules.md` carries RN-61 to RN-64, and `docs/analytics-permission-map.md` carries the notification routes.
- The instance database is rebuilt from the three scripts before the release that reads these tables.

## Compliance

- `tests/test_notification_schema.py` (PostgreSQL): the arc CHECK refuses zero and two subjects, each of the three
  `UNIQUE` subjects refuses a second event, a user may be the subject of more than one, `kind` follows the subject, and
  `retail_app` holds default DML on both tables and nothing more.
- `tests/test_notification_announcer.py`: a signed-in `GET` runs the announcer; `HEAD`, `POST` and an anonymous request do
  not; a failure is logged and the page is served.
- `tests/test_notification_announcer_db.py` (PostgreSQL): two connections announcing at once leave one event and one set
  of recipients; an experiment ending today, one ended 15 days ago, and one with no end date are not announced.
- `tests/test_notification_service.py` and `tests/test_notification_routes.py`: recipients are the active holders of the
  permission, the actor included; another user's id answers 404; the count is capped at `99+`; a count that cannot be read
  hides the badge.
- `contracts/db/check_grants.sh` and `.github/workflows/ci.yml` (`database` job): no `svc_*` role reaches either table,
  and both have at least 30 rows.
- From the repository root, this prints nothing, so notifications never touch Redis or a service:

  ```bash
  grep -nE '^\s*(import|from)\s+(redis|requests|httpx|urllib)' \
    web/services/notifications.py web/db/notifications.py web/routes/notifications.py
  ```
