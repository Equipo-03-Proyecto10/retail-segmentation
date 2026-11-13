# ADR-0081 — The domain-event table is owned by the schema owner, and a subject is named once, in the common fields

**Status:** Accepted (2026-11-13)
**Owner:** Marcelo
**Issue:** #1004
**Supersedes:** in part [ADR-0072](0072-domain-events-are-recorded-by-one-definer-function-into-a-hash-chained-append-only-table-that-audit-trail-reads.md) (the first bullet of section 6, and the `details` column of section 1's table for `CALCULATION_RUN`, `PERMISSION_CHANGED`, `CRITICAL_ACTION` and `HISTORY_READ`)
**Superseded by:** —

---

## Context

ADR-0072 decided how domain events are recorded. Its contract
([`api_audit.record_event.v1.md`](../../contracts/db/api_audit.record_event.v1.md), #980) and its
implementation (#981, F21-25) were reviewed against it, and both differ from it in three places.
Both reviews named the differences and called them non-blocking: #980 in finding 4, #981 in
finding 5. The pull requests merged as built. Until now the differences are written only in the
contract and in the two pull request descriptions. AGENTS.md says an accepted record is superseded,
never edited, and that no architectural decision is made silently in code. So the record has to
follow what was built.

What is fixed already:

- ADR-0072 section 2 makes `api_audit.record_event_v1` the only writer. It is `SECURITY DEFINER`,
  owned by `definer_audit`, and computes the hash chain under advisory lock `272001`. The function
  reads the chain head and any stored row for a repeated `event_id` before it inserts.
- ADR-0072 section 6 says the table "is owned by `definer_audit`, which holds `INSERT` only".
- ADR-0072 section 2, check 3, refuses any `details` key that contains `hash`, among other words.
  Section 1 names a `details` key `parameters_hash` for a calculation.
- ADR-0072 section 1 lists `subject_user_id` for a permission change and `customer_id` for a
  history read as `details` keys. The common fields `subject_type` and `subject_id` already name
  what an event is about.

None of these is a judgement call made now. Each one is a conflict inside ADR-0072, or between
ADR-0072 and a privilege the function needs, and the implementation had to settle it one way.
This record says which way, and why. What stays uncertain is only whether a later reader will
find section 6's "owned by `definer_audit`" and not this record. The status line on ADR-0072 is
there for that.

## Decision

`audit_events.domain_event` is owned by the schema owner, not by `definer_audit`. `definer_audit`
holds `INSERT` and `SELECT` on it, since the function it owns reads the chain head and a repeated
`event_id`. Only the owner holds `UPDATE`, `DELETE` and `TRUNCATE`, and the
`domain_event_append_only` and `domain_event_no_truncate` triggers refuse all three as a second
layer, as ADR-0072 already provided. A calculation's digest of its parameters is the `details`
key `parameters_digest`, 64 lowercase hex characters, so check 3 stays as strict as ADR-0072
wrote it. Each value is stored once. The subject of a permission change (the user) and of a
history read (the customer) is the common `subject_type` and `subject_id`, and so is the subject
of a critical action. A calculation's rule-set version is the common `rule_set_version` field.
None of them is a `details` key. Everything else in ADR-0072 stays binding.

### The three deviations, and the reason for each

| # | ADR-0072 says | As built (#980, #981) | Why |
|---|---|---|---|
| 1 | §6: the table "is owned by `definer_audit`, which holds `INSERT` only" | The schema owner owns the table. `definer_audit` holds `INSERT` and `SELECT`. Only the owner holds `UPDATE`, `DELETE` and `TRUNCATE`, and two triggers refuse them | A table's owner holds every privilege on it and can drop its triggers. Owned by `definer_audit`, the role a function runs as on every producer's call would also be able to `UPDATE`, `DELETE` and `TRUNCATE` the evidence. That is weaker than the owner-only rule section 6 states next. `SELECT` is needed because the function reads the head to chain and reads a stored row so that a repeat returns it (section 1, `event_id`) |
| 2 | §1: a calculation carries `parameters_hash` | The key is `parameters_digest` | Section 2's check 3 refuses any key that contains `hash`. Keeping the name would mean either an exception in the credential check or a key that can never be sent. Renaming the key keeps the check without exceptions |
| 3 | §1: `subject_user_id` (a permission change) and `customer_id` (a history read) are `details` keys | Neither is a `details` key. The subject is the common `subject_type` (`user`, `customer`) and `subject_id`, which the function requires for both kinds | The same value would otherwise be stored twice and could disagree. The common fields are indexed (`domain_event_subject_key`) and filterable through `GET /v1/domain-events`, and the `details` keys would be neither |

Section 1 also put a calculation's `rule_set_version` and a critical action's `subject_type` and
`subject_id` in the `details` column. They are common fields in the same table. The contract
reads them that way, and so does this record. That is the same rule as deviation 3, and not a
fourth deviation.

Section 5's sentence "the data subject is named only by `customer_id` or `subject_user_id`" now
reads "by `subject_id`". The reasoning of section 5 does not change: the key is kept on
anonymization, so nothing in the table is rewritten.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Change the schema to match ADR-0072: `ALTER TABLE ... OWNER TO definer_audit`, revoke its `SELECT` | The function could no longer read the head or a repeated `event_id`, so the chain and idempotency break. As owner, `definer_audit` would also gain `UPDATE`, `DELETE` and `TRUNCATE` and the power to drop the guard triggers. That is the opposite of what section 6 wants |
| Keep `parameters_hash` and exempt that one key from check 3 | An exception list in a credential check grows. The check is meant to hold even if a kind's keys are widened later (section 2), and an exempt name defeats that |
| Keep `subject_user_id` and `customer_id` as `details` keys beside the common fields | The value is stored twice, a producer can disagree with itself, and only the common field can be filtered and indexed |
| Leave the deviations in the contract and the PR descriptions only | AGENTS.md: no architectural decision is made silently in code. A reader of ADR-0072 would otherwise believe the definer owns the table |

## Consequences

**What this makes easy.** ADR-0072 and the database say the same thing again. A reviewer
checking grants reads one rule: the owner alone can change rows, `definer_audit` appends and
reads, and `svc_audit_trail` reads. Producers have one place for a subject.

**What this makes hard.** ADR-0072 is now read together with this record for section 1's table
and section 6's first bullet, and its status line says so. `definer_audit` can read every domain
event, not only append. That role is not a login role, and only the function's body runs as it,
but it is one more reader than section 6 listed.

**What must now be true elsewhere.**
- [`contracts/db/api_audit.record_event.v1.md`](../../contracts/db/api_audit.record_event.v1.md)
  cites this record in its `details` section and in "Security and use".
- `contracts/db/grants.csv` keeps `definer_audit` with `INSERT` and `SELECT` on
  `audit_events.domain_event`, and no `UPDATE`, `DELETE` or `TRUNCATE` for any role but the
  owner. `sql/01_schema.sql` creates the table without changing its owner.
- A new kind or key follows this rule. A subject goes in `subject_type` and `subject_id`, and no
  key name contains a refused word.

## Compliance

- `contracts/db/check_grants.sh` compares the live grants with `grants.csv` in both directions.
  It fails if `definer_audit` loses `SELECT` or gains `UPDATE`, `DELETE` or `TRUNCATE`.
- `SELECT tableowner FROM pg_tables WHERE schemaname = 'audit_events' AND tablename =
  'domain_event'` returns the schema owner, not `definer_audit`.
- The tests that ADR-0072's compliance section lists still pass. A `details` key `password_hash`,
  or a `parameters_hash`, raises `MA003`. A `PERMISSION_CHANGED` with no `subject_type`/`subject_id`
  raises `MA004`. A `details` key `subject_user_id` or `customer_id` raises `MA002`.
- `python3 docs/check.py .` passes.
