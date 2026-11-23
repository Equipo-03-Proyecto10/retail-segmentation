# F25-14 experiment pipeline validation

This page records the deterministic validation of experimentation-service's own
assignment, statistics, result and attribution paths. The study does not copy an
estimator: repeated runs call `app.verify.deal` and
`app.stats.compare_proportions`, the functions used by assignment and result
operations. Database cases plan and assign through the Flask routes, write sales
through the owner tables visible to the service, and read the service routes.

## Repeated statistical study

All tests use two-sided alpha 0.05 and 95% intervals.

The A/A study fixes seed `845014`, 1,200 customer UUIDs and their 10% null
outcomes before assignment. It performs 1,000 independent assignments, each with
the production HMAC deal and a distinct deterministic 32-byte salt, and analyses
each split with the production pooled z test and unpooled Wald interval. It
rejects 46 of 1,000 nulls: a 4.6% false-positive rate. The stated tolerance is
33 through 69 rejections, the inclusive central 99% integer-quantile interval of
a Binomial(1000, 0.05) variable. Thus 4.6% is consistent with the nominal 5%.

The injected-effect study fixes seed `845015`, 1,000 repetitions and 800
customers per arm. Every repetition uses a new deterministic salt and the same
production HMAC deal. Outcomes are then generated at control probability 0.10
and treatment probability 0.15, a true absolute effect of 0.05. The first
interval is `[0.004460, 0.070540]` around an estimate of `0.0375`, so it covers
the known effect. Across repetitions, 961 of 1,000 intervals cover 0.05 (96.1%);
the average recovered effect is `0.04961`. The coverage tolerance is 931
through 967, the inclusive central 99% integer-quantile interval of a
Binomial(1000, 0.95) variable.

These wide 99% validation tolerances distinguish an estimator defect from the
ordinary Monte Carlo variation of a finite, fixed study. Exact observed counts
are asserted as well, so any change is reviewed rather than silently absorbed by
the tolerance.

## Database and HTTP-path checks

- A/A: 200 customers are planned and assigned through the routes. Ten customers
  in each service-assigned arm buy in-window. Reading
  `/v1/experiment-results/{id}` returns 10 conversions per arm, zero difference,
  p-value 1 and an interval containing zero.
- Injected effect: 400 customers are planned and assigned through the routes.
  Twenty of 200 control customers and 30 of 200 treatment customers buy
  in-window. The result recovers the injected five percentage points and its
  interval contains 0.05. Seeded test sales make the result explicitly synthetic.
- Stability: after route assignment, two customers receive a new segment row;
  one withdraws `PERSONALIZATION` and one withdraws `MARKETING_CONTACT`. The
  assignment list is byte-for-byte the same mapping, and `app.verify.verify`
  reports no disagreement using the route's salt, arms, assignment keys and rows.
- Contamination: two experiments have non-overlapping written dates but overlap
  in their real periods because one is assigned long after its start. In either
  assignment order, the second assignment is refused with 409 and no customer is
  shared. Substituting the pre-fix predicate makes the late experiment's
  second-assignment cases return 201 in both tested database time zones.
- Counted once: one sale qualifies for two campaign experiments and has an
  exposure in each. Conversion and attribution operations select one holder;
  `/v1/sale-attributions` contains one row, and the sum of `attributed_sales`
  across both experiment result reports is one, as ADR-0069 requires.

Each PostgreSQL test receives a private database copied from the seeded template
by `services/experimentation/tests/conftest.py`; no immutable rows are written to
a database shared with another test.

## Reproduce

From the repository root, with PostgreSQL and Redis available on the lane under
test:

```sh
bash .github/scripts/stage-contracts.sh experimentation
cd services/experimentation
uv sync --frozen
uv run --frozen pytest -q \
  tests/test_validation_statistics.py tests/test_validation_pipeline.py
```

The focused run completed 14 parametrized cases in 15.80 seconds on the local CI
environment (UTC and America/Mexico_City database sessions). Run the complete
service suite with `uv run --frozen pytest`.
