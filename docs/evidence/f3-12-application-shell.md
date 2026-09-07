# F3-12 — The authenticated shell

Evidence that a signed-in user lands somewhere, is told who they are, and can
move between the sections their role opens. Every item on the demonstration
list is reached through this shell, which is why it exists before the modules
that hang off it.

## How this run was produced

The application from this branch against the seeded Compose database. Four
routes stood in for pages later stories build — `catalog.index` (F3-04, #64),
`users.index` (F3-06, #66), `audit.index` (F3-11, #103) and `segment_run.index`
(F3-10, #102) — carrying nothing but the `@requires(...)` their stories will
carry. The shell, the navigation and the landing page are the shipped ones.

## Signing in lands on a page that names me and my role

```
POST /login -> 302  Location: /
GET  /      -> MOSAIQ Administrator · System administrator · 30 Customers …
```

The role is read from `role.description` rather than shown as `ADMIN`: the
code is what the matrix is keyed by, not what a person should be asked to read.
Both come from the database at render time, not from the session, so a renamed
or re-roled account shows what is true now.

## The same page, three roles

| Signed in as | Menu | Figures | Recent changes |
|---|---|---|---|
| `ADMIN` | Home · Catalogs · Users · Segment run · Audit log | Customers, Products, Stores, Active users | shown |
| `ANALYST` | Home · Catalogs | Customers, Products, Stores | hidden |
| `CUSTOMER` | Home | none | hidden |

The figures follow the permission matrix for the same reason the navigation
does: a dashboard that counted products for a loyalty customer would leak
exactly what `requirements.md` §3 says that role may not read. The customer is
told plainly that their role opens none of them rather than being shown an
empty page.

## Hiding the link is not the control

The loyalty customer's menu holds one entry. The routes behind the other four
are still refused, which is the half that matters:

```
  /catalogs     -> 403
  /users        -> 403
  /audit        -> 403
  /segment-run  -> 403
```

## The current section is marked

`Home` carries `aria-current="page"` and the underline on the landing page; on
`/audit` the marking moves to `Audit log`. The marking is by blueprint, not by
endpoint, so a detail page inside a section still marks the section it belongs
to once those pages exist.

## Unattributed entries are shown as unattributed

Every audit row the seed produced has `user_id` NULL, because the actor is
genuinely unknown there. The shell renders that as `unattributed` rather than
inventing an actor or dropping the row. Changes the application makes are
credited — see [`f4-01-authorization.md`](f4-01-authorization.md).

## At 375 px and at 1440 px

| | |
|---|---|
| Administrator, 1440 px | [`f3-12-shell-admin-1440.png`](f3-12-shell-admin-1440.png) |
| Administrator, 375 px | [`f3-12-shell-admin-375.png`](f3-12-shell-admin-375.png) |
| Analyst, 1440 px | [`f3-12-shell-analyst-1440.png`](f3-12-shell-analyst-1440.png) |

At 375 px the five entries wrap to a second line, the figures fall into two
columns, and the audit table scrolls inside its own box — the page itself never
scrolls sideways. At 1440 px the whole menu sits on the brand's line and the
figures form one row.

## Not evidenced here

The sections themselves. This story builds the shell and the landing page; the
pages the navigation points at are F3-04 (#64), F3-05 (#65), F3-06 (#66), F3-10
(#102) and F3-11 (#103), and each lights up its own entry by registering its
blueprint.
