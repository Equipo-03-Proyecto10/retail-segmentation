# F5-01 — Functional tests over the key flows

Deliverable 7/11 in [`docs/scope.md`](../scope.md) §5 asks for test results
captured as evidence. This covers the acceptance criteria: login, each CRUD
module and image upload are exercised, the suite passes in CI, and the result
is recorded here.

## What each flow is exercised by

Rather than one large file, coverage of the key flows is spread across the
stories that actually built them, plus one new file that closed the one real
gap:

| Flow | Covered by | Note |
|---|---|---|
| Login (accepted) | `tests/test_shell.py::test_signing_in_lands_on_a_page_that_names_me_and_my_role` | |
| Login (rejected) | `tests/test_negative_flows.py::test_rejected_login` | F5-02's negative case |
| Image upload | `tests/test_uploads.py` | Round trip, rejection, retrieval — F3-07 |
| Catalog CRUD — read | `tests/test_catalog.py` | Consultation module, F3-05 |
| Catalog CRUD — refused | `tests/test_negative_flows.py` | Empty/invalid fields, unauthorized roles |
| **Catalog CRUD — happy path** | **`tests/test_admin_crud.py`** | **New for this story** |
| User management | `tests/test_single_administrator.py` | Create, promote, deactivate |

## The gap this story closed

Before this story, no test exercised a *successful* create, edit or delete
across the five catalogs `catalog.write` protects (`store`, `category`,
`channel`, `product`, `role`) — only the read path and the refused path
existed. `tests/test_admin_crud.py` adds 19 tests: a listing check plus a
successful create/edit/delete for each catalog (edit is not repeated for
`product`, whose form and image interaction is already covered end to end by
`test_uploads.py`).

Each test signs in as `ADMIN` (the only role holding `catalog.write`),
monkeypatches the corresponding `web.db.*` function, and asserts three things:
the response redirects to the listing, the database function was called with
exactly the form fields submitted, and — for delete — that a referenced row
correctly refuses (already covered by the pre-existing suite) while an
unreferenced one succeeds.

## Result

```bash
$ pytest tests/test_admin_crud.py -v
...
19 passed in 0.25s

$ pytest -q
...
517 passed in 3.07s
```

517 is 498 (before this story) plus the 19 new tests, with no regressions
elsewhere. `black --check .` and `ruff check .` both pass on the new file, so
CI's `quality` job is expected to stay green.
