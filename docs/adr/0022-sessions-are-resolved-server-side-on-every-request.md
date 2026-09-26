# ADR-0022 — Sessions are resolved server-side on every request

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #251
**Supersedes:** ADR-0007, in part — only its consequence that "the session's `role_code` is trusted until sign-out"
**Superseded by:** —

---

## Context

ADR-0007 put the permission matrix in code and let the gate read the role from
Flask's signed session cookie. It accepted a cost: "a role change or a
deactivation does not take effect in an open session".

The full-system QA pass of `develop` (#251) showed the cost is larger than
that sentence. The cookie is the whole session, so nothing on the server can
end it:

- A cookie copied before `POST /logout` still returned `200` on `/admin/users`
  afterwards, and stays valid for Flask's 31-day default. RF-02 requires "the
  session is invalidated server-side rather than only cleared in the browser".
- A user deactivated mid-session kept full access. RF-09 says deactivation "is
  how access is removed".

Both are requirements, not preferences, so the trade-off ADR-0007 accepted is
not available. What stays open is how much state to keep. A session table
costs one indexed read per signed-in request, on a single-VM monolith whose
database sits on the same machine (ADR-0001).

## Decision

The signed cookie carries only an opaque `session_id`, recorded in a new
`app_session` table at sign-in. A `before_request` hook, registered ahead of
the authorization gate, resolves it on every signed-in request with one query
over `app_session`, `app_user` and `role`. The query requires an open session
and an active user, and returns the current role. If it finds nothing, the
session is cleared and the gate sees an anonymous visitor. Otherwise the role
and name in the session are refreshed from the database. Signing out revokes
the row, and deactivating a user revokes all of theirs. The permission matrix
itself stays in code, exactly as ADR-0007 decided.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep the cookie session and re-check only `is_active` on each request | Fixes RF-09 but not RF-02: a copied cookie of a still-active user would survive sign-out. |
| A per-user `session_version` column on `app_user`, bumped on sign-out | Signing out one browser would sign out all of them. `app_user` is audited (RN-28), so every sign-out would also write an audit entry. |
| A server-side session extension (Flask-Session with a database backend) | Adds a dependency, and a table whose schema lives outside `sql/01_schema.sql`, which AGENTS.md forbids. |
| Shorten the cookie lifetime | Narrows the window without closing it; RF-02 asks for invalidation, not expiry. |

## Consequences

**What this makes easy.** Signing out, deactivation and role changes take
effect on the next request. An administrator can end a session by revoking
its row, with no redeploy and no secret-key rotation. Cookies issued before
this change carry no `session_id`, so they are simply signed out.

**What this makes hard.** Every signed-in request now costs one indexed query,
including requests the gate will refuse, so "a refused request opens no
connection" now holds only for anonymous visitors. `app_session` grows by one
row per sign-in and nothing prunes it yet. A periodic delete of rows revoked
long ago is cheap, but it is not part of this change. Route tests that sign in
by writing the cookie directly now depend on `tests/conftest.py` stubbing the
lookup; tests of the lookup opt out with `@pytest.mark.real_sessions`.

**What must now be true elsewhere.** Any new way of ending a user's access must
also revoke their sessions, as `set_active` does. The cookie must never again
be treated as the source of a user's role. `app_session` is seed-exempt, is not
audited, and is documented in `docs/data-model.md`.
