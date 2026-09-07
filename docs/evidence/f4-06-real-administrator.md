# F4-06 — The real administrator on the instance

Evidence that a deployed instance can be given an administrator whose password
was never in this repository, and that the published one stops opening
anything.

## How this run was produced

A database built from the three ordered scripts, exactly as a fresh instance
is, and then the documented procedure from
[`runbook-instance-accounts.md`](../runbook-instance-accounts.md) run against
it.

```bash
psql -d retail_f406 -f sql/01_schema.sql -f sql/02_seed_30_per_table.sql
export DATABASE_URL=postgresql://retail_app:retail_app@127.0.0.1:5432/retail_f406
```

## Before

```
$ flask --app web.app account-report
30 accounts, 30 of them active.
  ADMIN              admin@mosaiq-demo.com ← published password
  ANALYST            user14@mosaiq-demo.com ← published password
  …
Demonstration accounts that can still sign in: 30
```

## What the command refuses

```
$ MOSAIQ_ADMIN_PASSWORD='Password123!' flask --app web.app provision-administrator …
Error: That is the password this repository publishes for the seeded accounts.
       The instance must not be reachable with it.

$ MOSAIQ_ADMIN_PASSWORD='short' flask --app web.app provision-administrator …
Error: The password must be at least 12 characters.
```

The refusal names the published password specifically, and
`tests/test_instance_administrator.py` checks that the string it refuses is
still the one `02_seed_30_per_table.sql` actually uses — so changing the seed's
password without changing the refusal fails the build.

## The procedure

```
$ flask --app web.app provision-administrator \
      --name "Real Person" --email real@udem.edu --deactivate-demo-accounts
real@udem.edu is now the administrator (there were 1 before).
Deactivated 30 demonstration accounts.
Administrators: 1. Demonstration accounts that can still sign in: 0.
```

## After

```
$ flask --app web.app account-report
31 accounts, 1 of them active.
  ADMIN              real@udem.edu
Demonstration accounts that can still sign in: 0
```

Checked directly against the database:

```
=== can anyone still sign in with the published password? ===
  admin@mosaiq-demo.com      refused
  user2@mosaiq-demo.com      refused
  user7@mosaiq-demo.com      refused

=== the real administrator ===
  real@udem.edu with its own password: signed in as ADMIN
  real@udem.edu with the published one: refused

  administrators in the database: 1
  the seeded administrator row: ('admin@mosaiq-demo.com', False)
  app_user audit entries: 63
```

The seeded administrator's row is still there and is inactive. That is
ADR-0008's decision: `audit_log.user_id` points at these rows, and deleting
them would turn every entry they produced into an unattributed one. RN-04 says
the same thing from the other direction — a user with history is deactivated,
never deleted.

The 63 `app_user` audit entries are exactly what the procedure should have
produced: 30 from the seed, 1 for the new account, 2 for the role transfer, and
30 deactivations.

## Never zero, never two

F4-02's partial unique index is in force throughout, and the procedure has to
work around it rather than against it. Promoting the successor first is refused
because the seat is taken; demoting the incumbent first is refused because it
would leave nobody. So the account is created in a placeholder role and the
role is moved onto it in one transaction — the instance holds one administrator
before, one after, and never two in between. Nothing is committed until all of
it succeeds, so a failure leaves the administrator that was there.

The other state a deployed instance can be in is handled too. With the seat
empty:

```
  administrators before: 0
$ flask --app web.app provision-administrator --name "Second Person" --email second@udem.edu
second@udem.edu is now the administrator (there were 0 before).
Administrators: 1. Demonstration accounts that can still sign in: 0.
```

## Rotating a password afterwards

```
$ flask --app web.app rotate-password --email second@udem.edu
The password for second@udem.edu has been changed.
  old password: refused
  new password: signed in
```

## Where the password comes from

`MOSAIQ_ADMIN_PASSWORD`, or the prompt — and nowhere else. It is not a
command-line option, so it does not reach the shell history or `ps`; when typed
it is asked for twice and never echoed; and `web/cli.py` opens no file, which a
test asserts. `.env.example` documents the variable with no value, because a
password with a safe default is not one.

## Not evidenced here

The run on the actual GCP instance. This is the procedure, verified against a
database built exactly the way the instance's is; performing it there and
capturing the result is part of F6-07 (#109), which records the deployed
state.
