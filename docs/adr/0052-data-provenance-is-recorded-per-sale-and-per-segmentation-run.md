# ADR-0052 — Data provenance is recorded per sale and per segmentation run, not per instance

**Status:** Accepted
**Owner:** Max
**Issue:** #457
**Supersedes:** —
**Superseded by:** —

---

## Context

ADR-0019 requires every screen, export and example derived from seeded or
injected data to say `Synthetic`, and says provenance "cannot be inferred from a
demonstration mode". It put the marker on the experiment tables only
(`experiment.data_origin`). The analytics built on segmentation runs and sales
had nowhere to read provenance from, so F12-01 added an instance flag,
`DATA_IS_SYNTHETIC` (default true), and RN-41 recorded that default as "a
decision recorded for confirmation".

The analytics audit of 2026-10-13 found three problems with that:

- The flag is the demonstration mode ADR-0019 rejected. It is true or false for
  the whole instance, so an instance holding the seed and some CSV-loaded sales
  is either all `Synthetic` or all real, and both are false.
- Only the segmentation dashboard and the Reports page read it. The migration
  matrix, migration explanation, model comparison, run history, segment history
  and consumption reports show figures from the same runs and sales with no
  marker at all.
- Nothing in the schema says which rows are synthetic, so no test or query can
  check that a page marks the right figures.

Two facts make a row-level answer possible. Sales enter the database only
through the seed, the CSV import (ADR-0020) and, in the distributed stage,
sales-service (ADR-0039), so each writer knows what it writes. A segmentation run
reads a fixed window of sales once, so whether it read any synthetic sale is
known at the moment it runs.

What stays uncertain: which existing instance rows came from the seed and
which from CSV loads. The seed's `TXN-` identifiers suggest an answer, but a CSV
file may have used the same shape. The first follow-up story settles that
against the instance itself.

## Decision

`transaction` gains `data_origin`, `OBSERVED` or `SEEDED`, `NOT NULL` with no
default, so every writer has to state it. The seed writes `SEEDED`, while the CSV
import and sales-service write `OBSERVED`. `segmentation_run` gains the same
column, which the segment run service sets when the run executes: `SEEDED` if
any sale it scored is `SEEDED`, otherwise `OBSERVED`. Seeded runs are `SEEDED`.
Every page or export that renders a figure takes its marker from these columns.
A figure from one run is marked by that run, and a figure that compares two runs
is marked if either run is `SEEDED`. A figure aggregated directly from sales is
marked if any sale it aggregates is `SEEDED`. An experiment figure is marked if
the experiment is not `OBSERVED` or any conversion it counts links a `SEEDED`
sale. `DATA_IS_SYNTHETIC` and `Config.data_is_synthetic` are removed.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep the instance flag and add the badge to the pages that lack it | It is the demonstration mode ADR-0019 rejected. On an instance that mixes the seed with loaded sales it marks real figures `Synthetic` or seeded figures real, and no query can tell which |
| Mark runs only, and keep the flag for pages that read sales directly | The Reports page and the consumption reports aggregate `transaction` without a run, so they would still depend on the flag. The run's marker would also be computed from sales that carry no provenance of their own |
| Derive a run's provenance at render time from the sales in its window | A CSV load with past `occurred_at` values adds sales to an old run's window after it ran, so the page would describe sales the run never read. The run records what it read when it runs |
| Link each sale to its `sales_load` and treat seeded loads as synthetic | The seed's `sales_load` rows are themselves demonstration data with no sales linked to them, and sales-service writes sales without a load. It would add a foreign key to record one bit |

## Consequences

**What this makes easy.** A test or CI query can check provenance row by row.
For example, it can check that no seeded sale is `OBSERVED`, and that a run which
read a `SEEDED` sale is `SEEDED`. An instance that receives real sales after the
seed shows real figures unmarked and seeded figures marked, with no setting to
flip.

**What this makes hard.** `transaction` changes shape. The CSV import and
sales-service's sales boundary must both supply the column, and the boundary
guards that already police `svc-sales:` identifiers must also require
`OBSERVED` from `svc_sales`. The instance database needs a backfill whose rule is
decided against its actual rows. Every sales-derived query must return whether
it aggregated a `SEEDED` sale, which is one more column for each report read.

**What must now be true elsewhere.**
- `sql/01_schema.sql`, the seed, and the `transaction` and `segmentation_run`
  entries in `docs/data-model.md`, with the 4NF argument for the new columns.
- RN-41 states this rule instead of the instance flag. The rules for the
  migration, comparison, history and consumption reports state their marker.
- `.env.example`, `README.md` and `web/config/__init__.py` lose
  `DATA_IS_SYNTHETIC`.
- `docs/demo-system-guide.md` §8.11 and its list of limitations describe the
  row-level marker.
- sales-service's contract and boundary section write `OBSERVED` (#454).

## Compliance

- CI's seed compliance block fails if any seeded sale or run is `OBSERVED`.
- A database test runs a segmentation over a window holding one `SEEDED` sale
  and asserts the run is `SEEDED`. Over `OBSERVED` sales only, it asserts the run
  is `OBSERVED`.
- For each page named in the Context, a UI test renders it from a `SEEDED` and
  an `OBSERVED` source. The first must contain the literal `Synthetic`, and the
  second must not.
- `grep -rn DATA_IS_SYNTHETIC` over the repository finds nothing.
