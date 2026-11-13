# `api_audit.record_event_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_audit.record_event_v1` |
| Kind | PostgreSQL function |
| Owning boundary | audit-trail |
| Producer | audit-trail-service (owns the table; it never writes it except through this function) |
| Consumers | users, sales, segments, customer-preferences, stock, customers, campaigns, audit-trail; and, once their roles exist, rfm-engine (`svc_rfm`), model-registry (`svc_models`), analytics-kpis (`svc_kpis`) and experimentation (`svc_experiments`) |
| Signature | `api_audit.record_event_v1(uuid, text, text, timestamptz, text, uuid, text, smallint, text, text, bigint, bigint, text, jsonb)` |
| regprocedure | `api_audit.record_event_v1(uuid,text,text,timestamp with time zone,text,uuid,text,smallint,text,text,bigint,bigint,text,jsonb)` |
| Result | `TABLE (recorded_at timestamptz, entry_hash text, created boolean)`: one row |

It records one domain event (ADR-0072) in `audit_events.domain_event`, the
append-only, hash-chained table audit-trail reads as `GET /v1/domain-events`
(`contracts/audit-trail/openapi.yaml`). It is the only writer of that table,
and a cross-boundary write authorized by ADR-0072 beside
`api_stock.consume_v1`, `api_stock.restock_v1` and
`api_segments.apply_assignment_v1`. The table itself is audit-trail's own and
not part of this contract.

A producer calls it **inside the transaction of the act it records**, as its
own `svc_*` role, so no act commits without its event and no event survives a
rolled-back act. A logged read calls it before it returns any data, and fails
closed: when the call raises, the read answers 503 `audit_unavailable` with no
data (ADR-0072 section 4).

## Arguments

| Position | Name | Type | Meaning |
|---:|---|---|---|
| 1 | `event_id` | `uuid` | Chosen by the caller; the idempotency key. Not null. |
| 2 | `kind` | `text` | One of the kinds below, and one the caller is mapped to. |
| 3 | `outcome` | `text` | `SUCCESS`, or `REFUSED` for a refused `HISTORY_READ` only. |
| 4 | `occurred_at` | `timestamptz` | The moment of the act. Not null, and not more than five minutes after the database's `now()`. |
| 5 | `actor_type` | `text` | `USER` or `SYSTEM`. |
| 6 | `actor_user_id` | `uuid` | The verified token's subject for `USER`; null for `SYSTEM`. |
| 7 | `actor_role` | `text` | The verified token's role code for `USER`; null for `SYSTEM`. |
| 8 | `store_id` | `smallint` | The store the act happened in, or null. |
| 9 | `subject_type` | `text` | What the event is about (`SubjectType` in the OpenAPI), or null where the kind allows it. |
| 10 | `subject_id` | `text` | That subject's own id as text, 1 to 80 characters; null exactly when `subject_type` is. |
| 11 | `model_version_id` | `bigint` | The model version, for anything computed or scored, or null. |
| 12 | `rule_set_version` | `bigint` | The rule-set version a calculation ran under, or null. |
| 13 | `correlation_id` | `text` | The request's `X-Correlation-ID`, or null. |
| 14 | `details` | `jsonb` | A JSON object whose keys are exactly those the kind allows. |

`source` is never an argument: the function takes it from `session_user`, which
`SET ROLE` cannot change. `recorded_at` is the database's `now()`, and
`prev_hash` and `entry_hash` are computed by the function.

Callers cast the store id and pass every argument positionally:
`SELECT * FROM api_audit.record_event_v1($1, $2, $3, $4, $5, $6, $7, $8::smallint, $9, $10, $11, $12, $13, $14::jsonb)`.

## Kinds, writers and `details`

| Kind | Writers (`session_user`) | Subject | `details` keys (required; *optional*) |
|---|---|---|---|
| `CALCULATION_RUN` | `svc_rfm`, `svc_segments`, `svc_kpis` | `calculation` or none | `calculation` (`RFM`, `SEGMENTATION`, `KPI`), `status` (`COMPLETED`, `FAILED`), `input_count`, `output_count`; *`parameters_digest`*, *`window_from`*, *`window_to`*, *`data_origin`* (`OBSERVED` or `SEEDED`, as ADR-0052 defines them: `SEEDED` when any sale the calculation read is seeded) |
| `MODEL_PUBLISHED`, `MODEL_ROLLED_BACK` | `svc_models` | `model_version`, with `model_version_id` not null | `previous_version_id` (integer or null), `reason_code` |
| `PERMISSION_CHANGED` | `svc_users` | `user`, required | `change`; *`from_role`*, *`to_role`*, *`store_ids`* |
| `CRITICAL_ACTION` | `svc_sales`, `svc_stock`, `svc_customers`, `svc_experiments`, `svc_campaigns` | required | `action` (`SALE_REVERSED`, `STOCK_ADJUSTED`, `CUSTOMER_ANONYMIZED`, `AUDIT_PAYLOAD_REDACTED`, `EXPERIMENT_FROZEN`, `CAMPAIGN_ACTIVATED`); *`reason_code`* |
| `HISTORY_READ` | `svc_preferences`, `svc_segments`, `svc_sales` | `customer`, required | `history` (`CONSENT`, `SEGMENT`, `SALES`) |
| `AUDIT_READ` | `svc_audit_trail` | `domain_event` or `audit_entry` for a read of one, none for a list | `resource` (`AUDIT_ENTRIES`, `DOMAIN_EVENTS`, `DOMAIN_EVENT_VERIFICATION`), `filters` (array of filter names, possibly empty) |

`PERMISSION_CHANGED.change` is one of:

| `change` | Also requires |
|---|---|
| `ROLE_ASSIGNED`, `ADMINISTRATOR_TRANSFERRED` | `from_role` and `to_role`, different role codes |
| `STORES_ASSIGNED`, `STORES_UNASSIGNED` | `store_ids`: a non-empty array of distinct store ids, the stores added or removed |
| `USER_DEACTIVATED`, `USER_REACTIVATED` | nothing else |

An administrator transfer records two events in its transaction, one for each
user whose role moved. A store-assignment replacement records at most two, one
for the stores it added and one for the stores it removed.

ADR-0072's table names a few `details` keys that this contract carries as
common fields instead, so a value is stored once ([ADR-0081](../../docs/adr/0081-the-domain-event-table-is-owned-by-the-schema-owner-and-a-subject-is-named-once-in-the-common-fields.md) records each
deviation and its reason): the `subject_user_id` of a
permission change and the `customer_id` of a history read are `subject_id`;
the `subject_type` and `subject_id` of a critical action are the common
fields; a calculation's `rule_set_version` is the common field. Its
`parameters_hash` is named `parameters_digest`, because check 3 refuses any key
containing `hash`.

Values in `details` are JSON strings, integers, booleans or null, and arrays of
these one level deep where the table says so (`store_ids`, `filters`). No value
is an object. `reason_code` is `^[A-Z][A-Z0-9_]{0,39}$`; role codes are
`^[A-Z][A-Z_]{1,29}$`; timestamps are RFC 3339 with an offset;
`parameters_digest` is 64 lowercase hex characters.

## Checks, in order

Each check raises before any row is written, with its own SQLSTATE and
constraint name. The caller maps every one to a 5xx: a refused record is the
producer's fault, not the client's.

| Order | SQLSTATE | Constraint name | Raised when |
|---:|---|---|---|
| 1 | `MA001` | `event_kind_not_permitted` | `kind` is null, not in the closed list, or not mapped to `session_user`. |
| 2 | `MA003` | `event_credential_refused` | Any key in `details`, at any depth, contains `password`, `hash`, `secret`, `token`, `key` or `credential` (case-insensitive), or any string in `details`, `subject_id` or `correlation_id` is shaped like a JWT (three base64url segments) or a password hash (`$argon2…`, `$2a$`, `$2b$`, `$2y$`). RN-29. |
| 3 | `MA002` | `event_details_invalid` | `details` is not an object, misses a required key, has a key the kind does not allow, or holds a value of the wrong type, enum or depth. |
| 4 | `MA004` | `event_invalid` | A common field breaks its rule: a null `event_id`, `outcome` or `occurred_at`; `occurred_at` more than five minutes ahead; a `USER` without `actor_user_id` or `actor_role`, or a `SYSTEM` with either; `REFUSED` outside `HISTORY_READ`; a subject missing or of the wrong type for the kind; `subject_id` without `subject_type` or the reverse; a model kind without `model_version_id`. |
| 5 | `MA005` | `event_id_conflict` | `event_id` already names an event recorded by another `session_user` or of another kind. |

The credential check runs before the key check so that a credential is always
reported as `MA003`, whichever key carries it, and it covers the whole payload
so it still holds if a kind's keys are later widened.

A repeated `event_id` from the same `session_user` and kind returns the stored
row with `created = false` and writes nothing, so a retry cannot double-count.

## Hash chain

Each event stores `prev_hash`, the `entry_hash` of the event recorded before it
(64 zeros for the first), and `entry_hash`, the SHA-256 of the event's canonical
form: the `jsonb` text of every column but `entry_hash`, with timestamps in UTC.
The function takes the transaction-scoped advisory lock `272001`
([`advisory-locks.md`](advisory-locks.md)) before it reads the head, so the
chain has one head. `GET /v1/domain-events/verification` recomputes it.

## Security and use

- `SECURITY DEFINER`, owned by `definer_audit`, with
  `search_path = public, pg_temp`. `EXECUTE` is revoked from `PUBLIC` and
  granted to each existing writer role above, and to nobody else. A writer
  whose role does not exist yet receives its grant in the pull request that
  creates the role.
- The schema owner owns `audit_events.domain_event`, not `definer_audit`
  ([ADR-0081](../../docs/adr/0081-the-domain-event-table-is-owned-by-the-schema-owner-and-a-subject-is-named-once-in-the-common-fields.md), which supersedes ADR-0072 section 6 on this point).
  `definer_audit` holds `INSERT` and `SELECT` on it: `SELECT` reads the chain
  head and a repeated `event_id`. No role but the schema owner holds
  `UPDATE`, `DELETE` or `TRUNCATE`, and a trigger refuses all three as a
  second layer.
- `svc_audit_trail` holds `SELECT` on the table. No other `svc_*` role and not
  `retail_app` holds any privilege on it, and the web does not call this
  function (ADR-0046).
- Grants are listed in [`grants.csv`](grants.csv) by the implementing pull
  request, with the grants in `sql/01_schema.sql`.

## Compatibility

Adding a kind, a writer, a `details` key or a `change`, `action`, `history`,
`resource` or filter value widens the allowed set and is a contract change
here and in `contracts/audit-trail/`, but not breaking for an existing caller.
Changing the signature, the result, an SQLSTATE or constraint name, removing an
allowed value, or relaxing a check is breaking. A breaking change ships
`api_audit.record_event_v2` beside `record_event_v1`, with its own `.v2.md`
contract; v1 is removed only after every producer has moved.
