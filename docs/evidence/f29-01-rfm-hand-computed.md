# F29-01 (#886): RFM validated against values computed by hand

A small population was worked out on paper first: each customer's purchases, the
net of the cancellation and the return, and the recency, frequency and spend with
their quintile scores. Then the web and rfm-engine were run on the same
population and each was compared with the paper, customer by customer. The
fixture and both tests are committed, so the comparison reruns on every push.

Governing records: [RN-21](../business-rules.md) (a customer with no sales is
unassigned), [RN-34](../business-rules.md) (the window, closed at both ends),
[RN-51](../business-rules.md) (a quintile boundary per distinct value, so ties
share a score), [ADR-0062](../adr/0062-a-reversal-is-a-recorded-fact-and-every-reader-counts-net-units.md)
(net sales are the one definition both systems read) and
[ADR-0045](../adr/0045-services-never-import-the-web-system.md)
(services never import the web).

## The criteria, and where each is answered

| Criterion of #886 | Answered by |
|---|---|
| Hand-computed R, F and M match the automatic values for every customer | [The worksheet](#2-the-worksheet) and [the comparison](#5-the-two-systems-beside-the-hand-values): 14 customers, no difference |
| The fixture covers window edges, time zones, cents, cancellations, partial returns, a customer with no purchases and ties | [What the fixture covers](#1-what-the-fixture-covers) names the customer for each |
| The web and rfm-engine are each compared with the hand values | `tests/test_rfm_hand_computed.py` (web) and `services/rfm-engine/tests/test_hand_computed.py` (rfm-engine); `tests/test_rfm_hand_fixture_copies.py` fails when their two copies of the fixture differ |

## 1. What the fixture covers

`tests/fixtures/rfm-hand/fixture.json`, with a byte-identical copy in
`services/rfm-engine/tests/fixtures/rfm-hand/` (a service may not read outside its
own directory). Every purchase is written as a number of **calendar days before
the instant, at the same local clock time, plus minutes**, so the same fixture can
be read at the web's own `now()` and at a fixed instant.

- **Window.** 60 days, closed at both ends (RN-34).
- **Time zone.** `America/New_York`, pinned for the session in both tests. The
  fixed instant of the engine run is **2026-04-20 00:10 EDT**. The clocks went
  forward on 2026-03-08, so the window's first instant, 60 calendar days back, is
  **2026-02-19 00:10 EST**, and anything bought before 2026-03-08 sits across a
  clock change from the instant.
- **Population.** Fourteen customers; eleven have a purchase in the window.

| Case the story names | Customer |
|---|---|
| A purchase on the window's first instant: counts | `edge_in` |
| A purchase one minute before it: does not count | `edge_out` |
| A purchase on the last instant (the instant itself): counts | `upper_edge` |
| A time-zone case: a purchase 40 minutes before the calendar edge, which a window of 24-hour days (starting 23:10 EST on 02-18) would include | `dst_trap` |
| A time-zone case: R across a clock change | `dst_boundary` |
| Cents | `big` (450.00 + 99.99 + 5.01), `partial`, `heavy` (8 × 9.99) |
| A cancellation | `cancel_one` (the newest sale cancelled), `cancelled_all` (the only sale cancelled) |
| A partial return | `partial` (2 of 5 units returned) |
| No purchase at all, and no purchase in the window | `none`; `edge_out`, `cancelled_all` |
| Ties (RN-51) | F: `edge_in`, `dst_trap`, `cancel_one`, `tie_b` (all 1) and four customers at 2. M: `tie_a` and `tie_b` (80.00), `cancel_one` and `frequent` (60.00). Last purchase: `tie_a` and `tie_b` (same instant) |

## 2. The worksheet

Purchases are local time. "Net" is what is left after the reversal (ADR-0062): a
cancelled sale has no net unit and is not a purchase; a returned unit leaves the
sale a purchase and takes its price off the total.

| Customer | Purchases (local) | In window? | Net |
|---|---|---|---|
| `edge_in` | 2026-02-19 00:10 EST, 1 × 25.50 | on the first instant: yes | F 1, M 25.50 |
| `edge_out` | 2026-02-19 00:09 EST, 1 × 500.00 | one minute early: no | no purchase |
| `dst_trap` | 2026-02-18 23:30 EST, 1 × 99.99; 2026-02-27 00:10 EST, 1 × 10.00 | first: no (before the calendar edge); second: yes | F 1, M 10.00 |
| `dst_boundary` | 2026-02-24 00:10 EST, 1 × 40.00; 2026-03-02 00:10 EST, 1 × 35.25 | both | F 2, M 75.25 |
| `upper_edge` | 2026-04-15 00:10 EDT, 1 × 20.00; 2026-04-20 00:10 EDT, 1 × 10.00 | both (the second on the instant) | F 2, M 30.00 |
| `cancel_one` | 2026-03-31, 2 × 30.00 = 60.00; 2026-04-14, 1 × 80.00 **cancelled** | both, the second nets to zero | F 1, M 60.00 |
| `partial` | 2026-04-05, 2 × 7.25 = 14.50; 2026-04-12, 5 × 19.99 = 99.95, **2 returned** → 3 × 19.99 = 59.97 | both | F 2, M 14.50 + 59.97 = 74.47 |
| `tie_a` | 2026-04-10, 1 × 40.00; 2026-04-17, 1 × 40.00 | both | F 2, M 80.00 |
| `tie_b` | 2026-04-17, 2 × 40.00 = 80.00 | yes | F 1, M 80.00 |
| `frequent` | 03-21, 03-27, 04-02, 04-08, 04-19, 1 × 12.00 each | all | F 5, M 60.00 |
| `big` | 2026-03-11, 3 × 150.00 = 450.00; 04-07, 99.99; 04-18, 5.01 | all | F 3, M 555.00 |
| `heavy` | 03-23, 03-25, 03-29, 04-01, 04-04, 04-11, 04-13, 04-16, 1 × 9.99 each | all | F 8, M 79.92 |
| `cancelled_all` | 2026-04-16, 3 × 15.00 **cancelled** | nets to zero | no purchase |
| `none` | none | | no purchase |

The three customers with no purchase are **unassigned** (RN-21): present, with
nothing, and not scored zero.

### Recency

R is the time since the customer's last net purchase. The web keeps it as the
timestamp of that purchase (`recency_last_purchase_at`) and scores by ordering the
distinct timestamps; rfm-engine keeps it as whole days at the instant and scores
against boundaries.

| Customer | Last net purchase | Days back |
|---|---|---|
| `upper_edge` | 04-20 | 0 |
| `frequent` | 04-19 | 1 |
| `big` | 04-18 | 2 |
| `tie_a`, `tie_b` | 04-17 | 3 (the same instant) |
| `heavy` | 04-16 | 4 |
| `partial` | 04-12 | 8 |
| `cancel_one` | 03-31 | 20 (the cancelled sale is not a purchase) |
| `dst_boundary` | 03-02 | 49 |
| `dst_trap` | 02-27 | 52 (the earlier purchase is outside the window) |
| `edge_in` | 02-19 | 60 |

### Quintile scores (RN-51)

Each measure's boundaries are cut once per **distinct** value, best first, into
five groups; with `D` distinct values the first `D mod 5` groups take one extra.
Score 5 is the best group. Customers on the same value share the score.

| Measure | Distinct values, best first | Groups → score |
|---|---|---|
| R (10 distinct) | 0, 1, 2, 3, 4, 8, 20, 49, 52, 60 | {0, 1} → 5; {2, 3} → 4; {4, 8} → 3; {20, 49} → 2; {52, 60} → 1 |
| F (5 distinct) | 8, 5, 3, 2, 1 | one value each: 8 → 5, 5 → 4, 3 → 3, 2 → 2, 1 → 1 |
| M (9 distinct) | 555.00, 80.00, 79.92, 75.25, 74.47, 60.00, 30.00, 25.50, 10.00 | sizes 2, 2, 2, 2, 1: {555.00, 80.00} → 5; {79.92, 75.25} → 4; {74.47, 60.00} → 3; {30.00, 25.50} → 2; {10.00} → 1 |

Had the quintiles been cut over rows, as the web did before #352, the four
customers on F = 1 and the four on F = 2 would have been spread across scores by
their position, and the customer on F = 3 would have scored 5. The hand scores
are the ones a business user can defend.

**The boundaries rfm-engine is given.** The engine does not look at the
population: its rule set freezes the boundaries. The hand quintiles above give
them (the largest R that scores 5, 4, 3, 2; the smallest F and M that score
5, 4, 3, 2):

- recency `[1, 3, 8, 49]`
- frequency `[8, 5, 3, 2]`
- monetary `[80.00, 75.25, 60.00, 25.50]`

The cents are what separate `heavy` (79.92, score 4) from the 80.00 tie (score 5),
and `partial` (74.47, score 3) from 75.25 (score 4). Without the return, `partial`
would be 114.45 and score 5.

### The clock change

`dst_boundary`'s last purchase is 2026-03-02 00:10 EST and the instant is
2026-04-20 00:10 EDT. The clocks went forward in between, so the elapsed time is
49 × 24 − 1 = **1175 hours**: 48 days and 23 hours. The hand value is **49**, the
calendar days, and it is the largest R that scores 2. Both systems agree on it,
but not for the same reason, and one of the reasons is fragile (see
[Findings](#6-findings)).

## 3. How the web was run

`tests/test_rfm_hand_computed.py`, against a throwaway database loaded with the
three scripts (`MOSAIQ_TEST_OWNER_URL`). The web reads `now()`, which is the
transaction's start and cannot be set, so the fixture is inserted relative to it
and scored in the same transaction, which is rolled back. The seed's own sales are
removed inside that transaction, because the quintiles are drawn from the
population and a seeded sale in the window would move them. Reversals are
written as `svc_sales` writes them. The query run is `web.db.segments`'s
`score_rfm_rules` and `read_rfm_inputs`, unchanged, as `retail_app`.

## 4. How rfm-engine was run

`services/rfm-engine/tests/test_hand_computed.py`, against the same database. It
publishes a rule set with the boundaries above, creates the customers and sales
(reversals through the `Shop` helper, as `svc_sales` writes them), and calls
`recompute.customer` as `svc_rfm` for each customer at **2026-04-20 00:10 EDT**,
with the session in `America/New_York`. The purchases are placed with Python's
calendar arithmetic, independent of the database's.

## 5. The two systems beside the hand values

Observed values (the tests assert them; the table is from a run of each).

| Customer | Hand: R days · F · M · R F M | Web: F · M · R F M | rfm-engine: R days · F · M · R F M |
|---|---|---|---|
| `edge_in` | 60 · 1 · 25.50 · 1 1 2 | 1 · 25.50 · 1 1 2 | 60 · 1 · 25.50 · 1 1 2 |
| `edge_out` | unassigned | all absent | unassigned · 0 · 0.00 |
| `dst_trap` | 52 · 1 · 10.00 · 1 1 1 | 1 · 10.00 · 1 1 1 | 52 · 1 · 10.00 · 1 1 1 |
| `dst_boundary` | 49 · 2 · 75.25 · 2 2 4 | 2 · 75.25 · 2 2 4 | 49 · 2 · 75.25 · 2 2 4 |
| `upper_edge` | 0 · 2 · 30.00 · 5 2 2 | 2 · 30.00 · 5 2 2 | 0 · 2 · 30.00 · 5 2 2 |
| `cancel_one` | 20 · 1 · 60.00 · 2 1 3 | 1 · 60.00 · 2 1 3 | 20 · 1 · 60.00 · 2 1 3 |
| `partial` | 8 · 2 · 74.47 · 3 2 3 | 2 · 74.47 · 3 2 3 | 8 · 2 · 74.47 · 3 2 3 |
| `tie_a` | 3 · 2 · 80.00 · 4 2 5 | 2 · 80.00 · 4 2 5 | 3 · 2 · 80.00 · 4 2 5 |
| `tie_b` | 3 · 1 · 80.00 · 4 1 5 | 1 · 80.00 · 4 1 5 | 3 · 1 · 80.00 · 4 1 5 |
| `frequent` | 1 · 5 · 60.00 · 5 4 3 | 5 · 60.00 · 5 4 3 | 1 · 5 · 60.00 · 5 4 3 |
| `big` | 2 · 3 · 555.00 · 4 3 5 | 3 · 555.00 · 4 3 5 | 2 · 3 · 555.00 · 4 3 5 |
| `heavy` | 4 · 8 · 79.92 · 3 5 4 | 8 · 79.92 · 3 5 4 | 4 · 8 · 79.92 · 3 5 4 |
| `cancelled_all` | unassigned | all absent | unassigned · 0 · 0.00 |
| `none` | unassigned | all absent | unassigned · 0 · 0.00 |

The web also returns the last purchase as a timestamp; the test compares each
with the hand instant (for example `edge_in`'s is exactly the window's first
instant), which is the web's R. Customers with no purchase come back from the web
with every value null, and from rfm-engine as `unassigned`, F 0, M 0.00 and no
R or scores.

To see the test notice a wrong value, the fixture was changed on a copy: one
returned unit instead of two for `partial`, and the cancellation of `cancel_one`
removed. Each made the web test fail naming that customer.

## 6. Findings

1. **R across a clock change depends on how Python subtracts two instants.**
   `profile.build` computes `int((as_of - last_at) // ONE_DAY)`. When both
   datetimes carry the same zone object, which is what psycopg returns for a
   connection with one session zone, Python subtracts the wall clocks and the
   offset change is ignored: 49 days for `dst_boundary`. When `as_of` carries
   another zone (UTC, say) the same instants subtract as elapsed time: 1175 hours
   is **48** days. Run on the fixture's two instants:

   ```text
   same zone: 49   as_of in UTC: 48
   ```

   The contract says only "whole days from the latest net purchase to
   `computed_at`". In production the database zone has no clock change, so the two
   readings never differ today; they would in a zone that has one. Nothing was
   changed here: which reading is meant is a decision for an ADR, and the test
   pins the first reading, which is the one the service takes with its own
   connection.
2. **The window is calendar days in the session zone, in both systems.** Both use
   `make_interval(days => …)`, so the first instant of the window is 60 calendar
   days back, not 1440 hours. `dst_trap` is the case that would show a 24-hour
   window: its 99.99 purchase is inside 1440 hours and outside 60 calendar days,
   and both systems left it out.
3. **Labels are not compared.** The story asks for R, F and M. The engine's rule
   set here is four boxes that tile the 125 triples, because the labels are not
   what is being checked; the web's labels come from the seeded bands. Label
   parity under the seeded rule set is `services/rfm-engine/tests/test_parity_with_the_web.py`.
4. **One assumption of the web run.** The web test removes the seeded sales inside
   its rolled-back transaction with triggers off, which needs the superuser that
   `MOSAIQ_TEST_OWNER_URL` names in CI and in the local setup.

## 7. Rerun

```sh
python -m pytest -q tests/test_rfm_hand_computed.py tests/test_rfm_hand_fixture_copies.py
cd services/rfm-engine && uv run --frozen pytest -q tests/test_hand_computed.py
```

Both need a database loaded with the three scripts (`MOSAIQ_TEST_OWNER_URL` for the
first, `TEST_DATABASE_URL` for the second).
