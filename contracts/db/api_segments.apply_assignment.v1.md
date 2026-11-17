# `api_segments.apply_assignment_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_segments.apply_assignment_v1` |
| Kind | PostgreSQL function |
| Owning boundary | segments |
| Producer | segments-service |
| Consumer | rfm-engine |
| Signature | `api_segments.apply_assignment_v1(uuid, uuid, varchar, smallint, smallint, smallint, timestamptz, int, numeric, text, bigint, text, int)` |
| regprocedure | `api_segments.apply_assignment_v1(uuid,uuid,character varying,smallint,smallint,smallint,timestamp with time zone,integer,numeric,text,bigint,text,integer)` |
| Result | `text`: `opened`, `changed` or `unchanged` |

It is the operation that applies a customer's segment change (ADR-0063). The
segments boundary owns it and enforces its one rule on every call: a customer
has at most one open assignment (RN-20), and a customer scored with no label is
recorded as the unassigned result and never skipped (RN-21). rfm-engine calls
it in the same transaction as the profile it computed, so the profile and the
assignment commit together or not at all. It is the second write that crosses a
service boundary, after `api_stock.consume_v1` (E-05). It is a function in the
database and never an HTTP call.

The function closes the customer's open row at the instant it opens the new one
and writes the new row, as the web's run does, so history is closed and never
rewritten. It does not recompute anything: the label, scores and raw values are
the caller's result.

## Arguments

| Position | Name | Type | Meaning |
|---:|---|---|---|
| 1 | `p_batch_id` | `uuid` | Names the batch of changes this call belongs to. All calls with one batch id write into one run; the first call creates it. Redelivering the same batch reuses its run. |
| 2 | `p_customer_id` | `uuid` | The customer. |
| 3 | `p_label_code` | `character varying` | The stable label code; `NULL` is the unassigned result (RN-21). |
| 4 | `p_r_score` | `smallint` | Recency score 1 to 5; `NULL` when the label is `NULL`, or for a method that stores no scores. |
| 5 | `p_f_score` | `smallint` | Frequency score 1 to 5; same rule. |
| 6 | `p_m_score` | `smallint` | Monetary score 1 to 5; same rule. |
| 7 | `p_recency_last_purchase_at` | `timestamp with time zone` | The latest net purchase in the window; `NULL` when there is none. |
| 8 | `p_frequency_count` | `integer` | Net purchases in the window; `0` or more. |
| 9 | `p_monetary_total` | `numeric` | The sum of net amounts in the window, stored as `numeric(12,2)`; `0` or more. |
| 10 | `p_cause` | `text` | One of `SALE`, `REVERSAL`, `AGEING`, `VERSION_CHANGE`. `FULL_RUN` is the web's administrator run and is refused here. |
| 11 | `p_model_version_id` | `bigint` | The model version the result was scored under. Required. |
| 12 | `p_method` | `text` | `RFM_RULES` or `KMEANS`. |
| 13 | `p_window_days` | `integer` | The window the result measured, greater than 0. |

Callers cast the nullable and typed arguments:
`SELECT api_segments.apply_assignment_v1($1::uuid, $2::uuid, $3::varchar, ...)`.
An uncast `NULL` finds no function and fails with SQLSTATE 42883.

## What it writes

| Condition | Outcome | Effect |
|---|---|---|
| The customer has no open row | `opened` | Inserts the first open row. |
| The open row holds a different label, or a different unassigned state | `changed` | Closes the open row and inserts a new one. |
| The open row holds the same label, or both are unassigned | `unchanged` | Writes nothing, creates no run and announces nothing. |

A customer gets a history row only when their label or unassigned state changes
(ADR-0063). Scores and raw values on an otherwise identical label do not make a
row, which keeps a refresh from becoming history; the caller keeps them in its own
profile. The run is created, when `opened` or `changed` is the first write of its
batch, with the scope the cause implies and the version of the call:

| Cause | Run scope | Meaning |
|---|---|---|
| `SALE`, `REVERSAL`, `AGEING` | `INCREMENTAL` | An event or the passage of time moved the customer. |
| `VERSION_CHANGE` | `FULL` | A publication or rollback moved the customer (ADR-0064). |

A run's `executed_by` is empty: automatic changes belong to the system (RN-05).
The run records its `method`, `window_days`, `model_version_id` and `batch_id` in
its parameters, and `customer_count` is the number of rows the batch wrote. Calls
of one batch must agree on cause, version, method and window; one that does not is
refused. The new history row carries the run and the cause, `valid_from` set to the
call's transaction time (`now()`), and `valid_to` empty. A concurrent
call for the same customer waits for the first and then compares against what it
wrote; the function serializes them per customer, and a lock key it takes is
registered in `advisory-locks.md` first.

The row's insert raises `stream:segments.v1.assignment_changed` and the run's
insert raises `stream:segments.v1.run_recorded`, both through the capture
triggers, in the same transaction.

## Errors

| SQLSTATE | Constraint name | Meaning | rfm-engine maps it to |
|---|---|---|---|
| 22023 | none | An argument is out of range: a required one is `NULL`, a score is outside 1 to 5, a count or total is negative, `p_window_days` is at most 0, `p_method` or `p_cause` is not listed, or a score is given with no label. Raised before any row is touched. | a defect of the caller; the profile is not written |
| 23514 | `unknown_label` | `p_label_code` is not in the label vocabulary. | `profile_rejected` |
| 23514 | `unknown_customer` | `p_customer_id` is not in `api_customers.customer_ref_v1`. | `profile_rejected` |
| 23514 | `batch_mismatch` | A call of a batch disagrees with the batch's cause, version, method or window. | a defect of the caller |

These are database outcomes. rfm-engine maps them to its own behaviour; the
segments service never answers them over HTTP. A unique violation on the open-row
index is never surfaced: the function waits and re-reads instead.

## Security and use

- SECURITY DEFINER, owned by `definer_segments`, with
  `search_path = public, pg_temp`. Its owner holds the privileges on the segmentation
  tables that the caller does not.
- EXECUTE is granted to `svc_rfm` only. `contracts/db/grants.csv` is not touched
  by this contract; the row arrives with the function, in the story that creates it
  (F22-15).
- A caller invokes it once per customer, inside the transaction that writes that
  customer's profile, and may call it for many customers in one transaction with one
  batch id. Calling twice with the same arguments returns `unchanged` the second time,
  so a redelivered event is harmless (RN-65).
- The `models.run_version` link is model-registry's. Segments reads the version of a
  run through the view that model-registry publishes (F22-10); this function records
  `p_model_version_id` in the run's parameters so that the link is never absent.

## Compatibility

Changing the signature, the result, an error code or constraint name, the rule
that decides `opened`, `changed` and `unchanged`, or the atomic behavior is
breaking. A breaking change ships `api_segments.apply_assignment_v2` beside
`apply_assignment_v1`, with its own `.v2.md` contract; v1 is removed only after
rfm-engine has moved.
