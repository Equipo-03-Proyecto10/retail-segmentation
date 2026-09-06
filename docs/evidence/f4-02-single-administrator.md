# F4-02 — Exactly one administrator, refused twice

Evidence for RN-01 and RF-05. `AGENTS.md` states the rule and the reason it
needs both halves: "The application check alone is bypassed by a direct
`INSERT`; the index alone surfaces as an unexplained database error. Both, or
neither counts."

So the interesting evidence is not that the rule holds — it is that each half
holds **with the other one removed**, which is the issue's third acceptance
criterion and the one a unit test cannot fake.

## How this run was produced

Two databases were built from the same two scripts against the running
container, and the index was then dropped from the second:

```bash
psql -d retail_f402         -f sql/01_schema.sql -f sql/02_seed_30_per_table.sql
psql -d retail_f402_noindex -f sql/01_schema.sql -f sql/02_seed_30_per_table.sql
psql -d retail_f402_noindex -c "DROP INDEX ux_app_user_single_administrator"
```

Both scripts ran clean with the index in place, and the seed's single
administrator did not trip it — Definition of Done item 3.

## The schema half, with the application out of the picture

`sql/verify_integrity.sql`, run as `psql`, no Python anywhere near it:

```
-- N17: a second administrator, inserted directly [expect: 23505 unique_violation]
ERROR:  duplicate key value violates unique constraint "ux_app_user_single_administrator"
DETAIL:  Key (role_id)=(1) already exists.

-- N18: promoting a second user to administrator [expect: 23505 unique_violation]
ERROR:  duplicate key value violates unique constraint "ux_app_user_single_administrator"
DETAIL:  Key (role_id)=(1) already exists.

-- P5: the administrator row still takes ordinary updates
UPDATE 1
 P5 renamed: Rotated administrator
```

`P5` is there because a rule that also blocked renaming the administrator would
break the rotation F4-06 (#107) has to perform. The index constrains how many
rows hold `role_id = 1`, not what else those rows say.

The whole script now runs 5 positive and 18 negative cases; all 18 were refused
and no positive case errored.

## What the database does when the index is gone

```
BEGIN;
INSERT INTO app_user (role_id, name, email, password_hash)
VALUES (1, 'Second', 'second@x.com', 'h');
 administrators now: 2
```

Two administrators, no complaint. This is what the application check alone
would leave standing.

## The application half, on that same index-less database

```
### the application half alone — index dropped
  administrators in this database: 1
  create_user(role_code='ADMIN')             -> REFUSED: This system has exactly one administrator, and the role is already taken.
  change_role(analyst -> ADMIN)              -> REFUSED: This system has exactly one administrator, and the role is already taken.
  change_role(administrator -> ANALYST)      -> REFUSED: This is the only administrator, and the system may not be left without one.
  set_active(administrator, False)           -> REFUSED: This is the only administrator, and the system may not be left without one.
  set_active(analyst, False)                 -> allowed
  create_user(role_code='ANALYST')           -> allowed
  transfer_administrator(admin -> analyst)   -> allowed
```

The same seven operations behave identically on the indexed database. Note the
two directions: the index can only refuse a *second* administrator, and the
last four lines are the half no index can express — refusing to leave the
system with *none*, while leaving ordinary user administration alone.

## The race the count check cannot win

`count_administrators` and the write are two statements, so two requests can
both pass the check. The database settles it, and the loser must still read a
sentence about the rule rather than a constraint name:

```
### the race the count check cannot win (indexed database)
  administrators both connections see: 0
  connection A: passed the check, inserted, not yet committed
  connection B: passed the same check, its INSERT is blocked on the index
  connection A: committed
  connection B: REFUSED: This system has exactly one administrator, and the role
                is already taken. Move the current administrator to another role
                first, or choose a different role for this user.
```

Connection B's error is a real `psycopg.errors.UniqueViolation` raised by
PostgreSQL, translated by `web/services/users.py` into the same message the
application's own check produces. This is the case the unit tests can only
stand in for.

## How the seat legitimately changes hands

The two refusals together would deadlock a rotation: promoting the successor is
refused because the seat is taken, and demoting the incumbent is refused because
it would leave nobody. Both are correct in isolation, and F4-06 (#107) still has
to replace the seeded administrator on the instance.

`transfer_administrator` is the one operation that does both, demoting before
promoting so the index is never asked to hold two at once, inside the caller's
transaction. Without it the instance procedure would have to bypass the service
with raw SQL — which is the outcome the rule exists to prevent.

## Not evidenced here

The user-management screens: listing, forms and the messages a person actually
reads are F3-06 (#66), which calls these three functions. What this story
delivers is the rule and the code path every write to `app_user` goes through.
