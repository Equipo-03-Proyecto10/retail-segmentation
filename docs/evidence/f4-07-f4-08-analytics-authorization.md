# F4-07 and F4-08 — Analytics permissions mapped, and every analytics route verified

Evidence for F4-07 (#202) and F4-08 (#231), closed with no file here. F4-07 maps
every Delivery 2 analytics surface to a permission the existing role profiles
already hold, without inventing a parallel scheme. F4-08 proves the map holds:
every analytics route declares a permission, each profile reaches exactly the routes
it was assigned and no others, an anonymous request is refused, and the single
administrator is still refused twice. It also gives the two unreferenced captures
`f4-role-code-immutable-*.png` their home: they belong to the single-administrator
rule, through #252 (renaming a role code once granted administrator rights).

No schema change was made and no application code was changed.

## What was run, and against what

Branch `docs/443-analytics-evidence`, from `develop` at `f2b9c81`; the stack from
`docker compose -p ev443 -f compose.yaml -f compose.proxy.yaml up --build -d` from
an empty volume (PostgreSQL 16.14, NGINX 1.27.5 with the production CSP, Chromium 153
through Playwright). All seven demonstration profiles were signed in through the real
sign-in form by a small script, one request client per profile plus one with no
session. NGINX throttles `POST /login` to six a minute, so the script paces its
sign-ins about ten seconds apart. The routes come from the application's own
test table (`ANALYTICS_ROUTES` in `tests/test_analytics_authz.py`), so the live run
and the test suite check the same 45 routes.

Captures are full-page at 375 px and 1440 px with the classic scrollbar hidden, so the
layout is exactly 375 and 1440 CSS pixels wide.

## F4-07 — the map (#202)

| Acceptance criterion | Proof |
|---|---|
| Every analytics surface names an existing permission or a new one with the profiles that hold it | [`../analytics-permission-map.md`](../analytics-permission-map.md), quoted below, and the live matrix under F4-08, which reads the declaration off every route |
| A new permission is in `ALL_PERMISSIONS` and in each profile that needs it, and no profile gains one without need | The permission table below, read from `web/middleware/authz.py`: the one new permission, `sales_ingest.execute`, is held by `ADMIN` alone |
| `ADMIN` remains the only administrative profile and no new profile is created | Seven profiles in code, seven roles in the database, one administrator account |
| The map is reflected in the permission matrix in `docs/requirements.md` §3 | A script compares the matrix cell by cell with `PERMISSIONS`: 56 cells, 0 differences |

The map as committed:

| Phase | Surface | Permission | Profiles |
|---|---|---|---|
| 7 | Segment assignment history / migration (read) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 7 | Customer segment timeline, as-of lookup and the sales behind a change (`/catalog/customers/<id>`, `/catalog/customers/<id>/segment-changes/<run>`, #337) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 7 | Run RFM_RULES / KMEANS | `segment_run.execute` (existing) | ADMIN |
| 8 | Sales CSV ingestion | `sales_ingest.execute` (/admin/sales-import/) | ADMIN |
| 8 | Consumption profile (read) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 9 | Model comparison, parameters and quality measures (read) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 10 | Recommendations (read) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 11 | Campaigns and experiments (assignment, exposure, conversion) | `campaign.read` / `campaign.write` (existing) | same as campaigns today |
| 11 | Experiment arm detail and bulk exposure (`/experiments/<id>/groups/<group_id>`) | `campaign.read` / `campaign.write` | same as campaigns today |
| 12 | Segment / RFM / migration dashboards | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 12 | Revenue summary by channel and store (`/reports/`) | `report.read` (existing) | everyone who already holds `report.read` |
| 12 | Revenue by stable segment label inside the segmentation dashboard | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 12 | Experiment dashboard (`/experiment-dashboard/`, #415) | `campaign.read` (existing) | same as campaigns today |
| 12 | Experiment report and its CSV export (`/experiment-report/`) | `campaign.read` (existing) | same as campaigns today |
| 12 | Consumption-shift and recommendation reports (`/consumption-reports/`) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |

`ADMIN` remains the only administrative profile. No new profile is created.

The permissions each profile holds, read from the code (`yes` is held):

```text
$ python f4_permissions.py
14 permissions in ALL_PERMISSIONS, 7 profiles in PERMISSIONS

permission                 ADMIN   ANALYST STORE_MAN MARKETING INVENTORY   AUDITOR  CUSTOMER
audit.read                   yes         .         .         .         .       yes         .
campaign.read                yes       yes         .       yes         .       yes         .
campaign.write               yes         .         .       yes         .         .         .
catalog.read                 yes       yes       yes       yes       yes       yes         .
catalog.write                yes         .         .         .         .         .         .
inventory.write              yes         .         .         .       yes         .         .
report.read                  yes       yes       yes       yes       yes       yes         .
sales_ingest.execute         yes         .         .         .         .         .         .
segment.read                 yes       yes       yes       yes         .       yes         .
segment.write                yes         .         .         .         .         .         .
segment_run.execute          yes         .         .         .         .         .         .
user.read                    yes         .         .         .         .       yes         .
user.self                      .         .         .         .         .         .       yes
user.write                   yes         .         .         .         .         .         .
```

The roles in the database, and who holds the administrator seat:

```text
$ psql -d retail -c "select r.role_id, r.code, (select count(*) from app_user u where u.role_id = r.role_id) as users from role r order by 1"
 role_id |       code        | users 
---------+-------------------+-------
       1 | ADMIN             |     1
       2 | ANALYST           |     5
       3 | STORE_MANAGER     |     5
       4 | MARKETING         |     5
       5 | INVENTORY_PLANNER |     5
       6 | AUDITOR           |     5
       7 | CUSTOMER          |     4
(7 rows)
```

And the matrix in `docs/requirements.md` §3 against `PERMISSIONS`, each column
translated to its permissions (`Catalogs` to `catalog.read` and `catalog.write`, and so
on):

```text
$ python f4_doc_vs_code.py
7 profiles x 8 columns = 56 cells compared
differences: 0
profiles in code: ['ADMIN', 'ANALYST', 'AUDITOR', 'CUSTOMER', 'INVENTORY_PLANNER', 'MARKETING', 'STORE_MANAGER'] | profiles in doc: ['ADMIN', 'ANALYST', 'AUDITOR', 'CUSTOMER', 'INVENTORY_PLANNER', 'MARKETING', 'STORE_MANAGER']
permissions in ALL_PERMISSIONS not held by any profile: []
ADMIN is the only profile holding: ['catalog.write', 'sales_ingest.execute', 'segment.write', 'segment_run.execute', 'user.write']
```

`sales_ingest.execute`, `segment_run.execute`, `segment.write`, `catalog.write` and
`user.write` are held by `ADMIN` alone. Three declared permissions have no route
(`inventory.write`, `segment.write`, `user.self`); the map's second table says why,
and they grant nothing until a route names them.

## F4-08 — the verification (#231)

| Acceptance criterion | Proof |
|---|---|
| Every route added in phases 7 to 12 declares a permission, and an undeclared route fails the test | Mutation A below: remove one declaration and the suite fails |
| Each non-administrative profile is permitted exactly the routes F4-07 assigned it and refused all others | The live matrix below, 340 cells with 0 mismatches, and mutation B |
| An anonymous request to any analytics route is refused | The `ANONYMOUS` column: 302 to sign-in for every `GET`, 403 for every `POST` |
| A second `ADMIN` is refused by the application and by the partial unique index, both halves exercised | Live application refusal, direct SQL as owner and as `retail_app`, and the test, below |
| The suite passes in CI alongside `tests/test_authz.py` | `tests/test_authz.py` and `tests/test_analytics_authz.py` pass together, below |

### Every route, every profile

For each of the 45 routes, each profile requested it (a `GET`), or for a route that
writes, was sent a `POST` carrying a valid session and CSRF token (the anonymous
`POST` carries none). A profile that holds the route's permission must be served
(anything but 302 or 403: `200`, or `404` or `409` where the id or the experiment's
state makes the view answer for itself); one that does not must be refused (403). A
permitted `POST` is shown as `-`, because it would write; the mocked-connection suite
covers those.

```text
$ python f4_matrix.py
route                                       perm                      ADMIN  ANALYST STORE_MA MARKETIN INVENTOR  AUDITOR CUSTOMER ANONYMOU
GET /catalog/segments                       segment.read                200      200      200      200      403      200      403      302
GET /catalog/segments/1                     segment.read                200      200      200      200      403      200      403      302
GET /catalog/customers                      segment.read                200      200      200      200      403      200      403      302
GET /catalog/customers/<customer>           segment.read                200      200      200      200      403      200      403      302
GET /catalog/customers/<customer>/profile   segment.read                200      200      200      200      403      200      403      302
GET /catalog/customers/<customer>/recommend segment.read                200      200      200      200      403      200      403      302
GET /catalog/customers/<customer>/segment-c segment.read                200      200      200      200      403      200      403      302
GET /run-history/                           segment.read                200      200      200      200      403      200      403      302
GET /run-history/1                          segment.read                200      200      200      200      403      200      403      302
GET /migration-matrix/                      segment.read                200      200      200      200      403      200      403      302
GET /migration-explanation/                 segment.read                404      404      404      404      403      404      403      302
GET /model-comparison/                      segment.read                200      200      200      200      403      200      403      302
GET /segment-run/                           segment_run.execute         200      403      403      403      403      403      403      302
POST /segment-run/                          segment_run.execute           -      403      403      403      403      403      403      403
GET /campaigns/                             campaign.read               200      200      403      200      403      200      403      302
GET /campaigns/new                          campaign.write              200      403      403      200      403      403      403      302
POST /campaigns/new                         campaign.write                -      403      403        -      403      403      403      403
GET /campaigns/1/edit                       campaign.write              200      403      403      200      403      403      403      302
POST /campaigns/1/edit                      campaign.write                -      403      403        -      403      403      403      403
POST /campaigns/1/activate                  campaign.write                -      403      403        -      403      403      403      403
GET /experiments/                           campaign.read               200      200      403      200      403      200      403      302
GET /experiments/new                        campaign.write              200      403      403      200      403      403      403      302
POST /experiments/new                       campaign.write                -      403      403        -      403      403      403      403
GET /experiments/1/edit                     campaign.write              200      403      403      200      403      403      403      302
POST /experiments/1/edit                    campaign.write                -      403      403        -      403      403      403      403
GET /experiments/1/assign                   campaign.write              409      403      403      409      403      403      403      302
POST /experiments/1/assign                  campaign.write                -      403      403        -      403      403      403      403
GET /experiments/1/exposure                 campaign.read               200      200      403      200      403      200      403      302
GET /experiments/1/groups/1                 campaign.read               200      200      403      200      403      200      403      302
POST /experiments/1/groups/1                campaign.write                -      403      403        -      403      403      403      403
POST /experiments/1/exposure                campaign.write                -      403      403        -      403      403      403      403
GET /experiments/1/conversion               campaign.read               200      200      403      200      403      200      403      302
POST /experiments/1/conversion              campaign.write                -      403      403        -      403      403      403      403
GET /experiments/1/uplift                   campaign.read               200      200      403      200      403      200      403      302
GET /segmentation-dashboard/                segment.read                200      200      200      200      403      200      403      302
GET /segment-history-report/                segment.read                200      200      200      200      403      200      403      302
GET /consumption-reports/                   segment.read                200      200      200      200      403      200      403      302
GET /experiment-dashboard/                  campaign.read               200      200      403      200      403      200      403      302
GET /experiment-report/                     campaign.read               200      200      403      200      403      200      403      302
GET /experiment-report/export.csv           campaign.read               200      200      403      200      403      200      403      302
GET /reports/                               report.read                 200      200      200      200      200      200      403      302
GET /admin/sales-import/                    sales_ingest.execute        200      403      403      403      403      403      403      302
POST /admin/sales-import/                   sales_ingest.execute          -      403      403      403      403      403      403      403
GET /admin/sales-import/1                   sales_ingest.execute        200      403      403      403      403      403      403      302
GET /admin/sales-import/1/rejections.csv    sales_ingest.execute        200      403      403      403      403      403      403      302

routes: 45  cells compared: 340  cells skipped (permitted POST): 20
mismatches against the declared permission: 0
```

340 cells were compared and none disagreed with the permission the route
declares. Two readings. `GET /migration-explanation/` answers `404` to the profiles
that hold `segment.read` because the request carries no run or customer; it reached the
view, which is the point, and the profiles without the permission got 403 before it ran.
`GET /experiments/1/assign` answers `409` to `ADMIN` and `MARKETING` because experiment
1 has already been assigned; again the view answered. `CUSTOMER` holds `user.self` only
and reached none of the 45.

### An undeclared or widened route fails the build

The checks were run on a **copy** of the working tree (`rsync` to a scratch directory,
removed afterwards); the worktree was not edited. Mutation A deletes the
`@requires(SEGMENT_READ)` of the run-history list; mutation B gates the campaign form on
`campaign.read` instead of `campaign.write`:

```text
$ ./f4_mutation.sh
### baseline, unmodified copy
418 passed in 8.86s

### mutation A: the run-history list loses its @requires declaration
26d25
< @requires(SEGMENT_READ)
5 failed, 413 passed in 9.16s
Endpoint run_history.index declares no authorization requirement and was refused. Decorate the view with @public or @requires(...) in web/middleware/authz.py terms.
FAILED tests/test_analytics_authz.py::test_a_profile_is_permitted_exactly_the_routes_the_map_assigns_it[GET /run-history/-CUSTOMER]
FAILED tests/test_analytics_authz.py::test_a_profile_is_permitted_exactly_the_routes_the_map_assigns_it[GET /run-history/-INVENTORY_PLANNER]
FAILED tests/test_analytics_authz.py::test_a_route_is_declared_with_exactly_the_mapped_permission[GET /run-history/]
FAILED tests/test_analytics_authz.py::test_an_anonymous_request_is_refused_before_the_view_runs[GET /run-history/]
FAILED tests/test_analytics_authz.py::test_every_analytics_route_declares_a_permission
reference=5cd11ee7 actor='-' client='127.0.0.1': Endpoint run_history.index declares no authorization requirement and was refused. Decorate the view with @public or @requires(...) in web/middleware/authz.py terms.
reference=a3696290 actor='11111111-1111-1111-1111-000000000007' client='127.0.0.1': Endpoint run_history.index declares no authorization requirement and was refused. Decorate the view with @public or @requires(...) in web/middleware/authz.py terms.
reference=c124e79b actor='11111111-1111-1111-1111-000000000007' client='127.0.0.1': Endpoint run_history.index declares no authorization requirement and was refused. Decorate the view with @public or @requires(...) in web/middleware/authz.py terms.

### mutation B: GET and POST /campaigns/new gated on campaign.read instead of campaign.write
94c94
< @requires(CAMPAIGN_WRITE)
---
> @requires(CAMPAIGN_READ)
FAILED tests/test_analytics_authz.py::test_a_route_is_declared_with_exactly_the_mapped_permission[GET /campaigns/new]
FAILED tests/test_analytics_authz.py::test_a_route_is_declared_with_exactly_the_mapped_permission[POST /campaigns/new]
FAILED tests/test_analytics_authz.py::test_a_profile_is_permitted_exactly_the_routes_the_map_assigns_it[GET /campaigns/new-ANALYST]
FAILED tests/test_analytics_authz.py::test_a_profile_is_permitted_exactly_the_routes_the_map_assigns_it[GET /campaigns/new-AUDITOR]
FAILED tests/test_analytics_authz.py::test_a_profile_is_permitted_exactly_the_routes_the_map_assigns_it[POST /campaigns/new-ANALYST]
FAILED tests/test_analytics_authz.py::test_a_profile_is_permitted_exactly_the_routes_the_map_assigns_it[POST /campaigns/new-AUDITOR]
6 failed, 412 passed in 8.86s
```

In mutation A the route answers 403 to everyone (the log line says the endpoint
`declares no authorization requirement and was refused`, the default deny of ADR-0007),
`test_every_analytics_route_declares_a_permission` fails with the endpoint named, and
four more tests fail. In mutation B the declared permission no longer matches the map
and the two read-only profiles that hold `campaign.read` (`ANALYST`, `AUDITOR`) become
able to open the form, which the permitted-exactly test catches. Unmodified, the file
passes 418 tests.

### The single administrator, refused twice

| Half | Observed |
|---|---|
| The application | `POST /admin/users/new` with `role_code=ADMIN`: **409**, `This system has exactly one administrator, and the role is already taken. Move the current administrator to another role first, or choose a different role for this user.` |
| The partial unique index | A direct `INSERT` of a second administrator, and a direct `UPDATE` promoting an existing user: both refused with `duplicate key value violates unique constraint "ux_app_user_single_administrator"`, as the schema owner and as `retail_app` |
| The role code | A forged `POST /admin/roles/2/edit` carrying `code=ADMIN` is refused, 400, `A role's code cannot change: the permission matrix is keyed by it.`, and a direct `UPDATE role SET code = …` is refused by `fn_role_code_immutable` (`role 1 keeps its code ADMIN`, SQLSTATE 23514), as owner and as `retail_app` |

```text
$ psql -U postgres -d retail -v VERBOSITY=verbose -f f4_db.sql      (every statement inside BEGIN ... ROLLBACK)
-- second administrator by a direct INSERT
ERROR:  23505: duplicate key value violates unique constraint "ux_app_user_single_administrator"
DETAIL:  Key (role_id)=(1) already exists.
-- promoting an existing user by a direct UPDATE
ERROR:  23505: duplicate key value violates unique constraint "ux_app_user_single_administrator"
DETAIL:  Key (role_id)=(1) already exists.
-- renaming a role code by a direct UPDATE (#252)
ERROR:  23514: role 1 keeps its code ADMIN
CONTEXT:  PL/pgSQL function fn_role_code_immutable() line 4 at RAISE
-- the same code change aimed at another role
ERROR:  23514: role 2 keeps its code ANALYST
CONTEXT:  PL/pgSQL function fn_role_code_immutable() line 4 at RAISE

$ psql -h 127.0.0.1 -U retail_app -d retail -f f4_db.sql
-- second administrator by a direct INSERT
ERROR:  duplicate key value violates unique constraint "ux_app_user_single_administrator"
DETAIL:  Key (role_id)=(1) already exists.
-- promoting an existing user by a direct UPDATE
ERROR:  duplicate key value violates unique constraint "ux_app_user_single_administrator"
DETAIL:  Key (role_id)=(1) already exists.
-- renaming a role code by a direct UPDATE (#252)
ERROR:  role 1 keeps its code ADMIN
CONTEXT:  PL/pgSQL function fn_role_code_immutable() line 4 at RAISE
-- the same code change aimed at another role
ERROR:  role 2 keeps its code ANALYST
CONTEXT:  PL/pgSQL function fn_role_code_immutable() line 4 at RAISE

$ psql -U postgres -d retail      (after the application and SQL attempts)
 administrators 
----------------
              1
(1 row)
 users_named_second_admin 
--------------------------
                        0
(1 row)
 role_id |  code   
---------+---------
       1 | ADMIN
       2 | ANALYST
(2 rows)
```

(`BEGIN`, `ROLLBACK`, blank lines, and for the owner run the `LOCATION`, schema, table and
constraint-name detail lines were removed; nothing else.)

| Refusal | 1440 px | 375 px |
|---|---|---|
| A second administrator through the user form: 409 with the rule explained | [capture](f4-08-second-admin-refused-1440.png) | [capture](f4-08-second-admin-refused-375.png) |
| The role's code is read-only on the edit form: `Fixed once created: permissions are keyed by the code.` (no marker, and no field name, so it is not even posted) | [capture](f4-role-code-immutable-1440.png) | [capture](f4-role-code-immutable-375.png) |
| A forged post that carries a code: 400, `A role's code cannot change: the permission matrix is keyed by it.` | [capture](f4-08-role-code-change-refused-1440.png) | [capture](f4-08-role-code-change-refused-375.png) |

The two `f4-role-code-immutable-*` captures are the ones the issue found
unreferenced. They were retaken: the originals predated the current navigation and the
375 px one was 360 px wide. They show the read-only code field of the `ANALYST` role
as it is today.

### The suite

```text
$ pytest tests/test_authz.py tests/test_analytics_authz.py tests/test_single_administrator.py tests/test_negative_flows.py
950 passed in 19.61s
```

418 of those are `tests/test_analytics_authz.py` (one case per route and profile) and 38
are `tests/test_authz.py`. The criterion about the single administrator is the test
`test_a_second_administrator_is_refused_by_the_application_and_by_the_index`; the
suite's other checks of the rule are in `tests/test_single_administrator.py` (20 tests),
including `test_the_schema_still_carries_the_partial_unique_index`. The whole suite,
`pytest -q`, was `3040 passed, 14 skipped in 51.95s`; the 14 skips are the
`postgres`-marked tests and one that needs `yaml`.

## What was not run

- A permitted `POST` was not sent live: it would write (assign an experiment, record an
  exposure, run a segmentation). Those cells are `-` and are covered by the mocked
  suite only.
- CI itself was not run. `tests/test_authz.py` and `tests/test_analytics_authz.py` ran
  together locally, and the `postgres`-marked tests that CI runs under
  `MOSAIQ_REQUIRE_POSTGRES` were skipped.
- Only the 45 routes in the test table were checked live. The completeness test
  (`test_no_route_reaching_analytics_is_missing_from_the_table`) is what keeps that
  table in step with the application; it passed.
- Nothing was run on the Compute Engine instance.

## The scripts

<details><summary><code>f4_matrix.py</code> (needs the sign-in helper <code>mq.py</code>, listed in <a href="f7-04-f7-05-migration-and-matrix.md">F7-04 and F7-05</a>)</summary>

```python
"""F4-08 live matrix: every analytics route in tests/test_analytics_authz.py, requested as every profile."""
import sys
sys.path.insert(0, "<repo>")
sys.path.insert(0, "<scripts>")
from tests.test_analytics_authz import ANALYTICS_ROUTES, USER_ID
from web.middleware.authz import PERMISSIONS
from mq import Client

CUSTOMER = "00000000-0000-0000-0000-000000000001"
PROFILES = {
    "ADMIN": "admin@mosaiq-demo.com", "ANALYST": "user14@mosaiq-demo.com", "STORE_MANAGER": "user15@mosaiq-demo.com",
    "MARKETING": "user10@mosaiq-demo.com", "INVENTORY_PLANNER": "user11@mosaiq-demo.com",
    "AUDITOR": "user12@mosaiq-demo.com", "CUSTOMER": "user13@mosaiq-demo.com",
}
routes = [(e, m, p.replace(USER_ID, CUSTOMER), perm) for e, m, p, perm in ANALYTICS_ROUTES]
clients = {}
for role, email in PROFILES.items():
    c = Client()
    status, loc = c.login(email)
    assert status == 302, (role, status)
    clients[role] = c
clients["ANONYMOUS"] = Client()

mismatches = 0
cells = {}
for endpoint, method, path, perm in routes:
    for role, c in clients.items():
        holds = role != "ANONYMOUS" and perm in PERMISSIONS[role]
        if method == "GET":
            code = c.get(path)[0]
            served = code not in (302, 403)
        else:
            if holds:
                cells[(endpoint, method, role)] = "-"
                continue  # a permitted POST writes; the mocked-connection suite covers it
            if role == "ANONYMOUS":
                code = c.request("POST", path, {})[0]
            else:
                code = c.post(path, {}, token_from="/")[0]
            served = code not in (302, 403)
        expected_served = holds
        ok = served == expected_served
        if not ok:
            mismatches += 1
        cells[(endpoint, method, role)] = f"{code}" + ("" if ok else "!!")

roles = list(clients)
print(f"{'route':44}{'perm':22}" + "".join(f"{r[:8]:>9}" for r in roles))
for endpoint, method, path, perm in routes:
    print(f"{(method + ' ' + path.replace(CUSTOMER, '<customer>'))[:43]:44}{perm:22}" + "".join(f"{cells[(endpoint, method, r)]:>9}" for r in roles))
print()
print("routes:", len(routes), " cells compared:", sum(1 for v in cells.values() if v != '-'), " cells skipped (permitted POST):", sum(1 for v in cells.values() if v == '-'))
print("mismatches against the declared permission:", mismatches)
```

</details>

<details><summary><code>f4_permissions.py</code> and <code>f4_doc_vs_code.py</code></summary>

```python
import sys
sys.path.insert(0, "<repo>")
from web.middleware.authz import PERMISSIONS, ALL_PERMISSIONS
perms = sorted(ALL_PERMISSIONS)
print(f"{len(perms)} permissions in ALL_PERMISSIONS, {len(PERMISSIONS)} profiles in PERMISSIONS\n")
print(f"{'permission':22}" + "".join(f"{r[:9]:>10}" for r in PERMISSIONS))
for p in perms:
    print(f"{p:22}" + "".join(f"{('yes' if p in PERMISSIONS[r] else '.'):>10}" for r in PERMISSIONS))
```

```python
"""F4-07: docs/requirements.md §3 against PERMISSIONS in web/middleware/authz.py, cell by cell."""
import re, sys
ROOT = "<repo>"
sys.path.insert(0, ROOT)
from web.middleware.authz import PERMISSIONS, ALL_PERMISSIONS

lines = open(ROOT + "/docs/requirements.md", encoding="utf8").read().splitlines()
start = next(i for i, l in enumerate(lines) if l.startswith("| Role | Catalogs"))
header = [c.strip() for c in lines[start].strip("|").split("|")]
doc = {}
for l in lines[start + 2:]:
    if not l.startswith("|"):
        break
    cells = [c.strip().strip("`") for c in l.strip("|").split("|")]
    doc[cells[0].strip("`")] = dict(zip(header[1:], cells[1:]))

# column -> (read permission, write permission, execute permission, own permission)
COLUMNS = {
    "Catalogs": ("catalog.read", "catalog.write", None, None),
    "Users": ("user.read", "user.write", None, "user.self"),
    "Segments and rules": ("segment.read", "segment.write", None, None),
    "Campaigns": ("campaign.read", "campaign.write", None, None),
    "Segment run": (None, None, "segment_run.execute", None),
    "Sales ingestion": (None, None, "sales_ingest.execute", None),
    "Reports": ("report.read", None, None, None),
    "Audit log": ("audit.read", None, None, None),
}
def level(role, col):
    r, w, x, own = COLUMNS[col]
    p = PERMISSIONS[role]
    if x:
        return "run/ingest" if x in p else "—"
    if w and r in p and w in p:
        return "full"
    if r and r in p:
        return "read"
    if own and own in p:
        return "own"
    return "—"
def doc_level(text):
    t = text.lower()
    if t in ("run", "ingest"): return "run/ingest"
    if t.startswith("full"): return "full"
    if t.startswith("read"): return "read"
    if t.startswith("own"): return "own"
    return "—"
diffs = []
n = 0
for role, row in doc.items():
    for col in COLUMNS:
        n += 1
        got, want = level(role, col), doc_level(row[col])
        # the doc's INVENTORY_PLANNER catalogs cell says "read, `inventory` write": a dormant permission, not a catalog one
        if got != want:
            diffs.append((role, col, "code=" + got, "doc=" + row[col]))
print(f"{len(doc)} profiles x {len(COLUMNS)} columns = {n} cells compared")
for d in diffs: print("DIFFERENCE:", *d)
print("differences:", len(diffs))
print("profiles in code:", sorted(PERMISSIONS), "| profiles in doc:", sorted(doc))
print("permissions in ALL_PERMISSIONS not held by any profile:", sorted(ALL_PERMISSIONS - set().union(*PERMISSIONS.values())))
print("ADMIN is the only profile holding:", sorted(p for p in ALL_PERMISSIONS if [r for r in PERMISSIONS if p in PERMISSIONS[r]] == ["ADMIN"]))
```

</details>

<details><summary><code>f4_mutation.sh</code> and <code>f4_db.sql</code></summary>

```sh
#!/bin/sh
# F4-08: break the authorization in a COPY of the working tree and show the suite failing. The worktree itself is never edited.
W=<repo>
X=<scratch>/mut
PY=python
rm -rf $X && mkdir $X && rsync -a --exclude .git --exclude docs/evidence --exclude .env --exclude __pycache__ $W/ $X/ && cd $X
echo '### baseline, unmodified copy'
$PY -m pytest -p no:cacheprovider -q tests/test_analytics_authz.py 2>&1 | tail -1
echo
echo '### mutation A: the run-history list loses its @requires declaration'
python3 - <<'PYEOF'
p = 'web/routes/run_history.py'
s = open(p).read()
i = s.index('@bp.get("/")')
j = s.index('@requires(SEGMENT_READ)', i)
open(p, 'w').write(s[:j] + s[j + len('@requires(SEGMENT_READ)\n'):])
PYEOF
diff $W/web/routes/run_history.py web/routes/run_history.py
$PY -m pytest -p no:cacheprovider -q tests/test_analytics_authz.py 2>&1 | grep -E "^(FAILED|[0-9]+ (passed|failed))|declares no authorization requirement" | sed -E 's/^.*ERROR +web.app[^ ]* +//' | sort -u
cp $W/web/routes/run_history.py web/routes/run_history.py
echo
echo '### mutation B: GET and POST /campaigns/new gated on campaign.read instead of campaign.write'
python3 - <<'PYEOF'
p = 'web/routes/campaigns.py'
s = open(p).read()
old = '@bp.route("/new", methods=["GET", "POST"])\n@requires(CAMPAIGN_WRITE)'
assert old in s
open(p, 'w').write(s.replace(old, '@bp.route("/new", methods=["GET", "POST"])\n@requires(CAMPAIGN_READ)'))
PYEOF
diff $W/web/routes/campaigns.py web/routes/campaigns.py
$PY -m pytest -p no:cacheprovider -q tests/test_analytics_authz.py 2>&1 | grep -E "^(FAILED|[0-9]+ (passed|failed))"
cd / && rm -rf $X
```

```sql
\set ON_ERROR_STOP off
\echo '-- second administrator by a direct INSERT'
BEGIN;
INSERT INTO app_user (role_id, name, email, password_hash) VALUES (1, 'Second Admin', 'second.admin@example.com', 'x');
ROLLBACK;
\echo '-- promoting an existing user by a direct UPDATE'
BEGIN;
UPDATE app_user SET role_id = 1 WHERE email = 'user14@mosaiq-demo.com';
ROLLBACK;
\echo '-- renaming a role code by a direct UPDATE (#252)'
BEGIN;
UPDATE role SET code = 'ROOT' WHERE role_id = 1;
ROLLBACK;
\echo '-- the same code change aimed at another role'
BEGIN;
UPDATE role SET code = 'ADMIN' WHERE role_id = 2;
ROLLBACK;
```

</details>
