# F4-01 — Authorization enforced at the route level

Evidence that the permission matrix in [`requirements.md`](../requirements.md)
§3 is a control and not a promise: the same URL, requested by three roles,
answers differently, and the refusal happens before the view runs.

Covers RF-03, RF-04, HU-03 and HU-04. The single-administrator rule is F4-02
(#70) and is not evidenced here.

## How this run was produced

The database is the seeded one from the Compose stack; the application is the
one in this branch, run against it.

```bash
docker compose up -d db     # the three ordered scripts, via docker/initdb.sh
DATABASE_URL=postgresql://retail_app:retail_app@127.0.0.1:5432/retail \
  FLASK_ENV=development python -m web.app
```

Four routes stood in for pages later stories build — `catalog.index` (F3-04,
#64), `users.index` (F3-06, #66), `audit.index` (F3-11, #103) and
`segment_run.index` (F3-10, #102) — each carrying nothing but the
`@requires(...)` its story will carry. The middleware, the matrix and the
templates are the shipped ones; only those four view functions were
scaffolding, and they are not in the repository.

Accounts are the seeded demonstration ones, all with the published password:
`admin@mosaiq-demo.com` (ADMIN), `user2@…` (ANALYST), `user6@…` (AUDITOR).

## RF-04 — the same URL, three roles

| Route | Permission it declares | ADMIN | ANALYST | AUDITOR |
|---|---|---|---|---|
| `/catalogs` | `catalog.read` | 200 | 200 | 200 |
| `/users` | `user.read` | 200 | **403** | 200 |
| `/audit` | `audit.read` | 200 | **403** | 200 |
| `/segment-run` | `segment_run.execute` | 200 | **403** | **403** |

The auditor reading the audit log and being refused the segment run is the
matrix's own emphasis, reproduced: only `ADMIN` runs a recalculation that
rewrites a column on every customer.

## RF-03 / HU-03 — refused, then returned where they were going

```
GET /audit                    -> 302  Location: /login?next=/audit
GET /login?next=/audit        -> <input type="hidden" name="next" value="/audit">
POST /login (correct password) -> 302  Location: /audit
GET /audit                     -> 200  <h1>Audit log</h1>
```

An off-site `next` never survives: `safe_next` refuses a scheme, a host, a
protocol-relative `//host`, a backslash and an embedded newline, so the
sign-in page cannot be turned into an open redirect
(`tests/test_authz.py::test_safe_next_refuses_anything_that_could_leave_the_site`).

An anonymous **POST** is refused with 403 rather than redirected: its body
cannot survive the round trip through a GET sign-in page, and silently dropping
it would be worse than refusing it.

## HU-04 — what the refusal looks like

The visitor gets the controlled error page, with the same reference the log
line carries and no stack trace:

```html
<h1>403</h1>
<p class="lede">Forbidden</p>
This page is not part of what your role may reach. Nothing went wrong;
<dt>Reference</dt>
<dd><code>8f01c3a5</code></dd>
```

The log gets one line naming who, where and how:

```
WARNING web.app: Access denied: missing audit.read.
  user=11111111-1111-1111-1111-000000000002 role=ANALYST
  endpoint=audit.index path=/audit method=GET
WARNING web.app: Access denied: missing segment_run.execute.
  user=11111111-1111-1111-1111-000000000006 role=AUDITOR
  endpoint=segment_run.index path=/segment-run method=GET
```

## The menu is driven by the permission set

Rendered from the same permission sets, with no role named in any template:

| Signed in as | Menu |
|---|---|
| ADMIN | Home · Catalogs · Users · Segment run · Audit log |
| AUDITOR | Home · Catalogs · Users · Audit log |
| ANALYST | Home · Catalogs |
| nobody | Home |

An entry whose story has not landed is skipped rather than rendered as a broken
link, so on `develop` today the menu is `Home` alone for every role.

## The default-deny half

A route that declares nothing is refused, which is what stops the next story
from shipping an unprotected page by accident:

```
$ pytest tests/test_authz.py -q
30 passed

$ pytest -q
62 passed
```

`test_every_registered_endpoint_declares_what_it_requires` walks
`app.view_functions` and fails the build when any endpoint carries neither
`@public` nor `@requires(...)`. It is the executable form of the acceptance
criterion, and the reason the matrix cannot quietly stop being enforced.

## The same menu at 375 px and 1440 px

Definition of Done item 10, captured headlessly with Chromium against the
running application.

| | |
|---|---|
| Administrator, 1440 px | [`f4-01-menu-admin-1440.png`](f4-01-menu-admin-1440.png) |
| Administrator, 375 px | [`f4-01-menu-admin-375.png`](f4-01-menu-admin-375.png) |
| Analyst, 1440 px | [`f4-01-menu-analyst-1440.png`](f4-01-menu-analyst-1440.png) |

The masthead is a wrapping flexbox with no fixed widths and no media query. At
375 px the five administrator entries wrap onto a second line and the name and
sign-out onto a third; nothing overflows horizontally and nothing is clipped.
At 1440 px the analyst's two entries and their session block share one line.

The administrator's session block wraps at 1440 px even though the window is
wide, because `.masthead` inherits the skeleton's `max-width: 44rem` text
column from `web/static/css/app.css`. That column predates this story and is
the placeholder chrome F3-08 (#68) replaces when the design system is applied;
it is readable and usable at both widths, which is what RNF-11 asks for, and
widening it here would be a design change this story has no mandate for.

## Not evidenced here

The single-administrator rule (RF-05, F4-02 #70) and the row scoping behind the
matrix's `own` and `own store` qualifiers (F3-05 #65, F3-11 #103). Both are
named in [ADR-0007](../adr/0007-permissions-in-code-with-a-default-deny-middleware.md)
as work this story deliberately leaves to the stories that own it.
