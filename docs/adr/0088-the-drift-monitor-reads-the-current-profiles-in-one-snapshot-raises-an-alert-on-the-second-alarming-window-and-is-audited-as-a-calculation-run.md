# ADR-0088 — The drift monitor reads the current profiles in one snapshot, raises an alert on the second alarming window, and is audited as a calculation run

**Status:** Accepted (2026-11-21)
**Owner:** Estefanía
**Issue:** #821
**Supersedes:** —
**Superseded by:** —

---

## Context

ADR-0065 fixes what data drift is: PSI on the segment shares and on R, F and M, and a
chi-square on the segment shares, with thresholds, a 200-customer minimum and a persistence
rule of two consecutive windows. It leaves open how a scheduled run reads its window, when
exactly an alert is raised, what is stored, and how the run is audited. F23-08 published the
read contract for reports and alerts, and `docs/services/model-registry.md` anticipated a
"drift sample" table kept fresh by the `profile_changed` stream.

Three points were judgement calls. First, the story text lists "R, F, M, categories and
channels", while ADR-0065 and the published contract list segment shares, R, F and M and say
categories and channels are not variables. Second, no history of profiles exists: the profile
view shows each customer's current assignment only, so a window "at its end" can be read
only as the state when the monitor runs. Third, `svc_models` is not a writer of any audit
kind, and the closed vocabulary of `CALCULATION_RUN` names three calculations.

## Decision

model-registry's monitor judges the published version only, on the variables of ADR-0065 and the
contract; categories and channels are not variables and are not monitored. A pass takes a
repeatable-read, read-only snapshot and reads every `scored` customer of
`api_rfm.current_profile_v1`: the customers' current assignments as one snapshot, as ADR-0065 defines
the window, whose end is the latest 03:00 UTC. The reference is fitted on every customer with
purchases at one instant, so the window is every scored customer too, and not only those recomputed
in the last 30 days, which would lean toward recent buyers. R is aged to the window's end (the
profile's `r_days` plus the whole days since its `computed_at`), because a profile is not rewritten
while only the clock moves. A customer whose label the reference does not name is kept, in a segment
bin of its own with a reference share of 0, and counted. The bins are the reference's own, plus a
`NO_VALUE` bin for R, F and M. A publication or rollback after the window's start makes the window
`NOT_COMPARED`, whenever the pass runs, so a version published after 03:00 is never compared with
profiles scored under the one before. A pass for a window that already has a report returns it and
writes nothing, so a repeated pass gives the same result. Each variable's state is `ALARM_PENDING` the
first window its alarm holds and `ALARM` the second, counting as the window before only the report one
day earlier; after a gap the variable starts over. An alert is raised by every report whose variable is
`ALARM`, as the published contract says (`listDriftAlerts`: an alarm that continues raises a further
alert in each report), so the first alert comes with the second alarming window. A reference with a
single segment has no chi-square to compute and cannot alarm on the segment shares. A report, its variables, statistics, bins and alerts are written once
and never change; what a reader can derive (the reference, the thresholds, a bin's contribution,
an alert's text) is not stored. A pass writes its report and its audit event together, in a
read-committed transaction of their own after the snapshot, so the event takes the trail's current
head and not the one the snapshot saw. The event is a `CALCULATION_RUN` with the calculation `DRIFT`,
written by `svc_models` as a `SYSTEM` actor, and a pass whose event cannot be recorded commits no
report. The new calculation value grows audit-trail's contract inside
v1 at version 1.5.0, under ADR-0076, ADR-0082 and the rule of ADR-0083.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Monitor categories and channels too, as the story text says | ADR-0065 is accepted and the contract says they are not variables; changing that is a decision for a superseding record, not for an implementation |
| A "drift sample" table fed by `profile_changed`, as the service doc anticipated | It copies a fact the profile view already holds and needs a consumer, a stale flag and a second source of truth; one snapshot of the view is enough for a daily pass |
| Alert once, on the move from `ALARM_PENDING` to `ALARM` | The read contract published by F23-08 says an alarm that continues raises a further alert in each report, and a contract merges before its code (ADR-0035); changing that is the contract's own change |
| Read only the customers recomputed in the trailing 30 days | rfm-engine recomputes a customer on an event or when a change falls due, so the sample would lean toward recent buyers against a reference of everyone, and a pass run after 03:00 would drop whoever bought since |
| Write the report and its audit event in the snapshot's transaction | The audit trail assigns its sequence under a lock but reads its head in the caller's snapshot; any event committed since the snapshot would be overwritten in sequence and the pass would fail |
| A table of the monitor's own for the run log | The run would not appear in the hash-chained trail audit-trail serves, and an auditor would look in two places |
| Store the thresholds, the reference and the contribution per row | Each is a function of the version, the statistic name or two shares; storing them lets a row disagree with its source |

## Consequences

**What this makes easy.** A report read later says what it said when it ran, and carries the version
it judged. Repeating a pass is safe, so two instances or a restart cannot double a report. Every
pass, including an insufficient or not-compared one, is in the audit trail.

**What this makes hard.** The window is the customers' current assignments when the pass runs, not
a reconstruction of the day it names, because the registry has no profile history; a pass that
runs late in the day reads later data than 03:00, and a profile recomputed after 03:00 is read as it
is now. A window is a day later than the one before and covers 29 of its 30 days, so two consecutive
windows are not independent samples; ADR-0065's persistence still holds, and F23-10 measures the
false-alarm rate it actually gives. Under a K-means version the segment shares come from the labels
rfm-engine's profiles carry, not from the current assignments of the segment history; reading those
is a later change with its own grant. After any publication the next 30 windows are `NOT_COMPARED`.
Categories and channels get no drift figure until a superseding record adds them.

**What must now be true elsewhere.**
- `contracts/db/api_audit.record_event.v1.md`, audit-trail's OpenAPI and XSD, and the function in
  `sql/01_schema.sql` name `DRIFT` and `svc_models` as a writer of `CALCULATION_RUN`;
  `contracts/vocabulary-growth.yaml` carries the entry.
- `contracts/db/advisory-locks.md` registers key 301001, the monitor's session lock.
- `contracts/db/grants.csv` lists `svc_models` on the five drift tables.

## Compliance

`services/model-registry/tests/test_drift.py` pins the statistics to the contract's example and
scans the monitor's sentences and sources for the outcome-test claim. `tests/test_monitor.py`
checks that the same window gives the same report and one audit event, that fewer than 200
customers and a version change inside the window, or published after its end, give no statistic,
that the alert is raised on the second window and again while the alarm continues, that a report
older than the window before does not count for persistence, that a label the reference never saw is
counted, that R is aged to the end, that another service's audit event during a pass does not break
it, that a pass whose audit fails commits nothing, and that the tables refuse every change and the
claim only an outcome test may make.
