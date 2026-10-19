# #348 — Login throttling

The sign-in route now applies two bounded sliding-window buckets to failed
credentials: a normalized account key and the trusted client address. Within
one worker, five failures in fifteen minutes make the next attempt return an
HTML `429 Too Many Requests` page with a `Retry-After` header. A correct
password is not accepted while the account or client remains throttled, and a
successful sign-in clears only that account's bucket. Account buckets are
worker-local in the Flask process; the NGINX client-IP bucket is shared across
both Gunicorn workers, which is the cross-worker defense. A direct connection
gets the account defense in its one worker but bypasses NGINX by design.

The instance NGINX configuration adds a shared `6r/m` client-IP limit with a
small burst, covering both Gunicorn workers before requests reach Flask. The
Compose proxy carries the same rule for local verification. No external
service or second deployable unit is involved.

The focused regression suite covers threshold and expiry, account/client
independence, successful reset, the HTML 429 response and the correct-password
case. It also checks both NGINX configurations contain the shared limit.

## Two workers behind NGINX (#550)

Rehearsed on 2026-10-19 with two Gunicorn workers behind the committed compose
proxy policy (only the upstream and listen addresses changed), PostgreSQL
16.15, and the default five failures in fifteen minutes. Each line is one
failed `POST /login` for a fresh account from one client address.

- Twelve attempts back to back: six `401`, then six `429` from NGINX. The
  shared limit (`6r/m`, burst 5) refuses the seventh before it reaches Flask.
- Twelve attempts 10.5 s apart, under the NGINX rate: nine `401`, then the
  application's `429` page at attempt 10, a `401` at attempt 11 and the `429`
  page again at attempt 12. Gunicorn's access log shows why: attempt 10 was
  the sixth to reach a worker that had counted five failures, and attempt 11
  went to the other worker, which had counted four.

So the five-failure threshold is exact per worker, not across the instance.
Making it exact across workers would need authentication state shared between
processes, which is a design decision of its own and is not made here.
