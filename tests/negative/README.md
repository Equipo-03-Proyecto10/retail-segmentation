# Deployed negative tests

F18-04 (#459). These tests send real requests through the distributed stack's
edge, `/api/<name>/v1/...`, with tokens that auth-service signed for real
accounts. A plain repository `pytest` skips them unless `MOSAIQ_BASE_URL` is
set.

Every case that applies to a service runs against each service in
`contracts/registry.yaml`, from that service's own contract: its operations and
their `x-mosaiq-roles`. A service added to the registry is therefore covered as
soon as it is deployed, with no edit here. Each run writes its own section of
[`docs/evidence/f18-04-negative-tests.md`](../../docs/evidence/f18-04-negative-tests.md):
every case's expected and actual result, and the cases this target cannot run,
with the reason.

| File | What it refuses |
|---|---|
| `test_auth.py` | A reused refresh token; a session's token after sign-out; the sixth sign-in after five failures (email and IP); the sixth wrong current password; the per-IP sign-in limit, last |
| `test_tokens.py` | No token, a tampered signature, `alg: none`, HS256 keyed with the public key, a wrong `iss`, a wrong `aud`, an expired token: 401 `invalid_token` from every service |
| `test_roles.py` | A role outside each operation's `x-mosaiq-roles`: 403 `forbidden_role` |
| `test_representations.py` | 406 for `Accept: text/csv`, 415 for `text/plain`, 400 for XML with a DOCTYPE or an entity, 413 for 65 KiB at the edge and on the service's own port, 422 for `page=0` and `size=101` |
| `test_rules.py` | users' single administrator (409 ×3), and sales' 409 `stock_unavailable`, 400 `idempotency_key_required` and, locally, 409 `idempotency_key_reused` |
| `test_revocation.py` | A signed-out session on every service; locally, customers refusing it with no database statement |
| `test_infrastructure.py` | redis-state stopped, locally (503 `auth_unavailable`, liveness up); the grant and boundary checks; two service roles refused another boundary; a direct INSERT of a second administrator, locally; `check-redis.sh`; the internal ports from outside |

## The local stack

Start the stack with the commands in the root README, "The full stack". The
suite uses one seeded account per role (`admin@` and `user2@` to `user7@`), all
with the public demonstration password from the root README. It signs them out,
but does not modify or deactivate them.

Copy the boundary checks into the database container once, then run the suite
from the repository root:

```bash
docker exec mosaiq-local-db-1 mkdir -p /repo/contracts
docker cp contracts/db mosaiq-local-db-1:/repo/contracts/
MOSAIQ_BASE_URL=http://127.0.0.1:8080 \
MOSAIQ_SIGNING_KEY=docker/distributed/keys/auth-local-dev.pem \
MOSAIQ_DIRECT_PORTS=1 \
MOSAIQ_DB_EXEC="docker exec -i -w /repo -e PGUSER=postgres -e PGDATABASE=retail mosaiq-local-db-1" \
MOSAIQ_REDIS_STATE_CONTAINER=mosaiq-local-redis-state-1 \
MOSAIQ_DB_CONTAINER=mosaiq-local-db-1 \
MOSAIQ_CUSTOMERS_CONTAINER=mosaiq-local-customers-1 \
  pytest -q tests/negative
```

The local stack's key is a throwaway, so this run also signs the wrong `iss`,
wrong `aud` and expired tokens. It accepts one sale, to show a reused key
refused, and the customers case changes the database's logging for its
duration. Neither is done anywhere but here.

### Order, and a stack that has just had Redis restarted

Two cases run after all the others, whatever their file: the per-IP sign-in
burst, which refuses every later sign-in from the same address for a minute,
and `redis-state` stopped, after which the run's sessions do not come back
([#714](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/714)).
Start the next run only after a minute, and expect its sign-ins to work again.

The Rust services keep one Redis connection each, with one attempt and no
retry (`packages/kit-rs/src/auth.rs`). After a `redis-state` restart that the
suite did not make, the first protected request to each Rust service is
refused once with 503 `auth_unavailable`, and the next reconnects. The suite
would report that 503 as a failed case, so do not restart Redis between runs;
the case that stops it leaves every service's connection dropped and clean.

## The instance

Run it on the instance itself, as root, from a copy of this tree that the
`postgres` user can read: the service ports bind 127.0.0.1, and the database
checks run as `postgres`. The suite reaches NGINX on 127.0.0.1:443 under the
published host name, so certificate verification is off for that loopback hop
only.

The administrator's credential goes only into the process environment, read at
a prompt. The suite signs in as that administrator, creates one active account
for each other role through `POST /api/users/v1/users`, and deactivates every
created account during teardown, including after a failed test. Ordinary
sign-ins start at least 10.5 seconds apart, to pass the edge's six-per-minute
limit. The expired-token case waits for a real token to expire, so a run takes
at least ten minutes.

```bash
read -r MOSAIQ_NEGATIVE_ADMIN_EMAIL
read -rs MOSAIQ_NEGATIVE_ADMIN_PASSWORD
export MOSAIQ_NEGATIVE_ADMIN_EMAIL MOSAIQ_NEGATIVE_ADMIN_PASSWORD
MOSAIQ_BASE_URL=https://127.0.0.1 MOSAIQ_HOST=mosaiq.maxthecoder.online \
MOSAIQ_INSECURE_TLS=1 MOSAIQ_DIRECT_PORTS=1 \
MOSAIQ_DB_EXEC="runuser -u postgres --" MOSAIQ_CHECK_REDIS=1 \
  pytest -q tests/negative
unset MOSAIQ_NEGATIVE_ADMIN_EMAIL MOSAIQ_NEGATIVE_ADMIN_PASSWORD
```

`check-redis.sh` reads `REDIS_OPS_PASSWORD` from the environment, as in
`deploy/README.md`, F18-01 step 6.

What the instance run leaves out, and records as not run:
- the wrong `iss` and `aud` tokens, which need auth's private key;
- the reused idempotency key, which needs an accepted sale;
- redis-state stopped, which stops a shared container;
- the customers logging case, which changes the database's logging;
- the direct INSERT of a second administrator, which writes to the live
  accounts table.

The local run covers each of them.

## From outside

The internal ports, 5001 to 5015, 6379 and 27017, must be closed or filtered
from outside the instance. From any other machine:

```bash
MOSAIQ_PROBE_HOST=34.51.123.31 pytest -q tests/negative/test_infrastructure.py -k outside
```
