# ADR-0008 — The instance keeps the demonstration accounts, deactivated

**Status:** Accepted
**Owner:** Marcelo
**Issue:** #107 (F4-06)
**Supersedes:** —
**Superseded by:** —

---

## Context

`sql/02_seed_30_per_table.sql` creates thirty accounts sharing the password
`Password123!`, written in this repository on a public branch. That is correct
locally and for the demonstration, and it is a hole on a published host: with
the permission matrix in place, those accounts are not equivalent to each other
— one reads the audit log, one reads every catalog, and one is the
administrator.

F4-06 (#107) asks for the decision to be recorded rather than made by accident,
and states the cost of each answer. Keeping the accounts as they are makes the
demonstration easy and leaves twenty-nine known-password logins on a public
host. Deleting them makes the demonstration run against data the graders cannot
sign in to — and deleting is not free either: `audit_log.user_id` references
`app_user` with `ON DELETE SET NULL`, so removing the rows would silently turn
every entry those users produced into an unattributed one, destroying exactly
the traceability the audit log exists for. RN-04 already says a user with
history is deactivated, never deleted.

## Decision

The instance keeps all thirty demonstration rows and **deactivates** them,
while the administrator role moves to a real account whose password was never in
this repository. `authenticate` refuses an inactive account, so the published
password opens nothing; the rows stay, so `audit_log` keeps its actors. The
transition is one command — `flask --app web.app provision-administrator
--deactivate-demo-accounts` — and `account-report` states afterwards, in one
line, how many demonstration accounts can still sign in.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Delete the demonstration accounts on the instance | `audit_log.user_id` is `ON DELETE SET NULL`: every entry they produced would become unattributed, which is a worse outcome than the accounts existing but being closed. It also contradicts RN-04 |
| Keep them active, rotate only the administrator | Twenty-nine known-password logins remain on a public host, and the matrix gives some of them real reach — `AUDITOR` reads the audit log, every staff role reads the catalogs |
| Keep them active with rotated passwords | Twenty-nine more credentials to distribute and store somewhere, for accounts nobody signs in to. The demonstration is driven by the team's own accounts |
| Give the instance its own seed without `app_user` rows | The three ordered scripts must run clean, in order, from empty — a second seed for the instance is a second thing to keep correct, and CI would only check one of them |

## Consequences

**What this makes easy.** The published password stops working everywhere in
one command, and the audit trail keeps naming who did what. A demonstration
account can be reopened for a specific need by reactivating one row, which is a
deliberate act somebody performs rather than a default.

**What this makes hard.** The graders cannot sign in as a seeded analyst or
auditor to see a non-administrator's view: the team has to create real accounts
for the roles it wants to show, through F3-06 (#66). The instance's `app_user`
table also carries thirty rows nobody uses, which is untidy and is the price of
keeping the log attributable.

**What must now be true elsewhere.** F6-07 (#109) records the state of the
instance as part of the deployment evidence, and `account-report` is what it
quotes. F6-08 (#110) rehearses the demonstration with real accounts, not
`user2@mosaiq-demo.com`. If a later story ever deletes an `app_user` row, it
inherits the attribution problem this record rejected.

## Compliance

`flask --app web.app account-report` prints the number of demonstration
accounts that can still sign in; on the instance that number is `0`. The
provisioning command prints the same number after it runs and says what to do
when it is not zero, and
[`evidence/f4-06-real-administrator.md`](../evidence/f4-06-real-administrator.md)
records a full run against a freshly seeded database, including the check that
the published password no longer authenticates anywhere.
