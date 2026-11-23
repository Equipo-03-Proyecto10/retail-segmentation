# F23-11 (#823): the outcome test of the predictive relation

Whether concept drift is claimed only with evidence. F23-07 adopted a relation
(ADR-0065): the probability that a customer repurchases within 30 days, given the
customer's segment. So criterion 3, closing the story as not planned, does not apply.
ADR-0089 decides how the test reads its cohorts and stores its result. Everything below
was run against a throwaway PostgreSQL 16.15 built from the three SQL scripts, as
`svc_models`, through the service's own code. **All of it is synthetic** (ADR-0052).

## How the test works

- **Cohorts.** The reference cohort is at `t0`, the first 03:00 UTC after the published
  version's latest publication or rollback. A current cohort comes every 30 days after
  it. A cohort is tested only once its outcome window `(t, t + 30 days]` is in the past.
- **Members.** A cohort is every customer whose assignment open at `t` has a label and
  was written under the published version (`api_segments.assignment_interval_v1`). The
  assignment view carries the version recorded by an incremental run, so a pending
  `models.run_version` link does not remove that customer from the cohort.
- **Outcome.** A member repurchased when a net purchase line of theirs falls in the
  window (`api_sales.net_purchase_line_v1`, net of reversals).
- **The test.** For each segment, the current rate is compared with the reference rate by
  a pooled two-proportion z-test, with Holm's correction across the tested segments. A
  segment passes at adjusted p < 0.05 with at least 5 points of difference. A segment with
  fewer than 100 customers on either side is not tested.
- **The claim.** The first passing cohort is `ALARM_PENDING` and claims nothing. A segment
  that passes again in the next cohort persists. That makes the report `ALARM` and raises
  the one alert that claims `CONCEPT_DRIFT`.

## Criterion 1: predicted and observed outcomes per period, matured customers only

`test_cohorts_start_at_the_tenure_and_are_one_horizon_apart` and
`test_an_immature_cohort_is_never_tested` check the following:

- A cohort whose window ends one second in the future gives no report.
- Consecutive cohorts are exactly 30 days apart, so no outcome is counted twice.

`test_the_real_cohort_read_runs_as_the_service` runs the production read of the view and
the net purchases as `svc_models`.

Each report names its cohort and its window. Each segment carries the reference rate
(predicted) beside the current rate (observed).

## Criterion 2: a planted change is detected, and a change in population alone is not concept drift

The reference cohort holds CHAMPION 300 (60%), LOYAL 500 (48%) and LOST 400 (8%).

| Planted | Cohort 1 | Cohort 2 | Claim |
|---|---|---|---|
| LOYAL drops from 48% to 30%; the others stay the same | LOYAL `PASSED`, report `ALARM_PENDING`, no alert | LOYAL `PASSED` and persisted, report `ALARM`, one alert | `CONCEPT_DRIFT`, on cohort 2 only |
| Three times the champions, a fifth of the loyal, three times the lost, each at its own rate | every tested segment `NOT_PASSED`, report `NORMAL` | the same | none |

For the planted change, the served report shows:

- LOYAL's reference as 500 customers, 240 repurchasers, rate 0.48.
- Its current rate as 0.3, a difference of −18.0 points, `persisted` true.
- The claim `CONCEPT_DRIFT`, beside the relation `REPURCHASE_GIVEN_SEGMENT`, the 30-day
  horizon and the cohort.

The alert has empty `statistics` and `bins`. Its `outcome_test` holds LOYAL only, and its
review is `REVIEW_PREDICTIVE_RELATION`:

> Customers of LOYAL repurchased within 30 days at 30.0% against 48.0% in the reference
> cohort, in two consecutive matured cohorts. Review the campaigns and recommendations
> that rely on these segments, and consider a draft version fitted on current data.

The population-only change moves the segment shares a great deal, so the data-drift
monitor would report a distribution change. But each segment keeps its own rate, so no
segment passes and nothing anywhere in the served reports says "concept drift".

The guards around it:

- A difference that is significant but under 5 points does not pass.
- A segment below 100 customers is `NOT_TESTED`.
- A cohort with no segment of 100 on each side is `INSUFFICIENT_DATA`, with no rows.
- A publication inside the window is `NOT_COMPARED`.
- Repeating a pass writes nothing, and the next pass continues the persistence chain with
  one audit event per cohort.

The schema refuses a data-drift report, variable or alert that names the outcome
(`tests/test_drift_outcome_schema.py`). The source scan allows the claim in one line of
the service.

## The run

```
TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5433/retail \
  python -m pytest -v services/model-registry/tests/test_outcome.py
```

All 17 pass:

```
test_the_z_test_is_the_contracts_example
test_equal_rates_of_zero_or_one_differ_by_nothing
test_holm_steps_down_and_never_decreases
test_a_change_in_the_population_alone_passes_nothing
test_a_change_in_what_a_segment_predicts_passes_and_persists_the_second_time
test_a_significant_change_under_five_points_does_not_pass
test_a_segment_under_the_minimum_on_either_side_is_not_tested
test_cohorts_start_at_the_tenure_and_are_one_horizon_apart
test_the_review_names_each_segment_its_two_rates_and_no_other_claim
test_an_immature_cohort_is_never_tested
test_a_planted_change_is_claimed_on_the_second_cohort_and_served
test_a_change_in_the_population_alone_is_never_concept_drift
test_a_cohort_with_no_segment_of_100_a_side_is_insufficient
test_repeating_a_pass_writes_nothing_and_a_later_pass_continues
test_a_publication_inside_a_cohort_is_not_compared
test_the_real_cohort_read_runs_as_the_service
test_the_real_cohort_keeps_an_incremental_run_while_its_link_is_pending
```

The z-test reproduces the published contract's example: LOYAL, 5,714 customers with
2,743 repurchasers against 5,421 with 2,063, gives z = −10.59 and p = 3.16e-26.

## What is not shown here

The seed holds thirty customers and a version published when the seed loads, so no real
cohort matures on a fresh database. The first real claim can come about three months
after a publication: the reference, a first cohort and a second one.
