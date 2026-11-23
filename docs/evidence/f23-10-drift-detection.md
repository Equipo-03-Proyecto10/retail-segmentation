# F23-10 (#822): planted drift, detection and false alarms

Whether an alert from the drift monitor is worth believing. Every table below is printed
by `python -m analysis.drift_detection_study` (about five and a half minutes on the
machine that produced it, no database) over the F23-01 evaluation dataset, fingerprint
`8a73b618d299d8abf08fd22d2cd10901b8222a0ca536bf015a77378e44e3826a`, and fresh draws of
its generator. **All of it is synthetic** (ADR-0052). Governing records: ADR-0065
(F23-07), ADR-0088 (F23-09).

## How the monitor is put to the test

**The monitor is the production code.** Each window is judged by model-registry's
`app/drift.py` in the order `monitor.run_once` calls it (segment shares, then R, F and M,
each with the state of the window before), and an alert is raised where
`drift.raises_alert` says so. The profiles it judges are built by rfm-engine's
`app/profile.py`, which fills `current_profile_v1`, and the reference histograms by
model-registry's `training._histogram`. The study loads those modules from their files
and copies nothing.

**The version.** An RFM_RULES version frozen on the evaluation dataset: its quintile
boundaries are that population's (the production query's distinct-value ntile, #352),
its bands are the seed's (RN-46), and its reference is the label shares and the R, F and
M deciles of the 3,979 profiles scored at the dataset's instant.

**Windows with no shift** are fresh draws of the same generator at seeds of their own:
the same distribution and other customers, which is ADR-0065's "a single fixed
distribution". That is stricter than resampling the reference's own customers. Two draws
in a row are the two consecutive windows of the persistence rule.

**Planted shifts** are applied to a window's sales before it is scored, on the same draws
as the no-shift windows, so a difference in the figures comes from the shift and not
from the draw:

| Shift | What is planted | Sizes |
|---|---|---|
| `SPEND` | Every sale's amount times (1 + size) | 0.10, 0.25, 0.50, 1.00 |
| `FREQUENCY` | Each sale except the customer's newest removed with probability size, so recency does not move | 0.10, 0.25, 0.40, 0.50 |
| `CATEGORY` | Each sale moved to category 1 with probability size | 0.10, 0.25, 0.50 |
| `CHANNEL` | Each sale moved to channel 1 with probability size | 0.10, 0.25, 0.50 |

## 1. The version

| Measure | Value |
|---|---|
| Customers in the reference | 3979 |
| Recency, largest days scoring 5, 4, 3, 2 | [27, 95, 123, 151] |
| Frequency, fewest purchases scoring 5, 4, 3, 2 | [21, 16, 11, 6] |
| Monetary, lowest spend scoring 5, 4, 3, 2 | ['4429.43', '2533.07', '1314.68', '572.39'] |
| Reference share of `CHAMPION` | 5.1% |
| Reference share of `LOYAL` | 8.5% |
| Reference share of `POTENTIAL` | 57.5% |
| Reference share of `HIBERNATING` | 2.3% |
| Reference share of `LOST` | 26.5% |
| R bins | 10, edges 0 to 179 |
| F bins | 8, edges 1 to 25 |
| M bins | 10, edges 46 to 513760 |

## 2. False alarms (criterion 2)

400 windows, drawn at their own seeds from the generator that made the reference; consecutive draws form 200 pairs.

| False alarm | Observed | 95% upper bound | Against 1% |
|---|---|---|---|
| Single window: any variable at its alarm threshold | 0/400 = 0.0% | 0.7% | within 1.0%, with 95% confidence |
| Alert: the second of two consecutive windows still alarming | 0/200 = 0.0% | 0.7% | within 1.0%, with 95% confidence |

An alert needs its own window at alarm, so the alert's bound is the tighter of its own and the single window's, whatever the overlap of consecutive windows.

| Variable | PSI, 95th percentile | PSI, largest | Windows at alarm |
|---|---|---|---|
| SEGMENT_SHARES | 0.0035 | 0.0047 | 0 |
| R | 0.0059 | 0.0080 | 0 |
| F | 0.0050 | 0.0077 | 0 |
| M | 0.0069 | 0.0105 | 0 |

**The bar.** ADR-0065 sets no false-alarm rate as such; it says the rate is measured, not
assumed. The one rate it states is the chi-square alarm level, p < 0.01, so this study
holds the false-alarm rate to 1%. With 0 alarms in 400 windows, the one-sided 95% upper
bound is 0.7%, under that 1% for a single window and for an alert. An alert needs its own
window to alarm, so the bound holds however much two consecutive windows overlap in
production (ADR-0088: 29 of their 30 days).

The noise is far below the threshold. The largest PSI on any variable in 400 windows was
0.0105, against an alarm at 0.25.

## 3. Detection per shift (criterion 1)

50 pairs per shift and size, planted on the first draws of the same no-shift windows.

| Shift | Planted | Measured on the sales | Windows at alarm | Detected (alert) | Alerts by variable | Alert explains it |
|---|---|---|---|---|---|---|
| SPEND | amounts x 1.1 | +10.0% | 0/100 = 0.0% | 0/50 = 0.0% | none | — |
| SPEND | amounts x 1.25 | +25.0% | 0/100 = 0.0% | 0/50 = 0.0% | none | — |
| SPEND | amounts x 1.5 | +50.0% | 0/100 = 0.0% | 0/50 = 0.0% | none | — |
| SPEND | amounts x 2 | +100.0% | 100/100 = 100.0% | 50/50 = 100.0% | M 50 | 50/50 = 100.0% |
| FREQUENCY | 10% of older sales removed | -8.2% | 0/100 = 0.0% | 0/50 = 0.0% | none | — |
| FREQUENCY | 25% of older sales removed | -20.5% | 0/100 = 0.0% | 0/50 = 0.0% | none | — |
| FREQUENCY | 40% of older sales removed | -32.9% | 6/100 = 6.0% | 0/50 = 0.0% | none | — |
| FREQUENCY | 50% of older sales removed | -41.1% | 100/100 = 100.0% | 50/50 = 100.0% | F 50, SEGMENT_SHARES 50 | 50/50 = 100.0% |
| CATEGORY | 10% of sales to category 1 | +8.5 points | 0/100 = 0.0% | 0/50 = 0.0% | none | — |
| CATEGORY | 25% of sales to category 1 | +21.3 points | 0/100 = 0.0% | 0/50 = 0.0% | none | — |
| CATEGORY | 50% of sales to category 1 | +42.6 points | 0/100 = 0.0% | 0/50 = 0.0% | none | — |
| CHANNEL | 10% of sales to channel 1 | +7.3 points | 0/100 = 0.0% | 0/50 = 0.0% | none | — |
| CHANNEL | 25% of sales to channel 1 | +18.3 points | 0/100 = 0.0% | 0/50 = 0.0% | none | — |
| CHANNEL | 50% of sales to channel 1 | +36.7 points | 0/100 = 0.0% | 0/50 = 0.0% | none | — |

### Mean PSI per variable (alarm at 0.25)

| Shift | Size | Segment shares | R | F | M |
|---|---|---|---|---|---|
| none | 0 | 0.0015 | 0.0032 | 0.0024 | 0.0037 |
| SPEND | 0.1 | 0.0021 | 0.0030 | 0.0025 | 0.0118 |
| SPEND | 0.25 | 0.0108 | 0.0030 | 0.0025 | 0.0546 |
| SPEND | 0.5 | 0.0421 | 0.0030 | 0.0025 | 0.1567 |
| SPEND | 1 | 0.1346 | 0.0030 | 0.0025 | 0.3626 |
| FREQUENCY | 0.1 | 0.0130 | 0.0030 | 0.0112 | 0.0119 |
| FREQUENCY | 0.25 | 0.0734 | 0.0030 | 0.0708 | 0.0484 |
| FREQUENCY | 0.4 | 0.2136 | 0.0030 | 0.2289 | 0.1275 |
| FREQUENCY | 0.5 | 0.3567 | 0.0030 | 0.4321 | 0.2267 |
| CATEGORY | 0.1 | 0.0015 | 0.0030 | 0.0025 | 0.0037 |
| CATEGORY | 0.25 | 0.0015 | 0.0030 | 0.0025 | 0.0037 |
| CATEGORY | 0.5 | 0.0015 | 0.0030 | 0.0025 | 0.0037 |
| CHANNEL | 0.1 | 0.0015 | 0.0030 | 0.0025 | 0.0037 |
| CHANNEL | 0.25 | 0.0015 | 0.0030 | 0.0025 | 0.0037 |
| CHANNEL | 0.5 | 0.0015 | 0.0030 | 0.0025 | 0.0037 |

**What it shows.**

- **Spend** is detected at double the spend (100% of 50 pairs, all on M) and not at +50%.
  At +50% the mean PSI on M is about 0.16. That is over the watch level of 0.10, so a
  screen shows it, but it is under the alarm level, so no alert is raised.
- **Frequency** is detected when about 41% of the sales go (100%, on F and on the segment
  shares) and not at 33%. At 33%, 6 windows in 100 reached the alarm threshold, but none
  did it twice in a row.
- **Category and channel** are never detected, and this is by design. ADR-0088 decided
  that categories and channels are not monitored variables. A planted category or channel
  shift leaves every customer's R, F and M as they were, so the monitor reads the same
  numbers as without the shift. The study shows this: the PSI of those rows equals the
  no-shift row to the fourth decimal, and `tests/test_drift_detection_study.py` checks
  that the statistics are identical. The planted change itself is real: up to 43 points
  of share for the category and 37 for the channel.

## 4. What an alert says (criterion 3)

The first alert at the largest size of each detected shift, as `drift.review_text` writes
it for a reader, beside what was planted:

**SPEND at 1, window pair ending at draw 1.** Planted: every amount multiplied by 2.

- `M`, `REVIEW_FEATURE_DISTRIBUTION`: PSI 0.355, ALARM.
  > The M values moved against the reference of this version: B9 (4413.39 to 9059.31) rose from 10.0% to 25.0%; B1 (46 to 316.89) fell from 10.0% to 2.6%; B10 (9059.31 to 513760) rose from 10.0% to 19.1%. Check whether its frozen scoring still fits the current customers; if it does not, register a draft version fitted on current data and compare it with this one. A distribution change does not show that the segments stopped predicting anything.

**FREQUENCY at 0.5, window pair ending at draw 1.** Planted: 50% of each customer's older sales removed.

- `SEGMENT_SHARES`, `REVIEW_SEGMENT_MIX`: PSI 0.3428, ALARM; CHI_SQUARE 529.824 (p = 2.37e-113), ALARM.
  > The segment mix moved against the reference of this version: CHAMPION fell from 5.1% to 0.1%; LOYAL fell from 8.5% to 2.0%; POTENTIAL rose from 57.5% to 69.5%. Check whether its frozen scoring still fits the current customers; if it does not, register a draft version fitted on current data and compare it with this one. A distribution change does not show that the segments stopped predicting anything.
- `F`, `REVIEW_FEATURE_DISTRIBUTION`: PSI 0.446, ALARM.
  > The F values moved against the reference of this version: B8 (12 to 25) fell from 11.0% to 1.1%; B7 (9 to 12) fell from 10.3% to 3.7%; B1 (1 to 2) rose from 17.0% to 28.4%. Check whether its frozen scoring still fits the current customers; if it does not, register a draft version fitted on current data and compare it with this one. A distribution change does not show that the segments stopped predicting anything.

An alert **explains the planted change** when it is raised on the variable the shift acts
on (M for spend, F for frequency) and that variable's values moved the planted way: toward
higher bins for spend, lower bins for frequency. Every detected spend and frequency alert
did, in 50 of 50 pairs each. A frequency shift also moves the segment mix, because the
bands score F. The alert says so in its own words, and it never claims more than a
distribution change.

**The same through the running monitor.** The same two planted shifts, M four times higher
and F cut to a third, were fed to `monitor.run_once` on a throwaway PostgreSQL 16.15
built from the three SQL scripts. There were two passes, a day apart, and the alert was
read back through `GET /v1/drift-alerts/{id}`:

```
TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5433/retail \
  python -m pytest -q services/model-registry/tests/test_monitor.py -k planted
2 passed
```

Each test checks the following:

- The planted variable is `ALARM_PENDING` on the first pass and `ALARM` on the second.
- It is the only variable at `ALARM`.
- The alert served is a `DISTRIBUTION_CHANGE` with `REVIEW_FEATURE_DISTRIBUTION`.
- Its text names the variable and the planted direction ("rose" for M, "fell" for F).

The database was discarded afterwards. The rest of `test_monitor.py` passed on the same
database (24 of 24).

## 5. Repeatability (criterion 4)

Study seed 822, reference seed 20261031, 5000 customers a window. SHA-256 of the figures: `cfb2ddcfacaa1cef431f6a2ae54294fa8138e44f73a31facc4d5be6d51630f25`

The study was run twice from scratch, and the two outputs were identical byte for byte.
Every random choice comes from a seed: the reference draw, each window's draw, and each
planted change (study seed, window, kind and size). The test pins the digest of a small
configuration, so a change to any figure has to be deliberate.

## Leads for the owner of ADR-0065

These are observations, not decisions. Each one would need a superseding record.

- **The alarm level is conservative.** Over 400 windows of 4,000 customers, the noise
  stays under PSI 0.011, but an alert needs 0.25. Spend has to double, or four sales in
  ten have to disappear, before an alert fires. A lower alarm level would catch smaller
  changes while staying far from the noise. That trade-off is ADR-0065's to make.
- **Categories and channels are invisible to this monitor**, although the story names them.
  Detecting them needs variables that ADR-0088 decided not to monitor.
- **The 1% bar is this study's reading** of ADR-0065, which states no rate, and the owner
  may set another.
