# Runbook — the administrator on the instance

The procedure that gives a deployed instance an administrator whose password
was never in this repository, and closes the seeded accounts that share the
published one. F4-06 (#107).

Run it once, on the instance, after the three SQL scripts and before the
instance is shown to anybody. It is safe to re-read: every command here says
what the instance looks like afterwards.

## Before you start

The password is read from `MOSAIQ_ADMIN_PASSWORD`, or asked for at the prompt
when that variable is unset. It is never a command-line argument — that would
put it in the shell history and in the output of `ps` — and it is never read
from a file in this repository.

Two refusals are worth knowing about in advance: the command will not install
`Password123!`, the password this repository publishes, and it will not install
anything shorter than 12 characters.

## 1. See what the instance currently holds

```bash
flask --app web.app account-report
```

On a freshly seeded instance:

```
30 accounts, 30 of them active.
  ADMIN              admin@mosaiq-demo.com ← published password
  ANALYST            user14@mosaiq-demo.com ← published password
  …
Demonstration accounts that can still sign in: 30
```

That last number is the one that matters. It must be `0` before the instance is
reachable from outside.

## 2. Install the real administrator

```bash
flask --app web.app provision-administrator \
    --name "Real Person" \
    --email person@udem.edu \
    --deactivate-demo-accounts
```

It asks for the password twice, or takes it from the environment:

```bash
read -rs MOSAIQ_ADMIN_PASSWORD && export MOSAIQ_ADMIN_PASSWORD
flask --app web.app provision-administrator --name "…" --email "…" --deactivate-demo-accounts
unset MOSAIQ_ADMIN_PASSWORD
```

```
person@udem.edu is now the administrator (there were 1 before).
Deactivated 30 demonstration accounts.
Administrators: 1. Demonstration accounts that can still sign in: 0.
```

**What it does, and why in that order.** RN-01 allows exactly one
administrator, which leaves no legal way to promote a successor directly:
creating a second is refused, and demoting the incumbent first is refused as
well. So the new account is created in a placeholder role and the role is moved
onto it in one transaction, the outgoing administrator inheriting the
placeholder. The instance never holds two administrators and never holds none,
including if the command fails part way — nothing is committed until all of it
succeeds.

The thirty seeded rows are **deactivated, not deleted**: `audit_log.user_id`
points at them, and deleting would turn every entry they produced into an
unattributed one. `authenticate` refuses an inactive account, so the published
password opens nothing. The reasoning, and the alternatives, are in
[ADR-0008](adr/0008-the-instance-keeps-the-demonstration-accounts-deactivated.md).

## 3. Confirm

```bash
flask --app web.app account-report
```

```
31 accounts, 1 of them active.
  ADMIN              person@udem.edu
Demonstration accounts that can still sign in: 0
```

Then sign in through the application once, as that address, and confirm the old
credentials are refused.

## Changing a password later

```bash
flask --app web.app rotate-password --email person@udem.edu
```

Same two sources for the password, same two refusals. This is also the answer
when the administrator's address stays and only the credential needs replacing.

## If the instance ends up with no administrator

`provision-administrator` handles it: with the seat empty it creates the account
as the administrator directly, and says `(there were 0 before)`. Nothing else
in the application can leave the instance in that state — demoting or
deactivating the last administrator is refused, F4-02 (#70).

## What the graders can sign in as

Only accounts the team creates. The seeded analyst, auditor and customer logins
are closed on the instance by design; a non-administrator view is demonstrated
with a real account created through the user module, F3-06 (#66). That trade is
ADR-0008's, and it is deliberate.
