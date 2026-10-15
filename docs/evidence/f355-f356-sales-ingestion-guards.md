# #355 and #356 — sales ingestion guards

The regression suite exercises the two defects found in the 2026-09-29 QA
round. The checks are intentionally split at the transport boundary: CSV rows
with an extra field are rejected by the adapter, while business rules are
rejected by the transport-free ingestion service.

```text
$ .venv/bin/pytest -q tests/test_ingestion_service.py tests/test_sales_csv.py \
    tests/test_sales_db.py tests/test_segments_pipeline_db.py
........................................................................ [100%]
73 passed
```

`get_transaction_by_source_id` now uses `SELECT ... FOR UPDATE`. The lock is
acquired before a line insert and total recomputation, so PostgreSQL's
READ-COMMITTED statement snapshot includes a line committed by a concurrent
loader before the recompute statement runs.

The service refuses future dates, extra CSV columns, prices at or below zero,
prices with more than two decimal places, sales before customer registration,
inactive products and formula-like source transaction ids. RFM reads include a
`now()` upper bound, so rows beyond the run's current time do not enter
recency scoring.
