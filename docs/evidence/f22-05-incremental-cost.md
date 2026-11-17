# F22-05 (#779): 1,000 sales cost the same with 5,000 more customers

[ADR-0063](../adr/0063-segments-change-on-events-and-on-a-schedule-scored-against-the-published-version.md)
says a recorded sale recomputes the customer it names and no one else. The fifth
acceptance criterion of #779 asks that the measured work for 1,000 sales follows the
sales and not the customer count. This is that measurement.

## How it was measured

`services/rfm-engine/tests/test_incremental_cost.py`, on 2026-11-17, against
PostgreSQL 16.15 built by the three scripts, with the service connected as `svc_rfm`.
It runs the same recomputation the stream consumer runs for an entry
(`recompute.from_sale`), one transaction per sale, and runs twice:

1. **Fewer customers.** 50 buyers each make 20 sales, 1,000 in all. The database holds
   80 customers and, once the phase has run, 50 profiles.
2. **More customers.** 5,000 more customers are added, each with a profile (an
   `unassigned` row). Then 50 other buyers make 20 sales each, 1,000 more. The database
   holds 5,130 customers and 5,100 profiles.

Per phase the test records the rows written to `rfm.customer_profile` (the table's
`n_tup_ins + n_tup_upd` from `pg_stat_user_tables`, flushed with
`pg_stat_force_next_flush()`), the shared buffers the measuring statement touches for
one buyer (`EXPLAIN (ANALYZE, BUFFERS)`), and the time of each recomputation. Each
sale is recomputed at the same instant, so every phase measures the same work.

## Result

| | Fewer customers | More customers |
|---|---:|---:|
| Customers in the database | 80 | 5,130 |
| Profiles before the phase's first sale | 0 | 5,050 |
| Sales recomputed | 1,000 | 1,000 |
| Profile rows written (insert or update) | 1,000 | 1,000 |
| Distinct profiles written | 50 | 50 |
| Shared buffers read by the measuring statement, one buyer | 30 | 38 |
| Median time of one recomputation | 0.92 ms | 0.91 ms |
| 95th percentile | 1.28 ms | 1.31 ms |
| Whole phase | 0.98 s | 0.96 s |

The customers grew 64 times and the work did not: the same 1,000 writes, one per sale
and none to anyone else, the same median time and a measuring statement that reads
eight more pages (an extra level of the customer index, not a scan). Three runs on the
same database gave medians of 0.88 to 0.93 ms in both phases.

## What the test asserts

- Each phase writes at most as many profile rows as it has sales, and the 50 buyers'
  profiles are the ones written.
- The measuring statement's buffers in the larger database are at most 1.25 times the
  smaller's plus 5.
- The median time in the larger database is at most three times the smaller's plus
  5 ms, a bound a scan of 5,000 profiles per event would not meet.
- `test_no_other_profile_is_read_for_writing` (`test_profile_events.py`) is the
  criterion's first half: every other profile row is locked by a second connection
  while one customer is recomputed, and a recomputation that selected, updated or
  locked any of them would wait for the lock and fail on the 500 ms lock timeout.

## Limits

One machine, one PostgreSQL version, a local connection, and 20 sales per customer.
It shows the shape of the cost, not a capacity: the figure to compare is the ratio
between the two phases. `RFM_EVIDENCE_OUT=<file>` makes the test write its figures as
JSON for another run.
