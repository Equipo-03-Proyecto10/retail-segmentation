# ADR-0025 — State-changing requests carry a synchroniser CSRF token

**Status:** Accepted
**Owner:** Max
**Issue:** #294
**Supersedes:** —
**Superseded by:** —

---

## Context

The 2026-09-28 QA pass (#294) found that none of the 21 `method="post"` forms
in `web/templates` carries a CSRF token, `flask-wtf` is not a dependency, and
`web/middleware/authz.py` never checks `Origin` or `Referer`. The only
defence today is the session cookie's `SESSION_COOKIE_SAMESITE="Lax"`
(`web/security.py`).

What was verified: a forged POST carrying a valid session cookie and a
hostile `Origin`/`Referer`, sent directly with `requests`, deactivated a user
on a local replica — the server accepted it. From a real browser on another
site, `SameSite=Lax` withholds the cookie and the same attack is refused
(403, anonymous). **Not exploitable cross-site from a real browser today.**
The residual exposure is what `SameSite` alone does not cover: `SameSite` is
per *site*, so any page on another subdomain of the same registrable domain
is same-site and still gets the cookie on a POST; older or non-conforming
browsers ignore the attribute entirely; and the cookie is a single control
whose removal — a future `SameSite=None` for an embedded client, say — would
silently open every form again with nothing else standing behind it.

## Decision

A per-session random token, checked on every unsafe-method request
(`POST`/`PUT`/`PATCH`/`DELETE`), alongside an `Origin` check, as a second
layer behind `SameSite=Lax`, not a replacement for it.

`web/middleware/csrf.py` generates a token with `secrets.token_urlsafe(32)`
into the Flask session on first use (`csrf_token()`, exposed to every
template through a context processor) and checks it on every unsafe request
(`verify_csrf()`, a `before_request` hook registered ahead of the
authorization gate in `web/middleware/__init__.py`): a missing or mismatched
`csrf_token` form field, compared with `hmac.compare_digest`, is refused
(403). When the request carries an `Origin` header, its scheme and host must
match the request's own; a **missing** `Origin` does not by itself refuse,
since older browsers do not send one and the token is the control that
covers them. The check runs for anonymous requests too — the login form is
one of the 21 sites — and does not depend on ADR-0022's session lookup, since
the token lives in the signed cookie itself, independent of who (if anyone)
`app_session` says is signed in.

No `flask-wtf` dependency: the same choice
[ADR-0021](0021-k-means-is-implemented-in-the-application-rather-than-taken-as-a-dependency.md)
made for k-means, for the same reason. There is no shared `<form>` macro in
`web/templates` today, so every one of the 21 sites needs its own hidden
`<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` line
regardless of which mechanism checks it — `flask-wtf`'s `{{ csrf_token() }}`
buys nothing over a same-shaped function this application owns, for a check
this small.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| `flask-wtf` `CSRFProtect` | A new runtime dependency for something the size of a few dozen lines. It still needs the same per-template hidden-field insertion (no macro exists to centralize it either way), and it does not include an `Origin` check on its own — the issue's second layer would be hand-written regardless, on top of a library whose own token lifecycle (rotation, per-render tokens) this application does not need |
| Rely on `SameSite=Lax` alone (do nothing) | Exactly the residual exposure this record names: other same-site subdomains, older browsers, and a single control whose future removal would silently reopen every form. The issue was filed because this was already judged insufficient on its own |
| Check `Referer` instead of, or as well as, `Origin` | `Referer` is more often stripped (privacy tooling, `Referrer-Policy`) and leaks the full requested path rather than just the origin; `Origin` is sent on every unsafe cross-origin request by every browser that matters here and carries no more information than needed for this check |

## Consequences

**What this makes easy.** A forged cross-origin POST is refused even in a
browser that ignores `SameSite`, or against a same-site-but-different
subdomain, without adding a dependency. The token lives in the same signed
session cookie the app already issues, so no new table, no new config, and
nothing for `sql/01_schema.sql` to carry.

**What this makes hard.** Every one of the 21 `<form>` sites, and any new one
a future story adds, must remember the hidden field; nothing enforces that
structurally the way the authorization gate's default-deny does for
permissions. The ~76 existing `client.post(...)` test call sites across 15
files carried no token, so the check is stubbed off by an autouse fixture in
`tests/conftest.py` for every test except ones marked
`@pytest.mark.real_csrf` — the same shape as `@pytest.mark.real_sessions` for
ADR-0022's session lookup. A route added later without its form template
covered by that convention is a route whose real behaviour the ordinary test
suite will not catch failing.

**What must now be true elsewhere.** Any new `method="post"` (or `put`,
`patch`, `delete`) form must render `{{ csrf_token() }}` into a hidden field,
the same way any new protected route must declare `@public`/`@requires(...)`
for ADR-0007. A test of a new state-changing route that needs the real check
exercised opts in with `@pytest.mark.real_csrf`, following `tests/test_csrf.py`.

## Compliance

```bash
# No CSRF dependency enters the runtime or the dev tooling.
# Must print nothing.
grep -rniE 'flask-wtf|wtforms' web/requirements.txt web/requirements-dev.txt pyproject.toml

# Every method="post" form carries the hidden token field.
# The two counts must be equal.
grep -rc 'method="post"' web/templates | awk -F: '{s+=$2} END {print s}'
grep -rc 'name="csrf_token"' web/templates | awk -F: '{s+=$2} END {print s}'
```

`tests/test_csrf.py` (opted into the real check with `@pytest.mark.real_csrf`)
covers: a missing token is refused; a wrong token is refused; the correct
token reaches the view; a mismatched `Origin` is refused even with the
correct token; a matching `Origin` is accepted; a missing `Origin` is not
refused on its own; `GET` is never checked; and the login page renders a
token matching the one stored in its own session.
