# ADR-0089 — The outcome test reads each cohort's segments from the assignment history, stores one row per segment, and claims concept drift only on the second passing cohort

**Status:** Accepted (2026-11-22)
**Owner:** Estefanía
**Issue:** #823 (story F23-11)
**Supersedes:** —
**Superseded by:** —

---

## Context

ADR-0065 adopted a predictive relation: the probability that a customer repurchases
within `H` = 30 days given the customer's segment. Its test is a two-proportion z-test
per segment, current matured cohort against the version's reference cohort, with a Holm
correction at family-wise alpha 0.05. A segment is tested only with at least 100
customers on each side. Concept drift is claimed when a segment passes and its rate
differs by at least 5 points, in two consecutive matured cohorts. F23-08 published the
read contract for that test (`OutcomeTest`, `OutcomeSegment`, the `CONCEPT_DRIFT` claim,
`REPURCHASE_RATE` and `REVIEW_PREDICTIVE_RELATION`), and ADR-0088 built the data-drift
half of the monitor.

The story says "Schema change: no", but the test cannot be written without one:

- `models.drift_variable` and `models.drift_statistic` accept only the data-drift
  variables and statistics. Nothing holds a segment's two rates, its z, its p-values or
  its result.
- `models.drift_alert` refuses any claim but `DISTRIBUTION_CHANGE`, any variable but
  the four of data drift, and any review but the two of data drift.
- A cohort is "the customers grouped by their segment at `t`" in the past. model-registry
  can read only each customer's current profile (`api_rfm.current_profile_v1`) and the
  label vocabulary (`api_segments.label_v1`), so it cannot see a segment at any past
  instant. The segment history is segments' table, and a service never reads another
  boundary's table (ADR-0034, ADR-0044).

ADR-0065 also leaves open which cohort is the reference. ADR-0064's reference
distribution holds the label shares and the R, F and M histograms of the fitted data. It
holds no repurchase rate, and the published contract's `ReferenceDistribution` has no
field for one.

## Decision

The outcome test judges the published version only, on matured cohorts that start at its
current tenure:

- **Cohort dates.** `t₀` is the first 03:00 UTC at or after the version's latest
  publication (a publication or a rollback). The **reference cohort** is the cohort at
  `t₀`. The current cohorts are `tₖ = t₀ + k·H` for `k ≥ 1`. A cohort is tested once
  `tₖ + H` is in the past, so its outcome is known and two consecutive cohorts never
  share an outcome day.
- **Members.** A cohort's customers are those whose assignment open at `t` carries a
  label and was written by a run of this version. A legacy web run is resolved through
  `models.run_version`; an incremental run carries `model_version_id` in its parameters,
  which the assignment view publishes while that link is pending (ADR-0087). An
  unassigned customer has no segment and is not in it.
- **Outcome.** A member repurchases if at least one line of theirs in
  `api_sales.net_purchase_line_v1` has `occurred_at` in `(t, t + H]`. That view is net of
  reversals, so a fully returned sale does not count (ADR-0062).
- **Reading the segment at `t`.** segments publishes a new read-only view,
  `api_segments.assignment_interval_v1`: one row per assignment, with its label, its run,
  the version an incremental run recorded, and its half-open interval
  `[valid_from, valid_to)`. svc_models is granted `SELECT` on it.
- **Storage.** A test is a `drift_report` of kind `OUTCOME_TEST`, whose window is the
  cohort's outcome window `(tₖ, tₖ + H]`, so `window_start` is the cohort's as-of date.
  Each segment of the version is a row of a new insert-only table,
  `models.drift_outcome_segment`. The row holds both sides' customers and repurchasers,
  z, the p-value, the Holm-adjusted p-value, the result and `persisted`. The rates, the
  difference in points, the thresholds, the horizon and the claim are functions of those
  values and the constants of ADR-0065, so they are not stored (as in ADR-0088).
- **State and alert.** The report also holds one `drift_variable` row named
  `REPURCHASE_RATE`:
  - `ALARM_PENDING` when some segment passed and none persisted.
  - `ALARM` when some segment passed in this cohort and in the one before it.
  - `NORMAL` otherwise.
  Only `ALARM` raises a `drift_alert`, with claim `CONCEPT_DRIFT` and review
  `REVIEW_PREDICTIVE_RELATION`. CHECKs tie that claim and that review to that variable,
  and a trigger keeps `REPURCHASE_RATE` on `OUTCOME_TEST` reports and the other four
  variables on `DATA_DRIFT` reports. So a data-drift alert still cannot make the claim.
- **Insufficient data and not compared.** A cohort in which no segment has 100 customers
  on both sides is `INSUFFICIENT_DATA`, with no segment rows. A cohort whose outcome
  window, or the reference cohort's, holds a publication or a rollback is `NOT_COMPARED`
  with reason `VERSION_CHANGE`.
- **The pass.** The monitor runs the outcome test in the same pass, lock and audit
  pattern as data drift (ADR-0088), as a `CALCULATION_RUN` of `DRIFT`.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep "Schema change: no" and store the segments as JSON on the report | One column holding many facts per segment is not in 1NF, cannot be constrained, and breaks the 4NF justification the project grades |
| Reuse `drift_statistic` with new statistic names (`Z`, `P_VALUE`, ...) per segment | A statistic row is keyed by variable, not by segment, and the counts are not statistics; the segment would have to be smuggled into the variable name |
| Read the segment at `t` from `customer_segment_history` directly with a grant on the table | A service never reads another boundary's table (ADR-0034, ADR-0044); the boundary publishes a versioned view instead |
| Store the reference repurchase rates in the version's `reference_distribution` at training | A rate needs a matured outcome after the version is in force, which does not exist when it is registered; and it would change the published `ReferenceDistribution` schema |
| Take the reference cohort at the version's `captured_at` | At that instant the customers were scored under the version before, so their segments are not this version's |
| Use every cohort since the publication as the reference | The reference would move with each cohort and drift into the current behaviour it is meant to be compared with |

## Consequences

**What this makes easy.** Each segment's two sides, its test and its result are a row a
reader can check by hand. A cohort's claim is read off its rows. A change in how many
customers each segment holds, with the rates per segment unchanged, cannot pass the test,
because each segment is compared with itself.

**What this makes hard.** Concept drift is slow. The first possible claim comes at
`t₀ + 3H` (reference, first cohort, second cohort), about three months after a
publication, and every publication or rollback starts over. A rollback to the same
version starts a new tenure, so its new cohorts can be reported beside an earlier
tenure's. The view publishes every assignment interval, so it grows with the history.

**What must now be true elsewhere.**
- `contracts/db/api_segments.assignment_interval.v1.md` is the view's contract.
  `contracts/db/grants.csv` lists svc_models on the view and on
  `models.drift_outcome_segment`.
- `sql/seed-exempt.txt` names `models.drift_outcome_segment`, for the same reason as the
  other drift tables. The seed's thirty customers cannot fill a segment of 100.
- `docs/data-model.md` justifies the new table and the widened constraints.
- F23-11's implementation fills the table and serves it through the published
  `OutcomeTest` without changing that contract.

## Compliance

The integration tests plant a change in the repurchase rate of one segment, and expect:
- `ALARM_PENDING` on the first passing cohort.
- `ALARM` and one `CONCEPT_DRIFT` alert on the second passing cohort.
- No claim when only the segment shares change.
- `INSUFFICIENT_DATA` below 100 customers a side.
- `NOT_COMPARED` across a publication.
- An immature cohort is never tested.

The schema tests expect a `DATA_DRIFT` alert or variable that names the outcome to be
refused.
