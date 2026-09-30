# ADR-0032 — Server-side sessions have idle and absolute lifetimes

**Status:** Proposed
**Owner:** Team 03
**Issue:** #359
**Supersedes:** —
**Superseded by:** —

## Context

ADR-0022 made the database row authoritative for revocation, deactivation and
role changes, but an open row otherwise lived forever. A copied session cookie
could therefore remain useful indefinitely, and an abandoned browser retained
access until it was explicitly signed out.

## Decision

Each `app_session` records `last_seen_at`, and every request accepts the row
only when it is younger than both the configured idle limit (30 minutes) and
the configured absolute limit (8 hours from `created_at`). A successful lookup
updates `last_seen_at` in the same committed unit of work. The signed Flask
cookie receives the same absolute lifetime as an additional client-side bound;
the database remains authoritative.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Expire only the signed Flask cookie | A copied cookie and a browser that ignores its expiry would outlive the policy. |
| Keep only `created_at` and use a rolling cookie | There would be no idle timeout, and continuous use could extend a session forever. |
| Add a per-user session version | Signing out one browser would sign out every browser and would audit a user row for each sign-out. |

## Consequences

**What this makes easy.** Revocation, inactivity and maximum session age are
checked in one server-side path before authorization, including direct
Gunicorn and alternate proxy deployments.

**What this makes hard.** Every authenticated request writes a timestamp, so
the session lookup is a committed write and the database sees more small
updates.

**What must now be true elsewhere.** `sql/01_schema.sql` owns the new column;
`.env.example` documents both limits; no route may treat the signed cookie as
the source of session validity.

## Compliance

`tests/test_server_side_sessions.py` covers the live lookup path and the
configured limits. `tests/test_config.py` covers the documented defaults and
environment overrides. The schema and seed scripts are checked by the normal
database CI job.
