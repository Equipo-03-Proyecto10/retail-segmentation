# ADR-0065 — Drift is claimed in three separate kinds, and concept drift only from a predictive outcome

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #819 (story F23-07)
**Supersedes:** —
**Superseded by:** —

---

## Context

The instructor requires that the project never claims concept drift from descriptive
data. Three different things are easily confused. A migration matrix shows who moved
between segments. `web/services/consumption_shift.py` shows a customer's own change in
channel, store or category (RN-36), claimed only with enough purchases and a clear
leader (RN-50). A change in how the whole population is spread over segments or
features says nothing about why, and nothing about whether segments still predict
anything.

Concept drift is a change in the relation between inputs and an outcome. A
segmentation is descriptive: it has no outcome, so there is nothing to drift until a
predictive task is chosen. That choice is a judgement call and is made here as a
working assumption, for the human to confirm. ADR-0064 gives each model version a
reference distribution; this record says what is compared against it.

## Decision

Four terms are fixed, each with the evidence that proves it and no other.

| Term | Definition | Evidence that proves it | What it does not prove |
|---|---|---|---|
| Individual migration | One customer's segment differs between two assignments under the same model version | Two rows of `customer_segment_history` for that customer, same version, neither with cause `VERSION_CHANGE`; aggregated, the migration matrix | Any change in behaviour of the population, or any cause |
| Individual consumption shift | One customer's dominant channel, store or leading category changed between two stated periods | The RN-36 report with RN-50 satisfied (at least 3 purchases per period, a strict leader in each) | A segment change, or drift |
| Data drift | The distribution of segments or of the RFM features across customers differs from the version's reference distribution | A statistic below, over its window, past its threshold on the persistence rule | That the segments stopped predicting anything |
| Concept drift | The relation between segment and a predictive outcome changed | The outcome test below, on matured cohorts | Anything, if no outcome is adopted |

**Predictive relation (working assumption, pending acceptance).** The monitored
relation is the probability that a customer repurchases within a fixed horizon given
the customer's segment. The outcome is observable: at an as-of date `t`, a customer
repurchases if at least one accepted sale of theirs falls in `(t, t + H]`, with `H` = 30
days. Net sales count (ADR-0062). Only cohorts with `t + H` already in the past are
used, so no unmatured outcome is read as a non-repurchase. The reference is the
per-segment repurchase rate in the version's reference cohort. The test is a two-proportion
z-test per segment, current matured cohort against reference, with Holm correction
across segments at family-wise alpha 0.05, and a segment is tested only when both
sides have at least 100 customers. Concept drift is claimed when at least one segment
passes that test and its rate differs by at least 5 percentage points, in two
consecutive matured cohorts. **If this record is accepted without this relation, no
predictive task is adopted, and concept drift is not monitored.**

**Wording rule.** While no outcome is monitored, or whenever the outcome test has not
run or has not passed, every screen, report, API field and export says "distribution
change", never "concept drift". The phrase "concept drift" may appear only on a result
of the outcome test above, with the relation, horizon and cohort named beside it.

**Data-drift measures.** Reference: the published version's reference distribution
(ADR-0064). Window: the customers' current assignments over the trailing 30 days,
taken as one snapshot at the window's end.

| Statistic | Applies to | Bins | Watch | Alarm |
|---|---|---|---|---|
| Population stability index (PSI) | Segment shares | One per segment | 0.10 | 0.25 |
| PSI | Each of R, F, M | Deciles of the reference, plus one bin for no value | 0.10 | 0.25 |
| Chi-square goodness of fit | Segment shares | One per segment | p < 0.05 | p < 0.01 |

An empty bin is floored at 0.0005 of its side before the logarithm. An alarm on
segment shares requires the PSI alarm threshold **and** the chi-square alarm; an alarm on a
feature requires its PSI alarm threshold. Either alarm must hold in two consecutive
windows. Watch is displayed, never alerted.

**False alarms are controlled by.** (1) A minimum of 200 customers in the window,
otherwise the result is "insufficient data", not a number. (2) PSI thresholds are
conventional, not derived, and are stated as such. (3) Alarm on segment shares needs two
independent signals (PSI and chi-square); the chi-square is a single test, so no
multiplicity correction applies to it, and the per-segment outcome tests carry their own
Holm correction above. (4) Persistence: two consecutive windows.
(5) A window that crosses a version change is not compared (ADR-0064): a
methodological difference is never drift. (6) The false-alarm rate is measured, not
assumed: F23-10 replays resampled data from a single fixed distribution and reports
how often an alarm fires.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Call any significant distribution change concept drift | The instructor forbids claiming it from descriptive data; the distribution can move while the relation to behaviour holds |
| Monitor no predictive task and never speak of concept drift | Safe, but loses the one defensible reading of RT-06; kept as the fallback if the human declines the relation |
| Predict churn or next segment as the outcome | Churn needs a definition the project does not have; next segment is the segmentation predicting itself |
| Kolmogorov-Smirnov or Wasserstein on features | Fine for continuous data, but RFM scores are discrete and ties distort them; PSI over reference bins is simpler to explain |
| Alarm on a single window | Seasonality and small samples would alarm on noise |

## Consequences

**What this makes easy.** The three claims cannot be conflated: each term names its
evidence. A reviewer can check any screen for the forbidden phrase.

**What this makes hard.** Concept drift is slow: it waits for a 30-day horizon to
mature and for two cohorts. Thresholds are conventions, and the false-alarm rate
must be measured to justify them.

**What must now be true elsewhere.**
- F23-08, F23-09 and F23-11 compute and show these statistics and use the wording rule.
- F23-10 validates detection and measures the false-alarm rate (AV-5).
- ADR-0064's reference distribution holds the segment shares and the R, F, M deciles, and the reference repurchase rates.
- RN-36 and RN-50 are unchanged; a shift is never reported as drift.
- RT-06 in the scope stays open until this record is accepted.

## Compliance

A test scans templates, API schemas and report generators for "concept drift" and
fails unless it appears on an outcome-test result. A test feeds a fixed distribution
and a shifted one to the PSI function and checks the thresholds, and one feeds fewer
than 200 customers and expects "insufficient data". A test creates a version change
inside the window and expects no comparison. A test with immature cohorts expects
them excluded from the repurchase rate.
