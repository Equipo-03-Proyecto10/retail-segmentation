# ADR-0063 — Segments change on events and on a schedule, scored against the published version

**Status:** Accepted (2026-10-31)
**Owner:** Marcelo
**Issue:** #775
**Supersedes:** in part, [ADR-0017](0017-segment-assignment-history-replaces-the-mutable-current-segment.md) (a run scores every customer) and [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (one synchronous cross-boundary call)
**Superseded by:** —

---

## Context

The instructor's retrospective says a platform is not dynamic only because an
administrator can press a button that recalculates everything, and asks the project
to define triggers, windows, periodicity, incremental update, the model version and
the treatment of late, duplicate and voided sales. One capability states it exactly:
recording a sale must not recalculate everyone, and a total verification must stay
possible.

Today the button is the only way a segment changes (RN-05, E-10), and four facts of
the code make "just update that customer" wrong:

1. **Time moves segments.** `web/db/segments.py` measures the window and recency from
   `now()`, so a customer's R and window change with no sale at all. An event-only
   update drifts from any recalculation.
2. **Scores are relative.** Quintiles are computed over the population (RN-51), and
   K-means normalizes min-max across the run (ADR-0018). Recomputed naively, one sale
   could move every customer's score.
3. **A run is assumed to be everyone.** `UNIQUE (run_id, customer_id)`,
   `segmentation_run.customer_count` and the dashboard's snapshot of one run (RN-41)
   all assume it, and `segmentation_dashboard`, `segments` and `model_comparison` read
   the latest run. A one-customer run would become that snapshot.
4. **Delivery is at least once.** Streams replay and can arrive out of order.

The judgement call is that frozen boundaries age: the population moves while the
version stands still. Drift monitoring exists to say when that matters (F23-07), and
publishing a new version is the answer.

## Decision

An event is a trigger, not a delta: rfm-engine recomputes the affected customer from
the net purchases under the published version, so a replay or a reordering cannot
change a result. It scores that customer against the boundaries or centroids frozen in
the version, never against the population. A full recalculation stays available and
verifies the outcome.

| Trigger | Source | Who is recomputed |
|---|---|---|
| A sale is recorded | `stream:sales.v1.recorded` | That customer |
| A sale is reversed | `stream:sales.v1.reversed` (ADR-0062) | That customer |
| Time passes | A daily pass, at 02:00 by default | Customers whose `next_change_at` has passed |
| A version is published or rolled back | Its stream (ADR-0064) | Everyone, recorded as a change of version |
| Verification | Weekly by default, and on demand | Everyone, writing no profile |

- **Windows.** The rule set holds the window, 180 days by default as today. A customer's profile is computed at the instant it is processed.
- **Time-driven changes.** Each profile stores `next_change_at`: the first instant its oldest sale leaves the window or its recency crosses a frozen boundary. The daily pass selects on that indexed column, so it reads only the customers who are due.
- **What "equivalent" means.** For every customer, raw R, F and M and the label held incrementally equal those of a recomputation from net purchases, with the same version at the same instant. Refitting boundaries is not a verification: it produces a new version.
- **Recording a change.** `segmentation_run` gains `scope`, `FULL` or `INCREMENTAL`. Each batch of changes is one `INCREMENTAL` run, whose `customer_count` is the customers it changed, so `UNIQUE (run_id, customer_id)` holds. A customer gets a history row only when their label or unassigned state changes. Readers of "the latest run" select `FULL`, and open rows stay the current truth, so the web and the services agree on a customer's segment (F22-17).
- **Cause.** Each history row records why it exists: `SALE`, `REVERSAL`, `AGEING`, `VERSION_CHANGE` or `FULL_RUN`.
- **The writer.** rfm-engine calls `api_segments.apply_assignment_v1` in the same transaction as the profile, a third synchronous write across a boundary, for the reason the first has: the two must commit together. The function is owned by the segments boundary, which keeps its one rule, and enforces it (RN-20, RN-21).
- **RN-05 narrows.** Only the administrator starts a full run. Automatic changes are attributed to the system, with `executed_by` empty. E-10 changes: the segmentation tables gain a second writer.
- **Sales.** Late, duplicate and voided sales follow ADR-0062. Consent follows F21-15's record.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| A nightly full recalculation | It is simple and it is the button on a timer. Every sale waits for the night, and every customer is written each time |
| Apply each event as a delta to R, F and M | It is cheaper per event, but replays, disorder and reversals each need compensation, and any error accumulates forever |
| Recompute quintiles on every change | One sale can move everyone, so segments flicker, and no incremental result could equal a recalculation |
| Hold incremental changes apart and merge them at the next full run | The web and the clients would disagree between runs, which is the parity failure the instructor raised |
| Rescan every customer in the daily pass | It works at this size, but it writes the cost into the design that the capability exists to avoid, and `next_change_at` is one indexed column |

## Consequences

**What this makes easy.** The answer to "when and how does a segment update" is one
table. A sale's effect can be traced to a row with a cause and a version. A wrong
profile is repaired by recomputing it, with no history of deltas to untangle.

**What this makes hard.** Frozen boundaries drift from the population until a new
version is published. `next_change_at` must be correct or a customer goes stale, which
F22-08's verification would catch. The web gains a column to filter on in its three
latest-run reads, and `INCREMENTAL` runs will far outnumber full ones in run history.

**What must now be true elsewhere.**
- F22-03 and F22-14 publish these in contracts, and `api_segments.apply_assignment_v1` joins E-05's list.
- F22-05 to F22-08 implement the triggers, F22-15 the writer and F22-17 the web's reads.
- `docs/business-rules.md` amends RN-05, and `docs/services/README.md` E-10.
- A published version must carry frozen boundaries or centroids (ADR-0064).

## Compliance

F22-08 reports zero differences between incremental state and a full recomputation,
over a scripted history of sales, reversals, late sales and redeliveries. A test proves
one sale writes one customer's profile. A test applies the same events twice and out
of order, and gets the same result. A test on the web proves a one-customer
`INCREMENTAL` run does not become the dashboard's snapshot.
