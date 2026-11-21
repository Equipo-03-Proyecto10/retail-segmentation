# ADR-0083 — Rule-set changes are critical actions, and the audit vocabulary that names them grows by accepted record

**Status:** Accepted (2026-11-18)
**Owner:** Estefanía
**Issue:** #804
**Supersedes:** —
**Superseded by:** —

---

## Context

F22-09 makes a change to an RFM rule set a governed act: a draft is created and edited, a
published rule set is never edited, and a change is a new version (ADR-0064). The issue asks that
each change be audited with its author. ADR-0072 already records critical actions as domain events
in the transaction of the act, through `api_audit.record_event_v1`, and `rule_set_version` is one of
the columns of the event table for this purpose.

Two closed vocabularies of that contract do not yet name the act. `CRITICAL_ACTION.action` lists
six actions, none of them a rule-set change, and the subject types do not include a rule set. Both
are published enums in audit-trail's OpenAPI and XSD. `oasdiff` reports an added response enum value
as breaking, and ADR-0076 and ADR-0082 let a closed vocabulary grow inside v1 only through an entry
in `contracts/vocabulary-growth.yaml` that names an accepted record. The judgement call is whether
a rule-set change belongs in the domain-event chain at all, or in a table of rfm-engine's own: the
chain is what audit-trail serves and verifies, so a second log would hide the change from the one
place an auditor looks.

## Decision

rfm-engine records every creation, edit, new version and publication of a rule set as a
`CRITICAL_ACTION` domain event, in the transaction of the change, naming its author. The actions are
`RULE_SET_CREATED`, `RULE_SET_EDITED`, `RULE_SET_VERSIONED` and `RULE_SET_PUBLISHED`, the subject
type is `rule_set` with the rule set's id, and `rule_set_version` is its version number. `svc_rfm`
joins the writers of `CRITICAL_ACTION`. The five names grow inside v1 of audit-trail's contract at
version 1.4.0 through an entry in `contracts/vocabulary-growth.yaml` that names this record, under
the rule of ADR-0076 and ADR-0082. A change that cannot be recorded is not made.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| A table of rfm-engine's own with the author and the action | It keeps the contract still, but the change would not appear in the hash-chained trail audit-trail serves, and an auditor would have to look in two places |
| Reuse an existing action, such as `EXPERIMENT_FROZEN` | It says something false about the event, and a filter on the action would return rule-set changes as experiments |
| Ship `/v2` of audit-trail for the new names | The service and the desktop client would move for a word, as ADR-0082 already weighed |

## Consequences

**What this makes easy.** "Who changed the rules, when, and from which version" has one answer, in
the trail every other critical action is in. A refused change leaves no event, and a change cannot
be made without one.

**What this makes hard.** A client written against the earlier list can meet an action or a subject
it does not know and must treat both as text. The contract version moves for each growth.

**What must now be true elsewhere.**
- `contracts/db/api_audit.record_event.v1.md`, the XSD, the OpenAPI, `SUBJECT_TYPES` in
  `services/audit-trail/src/events.rs` and the desktop client's list name the new values.
- `contracts/vocabulary-growth.yaml` carries the entry, and `svc_rfm` holds EXECUTE on the
  function (`contracts/db/grants.csv`).

## Compliance

`services/rfm-engine/tests/test_rule_sets.py` checks that each change is an event with its author,
that a refused change records none, and that a change whose event cannot be recorded is not made.
`contracts/check.sh` fails when the values are added without the entry.
