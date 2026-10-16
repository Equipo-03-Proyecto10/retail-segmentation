# Golden fixtures

Four rules exist twice: in the web, and in the service that ports them
([ADR-0045](../../docs/adr/0045-services-never-import-the-web-system.md),
accepted exception E-17). The services never import `web/`. Instead, this
directory captures what the web does on a fixed set of inputs. Each porting
service then commits that capture as its fixtures and holds its port to them.
`tests/golden/` is the one place outside `web/` that may import `web/`.

| Port | Web code captured | Cases (`cases/<port>.py`) | Fixture |
|---|---|---|---|
| sales | `ingest_row` | an accepted line, and each refusal: future-dated, before registration, inactive product, zero or negative quantity and price, three decimals, unknown customer, product, store and channel, no inventory row, insufficient stock, a `svc-sales:` source id | `services/sales/tests/golden/sales.json` |
| recommendations | `recommend` | every seeded customer, plus three synthetic ones: no history, an unassigned open row from an added run, assigned with no sale in the window | `services/recommendations/tests/golden/recommendations.json` |
| users | `create_user`, `update_user`, `change_role`, `set_active`, `transfer_administrator`, `validate_user` | a second administrator three ways, removing the only one two ways, a transfer and a refused one, a case-variant duplicate email two ways, a malformed email two ways, and two accepted writes | `services/users/tests/golden/users.json` |
| campaigns | `TRANSITIONS`, `transition`, `update_draft`, `validate_campaign` | the table, every (from, action) pair on the seeded campaigns, the draft-only edit, end before start two ways, and activation: an ended seeded draft (RN-49), a new draft with no experiment, and one whose experiment has no treatment (RN-24) | `services/campaigns/tests/golden/campaigns.json` |

## Capturing

Against a database freshly built by the three scripts, as the web's own role:

```bash
psql -v ON_ERROR_STOP=1 -f sql/00_create_database.sql
psql -v ON_ERROR_STOP=1 -d retail -f sql/01_schema.sql
psql -v ON_ERROR_STOP=1 -d retail -f sql/02_seed_30_per_table.sql
DATABASE_URL=postgresql://retail_app:retail_app@localhost:5432/retail \
  python -m tests.golden.capture --service sales --out services/sales/tests/golden
```

**Nothing is committed to the database.**
- Every case runs inside its own owner decorated with `atomic` from
  `web/db/transactions.py`.
- The web's own `@atomic` calls join that owner instead of committing.
- The owner rolls the case back once its outcome is recorded.
- Setup a case needs (an inactive product, a synthetic customer, a 2099 draft)
  happens inside that same rolled-back transaction.

`test_capture.py` checks that a full capture leaves every counted table as it
was.

## A fixture

```json
{
  "header": {
    "port": "sales",
    "web_commit": "<sha>",
    "captured_on": "<date>",
    "seed_sha256": "<sha256 of sql/02_seed_30_per_table.sql>",
    "as_of": {"anchor": "<timestamp>"}
  },
  "cases": [
    {"id": "insufficient-stock", "input": {"…": "…"}, "outcome": {"outcome": "refused", "error": "RowRejected", "reason": "…"}}
  ]
}
```

**What a case records.**
- Each case records its id, its input and a normalized outcome.
- The outcome is either `accepted`, with what the write left behind, or
  `refused`, with the refusal's reason. A database refusal is reduced to its
  SQLSTATE, constraint and primary message, never the failing row.
- A case may carry `service_differs`: where the port deliberately does not do
  what the web does, and why. The `svc-sales:` case is the first.

**The anchor, and why there is one.** The seed dates everything relative to
the moment it loads, so the cases do too. The anchor is the `run_at` of the
newest seeded segmentation run, which the seed stamps with `now()`.
- A sale's `occurred_at` is written as `{"days_before_anchor": N}`.
- Recommendations are computed `as_of` the anchor, and recorded without the
  absolute window bounds.
- Campaign dates in the seed are absolute instead, so transitions are judged on
  a fixed business date, `2026-10-16` (`cases.CAMPAIGN_TODAY`).

Two captures of the same commit differ only in `captured_on`, on any day.

## The workflow

**When a port is implemented.** Each porting story runs the capture and
commits its fixture in the pull request that creates `services/<name>/`:
- F14-04 for users;
- F14-18 for sales;
- F14-22 for recommendations;
- F14-28 for campaigns.

ADR-0045's guard fails as soon as `services/<name>/` exists without
`tests/golden/*.json`. The service's own CI then runs its port against the
fixture, never against web code.

**The drift job.** `.github/workflows/golden-drift.yml` runs nightly, and on
any pull request touching a captured web file, `web/db/`, `sql/` or this
directory. It:
1. builds a fresh database;
2. captures every port;
3. runs `python -m tests.golden.compare` against the committed fixtures,
   ignoring the header.

A difference fails the job, naming the fixture, the case and the web commit
the fixture was captured from. A port whose `services/<name>/tests/golden/`
does not exist yet is reported as "not yet captured" and skipped.

**When drift fails.** Open an issue. Its own pull request regenerates the
fixture and updates the port in the same change. One pull request never
changes `web/` and `services/` together. The web change lands first, and the
drift it causes is what tells the port to follow.

The fixtures are evidence of parity on these inputs, not proof for every
input.
