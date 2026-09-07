# F3-10 — The segment recalculation

Evidence for RF-12 and RN-21, and for the demonstration list's **"ejecución de
un proceso principal"**. This is the smallest thing that shows what MOSAIQ is
for: sales in, a segment per customer out.

## How this run was produced

The application from this branch against the seeded Compose database — 30
customers, 300 transactions spread over 180 days, 30 segment rules.

```bash
docker compose up -d db
DATABASE_URL=postgresql://retail_app:retail_app@127.0.0.1:5432/retail python -m web.app
```

Four runs, in order, with the audit log counted before and after each.

## What four runs did

| Run | Window | Processed | Assigned | Unmatched | Changed | Cleared | New audit entries | Time |
|---|---|---|---|---|---|---|---|---|
| 1 — first run | 180 | 30 | 13 | 17 | 30 | 0 | 30 | 0.031 s |
| 2 — again, no new sales | 180 | 30 | 13 | 17 | **0** | **0** | **0** | 0.004 s |
| 3 — a narrow window | 7 | 7 | 5 | 2 | 5 | 10 | 15 | 0.007 s |
| 4 — back to the full window | 180 | 30 | 13 | 17 | 15 | 0 | 15 | 0.009 s |

Every acceptance criterion is one of those rows:

- **Run 2 is the criterion that matters most.** The same sales produced the
  same assignment, so nothing was written and the audit log grew by nothing.
  That is not luck: every `ntile` window is ordered by `customer_id` after its
  measure, and a triple matching several rules always takes the lowest
  `segment_id`, so two runs cannot disagree. The `IS DISTINCT FROM` guard then
  keeps the `UPDATE` — and the trigger behind it — from firing at all.
- **Run 3 is RN-21.** Ten customers had no sales in a seven-day window and were
  cleared to `NULL` rather than left holding a stale segment. An empty segment
  is information; a wrong one is not.
- **New audit entries equal changed customers, every time**: 30, 0, 15, 15
  against 30, 0, 15, 15 changed. One entry per customer whose segment actually
  changed, and none for the others.
- **The entries are attributed.** The newest read
  `UPDATE by MOSAIQ Administrator: segment None -> 1`, because F4-01 names the
  acting user on the connection the triggers fire under.

## Why 17 of 30 customers match no rule

This is the seed's rule data, not the process, and it is worth stating plainly
because a reader seeing 17 unassigned customers will otherwise assume something
is broken:

```
distinct band combinations across all 30 rules: 3
m band range across every rule: (3, 5)
```

`02_seed_30_per_table.sql` generates the bands from `n` by a formula whose
period is three, so the thirty rules describe only three distinct band
combinations — and every one of them requires `m >= 3`. Two quintiles of the
customer base therefore cannot match any rule that exists, whatever their
recency or frequency. Assignment lands on two segments, 1 and 3, for the same
reason:

```
assignment spread: segment 1 -> 7 customers, segment 3 -> 6, unassigned -> 17
```

The process is doing what it is asked to. The rule set is what is degenerate,
and giving the demonstration a rule set that covers the score space is a change
to business data — it belongs to whoever owns the segment definitions, not to
this story.

## Reading two criteria that conflict

The issue asks for both of these:

> every customer with at least one transaction in the window is assigned a
> segment

> a customer whose triple matches no rule … is left unassigned and the run
> reports how many were unmatched

They cannot both hold when the rule set has gaps. The second is the more
specific and explicitly contemplates the case, so it governs: every customer
with sales in the window is **scored and accounted for** — 30 processed, 13
assigned plus 17 unmatched — and the ones no rule covers are left unassigned
and counted on the page.

## Only the administrator, live

```
  ADMIN      GET 200  POST 200
  ANALYST    GET 403  POST 403
  AUDITOR    GET 403  POST 403
  CUSTOMER   GET 403  POST 403
```

RN-05: a run rewrites a column on every customer, so an analyst who could
trigger it could change what every report says. The auditor reads the trail the
run leaves and cannot cause one. The refusal is the authorization middleware's,
before the view runs.

## What the page reports

```
Result
  30    Customers processed
  13    Segments assigned
  17    Matched no rule
  0.01s Time taken

  Window            180 days ending today
  Segments changed  0
  Segments cleared  0 — customers with no sales in the window

Nothing changed, so nothing was written and the audit log has no new entries.
Running again over the same sales always gives this result.
```

The window is per-run rather than per-deployment, which is what "configurable"
has to mean for a process somebody runs and then reads the result of. A window
that is not a usable number of days is refused with `400` and a sentence —
`window=0` answers *"The window must be between 1 and 3650 days."* — rather
than being silently coerced.

| | |
|---|---|
| 1440 px | [`f3-10-segment-run-1440.png`](f3-10-segment-run-1440.png) |
| 375 px | [`f3-10-segment-run-375.png`](f3-10-segment-run-375.png) |

## What this deliberately is not

`docs/roadmap.md` defers RFM computation, and this story brings forward its
smallest slice: quintile scoring over a window, matched against the
`segment_rule` bands already in the schema. Not k-means, not segment history,
not migration reporting, no dashboard. It writes `customer.current_segment_id`,
which [ADR-0004](../adr/0004-model-ahead-of-the-deferred-segmentation-modules.md)
records as a column the segment-history module will replace with an assignment
table, and it adds nothing else that depends on that column.
