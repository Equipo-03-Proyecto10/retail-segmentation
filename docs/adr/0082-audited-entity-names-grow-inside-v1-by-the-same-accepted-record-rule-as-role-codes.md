# ADR-0082 — Audited entity names grow inside v1 by the same accepted-record rule as role codes

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #1023
**Supersedes:** in part [ADR-0076](0076-role-codes-grow-inside-v1-only-by-an-accepted-record-and-clients-grant-nothing-for-an-unknown-role.md) (the words "the role codes now and only those")
**Superseded by:** —

---

## Context

`audit_log.entity` holds the name of the table `fn_audit` audited (ADR-0046). audit-trail's contract
publishes those names as a closed enum, `AuditedEntity`, in its OpenAPI and its XSD. The enum named
13 tables. A database loaded from empty already holds entries for four more, the `product_*` tables
(#1023), and every PR that gives another table the trigger adds more: `loyalty_card`,
`users.store_assignment`, six `stores.*` tables. A JSON client that checks the enum, or Electron
validating an XML page against the XSD, then refuses a page that contains such an entry.

The decision to widen the enum to every audited table is made in #1023: an audit trail that filters
rows out hides history. What is left is how the contract carries the growth. `oasdiff` reports an
added response enum value as breaking (`response-property-enum-value-added`), and
`contracts/check.sh` lets a closed vocabulary grow inside v1 only through an entry in
`contracts/vocabulary-growth.yaml`, which ADR-0076 reserves to "the role codes now and only those",
and whose entry pattern accepts upper-case codes only. Without a record, the widening would ship
`/v2` of audit-trail, and every table that gains the trigger would ship another.

The vocabulary is like the role codes in the ways ADR-0076 relied on. It changes only when a schema
change changes it, and that change is reviewed. The set is not a guess: it is the tables that carry
the `fn_audit` trigger, a name no schema reuses (ADR-0046), and a test reads it from the database.
Clients read the entity as text: Electron lists it and filters by it, and neither acts on an
unknown name.

## Decision

The names in audit-trail's `AuditedEntity` are a closed vocabulary that grows inside v1 when an entry
in `contracts/vocabulary-growth.yaml` names this record, the service, the exact contract version that
adds the names, and the names. `contracts/check.sh` then excuses exactly those added enum values and
validates each XML response against the base XSD with only those values added, as ADR-0076 decided
for role codes. The entry pattern accepts identifiers in either case, since a table name is lower
case. The enum is every table that carries the `fn_audit` trigger: the OpenAPI enum, the XSD
enumeration, `ENTITIES` in `services/audit-trail/src/lib.rs` and the desktop client's list change in
one pull request, and a test fails when a table gains the trigger without them.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Ship `/v2` of audit-trail for each new audited table | The desktop client and the service move for a word, and v1 needs its own retirement record, each time a table is audited |
| `x-extensible-enum` on `AuditedEntity` | ADR-0076 rejected it for the same reason: it changes the published shape, and the XSD has no equivalent, so the XML check would still fail |
| Have audit-trail serve only the entities it lists | The trail would hide the history of the product, store and user tables it is there to show, which an audit trail must not do |
| A plain `oasdiff --err-ignore` file | It does not expire, so it would excuse the same finding in a later, unrelated change |

## Consequences

**What this makes easy.** A PR that audits a table adds one entry and the names in the four places,
with no new major. The guard test shows what is missing.

**What this makes hard.** A client written against an old list can meet a name it does not know. A
client must treat an unknown entity as text. A reviewer must check that each entry names the tables
that really gained the trigger.

**What must now be true elsewhere.**
- `contracts/check.sh` accepts lower-case identifiers in an entry's `values`, and
  `tests/test_contracts_check.py` covers it. `contracts/README.md` (Versioning) names this record.
- `tests/test_audit_entities_contract.py` compares the tables that carry `fn_audit` with the
  OpenAPI enum, the XSD, `ENTITIES` and the desktop list.

## Compliance

`tests/test_audit_entities_contract.py` fails when a table gains the trigger without the contract and
the two lists naming it, and `tests/test_contracts_check.py` shows a lower-case entry excusing only
its names at its version.
