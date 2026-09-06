# F3-11 — The audit log, read from the application

Evidence for RF-14 and RNF-17. The table and its triggers landed with F2-05;
until now nothing in the application read them, so the demonstration had no way
to show the audit trail without opening a database client.

## How this run was produced

The application from this branch against the seeded Compose database. One
change was made *through the application* first — renaming store 3 — so the log
holds an attributed entry next to the seed's unattributed ones:

```bash
docker compose up -d db
DATABASE_URL=postgresql://retail_app:retail_app@127.0.0.1:5432/retail python -m web.app
```

## The list

```
Audit log
Every change to a catalog or a business rule, newest first. The log is
append-only: nothing here edits or removes an entry.

Entity: All · app_user · campaign · category · customer · experiment ·
        product · segment · segment_rule · store      From __ To __  [Filter] [Clear]

When              Entity      Key  Action  By                     Entry
2026-09-06 06:35  store       3    UPDATE  MOSAIQ Administrator   Open
2026-09-06 01:36  experiment  30   INSERT  unattributed           Open
2026-09-06 01:36  experiment  29   INSERT  unattributed           Open
…
Page 1 of 12 · 281 entries
```

The first row is the change the application made, credited to the signed-in
administrator because F4-01 names the actor on the connection. Everything below
it came from the seed, where the actor is genuinely unknown — shown as
`unattributed`, never invented, and never hidden.

The entity options come from the log itself rather than from a hard-coded list,
so an entity appears in the filter exactly when the log holds entries for it.

## Filtering and paging

| Filter | Result |
|---|---|
| none | Page 1 of 12 · 281 entries |
| `entity=store` | Page 1 of 2 · 31 entries |
| `entity=app_user` | Page 1 of 2 · 30 entries |
| `from=2026-09-06` | 281 entries |
| `from=2026-09-07` | No entries match those filters |
| `to=2026-09-05` | No entries match those filters |

`to` is inclusive of its whole closing day: the column is a timestamp and the
filter is a date, so `<= date` would silently drop everything after midnight.

A filter that cannot be read — `from=not-a-date`, `page=abc`, an entity the log
does not hold — is dropped and the page renders unfiltered. The value arrives
from a query string a person may have edited by hand, and an error page about
date formats is a worse answer than the log itself.

## One entry, before and after

Entry 283, the store rename:

```
store 3 · UPDATE · 2026-09-06 06:35:32
By: MOSAIQ Administrator          Fields changed: 2 of 5

  changed   city        San Nicolás    →  Monterrey
  same      is_active   true           →  true
  changed   name        Store 3        →  Monterrey Centro
  same      state       Nuevo León     →  Nuevo León
  same      store_id    3              →  3
```

Changed fields are distinguishable from unchanged ones by three signals, not by
colour alone: the row is tinted, the field name is bold, and it carries the word
`changed`. An INSERT has no before and a DELETE has no after, and every field of
those counts as changed — which is true, because the whole row arrived or left.

## No password hash, from either direction

An `app_user` entry (id 150) shows six fields:

```
  fields shown: ['created_at', 'email', 'is_active', 'name', 'role_id', 'user_id']
  the string "argon2" anywhere on the page: False
  the string "password_hash" anywhere on the page: False
```

Two independent reasons it is absent: `fn_audit()` strips `password_hash`
before the entry is ever written, and `web/services/audit.py` refuses to render
a field by that name even if a payload somehow carried one. The acceptance
criterion asks that the view not reintroduce it from elsewhere; the way to keep
that true is that the view cannot render it at all.

## Read-only, and only for two roles

```
  auditor  -> 200
  analyst  -> 403
  analyst on a detail page -> 403
```

`ADMIN` and `AUDITOR` read it; the other five roles are refused by the
authorization middleware, before the view runs. Signed out, the list redirects
to sign-in and returns there afterwards.

Nothing in the application can write to the log. Both routes are `GET`, a
`POST` to either answers 405, the only form that submits on the page is the
shell's sign-out, and `web/services/audit.py` has no function that writes —
RNF-17 is kept by giving the application no way to try.

## Not evidenced here

Screenshots at 375 px and 1440 px. The page reuses the `.table-scroll` and
`.listing` patterns verified at both widths in
[`f3-12-application-shell.md`](f3-12-application-shell.md), but the filter bar
is new and has not been looked at; a reviewer opens `/audit/` at both widths
before approving.
