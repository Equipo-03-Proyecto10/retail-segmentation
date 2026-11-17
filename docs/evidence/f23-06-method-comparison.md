# F23-06 (#818): RFM rules and K-means, compared on stated criteria

Which of the two segmentation methods is better, if either, judged by criteria written
down before the results and applied by the same code to both. Every table of results is
printed by `python -m analysis.method_comparison` (about 30 seconds, no database) over
the F23-01 evaluation dataset, fingerprint
`8a73b618d299d8abf08fd22d2cd10901b8222a0ca536bf015a77378e44e3826a`. **All of it is
synthetic** (ADR-0052). Governing records: ADR-0018 (the two methods behind one
pipeline; its pairing rule is ADR-0030's) and RN-39.

**The result in one line: the criteria favour neither method.** All three tie at their
stated margins. At half those margins each method wins one criterion (K-means
interpretability, RFM rules stability), and at double all three tie, so the verdict is
the same at both. Section 5 says what each method does better and what that means for
choosing between them.

## 1. What is compared, and why this study decides

`web/services/model_comparison.py` sets two runs side by side and reports where they
agree. By RN-39 it says nothing about which is better, and rightly: two methods can
disagree on a third of customers and each be sound, or agree and both be wrong. This
study is what decides, and the decision comes from the criteria in section 2. Agreement
is reported in section 6 as context and is not an input to the verdict.

- **RFM rules:** quintile R, F and M scores matched against the 30 bands of the seed
  (`RFM_RULES`, ADR-0017).
- **K-means, k = 4:** the model F23-03 chose and F23-04 and F23-05 examined (seed 11,
  min-max normalization), labelled by ADR-0030's proportional rank. This is the one
  compared.
- **K-means, k = 6:** the case where the vocabulary and the clusters pair one to one,
  which is where ADR-0018 started. It is a sensitivity, so that K-means is not judged
  only at the k that leaves two labels unused.

All three label the same 3,979 customers, those with a sale in the 180-day window.
Customers with none are unassigned by both (RN-21).

## 2. The criteria, defined before the results

Fixed in `analysis/method_comparison.py` before its first run, and applied identically
to every method through one interface (`Method`): what a method gives the study is the
label of each customer, and the code that scores it never knows how the label was made.

| Criterion | Question | Measure | Score | Margin |
|---|---|---|---|---|
| Interpretability | Does each segment behave as its label says? | The measurable words of each populated label's description in the seed (recent, often, used to buy often, no recent activity, the best or lowest on R, F, M) put to what the segment's customers do. These are F23-05's checks, generalised to all six labels, with its limits (30 days, 90 days, 80% as "most") | Share of claims that hold | 0.20 |
| Stability | Do the same customers keep their segment when something that should not matter changes? | Adjusted Rand index (ARI) between the labels before and after: over 20 bootstrap resamples of the customers (every method given the same ones, the method refitted or its quintiles recomputed on the resample, then everyone labelled), and over windows shifted by 30 days (six steps), as in F23-04 | Mean of the two mean ARIs | 0.10 |
| Usefulness for decisions | Do the segments say who will spend next? | Segment the customers on an earlier window, then the adjusted R-squared of the log of their spend in the period after, explained by segment (adjusted, so that more segments are not rewarded for being more). Two origins: window ending 180 days back judged on the next 180 days, and ending 90 days back judged on the next 90 | Mean of the two | 0.05 |

**Reading the scores.** A difference smaller than the criterion's margin is a tie. A
method is favoured overall only when it wins at least two criteria and loses none;
anything else favours neither. The margins are judgements, each on its criterion's own
scale, and the owner may move them, so the verdict is also computed at half and at
double each margin.

**What was and was not fixed in advance.** The criteria, measures, margins and the
verdict rule were in the module before the first run, and the results below were not
used to change them. One analysis was added after the first tables were seen, and is
labelled as such in section 4: counting only labels that hold at least 2% of customers
(F23-03's commercial limit), because a segment of a handful of customers passes its
claims trivially.

**How RFM rules were scored.** The evaluation dataset lives in memory and the production
RFM is a SQL query, so the study carries a Python replica of that query, reading the 30
bands from the seed file and not copying them. It follows the query's `ntile(5)` over the
**distinct** values of each measure (#352), so equal values share a score. It was checked
against `score_rfm_rules` on the loaded dataset: all 5,000 customers, the 3,979 scored
agree on label and on all three scores with **0 differences**, and the 1,021 with no sale
agree as unassigned. `tests/test_method_comparison.py` keeps that check on a smaller
generated dataset (300 customers, seed 99; it needs PostgreSQL and is skipped without
it) and fails when the replica is changed to split ties by row.

## 3. The two methods on the current window

Each cell is the customers under a label, their share of all customers and of the
window's revenue.

| Label | RFM rules: customers (of all, of revenue) | K-means, k = 4: customers (of all, of revenue) | K-means, k = 6: customers (of all, of revenue) |
|---|---|---|---|
| CHAMPION | 201 (5.1%, 25.0%) | 529 (13.3%, 49.3%) | 1 (0.0%, 3.3%) |
| LOYAL | 336 (8.4%, 22.1%) | none | 528 (13.3%, 45.9%) |
| POTENTIAL | 1,852 (46.5%, 40.4%) | 1,471 (37.0%, 29.2%) | 1,471 (37.0%, 29.2%) |
| AT_RISK | 4 (0.1%, 0.2%) | 748 (18.8%, 15.5%) | 621 (15.6%, 13.9%) |
| HIBERNATING | 419 (10.5%, 8.4%) | none | 590 (14.8%, 4.6%) |
| LOST | 1,167 (29.3%, 4.0%) | 1,231 (30.9%, 6.1%) | 768 (19.3%, 3.0%) |

Two things in this table decide how the rest reads. RFM rules leave **AT_RISK with four
customers**: its bands need a low recency score and a high frequency score at once, and
in a window where customers who last bought long ago also bought rarely, almost no one
has both. And K-means at k = 6 gives CHAMPION to **one customer**, the extreme spender of
F23-02. K-means at k = 4 leaves LOYAL and HIBERNATING unused (ADR-0030).

## 4. Results

### Interpretability: does each segment behave as its label says

| Method | Claims that hold | Score | Labels used |
|---|---|---|---|
| RFM rules | 9 of 12 | 0.750 | 6 |
| K-means, k = 4 | 7 of 8 | 0.875 | 4 |
| K-means, k = 6 | 11 of 12 | 0.917 | 6 |

The claims that do not hold:

| Method | Label | Claim | What the segment measures |
|---|---|---|---|
| RFM rules | POTENTIAL | recent | 79.5% bought in the last 30 days (the limit is 80%) |
| RFM rules | POTENTIAL | a customer who is recent (new) | 40.5% had no purchase before the window |
| RFM rules | HIBERNATING | low frequency and monetary value | F 4.7 (median 4), M 3,096 (median 1,849) |
| K-means, k = 4 | POTENTIAL | a customer who is recent (new) | 51.0% had no purchase before the window |
| K-means, k = 6 | POTENTIAL | a customer who is recent (new) | 51.0% had no purchase before the window |

The RFM HIBERNATING claim fails because of the seed and not because of the method: the
label's description says "Low recency, frequency and monetary value", while the seed's
own comment on its bands says "a mid or high spend (M 3-5)". A segment built to hold
mid and high spenders cannot show low monetary value. It is counted as a failure here
because the criterion is the label's description, and it is worth reconciling in the
seed whichever way the owner prefers.

The claim tables behind those scores. One rule differs from F23-05's: "recency has
slipped" for AT_RISK is read against the median recency of all customers, because the
other method may have no POTENTIAL segment to compare with; its outcome for K-means at
k = 4 is unchanged, and a test checks that these claims fail exactly where F23-05's did.

RFM rules, 9 of 12:

| Label | What its description claims | What the segment measures | Holds |
|---|---|---|---|
| CHAMPION | highest recency, frequency and monetary value | R 5 d, F 18.2, M 19,338 | yes |
| LOYAL | buys often | F 12.6 against a median of 4 | yes |
| LOYAL | buys recently | R 12 d against a median of 30 | yes |
| POTENTIAL | recent | 79.5% bought in the last 30 days | **no** |
| POTENTIAL | room to grow frequency | F 5.7 against 18.2 for the best segment | yes |
| POTENTIAL | a customer who is recent (new) | 40.5% had no purchase before the window | **no** |
| AT_RISK | used to buy often | 12.5 purchases in the 180 days before the window, against 6.2 for all customers | yes |
| AT_RISK | recency has slipped | R 108 d against a median of 30; F 11.5 now, 12.5 before | yes |
| HIBERNATING | low recency | R 115 d against a median of 30 | yes |
| HIBERNATING | low frequency and monetary value | F 4.7 (median 4), M 3,096 (median 1,849) | **no** |
| LOST | no recent activity | 0.0% bought in the last 90 days; R 148 d | yes |
| LOST | across all three measures | F 1.6 and M 532, the lowest of the segments | yes |

K-means, k = 4, 7 of 8:

| Label | What its description claims | What the segment measures | Holds |
|---|---|---|---|
| CHAMPION | highest recency, frequency and monetary value | R 8 d, F 14.7, M 14,447 | yes |
| POTENTIAL | recent | 100.0% bought in the last 30 days | yes |
| POTENTIAL | room to grow frequency | F 5.7 against 14.7 for the best segment | yes |
| POTENTIAL | a customer who is recent (new) | 51.0% had no purchase before the window | **no** |
| AT_RISK | used to buy often | 9.1 purchases in the 180 days before the window, against 6.2 for all customers | yes |
| AT_RISK | recency has slipped | R 94 d against a median of 30; F 5.4 now, 9.1 before | yes |
| LOST | no recent activity | 0.0% bought in the last 90 days; R 149 d | yes |
| LOST | across all three measures | F 1.7 and M 763, the lowest of the segments | yes |

K-means at k = 6 holds 11 of 12; the one that fails is POTENTIAL's newcomer claim again.

**Sensitivity, added after the first tables were seen:** counting only labels that hold at
least 2% of customers.

| Method | Labels under the limit | Claims counted | Interpretability |
|---|---|---|---|
| RFM rules | AT_RISK | 10 | 0.700 |
| K-means, k = 4 | none | 8 | 0.875 |
| K-means, k = 6 | CHAMPION | 11 | 0.909 |

It widens the gap between RFM rules and K-means at k = 4 (0.175) without reaching the
margin of 0.20. Against K-means at k = 6 it crosses it (0.209), so K-means at k = 6 then
leads on interpretability while RFM rules lead on stability: one win each, and the
verdict stays the same for both pairs.

### Stability: do the same customers keep their segment

| Method | Bootstrap ARI, mean of 20 | Bootstrap ARI, lowest | Window ARI, mean of 6 30-day steps | Window ARI, lowest | Customers keeping their label per step | Stability score |
|---|---|---|---|---|---|---|
| RFM rules | 0.95 | 0.82 | 0.46 | 0.25 | 72.2% | 0.71 |
| K-means, k = 4 | 0.84 | 0.52 | 0.47 | 0.29 | 64.3% | 0.65 |
| K-means, k = 6 | 0.72 | 0.49 | 0.41 | 0.29 | 49.7% | 0.56 |

Seeds, which only K-means has (RFM rules take none and give the same labels every time):
mean pairwise ARI over 20 seeds k = 4: 0.86, k = 6: 0.77. Seeds are not in the score
because RFM has none to vary, and a measure one method cannot be given is not on the same
footing; they are the cost K-means carries that the table does not charge.

Against resampling RFM rules are the steadier (0.95 against 0.84), because a quintile
boundary moves little when customers are redrawn, while the K-means fit on some
resamples draws its boundaries elsewhere (its lowest resample drops to 0.52). Against time they are equal: **neither method keeps customers in place when the
window moves by 30 days** (mean ARI 0.46 and 0.47), which is F23-04's finding read for
both methods rather than a fault of one.

### Usefulness: do the segments say who will spend next

| Method | Origin | Customers | Adjusted R-squared, log spend (scored) | Adjusted R-squared, raw spend (not scored) |
|---|---|---|---|---|
| RFM rules | 180 d back, 180 d ahead | 4,215 | 0.457 | 0.067 |
| RFM rules | 90 d back, 90 d ahead | 3,852 | 0.383 | 0.063 |
| RFM rules | usefulness score (mean of the log-spend column) |  | 0.420 |  |
| K-means, k = 4 | 180 d back, 180 d ahead | 4,215 | 0.354 | 0.067 |
| K-means, k = 4 | 90 d back, 90 d ahead | 3,852 | 0.446 | 0.076 |
| K-means, k = 4 | usefulness score (mean of the log-spend column) |  | 0.400 |  |
| K-means, k = 6 | 180 d back, 180 d ahead | 4,215 | 0.393 | 0.085 |
| K-means, k = 6 | 90 d back, 90 d ahead | 3,852 | 0.431 | 0.458 |
| K-means, k = 6 | usefulness score (mean of the log-spend column) |  | 0.412 |  |

| Method | Share who buy again in the 180 days after, by label (customers) |
|---|---|
| RFM rules | CHAMPION 100% (138), LOYAL 100% (595), POTENTIAL 86% (1,796), AT_RISK 100% (50), HIBERNATING 92% (481), LOST 36% (1,155) |
| K-means, k = 4 | CHAMPION 100% (965), POTENTIAL 85% (1,708), AT_RISK 60% (916), LOST 38% (626) |
| K-means, k = 6 | CHAMPION 100% (354), LOYAL 99% (1,041), POTENTIAL 82% (990), AT_RISK 69% (802), HIBERNATING 47% (536), LOST 38% (492) |

The two methods explain about the same share of later spend (0.420 and 0.400), and
**they trade the lead between the origins**: RFM rules explain more from 180 days back
(0.457 against 0.354) and K-means more from 90 days back (0.446 against 0.383). Neither is
consistently ahead. The raw-spend column is not scored because the extreme spenders
dominate it: at k = 6 it reaches 0.458 at the second origin, with a CHAMPION segment of
three customers. Under every method CHAMPION and LOYAL customers buy again nearly always
(99% to 100%) and LOST customers 36% to 38% of the time.

## 5. The verdict, from the criteria

| Criterion | RFM rules | K-means, k = 4 | Difference | Margin | Leads |
|---|---|---|---|---|---|
| interpretability | 0.750 | 0.875 | 0.125 | 0.20 | tie |
| stability | 0.709 | 0.651 | 0.058 | 0.10 | tie |
| usefulness | 0.420 | 0.400 | 0.020 | 0.05 | tie |

Wins: RFM rules 0, K-means 0, ties 3. **Overall: favours neither.** At half the margins:
neither; at double: neither.

Against K-means at k = 6 RFM rules lead on stability (0.709 against 0.562, difference
0.147, above its margin of 0.10), tie on the other two, and with one win and no loss that
is still not two wins: **favours neither**, at half the margins and at double.

**A result that favours neither, said plainly.** On these data and these criteria there is
no basis to call RFM rules or K-means the better method, and a report that picked one
would be reading a difference the criteria do not support. What the study can say is where
each leads, below the margin or not:

- **RFM rules are steadier and simpler to hold still:** 0.95 against 0.84 under
  resampling, 72% against 64% of customers keeping their label each month, no seed, the
  same labels every run, and a scoring that can be reproduced by hand from three sets of quintile boundaries and a
  table of bands. They also lead on stability against K-means at k = 6.
- **K-means scores higher on the words of its labels, on fewer labels, by less than the
  margin:** 7 of 8 claims against 9 of 12, and it fills AT_RISK (748 customers) where RFM rules fill it with four. It
  cannot name LOYAL or HIBERNATING at k = 4, and at k = 6 its CHAMPION is one person.
- **For deciding who to invest in they are equal here:** the same share of later spend
  explained, and the same pattern of who buys again: CHAMPION and LOYAL customers
  nearly always, LOST customers 36% to 38% of the time.

So the choice between them is not settled by quality and should be made on what the
business needs that these criteria do not measure. A team that needs a segment of
customers who are slipping away has K-means to thank for it on this data. A team that
needs labels that are the same on every run, and that explain themselves by three
thresholds, has RFM rules.

## 6. Context that is not in the verdict

Agreement between RFM rules and K-means at k = 4 (`model_comparison`): 2,805 of 3,979
customers share a label (70.5%); ARI between the two partitions 0.71.

| RFM rules (rows) by K-means, k = 4 (columns) | CHAMPION | POTENTIAL | AT_RISK | LOST |
|---|---|---|---|---|
| CHAMPION | 201 | 0 | 0 | 0 |
| LOYAL | 326 | 0 | 10 | 0 |
| POTENTIAL | 2 | 1471 | 379 | 0 |
| AT_RISK | 0 | 0 | 4 | 0 |
| HIBERNATING | 0 | 0 | 317 | 102 |
| LOST | 0 | 0 | 38 | 1129 |

The disagreement is where the methods draw their lines differently: K-means takes
379 of RFM's POTENTIAL and 317 of its HIBERNATING into AT_RISK, and puts RFM's LOYAL with
its CHAMPION. Seven in ten agreeing says how alike the groups are and does not say which
grouping is right, which is why it is not a criterion.

Recovery of the planted groups (synthetic only), ARI: RFM rules 0.40, K-means k = 4 0.65,
K-means k = 6 0.53. This is the one number that leans towards K-means. It is left out of
the verdict because only synthetic data has planted groups, and because what the
generator plants decides what a method can recover: a real customer base offers no such
answer to check against, so a criterion built on it would not carry over.

## 7. Limits of this study

- Synthetic data with six planted groups and one dataset. The method carries over; the
  scores do not, and a real customer base could separate the methods where this one
  does not.
- The margins are judgements. The verdict is shown at half and double them, and stays;
  it does not follow that no margin would change it.
- Interpretability scores a share of claims, and a method with fewer labels has fewer
  chances to fail one. The count of claims is in the table; the 2% sensitivity removes
  the claims that a segment of a handful passes trivially.
- Usefulness is prediction of later spend, not the effect of acting on a segment. It says
  the segments are informative about who spends; whether a campaign built on them pays
  off needs the comparison against a holdout that F23-05 describes, and this dataset
  cannot run it.
- Stability fits K-means at the registered seed 11; the cost of other seeds is the
  separate line in section 4. The bootstrap keeps K-means' min-max scale from the full
  population (as in F23-04) and recomputes RFM's quintile boundaries on each resample,
  which is how each method would respond to a different population.
- The windows of the planted behaviour are relative to the end date, so the size of the
  movement between windows belongs to the generator (F23-04's limit, which applies to
  both methods equally).
