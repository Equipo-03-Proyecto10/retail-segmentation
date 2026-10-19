# F7-06 and F7-07 — The migration explanation and the customer segment timeline

Evidence for F7-06 (#208) and F7-07 (#337), both closed with no evidence file
(the later linking and plain-language work on the same pages is in
[F7-08](f7-08-migration-explanation-links.md)). F7-06 explains why one customer
moved, or did not move, between two runs from the values and scores each run
stored. F7-07 puts a customer's whole segment history on their page: every
assignment, the assignment open on a date, recency in days, the date a label was
entered, and the sales that entered and left the calculation between two runs.

No schema change, nothing here writes.

## What was run, and against what

Branch `docs/443-analytics-evidence`, from `develop` at `f2b9c81`; the stack from
`docker compose -p ev443 -f compose.yaml -f compose.proxy.yaml up --build -d`
(PostgreSQL 16.14, NGINX 1.27.5 with the production CSP, Chromium 153 through
Playwright), signed in as the demonstration administrator. Seeded runs 1 to 30
are one RFM_RULES run a day over 180 days, so every customer already has a
history to read. Four runs were added from the **Segment run** page after two
customers were added in `psql`: 31 RFM_RULES over 180 days, 32 K-means (k = 6,
seed 17), 33 RFM_RULES over 14 days, 34 K-means (k = 6, seed 17) again. The
14-day run 33 makes sales age out of the window and 16 customers unassigned,
which is what gives the pages something to explain.

Captures are full-page at 375 px and 1440 px with the classic scrollbar hidden,
so the layout is exactly 375 and 1440 CSS pixels wide; `scrollWidth` equalled
`clientWidth` in every one, and wide tables scroll inside their own wrapper.

## F7-06 — the explanation (#208)

The page is `/migration-explanation/?run_a=…&run_b=…&customer_id=…`, reached from
a customer in a matrix cell or from a change on the timeline.

| Acceptance criterion | Proof |
|---|---|
| A customer who moved: raw recency, frequency, monetary and the R, F, M scores from both runs, with deltas | *Demo Customer 10*, runs 31 → 33, `LOYAL → AT_RISK`, below |
| It names which of the three moved most, from the stored values, not a recomputation | **Partly, see the finding below.** The sentence is on the segment-change page and the customer comparison, not on `/migration-explanation/`. The stored-values part is shown by the code |
| A customer who did not move: deltas shown, and the page says the label did not change | *Demo Customer 1*, runs 31 → 33, `CHAMPION` both times, below |
| A customer unassigned in a run: the page says no scores exist for that run, not zeros | *Demo Customer 14*, unassigned in run 33 both as the later run (31 → 33) and as the earlier run (33 → 34), below |

| Case | 1440 px | 375 px |
|---|---|---|
| Moved: customer 10, 31 → 33 | [capture](f7-06-explanation-moved-1440.png) | [capture](f7-06-explanation-moved-375.png) |
| Label unchanged: customer 1, 31 → 33 | [capture](f7-06-explanation-label-unchanged-1440.png) | [capture](f7-06-explanation-label-unchanged-375.png) |
| Unassigned in the later run: customer 14, 31 → 33 | [capture](f7-06-explanation-unassigned-later-1440.png) | [capture](f7-06-explanation-unassigned-later-375.png) |
| Unassigned in the earlier run: customer 14, 33 → 34 | [capture](f7-06-explanation-unassigned-earlier-1440.png) | [capture](f7-06-explanation-unassigned-earlier-375.png) |
| The segment-change page, with the *moved the most* sentence: customer 10, run 31 | [capture](f7-06-moved-the-most-1440.png) | [capture](f7-06-moved-the-most-375.png) |

What the pages read, copied from the rendered text:

- **Moved** (customer 10): `LOYAL → AT_RISK`. The table `Values and scores`: recency
  `10 days last purchase 2026-10-09` score 4 in run 31 and the same 10 days score 2
  in run 33, *Score change* `-2`; frequency `10 purchases` score 3 then `2
  purchases` score 3, `stable`; monetary `3,782.50 MXN` score 5 then `756.50 MXN`
  score 5, `stable`. The sentences give the raw deltas: `Frequency dropped from 10
  to 2 purchases — changed`, `Monetary fell from 3,782.50 to 756.50 MXN — changed
  (more than 10%)`, and for recency `Its score still went from 4 to 2: other
  customers moved the quintile cut points, not this customer's behaviour`.
- **Label unchanged** (customer 1): `The label did not change: stayed CHAMPION.`
  above the same table, whose deltas are shown: recency 5 → 5 `stable`, frequency
  5 → 5 `stable` (raw 12 → 4 purchases), monetary 3 → 2 `-1`.
- **Unassigned, later run** (customer 14): `LOYAL → Unassigned`. Run 33's cells read
  `Not measured`, its scores `—` (never `0`), and each sentence says `cannot be
  compared: the later run found no purchase in its window`. The earlier case is
  the mirror: `Unassigned → CHAMPION`, run 33's cells `Not measured`, `the earlier
  run found no purchase in its window`; run 34 is a K-means run, so its scores are
  `—` as well and the page's footnote says `A K-means run stores values but no
  scores`.

**Finding on criterion 2.** `most_changed_caption`, the sentence `Frequency moved
the most (score delta -1).`, is rendered by three templates: the segment-change
page, the history report's expanded row and the customer page's comparison. The
explanation page is not one of them:

```text
$ grep -n -i "transaction\|SELECT" web/services/segment_migration.py web/routes/migration_explanation.py
web/services/segment_migration.py:379:    columns, and does not touch transaction or transaction_line to

$ grep -rn "most_changed_caption" web/templates
web/templates/catalog/customer_segment_change.html:111:        {% if explanation.most_changed_caption %}<p class="mq-caption">{{ explanation.most_changed_caption }}</p>{%
web/templates/segment_history_report/index.html:86:                      {% if explanation.most_changed_caption %}
web/templates/segment_history_report/index.html:87:                        <p class="mq-caption">{{ explanation.most_changed_caption }}</p>
web/templates/catalog/customer_detail.html:110:        <p class="mq-caption">Recency is the days from the last purchase to the run that measured it. {% if timeline.compar
```

On the segment-change page for customer 10, run 31 (captured above) it reads
`Frequency moved the most (score delta -1).` On `/migration-explanation/` itself
(`web/templates/migration_explanation/index.html`) it does not appear: that page was
rewritten by #338 (F7-08) into one sentence per measure with a changed/stable
judgement on the raw values, plus the table's *Score change* column, which
shows the largest change without naming it. So the story's second criterion is
met by the change page, the customer comparison and the history report's expanded
row, and not by the explanation page its own title refers to. The pure function
and its tie handling are tested (`tests/test_migration_explanation.py`), but no
route test asserts the sentence on `/migration-explanation/`. Whether the
explanation page should name it again is a decision for the team; nothing was
changed here.

**From the stored values.** `explain_migration` reads the two stored history rows
and nothing else; the grep above (first block) finds `transaction` only in the
docstring that says the function does not touch it, and no `SELECT` in the service or
the route.

## F7-07 — the customer timeline (#337)

On `/catalog/customers/<id>`, with `?as_of=YYYY-MM-DD` for the lookup, and
`/catalog/customers/<id>/segment-changes/<run>` for a change. The customer is
*Demo Customer 10*, who has 34 assignments: a label that changed four times in
the added runs, and a 12-run and an 11-run stretch of one label before them.

| Acceptance criterion | Proof |
|---|---|
| Every assignment with label, run, `valid_from`, `valid_to`, R/F/M scores and raw measures | The timeline in [the capture below](f7-07-customer-timeline-as-of-1440.png): `34 assignments, newest first`, and SQL says 34 |
| A date asked "as of" shows the assignment open then | `?as_of=2026-10-05`, below, against SQL |
| Current and previous: recency in days; previous and current R/F/M side by side | The *Current and previous R/F/M* panel at the top of the same page: `10 days last purchase 2026-10-09`, scores and values for run 33 beside run 34 |
| Two consecutive assignments with different labels: the sales dated between the two runs' windows that entered the calculation, with amount and date | The change pages for runs 34 and 33, below, with the total recomputed from `transaction` |
| An unchanged rerun: *since* is the date the customer entered the label, not the last run's date | The timeline rows for runs 21 to 30 read `Champion since 2026-10-09`, against an islands query in SQL |

| What | 1440 px | 375 px |
|---|---|---|
| The customer page for customer 10, as of 2026-10-05: panel, lookup, timeline | [capture](f7-07-customer-timeline-as-of-1440.png) | [capture](f7-07-customer-timeline-as-of-375.png) |
| Run 33 → 34, label `At risk → Champion`: sales that **entered** run 34's window | [capture](f7-07-sales-entered-1440.png) | [capture](f7-07-sales-entered-375.png) |
| Run 32 → 33, label `Champion → At risk`: sales that **left** run 33's window | [capture](f7-07-sales-left-1440.png) | [capture](f7-07-sales-left-375.png) |

**As of a date**, the text under *Segment as of a date* for six requests, against
SQL (the assignment whose interval contains 00:00 on the next day):

| Request | Result |
|---|---|
| `as_of=2026-10-05` | `Hibernating since 2026-09-27 · run #16 · assignment 2026-10-05 to 2026-10-06` |
| `as_of=2026-10-09` | `Champion since 2026-10-09 · run #20 · assignment 2026-10-09 to 2026-10-10` |
| `as_of=2026-10-19` | `Champion since 2026-10-19 · run #34 · assignment 2026-10-19 to open` |
| `as_of=2027-01-01` | the same open assignment, run 34 |
| `as_of=2026-03-01` | `On 2026-03-01 no segmentation run had scored this customer.` |
| `as_of=bad-date` | HTTP 400, `Enter a date as YYYY-MM-DD between 1900-01-01 and 2100-12-31.`, and the timeline still shown |

**Sales between two runs.** Customer 10 went `At risk → Champion` from run 33
(a 14-day window) to run 34 (180 days). The page lists eight sales that entered:
`TXN-00000160` 2026-05-12 to `TXN-00000220` 2026-09-09, each 378.25 MXN, `8 sales`
`3,026.00`, in `run #34's window (2026-04-22 to 2026-10-19) and not in run #33's
(2026-10-05 to 2026-10-19)`. Going the other way, from run 32 to run 33, the same
eight sales are listed as having *left the window*. The total is the difference
between the two stored spends, and was recomputed from the `transaction` rows:

```text
== customer 10: how many assignments the timeline should list
 assignments 
-------------
          34
(1 row)
== the assignment open at the end of 2026-10-05 (valid_from <= 2026-10-06 00:00 and not yet closed by then)
 run_id | label_code  |          valid_from           |           valid_to            
--------+-------------+-------------------------------+-------------------------------
     16 | HIBERNATING | 2026-10-05 01:32:21.894309+00 | 2026-10-06 01:32:21.894309+00
(1 row)
== when customer 10 entered each label: the first valid_from of each unbroken stretch of the same label (newest stretches first)
 stretch | label_code  | entered_on | first_run | last_run | runs_in_a_row 
---------+-------------+------------+-----------+----------+---------------
       8 | CHAMPION    | 2026-10-19 |        34 |       34 |             1
       7 | AT_RISK     | 2026-10-19 |        33 |       33 |             1
       6 | CHAMPION    | 2026-10-19 |        32 |       32 |             1
       5 | LOYAL       | 2026-10-19 |        31 |       31 |             1
       4 | CHAMPION    | 2026-10-09 |        20 |       30 |            11
       3 | HIBERNATING | 2026-09-27 |         8 |       19 |            12
(6 rows)
== sales in run 34's window and not in run 33's (customer 10), recomputed from the transaction rows
 sales | amount  
-------+---------
     8 | 3026.00
(1 row)
== the stored values behind those sales: spend in run 33 and in run 34, and the difference
 spend_run_34 | spend_run_33 | difference 
--------------+--------------+------------
      3782.50 |       756.50 |    3026.00
(1 row)
```

The page carries one caveat of its own, which the capture shows: `Windows are
rebuilt from each run's date and length. A sale imported after a run but dated
before it appears here as if that run had counted it.`

**Since.** In the SQL above, customer 10 entered `CHAMPION` on 2026-10-09 at run
20 and kept it through run 30, eleven runs in a row, so every one of those rows shows
`Champion since 2026-10-09` (visible in the timeline capture, for the rows of runs
21 to 30, whose own run dates run from 2026-10-10 to 2026-10-19). The label then
changed with runs 31 to 33 and the *since* date moves to 2026-10-19 with each new
stretch.

## Tests

```text
$ pytest tests/test_migration_explanation.py tests/test_migration_explanation_route.py \
         tests/test_customer_timeline.py tests/test_customer_timeline_db.py tests/test_customer_timeline_route.py
114 passed in 1.78s
```

For F7-06: `test_shows_raw_values_and_scores_from_both_runs`,
`test_names_the_component_with_the_largest_score_delta`,
`test_a_tie_names_every_component_that_shares_it`,
`test_an_unchanged_label_still_reports_deltas`,
`test_unassigned_in_the_earlier_run_has_no_before_scores`,
`test_unassigned_in_the_later_run_has_no_after_scores`. For F7-07:
`test_the_page_lists_every_assignment_with_its_run_validity_and_rfm`,
`test_as_of_shows_the_assignment_open_at_the_end_of_that_day`,
`test_recency_is_shown_in_days`, `test_previous_and_current_rfm_sit_side_by_side`,
`test_opening_a_change_lists_the_sales_that_entered_with_amount_and_date`,
`test_since_on_an_unchanged_rerun_is_the_date_the_label_was_entered`.

## What was not run

- The explanation page naming the largest mover: not shown, because it does not
  render it (see the finding).
- The explanation of a customer absent from the earlier run, and the
  rank-only cases, are [F7-08](f7-08-migration-explanation-links.md)'s.
- A change where a sale was imported after a run but dated before it was not
  arranged; the page's own caveat describes it.
- Nothing was run on the Compute Engine instance.

## The script

<details><summary><code>f7_07.sql</code></summary>

```sql
\pset pager off
\echo '== customer 10: how many assignments the timeline should list'
select count(*) as assignments from customer_segment_history where customer_id='00000000-0000-0000-0000-000000000010';
\echo '== the assignment open at the end of 2026-10-05 (valid_from <= 2026-10-06 00:00 and not yet closed by then)'
select run_id, label_code, valid_from, valid_to from customer_segment_history where customer_id='00000000-0000-0000-0000-000000000010' and valid_from <= date '2026-10-06' and (valid_to is null or valid_to > date '2026-10-06');
\echo '== when customer 10 entered each label: the first valid_from of each unbroken stretch of the same label (newest stretches first)'
with h as (select run_id, label_code, valid_from, case when label_code is distinct from lag(label_code) over (order by valid_from) then 1 else 0 end as new_stretch from customer_segment_history where customer_id='00000000-0000-0000-0000-000000000010'),
s as (select *, sum(new_stretch) over (order by valid_from) as stretch from h)
select stretch, label_code, min(valid_from)::date as entered_on, min(run_id) as first_run, max(run_id) as last_run, count(*) as runs_in_a_row from s group by stretch, label_code order by stretch desc limit 6;
\echo '== sales in run 34''s window and not in run 33''s (customer 10), recomputed from the transaction rows'
select count(*) as sales, sum(t.total) as amount from transaction t, segmentation_run a, segmentation_run b
 where a.run_id=33 and b.run_id=34 and t.customer_id='00000000-0000-0000-0000-000000000010'
   and t.occurred_at > b.run_at - make_interval(days=>b.window_days) and t.occurred_at <= b.run_at
   and not (t.occurred_at > a.run_at - make_interval(days=>a.window_days) and t.occurred_at <= a.run_at);
\echo '== the stored values behind those sales: spend in run 33 and in run 34, and the difference'
select (select monetary_total from customer_segment_history where run_id=34 and customer_id='00000000-0000-0000-0000-000000000010') as spend_run_34,
       (select monetary_total from customer_segment_history where run_id=33 and customer_id='00000000-0000-0000-0000-000000000010') as spend_run_33,
       (select monetary_total from customer_segment_history where run_id=34 and customer_id='00000000-0000-0000-0000-000000000010') -
       (select monetary_total from customer_segment_history where run_id=33 and customer_id='00000000-0000-0000-0000-000000000010') as difference;
```

</details>
