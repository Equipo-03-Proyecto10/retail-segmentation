# ADR-0087 — An incremental change is applied to the segment history in the profile's transaction, under the published version, and a withdrawn PROFILING is the unassigned result

**Status:** Accepted (2026-11-21)
**Owner:** Estefanía
**Issue:** #811
**Supersedes:** [ADR-0066](0066-consent-is-an-append-only-record-by-purpose-and-anonymization-replaces-deletion.md), in part: for rfm-engine's `PROFILING`, a missing row is segmented and an unreadable view refuses the entry
**Superseded by:** —

---

## Context

[ADR-0063](0063-segments-change-on-events-and-on-a-schedule-and-are-scored-against-the-published-version.md)
decides that a customer's segment changes with their behaviour, scored against the published
version, by a function `api_segments.apply_assignment_v1` that rfm-engine calls in the same
transaction as the profile. The contract of that function was merged before it existed
(`contracts/db/api_segments.apply_assignment.v1.md`). Implementing it leaves four things
undecided, and a fifth was not foreseen.

1. **Which rule set a change is scored under.** rfm-engine scored under `RFM_RULE_SET_ID`
   "until the published version names one". The version the seed publishes was derived from a
   web run and names no rule set (`api_models.published_version_v1.rule_set_id` is `NULL`), and its
   window (90 days) is not the seeded rule set's (180).
2. **How a customer who withdrew `PROFILING` appears in the history.** [ADR-0066](0066-consent-is-an-append-only-record-by-purpose-and-anonymization-replaces-deletion.md)
   section 2 says the open assignment is closed and the customer is reported unassigned with
   reason `PROFILING_WITHDRAWN`, "as RN-21 reports unassigned states as they are". The contract
   knows four causes and no reason.
3. **What a customer with no decision is.** ADR-0066 says a purpose with no row is not
   granted, and the default for customers registered before consent existed is `GRANTED` for
   `PROFILING`. Nothing that registers a customer records a decision yet.
4. **How a run is told from a whole run.** Every history row has a run, and an incremental
   change is not a re-scoring of the population.
5. **Who announces the change.** `stream:segments.v1.assignment_changed` was contracted with
   its consumers and no producer.

## Decision

`apply_assignment_v1` closes the open row and opens the next at one instant in one transaction. It
takes the web's run lock `285001` shared, so it waits for a web run (which closes every open row at
the instant it read after that lock) and a web run waits for it, and it is serialized per customer
on advisory lock `300001`; a call that finds the same label (or the same unassigned reason) writes
nothing and creates no run. Each batch of changes is one run, found again
by its `batch_id`, with `scope = INCREMENTAL` (`FULL` for a `VERSION_CHANGE`), no `executed_by`,
and the version, the rule set it was scored under, method, window and cause in its parameters; the
calls of a batch that write agree on all of them, and the caller names a batch for each cause and
version an event or a pass records under, so a withdrawn customer inside a pass of ageing ones, or a
pass resumed after a publication, is a run of its own and refuses no one. Each history row carries
its `cause`.
An `AFTER INSERT` trigger on `customer_segment_history`, which any writer fires, announces the
customer on `stream:segments.v1.assignment_changed` when the row is a customer's first, or its label
or its unassigned reason differs from the row before it. `api_segments.current_label_v1` reports the
reason as `unassigned_reason`.

rfm-engine scores under the rule set the published version names, and under `RFM_RULE_SET_ID` when
the version names none, and records on the run the rule set it used. It reads the version from
`api_models.published_version_v1` and never from the registry's table. One version is published at a
time, whatever its method. Under a K-means version, which labels by centroids rfm-engine does not
hold, the profile is still written under `RFM_RULE_SET_ID` and only a withdrawal is applied, under
that version and its window. With no version published the profile is written and nothing is
applied. No entry is refused for the version, so none is dropped after five deliveries.

A customer whose current `PROFILING` decision is `WITHDRAWN` is not measured: rfm-engine writes
the unassigned profile and applies the result with cause `CONSENT_WITHDRAWN`, whatever event moved
them. The cause is added to the vocabulary, with `CONSENT_GRANTED` for the recomputation that
follows a consent event when the customer's `PROFILING` is granted; a consent event about another
purpose that finds the customer overdue records `AGEING`. The unassigned reason is not a column: a `NULL` label under cause
`CONSENT_WITHDRAWN` is `PROFILING_WITHDRAWN`, and any other `NULL` label is no purchases in the
window, so the reason is a function of two columns the row already has. Moving between the two
unassigned reasons is a change of state, and a history row. A consent event on
`stream:preferences.v1.consent_changed` makes rfm-engine re-read the decision and recompute that
customer, so a withdrawal does not wait for the next sale.

Only an explicit `WITHDRAWN` excludes. A customer with no `PROFILING` decision is segmented, and a
consent view rfm-engine cannot read refuses the entry, which is redelivered. This replaces, for
rfm-engine's `PROFILING` only, ADR-0066's reading of a missing row and of an unreadable view as not
granted; the rest of ADR-0066 stays binding, and `contracts/db/api_preferences.consent_state.v1.md`
says so.

The origin of a run's window (ADR-0052) is read by `fn_segmentation_window_origin`, which runs as
`definer_guard` (the E-13 pattern), so `definer_segments` holds no privilege on the sales table and
no boundary reads another's table to record a run.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Read a missing `PROFILING` decision as not granted, as the contract of `consent_state_v1` says every consumer does | Registration records no consent, so every customer registered after the migration would be unassigned and never segmented, and the platform's central rule would stop for them with no refusal anyone made. ADR-0066's table speaks of a withdrawal, which is an explicit decision. Recommendations reads absence as not granted for `PERSONALIZATION` because an offer is the intrusive act; a label computed from sales is not |
| Add `unassigned_reason` to the history | It is determined by `cause` and `label_code` already: a second column would be a fact stated twice, and could contradict the first. The data model keeps the history in 4NF |
| Treat an unreadable consent view as withdrawn, as ADR-0066 says | Closing every open assignment because a grant was missing after a deploy destroys the present for a configuration fault. rfm-engine refuses the entry instead: nothing is profiled and the entry is redelivered, which is what fail closed means for the act it guards |
| Keep scoring under `RFM_RULE_SET_ID` alone | The version recorded on the run would be one the scoring did not use whenever a publication names a rule set, and a publication would change nothing |
| Keep refusing an entry while no rules version is published | One version is published at a time, so publishing a K-means version would stop every profile, drop each entry after five deliveries, and leave a withdrawal of `PROFILING` unapplied |
| Grant `definer_segments` the two columns of the sales the origin guard reads | A boundary role would read another boundary's table, which AGENTS.md forbids; the guard's own function can hold that read instead |
| One run per call | A pass over thousands of customers would write thousands of runs, and "the latest run" would name one customer |
| Write the history from rfm-engine with its own grants | RN-20 and RN-21 would be enforced by the caller. ADR-0034 gives the segments boundary the write |

## Consequences

**What this makes easy.** A change is one function call whose result is `opened`, `changed` or
`unchanged`, and a redelivered event costs nothing. Every row says why it exists and, through its
run, under which version.

**What this makes hard.** Incremental runs are runs: anything that reads "the latest run" to mean a
whole population must select `scope = FULL`: the segments service's list does, and the web's reports
do not yet (F22-17, #813). Every incremental run also raises `run_recorded`, which segments needs to
move its cache keys and which empties recommendations' whole cache; the per-customer entry is what
lets a consumer keep its cache otherwise. Incremental runs have no `models.run_version` link until
model-registry links runs (it links none today); their version is in their parameters. The segments
API does not yet serve `cause` or the unassigned reason: its `AssignmentCause` lacks the consent
causes, and that contract changes first. A row
written by the function has no `segment_id`, so the web's "browse segment N" does not list it.
A pass resumed after a publication of another rule set keeps the rule set it began under and applies
nothing (a warning says so). A published K-means version cannot be scored by rfm-engine, which has no
centroids.
The web's batch run does not yet skip a customer who withdrew `PROFILING`, which ADR-0066 also asks
of it.

**What must now be true elsewhere.**
- `contracts/db/api_segments.apply_assignment.v1.md` lists `CONSENT_WITHDRAWN` and `CONSENT_GRANTED`.
- `contracts/db/advisory-locks.md` registers `300001` and the shared use of `285001`.
- `contracts/db/api_preferences.consent_state.v1.md` names rfm-engine's reading of `PROFILING`.
- Registration, when it records the three decisions, makes the absence of a `PROFILING` row rare
  and a revision of this record may read it as not granted.
- #813 (F22-17) reads only `FULL` runs.

## Compliance

`services/rfm-engine/tests/test_assignment.py` runs each acceptance criterion of #811 against the
seeded database: the close and open in one transaction with the previous row kept and one open row
even for two concurrent changes, the unchanged result, the outbox row and its rollback, the cause
and version on the run, the unassigned result of RN-21, and the withdrawal and re-grant of
`PROFILING`; a withdrawn customer inside a pass, a batch under another version, a change that waits
for a web run holding `285001`, a K-means or absent version, and `CONSENT_GRANTED` only on a grant. `contracts/db/check_grants.sh` checks the grants of `definer_segments` and
`svc_rfm`.
