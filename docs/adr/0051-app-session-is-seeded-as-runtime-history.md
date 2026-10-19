# ADR-0051 — `app_session` is seeded as runtime history, not exempted

**Status:** Accepted
**Owner:** Marcelo
**Issue:** #463
**Supersedes:** ADR-0022, in part: only its statement that "`app_session` is seed-exempt"
**Superseded by:** —

---

## Context

[ADR-0022](0022-sessions-are-resolved-server-side-on-every-request.md) introduced
`app_session` and, among its consequences, called it seed-exempt. The seed rework
for #463 (merged in #534) reversed that. `sql/02_seed_30_per_table.sql` now gives
`app_session` thirty rows, one finished sign-in per demonstration account, all
revoked. `sql/seed-exempt.txt` says why: an exemption is for a domain that really
is small (roles, channels, labels), and session history is not small. It is only
empty on a fresh database. ADR-0047 treats its notification tables the same way.

An accepted record is never edited to change what it decided, so ADR-0022 still
says the opposite of the seed and of `seed-exempt.txt`. The record has to say
which one is binding.

## Decision

`app_session` is not seed-exempt. The seed gives it at least thirty rows of
finished, revoked sign-ins, which can never authenticate a request because
`load_principal()` requires `revoked_at IS NULL`. The exemption list stays
reserved for tables whose real domain is smaller than thirty rows. The rest of
ADR-0022 is unchanged, including that `app_session` is not audited.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep the exemption and drop the seeded sessions | An empty session table shows no sign-out or revocation history, and the exemption would contradict the rule #463 applied to `sales_load` and `sales_load_rejection`: runtime history is seeded, not exempted |
| Leave ADR-0022 as it is and explain the gap only in `seed-exempt.txt` | The decision record would keep contradicting the schema it governs, which is the kind of drift graders and reviewers check for |

## Consequences

**What this makes easy.** The seed, `sql/seed-exempt.txt`, CI's 30-row check and
the decision records agree. A reader of ADR-0022 is sent here for the seed.

**What this makes hard.** Every seeded session must stay revoked. A seeded open
session would be a valid credential for whoever knows its fixed id.

**What must now be true elsewhere.**
- ADR-0022's status reads `Partially superseded by ADR-0051`.
- `app_session` is never named in `sql/seed-exempt.txt`.
- Each seeded `app_session` row has `revoked_at` set.

## Compliance

- CI's "Every table has at least 30 rows" step counts `app_session`, and
  `grep -x app_session sql/seed-exempt.txt` prints nothing.
- On a database built from the three scripts,
  `SELECT count(*) FROM app_session WHERE revoked_at IS NULL` returns 0.
