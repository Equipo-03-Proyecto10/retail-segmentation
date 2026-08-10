# Seed dataset strategy

**Status:** Stub — awaiting S0-08
**Owner:** Estefanía
**Story:** S0-08 · **Issue:** #26 · **Depends on:** S0-04a (complete)

---

> **This is a stub.** S0-08 in `../scrum/03-sprint-00-backlog.md` already points
> readers here — *"See `docs/data/seed-strategy.md` for the source, licence and
> transformation steps"* — as if the file existed. It did not. This carries the
> structure and the constraints so the story starts from a shape.

## Why this story matters more than its 3 points suggest

R-04, exposure 15. RFM over hand-entered rows produces degenerate quintiles, and
with no purchase history there is nothing between two runs for a migration
report to detect. The Sprint 2 demonstration — the part that distinguishes this
project from a CRUD application — becomes impossible. Nobody usually assigns
this story, and its absence is what kills the demonstration.

## Source dataset

*To be written in S0-08.*

- [ ] Dataset name, origin and URL
- [ ] **Licence, and whether it permits use and redistribution in a course project**
- [ ] Row and customer counts, and the date range actually present
- [ ] Why a public dataset with genuine purchase behaviour rather than fully synthetic generation

**Never commit the dataset.** `data/` and `*.csv` are gitignored, and that is a
hard rule in `AGENTS.md`. This document records how to obtain and transform the
data; the loader fetches it.

## Column mapping

*To be written in S0-08.* Source columns to `customer`, `product`, `category`,
`sales_transaction`, `sales_transaction_line`.

Two schema facts the mapping has to respect, both settled in S0-04a:

| Fact | Consequence for the loader | Source |
|---|---|---|
| `UNIQUE (source_system, external_transaction_id)` on `sales_transaction` | This is what makes `make seed` twice leave row counts unchanged. Do not truncate first and call it idempotency | D-08, CHECK 12 |
| `sales_transaction_line.category_id` is copied from the product at ingestion, not read live | The loader writes it; re-categorizing a product later must not rewrite feature history | D-12 |
| Returns are negative rows with `transaction_type = 'return'` and a sign constraint | Loading credit notes as sales inflates Monetary | D-07, CHECK 10 |
| An in-store transaction must name a store | The synthetic channel assignment cannot leave `store_id` null for `in_store` | CHECK 11 |

## Synthetic layers

*To be written in S0-08.* The base data lacks store and channel, so both are
assigned probabilistically per customer with a documented distribution.

- [ ] Store assignment distribution
- [ ] Channel assignment distribution
- [ ] Random seed, so the seed data is reproducible across machines

## Injected migration ground truth

*To be written in S0-08.* A known subset of customers is given controlled
behaviour change: frequency change, category shift, channel switch, spend
increase and decrease.

**The ground truth must be expressed in segment labels, not cluster indices.**
This is R-04's mitigation and it is not optional. Cluster indices are arbitrary
and unstable between runs (D-04), so a ground truth recorded as *"customer 417
moves from cluster 2 to cluster 5"* cannot be compared against detection output
at all — the detector emits label codes and the two runs' numbering has no
relationship. Record the expected movement as `champions → at_risk` and the
comparison becomes a join.

**Blocked on A-06.** `../scrum/assumption-register.md` records the working
assumption of six fixed labels with deterministic centroid-rank assignment.
A-06 resolving late does not block the schema, but it does block validating
migration detection against known answers. Confirm it before generating the
ground truth.

## Loader

*To be written in S0-08.*

- [ ] Invoked by `make seed` (the target is defined in S0-02)
- [ ] Idempotent on the natural key, verified by running it twice
- [ ] Writes ingestion telemetry to MongoDB during loading, exercising that path from day one

## Acceptance criteria

Carried from S0-08 in `../scrum/03-sprint-00-backlog.md`.

- [ ] Given an empty database, when `make seed` runs, then at least **2,000 customers and 100,000 transaction lines spanning at least 18 months** are loaded
- [ ] Given `make seed` run twice, when the row counts are compared, then they are unchanged
- [ ] Given the loaded data, when RFM is computed, then all five quintiles are populated in every dimension
- [ ] Given the injected ground truth, when two runs are compared, then the known migrating customers appear in the migration set

**Trigger.** Fewer than 2,000 customers or fewer than 18 months of history after
loading → raise immediately. R-04 classifies that as a Sprint 2 blocker
discovered in Sprint 0.

## References

- `../scrum/03-sprint-00-backlog.md` — S0-08
- `../scrum/04-risk-register.md` — R-04, and R-17 on labelling determinism
- `../scrum/assumption-register.md` — A-06, A-07
- `postgresql-model.md` — D-07, D-08, D-12
- `mongodb-design.md` — the telemetry collection this loader writes to
