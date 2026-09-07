# ADR-0007 — Permissions are declared in code and enforced by a default-deny middleware

**Status:** Accepted
**Owner:** Marcelo
**Issue:** #69 (F4-01)
**Supersedes:** —
**Superseded by:** —

---

## Context

[`requirements.md`](../requirements.md) §3 carries the permission matrix and
says of itself that it "is a specification; until that story lands, it is a
promise and not a control". F4-01 (#69) makes it a control, and doing so forces
two decisions that later stories inherit and cannot easily reverse — seven
issues are blocked on this one.

**Where the matrix lives.** The obvious relational answer is `permission` and
`role_permission` tables: the project is graded on a normalized model, and
"roles and permissions" reads like data. Against that: the matrix is a
specification the team agrees on in review and ships, not something an
administrator retunes at runtime. `segment_rule` is in the schema precisely
because an administrator *does* retune it without a deployment
([ADR-0004](0004-model-ahead-of-the-deferred-segmentation-modules.md)); nothing
in [`scope.md`](../scope.md) or the backlog asks for the same of permissions,
and RF-06 gives the administrator CRUD over `role`, not over grants. Two tables
would also put a second copy of the matrix next to the canonical one, and
`data-model.md` already refuses to hold a second copy for exactly that reason.

**What an undeclared route does.** This is the part that was genuinely
uncertain. Allowing undeclared routes makes every future story's mistake
invisible until somebody reads the diff carefully; refusing them makes the
mistake loud, but it also means a developer who adds a page and forgets a
decorator sees a 403 on their own work and has to be told why.

## Decision

The permission matrix is a frozen mapping from `role.code` to a set of
permission names in `web/middleware/authz.py`, transcribing
[`requirements.md`](../requirements.md) §3, which remains canonical; the
database holds `role` and nothing else about authorization. Every view declares
what reaching it needs — `@public`, or `@requires(...)` — and one
`before_request` gate in `web/middleware/` enforces the declaration before the
view runs, **refusing anything undeclared**. Menus are built from the caller's
permission set, never from their role code.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| `permission` and `role_permission` tables, joined per request | Puts a second copy of the matrix a schema change away from the canonical one in `requirements.md` §3, adds two tables to a 4NF model already reviewed, needs two more `sql/seed-exempt.txt` entries, and buys runtime editability nothing in scope asks for |
| Allow undeclared routes, protect them with a decorator per view | The failure mode is silence: a page ships reachable by everybody and nothing says so. The issue's own wording — "so that a new route cannot be left unprotected by accident" — is a request for the opposite |
| Check the role code in each view (`if session["role_code"] != "ADMIN"`) | Puts the same check in every view, so it is enforced as many times as somebody remembers to write it, and hard-codes roles where the matrix should speak |
| Re-read the role from the database on every request | One query per request to defend against a role change that, in a system with one administrator and no self-service role editing, only the administrator can make |

## Consequences

**What this makes easy.** A new page is one decorator away from being enforced,
and forgetting the decorator fails the test suite rather than shipping.
Reviewing "who may reach this" is reading one module against one table in
`requirements.md`. The menu follows the matrix automatically, so a permission
change never leaves a link visible that leads to a refusal.

**What this makes hard.** The matrix changes only by deployment: an
administrator cannot grant a permission from the interface, and nothing in the
application offers to. The session's `role_code` is trusted until sign-out, so
a role change or a deactivation does not take effect in an open session —
acceptable while only the administrator changes roles, and worth revisiting if
self-service role editing is ever in scope. Row scoping is *not* covered: the
matrix's `own` and `own store` qualifiers are enforced by the queries in F3-05
(#65) and F3-11 (#103), because a route-level gate decides whether a view runs,
not which rows it selects.

**What must now be true elsewhere.** Every story that adds a route declares its
requirement; F3-12 (#106) builds the shell on the menu mechanism rather than on
role names; F4-02 (#70) adds the single-administrator rule on top of this gate,
not beside it; F5-02 (#75) writes its negative tests against these refusals.

## Compliance

`tests/test_authz.py::test_every_registered_endpoint_declares_what_it_requires`
walks `app.view_functions` and fails when any endpoint carries no declaration —
the default-deny half is executable, not a review convention. The other half is
checked by `test_the_matrix_covers_exactly_the_seeded_roles`, which reads the
role codes out of `sql/02_seed_30_per_table.sql` and asserts they are exactly
the keys of the matrix, so a role added to the seed without a permission set is
a failing build rather than a silent hole.
