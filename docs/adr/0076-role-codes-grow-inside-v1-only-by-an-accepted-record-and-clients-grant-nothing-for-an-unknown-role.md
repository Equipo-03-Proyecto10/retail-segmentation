# ADR-0076 — Role codes grow inside v1 only by an accepted record, and clients grant nothing for an unknown role

**Status:** Accepted (2026-11-09)
**Owner:** Raquel
**Issue:** #754
**Supersedes:** —
**Superseded by:** —

---

## Context

[ADR-0060](0060-four-store-profiles-map-to-four-roles-and-services-read-each-users-stores-per-request.md)
adds three role codes, `SALES_ASSOCIATE`, `LOYALTY_OFFICER` and
`COMMERCIAL_SUPERVISOR`, and F19-04 must list them wherever a role appears. Two
v1 contracts publish the role vocabulary as a closed enum in their responses:
`role` in auth's `GET /v1/sessions/current`, and `role_code` across users'
responses, each with a matching `xs:enumeration` in the XSD.

[`contracts/README.md`](../../contracts/README.md) treats anything `oasdiff`
reports as breaking, and a breaking change ships `/v2` beside `/v1`. `oasdiff`
reports an added response enum value as an error
(`response-property-enum-value-added`), because a client written against the old
list may meet a value it does not expect. The rule is right in general. Applied
here it would ship `/v2` of auth and users, move every service and both clients
to it, and retire v1 under its own record, all to add three words.

The role vocabulary is unlike other enums. It changes only when a record decides
it (ADR-0041, then ADR-0060). It already lives in the `role` table, where codes
are immutable (RN-01). And the clients read the role as text: the Android client
admits only the roles in `FLOOR_ROLES`, and the desktop client refuses only
`CUSTOMER`. Neither crashes on a code it does not know. Every service enforces
its own `x-mosaiq-roles`, so a client that misjudges a role still gets a 403.

The judgement call is whether to loosen the gate, and how far.

## Decision

A closed vocabulary, the role codes now and only those, grows inside v1 when an
accepted record decides it and an entry in `contracts/vocabulary-growth.yaml`
names the record, the service, the exact contract version that adds the values,
and the values. `contracts/check.sh` then excuses those added enum values in
`oasdiff`'s report, and validates each XML response against the base XSD with
only those values added to its enumerations. Every other finding still fails,
and an entry stops applying once the contract moves to another version. A client
grants nothing for a role it does not recognize, and never fails because of one.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Ship `/v2` of auth and users | Every service and both clients move for three new words, and v1 needs its own retirement record. The cost is out of proportion to a change no client mishandles |
| Declare the role schemas with `x-extensible-enum` | It changes the published shape of two v1 schemas, and the XSD has no equivalent, so the XML check would still fail |
| Run `oasdiff` at WARN and ignore its errors for these two contracts | It turns off the whole gate for the two contracts that hold the security vocabulary, the place it matters most |
| A plain `oasdiff --err-ignore` file | It does not expire. An entry written for this change would still excuse the same finding in a later, unrelated one |

## Consequences

**What this makes easy.** A record that adds a role, such as ADR-0060, lands in
the contracts it touches without a new major, and the exception is visible in
review as one entry.

**What this makes hard.** A client written against the old list can now meet a
new role. The rule below is what makes that safe, and a client that breaks it is
a defect. A reviewer must check that each entry is backed by a record that
decides the vocabulary, not one that only needs it changed.

**What must now be true elsewhere.**
- `contracts/check.sh` reads `contracts/vocabulary-growth.yaml` as described, and
  `tests/test_contracts_check.py` covers it.
- `contracts/README.md` (Versioning) names this exception.
- The Android client keeps `FLOOR_ROLES` as an allow-list (F27-01 extends it),
  and the desktop client treats an unknown role as having no reads.

## Compliance

`tests/test_contracts_check.py` shows that an entry excuses only its values, only
at its version, and that an entry naming no record fails the check.
