# User stories

What the people who use MOSAIQ need from it, in their words.

**These are not the stories on the board.** [`backlog.md`](backlog.md) holds the
*delivery* stories — engineering work like "install PostgreSQL on the instance"
— because that is what the team schedules and estimates. The stories here are
*product* stories: what a role wants the application to do for them. The two
are traced to each other in the table at the end, and conflating them is what
makes a story list unreadable.

Ids are stable. Each story names the requirement it satisfies in
[`requirements.md`](requirements.md) and the delivery story that builds it.

---

## Signing in

### HU-01 — Sign in
**As a** member of staff, **I want** to sign in with my email and password, **so
that** the system knows who I am and what I am allowed to do.

- Given valid credentials, when I submit them, then I reach the home page signed in
- Given a wrong password, when I submit it, then I am told the credentials are invalid — without being told which half was wrong
- Given a deactivated account, when I sign in with correct credentials, then I am refused and told the account is inactive
- Given five consecutive failures, when I try again, then the attempt is refused for a cool-off period

`RF-01` · F3-03 (#63)

### HU-02 — Sign out
**As a** signed-in user, **I want** to sign out, **so that** nobody using this
machine after me inherits my session.

- Given I am signed in, when I sign out, then I am returned to the sign-in page
- Given I have signed out, when I press the browser's back button, then I see the sign-in page and not the cached page
- Given I have signed out, when the old session cookie is replayed, then it is not accepted

`RF-02` · F3-03 (#63)

### HU-03 — Be sent where I was going
**As a** user who followed a link while signed out, **I want** to land on the
page I asked for after signing in, **so that** I do not have to find it again.

- Given I request a protected page while signed out, when I sign in, then I arrive at that page
- Given no page was requested, when I sign in, then I arrive at the home page

`RF-03` · F4-01 (#69)

## Access by role

### HU-04 — Be refused what I may not see
**As the** organization, **we want** every page to check the signed-in user's
role before it runs, **so that** knowing a URL is never enough to reach it.

- Given a role without permission, when it requests the page, then the request is refused before the view runs and nothing about the content is revealed
- Given the refusal, when it is shown, then it explains that access is denied rather than showing a stack trace or a blank page
- Given the refusal, when it happens, then it is logged with the user, the route and the time

`RF-04` · F4-01 (#69)

### HU-05 — Keep exactly one administrator
**As the** organization, **we want** the system to hold one administrator and
only one, **so that** accountability for privileged changes is never ambiguous.

- Given an administrator exists, when a second is created through the application, then it is refused with a message explaining why
- Given an administrator exists, when a second is inserted directly into the database, then the database refuses it
- Given the sole administrator, when an attempt is made to demote or deactivate them, then it is refused — the system is never left without one

`RF-05` · F4-02 (#70)

## Catalogs

### HU-06 — Maintain the catalogs
**As the** administrator, **I want** to create, edit and remove categories,
products, stores, channels and roles, **so that** the data the business runs on
stays current without a developer.

- Given a catalog, when I open it, then I see its rows paginated with a search box
- Given a new row, when I save it with valid values, then it is stored and appears in the list
- Given an edit, when I save it, then the change is stored and recorded in the audit log
- Given invalid values, when I save, then each invalid field is marked with what is wrong and nothing is stored

`RF-06` · F3-04 (#64)

### HU-07 — Be stopped from breaking references
**As the** administrator, **I want** a deletion that would orphan data to be
refused in plain language, **so that** I understand the consequence instead of
seeing a database error.

- Given a category with products, when I delete it, then it is refused and I am told how many products still reference it
- Given a customer with sales, when I delete them, then it is refused and I am told sales exist
- Given a row nothing references, when I delete it, then it is removed and the deletion is recorded in the audit log

`RF-07` · F3-04 (#64)

### HU-08 — Put a picture on a product
**As the** administrator, **I want** to attach an image to a product, **so that**
staff recognise it in a list rather than reading SKUs.

- Given a JPEG, PNG or WebP under 5 MB, when I upload it, then it is stored and shown on the product
- Given a file of another type or over the limit, when I upload it, then it is refused with the reason and nothing is stored
- Given a product with an image, when I replace it, then the new file is stored and the old path no longer resolves from the product
- Given a product without an image, when it is listed, then a placeholder is shown rather than a broken image

`RF-08` · F3-07 (#67)

### HU-09 — Manage who has access
**As the** administrator, **I want** to create users, change their role and
deactivate them, **so that** access matches who actually works here.

- Given a new user, when I create them with an email and a role, then the account exists and can sign in
- Given an existing email, when I reuse it, then creation is refused
- Given a user who has left, when I deactivate them, then they can no longer sign in and their name still appears against their past actions in the audit log
- Given any of these, when it happens, then it is recorded in the audit log without the password hash

`RF-09` · F3-06 (#66)

## Consulting information

### HU-10 — Find a customer or a product
**As an** analyst, **I want** to search and filter customers and products, **so
that** I can answer a question without writing SQL.

- Given a list, when I search by name or SKU, then only matching rows are shown
- Given a result, when I open it, then I see its detail including its category, its channel and its current segment
- Given no matches, when the search returns, then I am told so rather than shown an empty table with no explanation
- Given more results than a page, when I page through them, then the search stays applied

`RF-10`, `RF-13` · F3-05 (#65)

### HU-11 — Check stock
**As an** inventory planner, **I want** to see stock by store and product, **so
that** I can spot what needs replenishing.

- Given the inventory view, when I open it, then I see quantity on hand per store and product with its last update time
- Given a store, when I filter by it, then only its stock is shown
- Given a quantity below a threshold, when it is listed, then it is visually distinguishable from healthy stock

`RF-11` · F3-05 (#65)

## The main process

### HU-12 — Recalculate segments
**As the** administrator, **I want** to recalculate every customer's segment
from the sales actually recorded, **so that** segments reflect behaviour rather
than a manual guess.

- Given recorded sales, when I run the recalculation, then each customer is scored on recency, frequency and monetary value over the chosen window and placed in the segment whose rule matches
- Given a customer with no sales in the window, when the recalculation runs, then they are left unassigned rather than kept in a stale segment
- Given the run finishes, when the summary is shown, then it reports customers processed, segments assigned, how many matched no rule, and how long it took
- Given a customer's segment changed, when the run finishes, then the change is in the audit log; if it did not change, there is no entry
- Given I am not the administrator, when I try to run it, then it is refused

`RF-12` · F3-10 (#102)

## Audit

### HU-13 — Trace a change
**As an** auditor, **I want** to read what changed, when, and who changed it,
**so that** I can answer a compliance question without database access.

- Given entries exist, when I open the audit view, then they are listed newest first with entity, action, actor and time
- Given a specific entity or date range, when I filter by it, then only matching entries are shown
- Given one entry, when I open it, then I see the values before and after, with the changed fields distinguishable
- Given an entry the seed produced, when I open it, then the actor is shown as unattributed rather than invented
- Given I am not an administrator or auditor, when I request the view, then it is refused

`RF-14` · F3-11 (#103)

### HU-14 — Never find a credential in the log
**As the** organization, **we want** the audit log to carry no password hashes,
**so that** a trail kept for years is not a second place credentials can leak
from.

- Given a user is created or edited, when the audit entry is written, then it contains no `password_hash` field
- Given the audit view, when an `app_user` entry is opened, then no credential is displayed from any source

`RNF-17` · already enforced by `fn_audit()`; verified as case P3 in [`evidence/f2-07-integrity-verification.md`](evidence/f2-07-integrity-verification.md)

## Running it

### HU-15 — Bring the whole thing up with one command
**As a** developer joining the team, **I want** one command to start a working
environment, **so that** my first day is not spent installing PostgreSQL and
ordering scripts by hand.

- Given a clean clone and a copied `.env`, when I run `docker compose up`, then the database is created, schemad, seeded, and the application answers
- Given the stack is up, when I query the database, then all 19 tables exist and exactly one administrator does
- Given I bring it down and up again, when it starts, then it reaches the same state with no manual step

`RNF-13`, `RNF-15` · F3-09 (#89)

---

## Traceability

| Story | Requirement | Delivery story | Issue |
|---|---|---|---|
| HU-01, HU-02 | RF-01, RF-02 | F3-03 | #63 |
| HU-03, HU-04 | RF-03, RF-04 | F4-01 | #69 |
| HU-05 | RF-05 | F4-02 | #70 |
| HU-06, HU-07 | RF-06, RF-07 | F3-04 | #64 |
| HU-08 | RF-08 | F3-07 | #67 |
| HU-09 | RF-09 | F3-06 | #66 |
| HU-10, HU-11 | RF-10, RF-11, RF-13 | F3-05 | #65 |
| HU-12 | RF-12 | F3-10 | #102 |
| HU-13 | RF-14 | F3-11 | #103 |
| HU-14 | RNF-17 | F2-05 | #58 — done, in review |
| HU-15 | RNF-13, RNF-15 | F3-09 | #89 |

Fifteen product stories, eleven delivery stories, one of them merged into review.
Every story except HU-14 needs F3-02 (#62) — the database connection — before it
can start.
