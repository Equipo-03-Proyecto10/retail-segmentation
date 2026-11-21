# ADR-0088 — The drift monitor reads the current profiles in one snapshot, raises an alert on the second alarming window, and is audited as a calculation run

**Status:** Proposed
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
repeatable-read snapshot and reads `api_rfm.current_profile_v1` for the customers scored in the
trailing 30 days whose label the reference names; that is the window, and its end is the latest
03:00 UTC. The bins are the reference's own, plus a `NO_VALUE` bin for R, F and M. A pass for a
window that already has a report returns it and writes nothing, so a repeated pass gives the same
result. Each variable's state is `ALARM_PENDING` the first window its alarm holds and `ALARM` the
second; an alert is raised once, on the move from `ALARM_PENDING` to `ALARM`, and not again
while the alarm goes on. A report, its variables, statistics, bins and alerts are written once
and never change; what a reader can derive (the reference, the thresholds, a bin's contribution,
an alert's text) is not stored. A pass is audited in its own transaction as a `CALCULATION_RUN`
with the calculation `DRIFT`, written by `svc_models` as a `SYSTEM` actor, and a pass whose event
cannot be recorded commits no report. The new calculation value grows audit-trail's contract inside
v1 at version 1.5.0, under ADR-0076, ADR-0082 and the rule of ADR-0083.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Monitor categories and channels too, as the story text says | ADR-0065 is accepted and the contract says they are not variables; changing that is a decision for a superseding record, not for an implementation |
| A "drift sample" table fed by `profile_changed`, as the service doc anticipated | It copies a fact the profile view already holds and needs a consumer, a stale flag and a second source of truth; one snapshot of the view is enough for a daily pass |
| Alert on every alarming window | An alarm that persists would raise a new alert each day for the same finding; ADR-0065 asks for persistence to cut noise, not to repeat it |
| A table of the monitor's own for the run log | The run would not appear in the hash-chained trail audit-trail serves, and an auditor would look in two places |
| Store the thresholds, the reference and the contribution per row | Each is a function of the version, the statistic name or two shares; storing them lets a row disagree with its source |

## Consequences

**What this makes easy.** A report read later says what it said when it ran, and carries the version
it judged. Repeating a pass is safe, so two instances or a restart cannot double a report. Every
pass, including an insufficient or not-compared one, is in the audit trail.

**What this makes hard.** The window is the customers' current assignments when the pass runs, not
a reconstruction of the day it names, because the registry has no profile history; a pass that
runs late in the day reads later data than 03:00. A window holds only customers scored inside it,
so a quiet month reads as `INSUFFICIENT_DATA`. Categories and channels get no drift figure until a
superseding record adds them.

**What must now be true elsewhere.**
- `contracts/db/api_audit.record_event.v1.md`, audit-trail's OpenAPI and XSD, and the function in
  `sql/01_schema.sql` name `DRIFT` and `svc_models` as a writer of `CALCULATION_RUN`;
  `contracts/vocabulary-growth.yaml` carries the entry.
- `contracts/db/advisory-locks.md` registers key 299001, the monitor's session lock.
- `contracts/db/grants.csv` lists `svc_models` on the five drift tables.

## Compliance

`services/model-registry/tests/test_drift.py` pins the statistics to the contract's example and
scans the monitor's sentences and sources for the outcome-test claim. `tests/test_monitor.py`
checks that the same window gives the same report and one audit event, that fewer than 200
customers and a version change inside the window give no statistic, that the alert is raised on
the second window and once, that a pass whose audit fails commits nothing, and that the tables
refuse every change and the claim only an outcome test may make.
