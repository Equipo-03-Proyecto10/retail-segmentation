# ADR-0084 — The primary result of an experiment is stored once, as four frozen relations, and read from them

**Status:** Proposed
**Owner:** Estefanía
**Issue:** #844
**Supersedes:** —
**Superseded by:** —

---

## Context

[ADR-0068](0068-an-experiment-is-planned-and-frozen-before-assignment-and-claims-an-effect-only-against-a-control.md)
fixes one primary look at an experiment, at `A` (the last assignment plus the window plus seven
days). "At `A` the result is computed once and stored. A later read is a labelled re-read and does
not replace the primary result." The contract of `getExperimentResult` says the same, and
`docs/services/experimentation.md` leaves the tables that hold the stored primary result to F25-13.

Story #844 (F25-13) says the opposite in its data-model section: "Writes: none. Schema change: no."
The two cannot both hold. Net sales are read from `api_sales.net_purchase_line_v1`, which keeps no
history of its reversals: a figure computed again after a late return is a different figure, and
nothing in the database can rebuild what the result was at `A`. A primary result that is only ever
computed on request is a look that is taken again at every read, which is the unbounded looking
ADR-0068 was written to end. What was genuinely uncertain is the shape of what is stored.

## Decision

The first read of `basis=PRIMARY` at or after the analysis instant computes the result, stores it in
four relations of the `experiments` schema and returns it; every later read returns what was stored
and recomputes nothing. The relations are `experiment_result` (one row: when it was computed, the
sample ratio p-value, whether it is synthetic), `experiment_result_arm` (one row per arm: its counts,
its net revenue and its revenue standard deviation), `experiment_result_comparison` (one row per
compared pair and metric: the difference, its interval, the p-value, the degrees of freedom and the
bootstrap interval) and `experiment_result_balance` (one row per arm and measure: the standardized
mean difference). They are written in one transaction under an advisory lock keyed by the experiment,
`svc_experiments` holds SELECT and INSERT only, and the plan's trigger refuses any UPDATE or DELETE
from anyone. What follows from the stored values is not stored: the status, the rates, revenue per
customer, whether a balance difference is flagged, and each comparison's label, role, test,
confidence level, conclusion, incremental revenue and whether its bootstrap agrees. The two flags a
look can raise, `SAMPLE_RATIO_FAILED` and `BALANCE_FLAGGED`, are written to `experiment_flag`, where
the plan's `UNDERPOWERED` already is. Before `A`, and for an experiment that was never planned, a
result holds counts and is never stored. `basis=RE_READ` computes again as of the request and
stores nothing.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Compute `PRIMARY` on every read once `A` has passed, and store nothing (the issue as written) | The "primary" figure would move with every late sale and reversal, so the single look is not single and the contract's "never rewritten" is false. It is also the only reading under which nothing could be tested for "a second read does not overwrite it" |
| Store the whole result as one `jsonb` document | One column would hide every fact from the schema, so no constraint could keep a count consistent with its arm, and the normalization decision the course grades would be a comment. The four relations carry real checks (`converted + pending + not_converted = analysed`, a pair of different arms, bootstrap present exactly for revenue) |
| Store every derived figure too (rates, conclusion, incremental revenue) | A conclusion depends on the plan's `UNDERPOWERED` flag and the comparison level, and incremental revenue on a difference and a count that are already stored: a second copy is a transitive dependency a later write could contradict |
| Serve only `RE_READ` and leave `PRIMARY` unavailable until another story | It leaves the acceptance criterion on a single look uncovered and the contract's default basis a refusal |

## Consequences

**What this makes easy.** A result read next month is the result that was read at `A`. The stored
checks and warnings travel with it, and `getExperiment` shows the flags a look raised.

**What this makes hard.** The first primary read is a write, so it needs a transaction and a lock,
and it is the one place `GET` records something. Reading a result also evaluates conversions first
(F25-11), which records sales not yet recorded. Both only add facts. A look taken before the
conversions of the window were complete is stored as it was; the answer to a wrong look is a new
experiment, as ADR-0068 says. The bootstrap runs 10,000 resamples on the first primary read and on
each re-read, which takes about a second for 8,000 customers per arm.

**What must now be true elsewhere.** `docs/data-model.md` carries the normal-form note for the four
relations. `sql/seed-exempt.txt` names them: they exist only for an experiment planned through the
service and read after its analysis instant, and the thirty seeded experiments are assignment rule
V0. The grants manifest lists their eight privileges.

## Compliance

`services/experimentation/tests/test_results.py` reads a primary result, adds a sale and a reversal,
and fails if a second primary read differs by one field or writes a row; it tries every UPDATE and
DELETE as the owner and expects the plan's trigger to refuse them. `tests/test_boundary.py` pins the
role's privileges to `contracts/db/grants.csv`.
