# ADR-0072 — Domain events are recorded by one definer function into an append-only, hash-chained table that audit-trail reads

**Status:** Accepted (2026-11-04)
**Owner:** Maximiliano
**Issue:** #802
**Supersedes:** —
**Superseded by:** —

---

## Context

`audit_log` answers "which row changed". `fn_audit()` fills it from table triggers, so
`action` holds only `INSERT`, `UPDATE` and `DELETE`, and the actor is whatever
`mosaiq.user_id` the connection set. It cannot say that a recalculation ran, which model
version a segment came from, that a role was transferred, or that someone read a customer's
history. F21-24 asks for those, and F21-25 (#803) and the desktop audit module (#884) wait on
this record.

What the repository already fixes:

- ADR-0024: `retail_app` holds `SELECT` and `INSERT` on `audit_log`, nothing else; the schema
  owner can archive. ADR-0034: only `svc_audit_trail` reads `audit_log`, no `svc_*` role writes
  it, and `fn_audit` is `SECURITY DEFINER` owned by `definer_audit`, which may only `INSERT`.
- audit-trail is registered with one rule, "append-only, attributed, free of credentials,
  readable by ADMIN and AUDITOR only" (RN-28 to RN-30), one resource, `audit-entries`, and is
  a Rust service at 48 MB. ADR-0043 and ADR-0059 say audit-trail cannot be dropped. Its XSD
  and OpenAPI enumerate `entity` and `action`.
- ADR-0044: boundaries meet through `api_<suffix>` views and functions and through Redis
  Streams relayed from the owner's outbox. `api_stock.consume_v1` is "the only cross-boundary
  write; a second one needs its own record". This record is that record, if accepted.
- ADR-0040's memory gate is 3,000 MB, which ADR-0059 moves to 3,500 MB (3,376 MB committed).
  Another container, even at 48 MB, spends a margin ADR-0059 already says is thin.
- ADR-0060: tokens carry a role only; floor roles are narrowed by store from
  `api_users.store_assignment_v1`; `AUDITOR` and `ADMIN` are not narrowed.
- ADR-0064: a model version is immutable, and F21-25's audit records every publication and
  rollback. ADR-0066 (proposed) says a read of one named customer's history is recorded with
  actor, customer, kind and time but never content, that its refusals are recorded too, and
  that anonymization is itself a domain event naming the administrator.
- The web runs RFM and K-means itself and consumes no service and no Redis (AGENTS.md). Its
  runs must be recordable too.

Parts that are judgement calls, for the reviewer to look at first: writing through a
synchronous function instead of through streams (section 2), failing a read when its record
cannot be written (section 4), keeping the events indefinitely (section 5), and the hash chain
(section 6). Nothing here was checked against legal advice.

## Decision

Domain events extend audit-trail; no new service is added. They are stored in a new
append-only table in a service schema, `audit_events.domain_event`, never in `audit_log`, and
exposed as a second audit-trail resource, `domain-events`, so the v1 `audit-entries` contract
and its XSD do not change. The only writer is one `SECURITY DEFINER` function,
`api_audit.record_event_v1`, owned by `definer_audit`, which each producing boundary calls
inside its own transaction as its own `svc_*` role. It accepts only a
closed list of event kinds, only the kinds its caller is mapped to, only the keys each kind
allows, and refuses credentials. Rows form a hash chain. audit-trail only reads: `ADMIN` and
`AUDITOR`, filterable by kind, actor and model version. The sections below make this exact.

### 1. What is recorded, by kind

A kind is a member of a closed list. Adding one is a contract change and a new version of the
function's allowed set, never a free string. `details` holds ids, codes, versions and counts
only, and its keys per kind are fixed by the contract.

| Kind | Recorded when | Writer | `details` carries (besides the common fields) |
|---|---|---|---|
| `CALCULATION_RUN` | An RFM recalculation, a segmentation run (RFM rules or K-means) or a published KPI recomputation finishes, or fails | rfm-engine, segments, analytics-kpis | `calculation` (`RFM`, `SEGMENTATION`, `KPI`), `rule_set_version` or `parameters_hash`, window, `data_origin`, input and output counts, `status` |
| `MODEL_PUBLISHED`, `MODEL_ROLLED_BACK` | A version changes state (ADR-0064) | model-registry | `model_version_id`, previous published version, reason code |
| `PERMISSION_CHANGED` | A role is assigned or an administrator is transferred, a user is deactivated or reactivated, a store assignment is added or removed | users | `subject_user_id`, `from_role` and `to_role` codes, or `store_ids`; never a password or a token |
| `CRITICAL_ACTION` | A sale is reversed, stock is adjusted, a customer is anonymized (ADR-0066), audit payloads are redacted, an experiment's frame is frozen, a campaign is activated | sales, stock, customers, experimentation, campaigns | `action` code, `subject_type`, `subject_id`, reason code |
| `HISTORY_READ` | A customer's consent log, segment history or per-customer sales list is read, or refused (ADR-0066) | customer-preferences, segments, sales | `history` (`CONSENT`, `SEGMENT`, `SALES`), `customer_id` |
| `AUDIT_READ` | `ADMIN` or `AUDITOR` reads `domain-events` or `audit-entries` | audit-trail | The filter names used, never their values or the rows returned |

Common fields, set by the function and never by the caller unless stated:

| Field | Source |
|---|---|
| `event_id` | UUID from the caller, the idempotency key: a repeat returns the stored row, so a retry cannot double-count |
| `kind`, `outcome` (`SUCCESS` or `REFUSED`) | Caller, kind checked against the closed list |
| `occurred_at` | Caller, the moment of the act; `recorded_at` is the server's `now()`, never client-supplied |
| `actor_type`, `actor_user_id`, `actor_role` | `USER` with the id and role code from the verified token, or `SYSTEM` for a schedule or a stream consumer, with a null id. A `USER` event with no id is refused |
| `source` | Taken from `session_user`, never from a parameter (`svc_models`, `svc_rfm`, ...) |
| `store_id` | Nullable. Set when the act happened in a store, so a store-scoped reader can be added later |
| `subject_type`, `subject_id` | What the event is about |
| `model_version_id`, `rule_set_version` | Nullable, set for anything computed or scored |
| `correlation_id` | The request's `X-Correlation-ID` |
| `details` | JSON object, closed per kind |

### 2. Who writes: one definer function, in the caller's transaction

The function is `SECURITY DEFINER`, `SET search_path = public, pg_temp`, `REVOKE ... FROM
PUBLIC`, `EXECUTE` granted to each producer's role and nobody else. It makes three checks
before it inserts, each raising a distinct SQLSTATE the caller maps to a 5xx, because a failed
record is the producer's fault and not the client's:

1. **Kind owned by the caller.** A mapping inside the function ties each kind to the
   `session_user`s allowed to record it (the Writer column above). `svc_models` cannot record
   `PERMISSION_CHANGED`.
2. **Keys closed.** `details` has exactly the keys its kind allows, and no value is an object
   or array deeper than the contract says.
3. **No credential (RN-29).** As a second layer after the closed keys, a recursive check
   rejects any key containing `password`, `hash`, `secret`, `token`, `key` or `credential`,
   and any string value shaped like a JWT or an argon2 hash. The check covers the whole
   payload, so it still holds if a kind's allowed keys are later widened.

A producer records the event in the same transaction as the act, so no act commits without its
record and no record exists for a rolled-back act. The caller states the actor; the function
cannot verify it, so a producer is trusted for the actor it passes, as it is for every other
id it writes. That trust is bounded by check 1 and by `source`.

Services still never call each other over HTTP, and no service writes another boundary's
table. Besides `api_stock.consume_v1`, `api_stock.restock_v1` (ADR-0061) and
`api_segments.apply_assignment_v1` (ADR-0063), this function is the one cross-boundary write,
authorized by this record and documented in `contracts/db/api_audit.record_event_v1.v1.md` with its grants in
`grants.csv`. Streams stay the way a service tells others that something changed; they are not
the way evidence is written (see the alternatives).

The web does not call this function: ADR-0046 gives `retail_app` `USAGE` on `public` alone,
so it cannot name `api_audit`. The web's batch run keeps recording itself through
`fn_audit` into `audit_log` as today, and its `CALCULATION_RUN` is not a domain event.

### 3. Where it lives: audit-trail, with a new resource

audit-trail's rule grows by exactly one clause: "...and it also reports the domain events
recorded under that same append-only, attributed, credential-free discipline". The registry
text is changed by the contract PR, with RN ids added when the contract story writes them. It
is still one rule: the store, its immutability and who may read it. Producing the events is
each producer's rule, as producing a stream event is.

- New top-level resource `domain-events`, owned by audit-trail alone, so no path mixes
  resources. `audit-entries` is unchanged, which keeps the v1 compatibility check passing.
- `GET /v1/domain-events`, `GET /v1/domain-events/{event_id}` and
  `GET /v1/domain-events/verification` (section 6), JSON and XML, XSD-validated,
  `x-mosaiq-roles` `ADMIN` and `AUDITOR`. No `POST`, `PUT`, `PATCH` or `DELETE` exists on the
  resource.
- Filters, all optional and combined with AND: `kind`, `actor_user_id`, `actor_role`,
  `model_version_id`, `subject_type`, `subject_id`, `store_id`, `outcome`, `source`, `from`,
  `to`, `page`, `size`. An unknown `kind` is 422 `invalid_query` with `details` naming it.
  Order is `recorded_at` then `event_id`, both descending.
- The tables never mix: `audit_log` for row changes, `audit_events.domain_event` for decisions.
  A consumer that wants "everything about customer 7" issues two reads.

### 4. Who reads, and which reads are logged

Reading domain events is `ADMIN` and `AUDITOR`, the same roles as `audit-entries`. Neither is
narrowed by store (ADR-0060). A floor role is never given the log: a seller who could read
"who looked at this customer" could also read what was withheld from them. `store_id` is
recorded so that a narrowed reader can be added, but that needs its own decision.

Reads that **are** logged as `HISTORY_READ`, success and refusal alike: a customer's consent
log, segment history and sales list, per named customer (ADR-0066). Reads of the audit trail
itself are logged as `AUDIT_READ`. Reads that are **not** logged: the permitted customer
profile at the till, directory search, aggregates and KPIs, catalogs, recommendations, and a
list of model versions. The test is "one named person's history, or the log itself"; a read
that names no individual and returns no personal data does not enter, so the log does not
become a second copy of the data or a record of every screen.

A read on a logged route fails closed: if the function cannot record the event, the route
answers 503 and returns no data. Otherwise the log would hold only the reads that happened to
succeed in being logged. `AUDIT_READ` records the reading of the log, so there is one such row
per request, including the request that lists `AUDIT_READ` rows; that is accepted noise, not a
loop.

### 5. Retention and personal data

Events are kept indefinitely, as RN-30 keeps `audit_log`. By construction they hold no
identity: `details` carries ids, codes, versions and counts, never a name, email, phone, free
text or the content of a read. The data subject is named only by `customer_id` or
`subject_user_id`, and ADR-0066 keeps the key of an anonymized customer, so an anonymization
needs nothing rewritten in this table. This is why events are never redacted, and why a
`details` key that could hold personal data is refused by check 2 and not removed later.

Two things remain personal: `actor_user_id` (an employee) and the fact that a customer's
history was read. Both are access records of the staff, kept as evidence, readable by two
roles only. No retention job deletes them. If a later legal decision sets a limit, the owner
role archives by partition with an operator-applied script, which is the exception ADR-0024
foresaw, and the chain records the cut (the archived head is stored as the new anchor).

### 6. Immutability and tamper evidence

- **Privileges.** The table is owned by `definer_audit`, which holds `INSERT` only;
  `svc_audit_trail` holds `SELECT` only; no `svc_*` role and not `retail_app` holds any
  privilege on the table. Nothing holds `UPDATE`, `DELETE` or `TRUNCATE` except the schema
  owner. A `BEFORE UPDATE OR DELETE` trigger raises on the table as a second layer, and the
  owner's archiving drops it only inside the operator script. ADR-0024 rejected such a trigger
  because it also stopped archiving; it is acceptable here because the table is new,
  archiving is the documented exception, and the trigger guards against a mistake and does
  not replace the privileges.
- **Hash chain.** Each row stores `prev_hash` and `entry_hash`, the SHA-256 of the canonical
  JSON of every other column plus `prev_hash`, computed by the function under a transaction
  advisory lock so the chain has one head. The first row has a fixed genesis `prev_hash`. The
  lock serializes writers, which is acceptable because domain events are infrequent next to
  sales (calculations, publications, role changes and named history reads).
- **Verification.** `GET /v1/domain-events/verification` recomputes the chain for a range
  (`from`, `to`, or the whole table) and answers the first row whose hash or link does not
  match, or `ok`, with the head hash and the row count. An auditor records the head hash
  outside the database when they review, which turns "the chain is consistent" into "it is
  the chain I saw". Without that external copy, whoever can rewrite every later row can
  rewrite the chain: the chain detects an edit, it does not prevent a superuser.
- **`audit_log`** is not chained or changed by this record.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| A new domain-audit service | It would add a container (48 to 64 MB) to a margin ADR-0059 calls thin, and a rule that is the same as audit-trail's: an append-only, attributed, credential-free store read by two roles. Two services would also split "everything about customer 7" across two contracts |
| Widen `audit_log`: new `action` values and the domain payload in `data_after` | `action` is checked to three values and enumerated in the XSD and OpenAPI, so widening breaks the v1 contract and ADR-0024's reading of the table. It would also put model versions and read events beside row copies that hold personal data |
| Producers write by Redis Streams from their outbox, audit-trail consumes and stores | Right for notification, wrong for evidence. The web has no Redis, so its runs would need a capture trigger with nothing to capture. A read has no row to trigger an outbox, so it would still need a write on the read path. The act and its record commit separately, so evidence can lag or be lost on a trim, and audit-trail would gain a Redis dependency and a 503 on Redis loss that has nothing to do with reading the log |
| Producers insert into a table owned by audit-trail with `INSERT` grants | Each role would hold a write privilege on another boundary's table, which ADR-0034 forbids, and could write any kind, any actor and any payload with no credential check |
| audit-trail exposes `POST /v1/domain-events` | Services never call each other over HTTP, and an HTTP write is neither atomic with the act nor authenticated as the producer, since tokens carry the end user's role |
| No hash chain, privileges only | Privileges stop the application roles, not the schema owner or a restored backup. The chain costs one hash per row and gives the auditor a way to notice, which is what tamper evidence asks for |
| Log every read | The log would become the largest table and a second copy of what people looked at. Reads that name no individual leak nothing the aggregate does not |
| Redact or purge events on anonymization | Not needed, since events carry no identity; a purge would also break the chain |

## Consequences

**What this makes easy.** One place answers who decided what, with the model version and the
rule set: an auditor filters by `kind`, `actor_user_id` and `model_version_id`. An act and its
record commit together. A new producer is a grant and a mapping row, not a new integration.
The v1 `audit-entries` contract is untouched. No container is added.

**What this makes hard.** audit-trail stops being read-only in spirit: it owns a table it
cannot write, and its registry rule changes. The function is a fourth cross-boundary write and
a synchronous dependency: a producer whose record fails fails its act, which is the intent but
couples availability. A logged read costs one extra insert and fails when the function does.
The advisory lock serializes writers. The actor is trusted from the producer, not verified.
Retention is open-ended until someone decides otherwise.

**What must now be true elsewhere.**
- `contracts/registry.yaml`: audit-trail's rule and resources are updated in the contract PR.
- `contracts/db/api_audit.record_event_v1.v1.md`, `grants.csv` and the idempotent audit-trail
  boundary section of `sql/01_schema.sql` (schema `audit_events`, `api_audit`, the function,
  the guard trigger and grants) are written under F21-24's contract and F21-25's
  implementation.
- ADR-0066's anonymization and read audit, and ADR-0064's publication and rollback, name the
  kinds above. If ADR-0066 is not accepted, `HISTORY_READ` and the anonymization action are
  removed from the list.
- Each producing service's contract states which kinds it records, and its tests assert the
  record in the same transaction as the act.
- The table is runtime history, like `app_session`, and is seeded with at least thirty rows,
  not exempted (ADR-0051: an exemption is for a domain that really is smaller, and an empty
  history shows nothing). The implementation PR leaves `sql/seed-exempt.txt` alone.
- `contracts/audit-trail/` gains the `domain-events` paths, XSD, and JSON and XML examples
  for each kind, a refused filter, and a verification result.

## Compliance

- As each `svc_*` role, `retail_app` and `svc_audit_trail`: `UPDATE`, `DELETE` and `TRUNCATE`
  on `audit_events.domain_event` fail with `insufficient_privilege`; a direct `INSERT` fails
  for every role but `definer_audit`. `contracts/db/check_grants.sh` compares the grants with
  `grants.csv` in both directions.
- Calling the function as `svc_models` with kind `PERMISSION_CHANGED`, with a `details` key
  `password_hash`, with a `token` key nested one level down, and with a `USER` actor and no
  id: each is refused and writes no row. A repeated `event_id` returns the same row and
  writes none.
- A transaction that records an event and then rolls back leaves no row.
- A test corrupts a row as the schema owner and `verification` names it; an untouched table
  answers `ok` with the head hash.
- A test lists `domain-events` as every role: `ADMIN` and `AUDITOR` read, the other eight get
  403, in JSON and XML. A read of a customer's history writes one `HISTORY_READ`, a 403 on the
  same route writes one with `outcome = REFUSED`, and with the function revoked the route
  answers 503 and returns no rows. A read of the till profile writes none.
- `python3 docs/check.py` passes, and the memory gate is unchanged, since no container is
  added.
