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
