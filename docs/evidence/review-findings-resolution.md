# F5-03 findings — resolution of #157–#165

Verified on 2026-09-07. This follows the
[original review](f5-03-code-review.md); that review remains a historical record.

## Changes and regression evidence

| Issue | Result | Verification |
|---|---|---|
| #157 | Malformed or out-of-range category parents return 400; duplicate names and missing parents return 409 with field errors and rollback | Create/edit regressions, live PostgreSQL unique/FK refusals, screenshots |
| #158 | Startup, authentication, uploads, writes, HTTP refusals and segment runs produce contextual application logs | `tests/test_event_logging.py`, the [logging convention](../logging.md) and local Gunicorn stderr |
| #159 | Invalid Argon2 hashes and verification errors return the generic 401; missing/inactive accounts perform dummy verification | Real malformed hash and mocked verification failures; no authenticated session |
| #160 | All six administrator failures use the common 404 handler and reference | Five missing edits and missing user activation, screenshots |
| #161 | A price is parsed once as Decimal, checked for finiteness and NUMERIC(10,2) range, rounded half up, and passed to the writer | Create/edit NaN, sNaN, infinities, overflow and rounding-boundary regressions; live Decimal persistence |
| #162 | Invalid ports fall back to 5000; requests larger than the file limit plus 64 KiB receive 413 before their body is read | Port cases, an input stream that fails if read, screenshots; existing per-file validation still passes |
| #163 | SQL lives in `web/db`; services own transactions through one shared lifecycle helper | Architecture guard, nested commit/rollback tests, live rollback after administrator installation and transfer |
| #164 | Blueprint names, route annotations, pagination, user validation and typed write failures use shared conventions | Full regression suite, 12-character CLI/form minimum, entity-specific forms retained deliberately |
| #165 | Previously implemented ADRs record acceptance; acceptance ownership and immutability are explicit | Status-only edits to historical decisions, systemd observation and Compose CI smoke test |

No schema, seed, dataset or uploaded file is changed. The new transaction
decision is submitted in ADR-0014 for reviewer acceptance with the PR. Read-only
queries continue to use the existing data-access functions; write operations
always go through services. Pagination arithmetic lives below routes so the
audit service does not import an HTTP module.

## Local verification

Python 3.12 with the exact dependencies in `web/requirements-dev.txt`.
The final local run passed **639 tests**; `black --check .` and
`ruff check .` both passed. [CI run 34136899955](https://github.com/Equipo-03-Proyecto10/retail-segmentation/actions/runs/34136899955)
also passed all three jobs: format/lint/tests, SQL scripts with seed counts
and restricted-role checks, and Compose/NGINX startup. Business-logic tests cover nested commits, rollback after
partial work and commit failure, the administrator rule, typed refusals, and
configuration/logging behavior.

The three SQL scripts ran in order against a new PostgreSQL 18.6 cluster:

```text
00_create_database.sql: passed
01_schema.sql: passed
02_seed_30_per_table.sql: passed (COMMIT)
```

Additional checks used the restricted `retail_app` role on that disposable
database. A forced failure during demonstration-account deactivation, after
administrator creation and transfer, left the original administrator in place
and no successor row. The same connection remained usable after both unique
and foreign-key category refusals. A Decimal price persisted as 0.15. Inventory
and audit filters worked with and without optional filters, including a closing
date of 9999-12-31. A second segment run changed no assignment.

## Production observations (read only)

The existing GCP service reported `active` and `enabled`. A parameterized query
against the existing database checked `list_price::text` against
`['NaN', 'Infinity', '-Infinity']` and returned **0** rows. This explicitly tests
NaN as well as both infinities; the predicate suggested in #161 does not find
every non-finite PostgreSQL numeric value.

No production account, row, file, configuration or service was changed. Release
testing remains the user's next step after review and merge.

## Screens at 375 px and 1440 px

Chromium rendered the real application with the disposable PostgreSQL database.
Every captured page fit the viewport without horizontal overflow.

| Case | 375 px | 1440 px |
|---|---|---|
| Category duplicate name (409) | [Image](review-category-conflict-375.png) | [Image](review-category-conflict-1440.png) |
| Invalid category parent (400) | [Image](review-category-parent-375.png) | [Image](review-category-parent-1440.png) |
| Non-finite price (400) | [Image](review-product-price-375.png) | [Image](review-product-price-1440.png) |
| Short user password (400) | [Image](review-user-password-375.png) | [Image](review-user-password-1440.png) |
| Missing edit record (404) | [Image](review-missing-record-375.png) | [Image](review-missing-record-1440.png) |
| Request body limit (413) | [Image](review-request-limit-375.png) | [Image](review-request-limit-1440.png) |

## Review and acceptance

Historical ADR acceptance is reconciled against implementations already merged
through the team's PR process. ADR-0006 retains the same two execution paths:
the deployed systemd service was observed running, and CI now boots Compose
from `.env.example`, initializes its database, checks HTML directly and through
NGINX, and exercises the account-report command. The CI result is a merge gate.

Independent team verification, approval, ADR acceptance, merging into `develop`,
branch deletion, and issue closure remain review/merge steps. This evidence
does not claim the full Definition of Done before those steps occur.
