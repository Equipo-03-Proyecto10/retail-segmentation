# F23-05 (#817): the segments, in business terms, with what to do about each

What each K-means segment is, what a marketer would do about it, and where its label
says more than its customers do. Every table is printed by
`python -m analysis.segment_profile` (a few seconds, no database) over the F23-01
evaluation dataset, fingerprint
`8a73b618d299d8abf08fd22d2cd10901b8222a0ca536bf015a77378e44e3826a`. **All of it is
synthetic** (ADR-0052): the actions below are reasoned from these numbers, none was
tried on a customer, and no effect size is claimed. Governing records: ADR-0030, RN-38.

## 1. The model

The model F23-03 chose and F23-04 found stable: k = 4, seed 11, a 180-day window, the
production fit and min-max normalization. 3,979 customers have a sale in the window and
are clustered; 1,021 have none and are unassigned (RN-21), so they are in no segment and
no table here. Cluster sizes are 1,471, 1,231, 748 and 529.

With four clusters and six labels, ADR-0030 pairs them by proportional rank: best to
worst, **CHAMPION, POTENTIAL, AT_RISK, LOST**. LOYAL and HIBERNATING take no cluster.
This matters throughout: at k = 4 **a label is a position among four groups, not a fixed
commercial meaning** (ADR-0030, "What this makes hard"), and section 3 shows where that
costs.

## 2. The segments

The centroid in raw units is the mean of its customers: recency in days since the last
purchase (measured from the newest sale in the window), frequency in purchases in the
window, monetary in spend in the window. It is the fit's normalized centroid turned back
into raw units, and a test checks that. The share of revenue is of the window's revenue.

| Segment (k = 4) | Customers | Share of customers | Share of revenue | R centroid (days since last purchase) | F centroid (purchases) | M centroid (spend) |
|---|---|---|---|---|---|---|
| CHAMPION | 529 | 13.3% | 49.3% | 8 | 14.7 | 14,447 |
| POTENTIAL | 1,471 | 37.0% | 29.2% | 15 | 5.7 | 3,077 |
| AT_RISK | 748 | 18.8% | 15.5% | 94 | 5.4 | 3,220 |
| LOST | 1,231 | 30.9% | 6.1% | 149 | 1.7 | 763 |

In plain words:
- **CHAMPION:** the customers who bought a week ago, about fifteen times in six months,
  and spent several times anyone else. One customer in eight, half the revenue.
- **POTENTIAL:** active customers who all bought within the last month (typically two
  weeks ago) but only about five or six times in six months. The largest group, and the one with the most room to grow.
- **AT_RISK:** customers who spend as much as POTENTIAL's but whose last purchase was
  about three months ago, after buying more often before.
- **LOST:** customers who bought once or twice, about five months ago, for little. Nearly
  a third of customers and 6% of revenue.

How each one behaves, including how it bought in the 180 days before the window:

| Segment | Median R, F, M | Bought in last 30 d | Bought in last 90 d | Purchases in the 180 d before the window | No purchase before the window | Extreme spenders, and their share of the segment's revenue |
|---|---|---|---|---|---|---|
| CHAMPION | 7 d, 14, 12,459 | 100% | 100% | 13.0 | 0% | 5, 17.7% |
| POTENTIAL | 14 d, 5, 2,161 | 100% | 100% | 4.3 | 51% | 12, 18.8% |
| AT_RISK | 94 d, 5, 2,697 | 0% | 42% | 9.1 | 0% | 4, 11.6% |
| LOST | 150 d, 1, 494 | 0% | 0% | 3.8 | 2% | 15, 22.4% |
| all customers | | | | 6.2 | | |

"Extreme spenders" are the 1% of customers the generator makes spend 25 times their
group's ticket (F23-02): they are 5 customers who carry 17.7% of CHAMPION's revenue and
15 who carry 22.4% of LOST's. Segment averages over spend should be read with that in
mind, and the medians are the safer figure.

## 3. Where a label does not match its customers

Each label's own description (`segment_label.description` in the seed) was put to what
its cluster measures. The rules are in `analysis/segment_profile.py`, and "recent" and
"no recent activity" are the stated limits of 30 and 90 days, with 80% as "most".

| Label | What its description claims | What the cluster measures | Holds |
|---|---|---|---|
| CHAMPION | highest recency, frequency and monetary value | R 8 d, F 14.7, M 14,447: the best of the four on each | yes |
| POTENTIAL | recent | 100% bought in the last 30 days | yes |
| POTENTIAL | room to grow frequency | F 5.7 against 14.7 for the best segment | yes |
| POTENTIAL | a customer who is recent (new) | 51% had no purchase before the window | **no** |
| AT_RISK | used to buy often | 9.1 purchases in the 180 days before the window, against 6.2 for all customers | yes |
| AT_RISK | recency has slipped | R 94 d, now 5.4 purchases against 9.1 before | yes |
| LOST | no recent activity | 0% bought in the last 90 days; R 149 d | yes |
| LOST | across all three measures | F 1.7 and M 763, the lowest of the four | yes |

Seven claims hold and one does not, and three more points the table does not show
need saying, because a marketer reading the labels would be misled by them.

- **POTENTIAL is not a group of newcomers.** Half of its customers (51%) have no purchase
  before the window and half have been buying for more than a year. The model sees only
  recency, frequency and spend, and tenure is none of them, so it cannot tell "recent
  customer" from "regular customer who buys a few times in six months". In this
  dataset the generator planted both: of its 1,471 customers, 748 are planted
  POTENTIAL and 684 are planted LOYAL (and 39 CHAMPION). Read the label as **"active, with room
  to grow frequency"**, and do not send it a welcome offer meant for new customers.
- **CHAMPION includes customers the vocabulary would call LOYAL.** 166 of its 529
  customers (31%) are planted LOYAL. At k = 4 LOYAL has no cluster (ADR-0030), so the
  best loyal customers join CHAMPION and the others POTENTIAL. CHAMPION is therefore the
  most active 13% of customers, not only the exceptional few.
- **LOST means "lowest of four", not "gone".** All 1,231 customers in it bought in the
  last six months, the median one 150 days ago. 991 of them are planted HIBERNATING and 229
  planted LOST, because HIBERNATING has no cluster at k = 4. The customers who really have
  gone (no sale in the window, 1,021 here, all planted LOST) are **unassigned, not LOST**
  (RN-21), so a count of LOST understates how many customers have left and
  overstates how many are about to.

These three are not defects of the fit: they are what ADR-0030 says about pairing four
clusters with six labels, measured. They do mean that a marketer should work from the
numbers in section 2 and use the label as a name.

## 4. What to do about each, and what would show it worked

These are recommendations from the numbers above, to be run as a comparison against a
group left alone, because the dataset cannot say what a customer would have done
unprompted. Each measure is taken over the same period for the contacted customers and
for a holdout drawn at random from the same segment; the action worked if the contacted
group is ahead and the gap is larger than the gap between two random halves of the
holdout.

| Segment | Action | Why these numbers | The measure that shows it worked |
|---|---|---|---|
| CHAMPION | Protect and reward: early access to new stock, a named contact for the largest accounts. No discount: they already buy about every two weeks | 13% of customers, 49% of revenue; all of them bought in the last 30 days, so a discount gives away margin on purchases that happen anyway | Retention: share still buying 90 days later, and spend per customer, against the holdout. Report with and without the extreme spenders, who carry 17.7% of the segment |
| POTENTIAL | Raise frequency: a second-purchase prompt a week or two after a purchase, and a reminder on repeat categories | The largest segment, 37% of customers, buying 5.7 times against 14.7 for CHAMPION and bought in the last 30 days, so the contact lands while they are active | Purchases per customer in the next 60 days against the holdout, and the share that reach CHAMPION's frequency at the next run (a migration in the matrix) |
| AT_RISK | Win back before they lapse: a reminder timed at about 60 to 90 days since the last purchase, offering what they bought before | They bought 9.1 times in the previous six months and 5.4 in this one, at 94 days since the last; 42% are still inside 90 days, so the window to act is closing | Reactivation: share with a purchase within 30 days of contact, against the holdout, and the share back in POTENTIAL at the next run |
| LOST | Spend little: one low-cost automated message, no discount, and stop after it | 31% of customers but 6% of revenue, 1.7 purchases at 763 each; a campaign here must cost less than it can return | Cost per customer who buys again within 60 days, against the segment's margin on 763 of spend; if it is higher, the action stops |

What each action shares: the segment is read at the time of the run, so a customer
contacted this month may be in another segment next month. F23-04 measured how far that
goes (at k = 4, 36% of customers change rank between windows 30 days apart), so a
campaign should be built from the latest run, and a measure compared against a holdout
from the same run.

## 5. Limits of this study

- Synthetic data with six planted groups, so a share in section 2 shows what the model
  finds in this dataset and not what a real customer base holds. The method carries over;
  the figures do not.
- The "planted groups" columns exist only because the generator knows the group. They show what each cluster contains, and no real data would offer them.
- The actions are untested here. The measures say what to look at, and this study
  has no data on how customers respond to contact.
- "Recent", "no recent activity" and "most" are stated limits (30 days, 90 days, 80%), not
  facts of the data, and the owner may move them.
- Monetary is spend in the window and includes the extreme spenders; section 2 shows their
  share per segment so it can be set aside.
