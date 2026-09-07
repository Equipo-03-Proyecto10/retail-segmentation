# F5-03 — Code review pass over consistency, error handling and logging (#76)

A whole-codebase review pass, run on 2026-09-07 against `develop` at `db3f01d`.
Not a pull-request review: the point of F5-03 is to look at the application as
one thing, which no per-PR review does.

Nothing here was fixed. #76's second acceptance criterion says a defect the
review finds is opened as an issue rather than corrected in place, so this
document is a map to nine issues rather than a diff.

## What was reviewed

Every Python module under `web/` (5,387 lines across 37 files), every Jinja2
template, `sql/01_schema.sql` where the application's error handling depends on
it, and the configuration in `web/config/`. The three questions #76 names —
consistency, error handling, logging — were asked of each.

Baseline before the review: **573 tests passed, `black --check .` and
`ruff check .` clean.** Every finding below is therefore something the suite
does not cover, which is most of what makes them worth writing down.

## Findings

| # | Issue | What | Reproduced |
|---|---|---|---|
| 1 | [#157](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/157) | The category form answers bad input with a 500 where every other catalog form marks the field | yes |
| 2 | [#158](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/158) | The application logs nothing outside the error handler and the authorization gate | yes |
| 3 | [#159](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/159) | A stored password hash argon2 cannot parse makes `/login` return 500 | yes |
| 4 | [#160](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/160) | Six administrator 404s bypass the error handler and lose the reference id | yes |
| 5 | [#161](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/161) | Product price validated with `float()`, stored with `Decimal()`, so `NaN` is accepted | yes |
| 6 | [#162](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/162) | `PORT` read without a guard; the upload ceiling not enforced at the request boundary | yes |
| 7 | [#163](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/163) | Transaction control and SQL cross the layer boundaries ADR-0003 draws | static |
| 8 | [#164](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/164) | Naming and structure drift across `web/routes` | static |
| 9 | [#165](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/165) | Ten of twelve ADRs are still marked Proposed | static |

Six were reproduced by driving the real routes, middleware, services and
templates with a mocked PostgreSQL connector, in the style of
`tests/test_admin_crud.py`. Three are structural and are verified by reading;
each issue carries the `grep` that reproduces its counts.

## Error handling

The controlled-error contract is stated in `web/errors.py`: a visitor sees a
branded page and never a traceback, and the failure leaves one log line
carrying a reference the visitor can quote. F5-02 (#75) verified it holds for
the paths its tests reach. This pass looked for the paths they do not.

Five inputs reach the visitor as a `500 Internal Server Error`:

| Input | Where | Issue |
|---|---|---|
| Rename a category onto an existing name | `web/db/categories.py:121` — the only `update_*` of five without `except UniqueViolation` | #157 |
| `parent_category_id` that is not a number | `web/routes/admin.py:302`, `:370` — unguarded `int()`, three lines from a guarded `.isdigit()` | #157 |
| A `password_hash` argon2 cannot parse | `web/services/auth.py:51` — catches `VerifyMismatchError` only, so `InvalidHashError` escapes | #159 |
| `list_price=1e1000` | overflows `NUMERIC(10,2)`; `create_product` catches only `UniqueViolation` and `ForeignKeyViolation` | #161 |
| `PORT` that is not a number | `web/config/__init__.py:148` — a bare `ValueError` at boot, not `ConfigurationError` | #162 |

One input is accepted that should not be: `list_price=nan` passes a validator
written with `float()` and is stored, because PostgreSQL `numeric` admits `NaN`
(#161). It is the one finding in this pass that writes a wrong row rather than
displaying one wrongly.

The shape underneath four of the five is the same: a family of near-identical
functions where one member was written without the handling its siblings have.
Five `update_*` functions, one missing an `except`; six 404 sites using
`abort`, six using `render_template` directly; three config readers guarded,
one not. Duplication is not itself the defect, but it is what let each of them
survive review — the correct siblings beside them read as evidence that the
pattern was applied.

## Logging

Three logging call sites exist in the whole application:

```
web/errors.py:59             app.logger.exception(...)        unhandled exception
web/middleware/authz.py:348  current_app.logger.warning(...)  access denied
web/middleware/authz.py:380  current_app.logger.error(...)    endpoint declares no requirement
```

`web/routes/`, `web/services/` and `web/db/` contain none. Nothing records a
successful or failed sign-in, a sign-out, a segment run, a rejected upload, a
constraint refusal, or the configuration the process started with. A
brute-force attempt against `/login` leaves no trace in `journalctl`, and
neither does the answer to "who signed in before this happened".

`web/log.py` also wires only `app.logger`, so a module reaching for
`logging.getLogger(__name__)` — the ordinary way to log from a service — gets
no handler and not the configured level. Nothing does this yet, which is why it
is cheap to settle now.

This is a gap, not a regression: no story before F5-03 asked for application
logging. #158 proposes a written convention — which events, at which level,
with which fields — before call sites are added, so a reviewer can check a diff
against something.

The database audit trail is unaffected and is a separate mechanism: `fn_audit()`
records row changes (RF-14), and #158 is about the events that never become a
row.

## Consistency

Naming and structure are close to consistent, and the places they are not are
in #163 and #164. In summary:

- SQL appears in three packages, not one: `web/services/users.py:227` and
  `web/cli.py:140,154` alongside `web/db/`. All three statements are correctly
  parameterized, so `AGENTS.md`'s hard rule holds — but ADR-0003 says the
  boundary exists so that "the parameterization rule becomes a review check
  rather than a hope, because SQL may appear in exactly one package", and
  grepping one package is no longer the check.
- `connection.commit()` appears in all three layers. Five `web/db` modules
  commit their own writes; `db/users.py` and `db/segments.py` do not, and their
  callers commit in `routes/admin.py`, `services/segmentation.py` and `cli.py`.
  Both conventions are defensible; having both is what costs.
- One blueprint of six is named `home_bp` rather than `bp`.
- Return annotations are all-or-nothing per module: 0 of 26 in `admin.py` and
  0 of 2 in `auth.py`, against 4/4, 10/10 and 2/2 in the three written later.
- `max(1, (total + _PER_PAGE - 1) // _PER_PAGE)` is copied 12 times in
  `admin.py` while `catalog.py:47` has the helper and `web/routes/pagination.py`
  is the module that already exists to hold it.
- User-form validation is inline in the route; the other five entities validate
  in `web/services/catalog.py`. It is the one set of rules that cannot be
  unit-tested without a request context.
- Failure is signalled three ways: `str | None` from `web/db` create/update,
  `bool` from `web/db` delete, typed exceptions from `web/services`. The first
  also puts user-facing English in the data-access layer.

## What the review did not find

Recorded because a review that reports only problems is not a review:

- **No SQL injection surface.** Every statement in `web/db/` is parameterized,
  including the filtered queries in `audit.py` and `inventory.py`, which build
  `WHERE` fragments from literals written in the file and bind only values.
  `web/db/metrics.py` whitelists table names at the call site because a table
  name cannot be a parameter.
- **No XSS surface in the templates.** No `|safe`, no `{% autoescape false %}`,
  no `{% raw %}` anywhere under `web/templates/`.
- **No missing authorization.** Every route carries `@public` or `@requires`,
  and `tests/test_authz.py` walks the URL map and fails the build if one does
  not. The default-deny gate refuses an undeclared endpoint.
- **No configuration drift.** All ten variables read from the environment are
  documented in `.env.example`, and none is read that is not.
- **No secrets in source.** The one published password is named in
  `web/cli.py:42` in order to be refused.
- **The single-administrator rule holds in both directions.** Application check
  and partial unique index, as `AGENTS.md` requires. The index is on `role_id`
  regardless of `is_active`, so `activate_user_view` not catching
  `SingleAdministratorError` — which reads like an omission next to
  `deactivate_user_view` — cannot in fact produce a second administrator.
- **No bare `except`, no `except Exception`, no silently swallowed error** in
  application code. `web/errors.py` catches `Exception` deliberately, as the
  last handler, and logs it.

## Limits of this pass

- Six findings were reproduced against mocked connectors rather than a live
  PostgreSQL. #161's stored-`NaN` step needs a real database to confirm, and
  its check query is written out in the issue for whoever repairs it.
- The review covered the application, not the deployment: `deploy/`, the NGINX
  configuration and the systemd unit were read for context only.
- ADR-0006 (systemd and Docker Compose) is the one decision whose subject the
  pass did not exercise. #165 says so rather than assuming it.
- Reviewing is not accepting. `docs/process.md` §6.1 requires each acceptance
  criterion to be verified by a team member other than the author, and #76 is
  not Done until someone else has read this and the nine issues it points at.

## Reproducing the baseline

From a clean clone, using the README's virtual environment and
`pip install -r web/requirements-dev.txt`:

```bash
pytest
black --check .
ruff check .
```

Result on `db3f01d`: **573 passed**, 55 files unchanged, all lint checks passed.
No application, schema, seed or configuration change is proposed by this
document.
