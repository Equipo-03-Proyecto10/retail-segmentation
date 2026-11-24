# The platform's profiles under load, with Locust

F29-15 (#1191). The final delivery's instructions require Locust. k6 is still
used for the products reads bench next door ([`../README.md`](../README.md),
ADR-0042). This directory measures a different thing: the whole platform,
with mixed profiles, through the same doors its clients use. The report and the
runs are in [`docs/evidence/f29-15-locust-load.md`](../../docs/evidence/f29-15-locust-load.md).

| Path | What it is |
|---|---|
| `locustfile.py` | The four weighted user classes, the sign-in pacing, the load shape, and the per-request recorder |
| `report.py` | Turns a run into the report's tables. Standard library only |
| `run.sh` | One run: records the facts (commit, machine, images), runs Locust, writes the report, and gzips the CSVs |
| `accounts.py` | Creates (and later deactivates) the pools of floor and desktop test accounts, through users-service |
| `stack.py` | Renders an isolated local stack (its own compose project, network, volumes and ports) from `compose.distributed.yaml` |
| `requirements.txt` | Locust, pinned, for a virtualenv of its own |
| `results/<date>-<label>/` | The committed runs |

## The user classes

| Class | Weight | Account (role) | Path | What it does |
|---|---:|---|---|---|
| `WebAnalyst` | 5 | ANALYST | the web, HTML, session cookie | `/catalog/customers` (pages and `?q=`), `/catalog/customers/{id}`, `/catalog/segments`, `/catalog/segments/{id}`, `/segmentation-dashboard/` |
| `FloorSeller` | 3 | SALES_ASSOCIATE | the edge, JSON (Android) | `POST /api/sales/v1/sales` with a fresh `Idempotency-Key`, 5% of them a replay of the previous one (expects `200` and `Idempotent-Replayed: true`). Also `GET /api/products/v1/products` and `GET /api/stock/v1/stock-levels` |
| `DesktopReports` | 2 | ANALYST | the edge, XML (Electron) | `/api/analytics-kpis/v1/kpi-reports/` `net-sales` (by store; by segment and month), `spend`, `retention`, `migrations`, `demand-coverage`, `definitions`, with `Accept: application/xml`. An XML `<Error>` body fails the request |
| `OpsAdmin` | 1 | ADMIN | the ops console and the edge | the console's `/status.json` and `/`, `GET /api/users/v1/users`, `GET /api/audit-trail/v1/audit-entries` |

Every user waits 1–3 s between tasks. A request fails when its status, its
`Content-Type`, or (for XML) its body is not what the client expects. Each
request carries `X-Correlation-ID: locust-<run id>-<hex>`, so its rows can be
found in the logs and in the sales tables.

## Sign-in and the edge's limit

The edge allows 6 sign-ins a minute per IP, with a burst of 5, both on the
web's `POST /login` and on `POST /api/auth/v1/sessions`
(`deploy/nginx/mosaiq.conf`). The run signs in once per account, before the
first step: once in the web, and once in auth for each floor, desktop and
admin account (four sign-ins without pools, 26 with the pools below). The
sign-ins are spaced `MOSAIQ_LOCUST_SIGN_IN_INTERVAL` (10.5 s) apart. All the
users on one account share its cookie or token. Access tokens live ten
minutes, so the first user to find one older than eight minutes rotates the
pair through `POST /api/auth/v1/sessions/refresh`, which the edge does not
count. The other users on that account wait on the same lock.

## Account pools: the per-account limit

Every service limits each account to `RATE_LIMIT_REQUESTS` (120) requests per
`RATE_LIMIT_WINDOW_SECONDS` (60), per service. The limit is keyed on the
token's `sub` (`packages/kit-py`, `packages/kit-rs`). Forty virtual users on one
account therefore hit that limit, and every request past it is a `429
rate_limited`. That is what the first local run measured
([`results/2026-11-24-local-shared-accounts/`](results/2026-11-24-local-shared-accounts/)).
A real floor has one account per person, so a run that measures the platform
spreads each API class over a pool:

```bash
python3 bench/locust/accounts.py create "$work/accounts.json"   # 12 SALES_ASSOCIATE, 12 ANALYST
MOSAIQ_LOCUST_ACCOUNTS_FILE="$work/accounts.json" LOCUST=… bash bench/locust/run.sh <label>
python3 bench/locust/accounts.py retire "$work/accounts.json"   # deactivates them
```

`accounts.py` signs in once as the administrator. It creates the accounts
through `POST /api/users/v1/users`, and assigns the floor accounts to
`MOSAIQ_LOCUST_STORE_IDS` with `PUT /api/users/v1/store-assignments/{id}`.
Their passwords are random, and it writes them to a 0600 JSON file. Keep that
file outside the repository. Each virtual user of a class takes the next account
of its pool, round-robin. The pool's sign-ins are paced like the others, so 26
sign-ins take about 4.5 minutes. The web analyst keeps one session: the web has
no per-account limit. The admin keeps one account, because there is exactly one
administrator.

## The load shape and the definitions

Steps of `MOSAIQ_LOCUST_STEPS` users (default 10, 20, 40, 80, 120, 160),
`MOSAIQ_LOCUST_STEP_SECONDS` each (60), spawned at `MOSAIQ_LOCUST_SPAWN_RATE`
users a second (10). Then the load drops to `MOSAIQ_LOCUST_RECOVERY_USERS` (10)
for `MOSAIQ_LOCUST_RECOVERY_SECONDS` (180). Locust's model is closed: a user
waits for each answer, so an overloaded platform shows longer latency and
flat throughput, not a queue of arrivals.

The criteria are the k6 bench's (bench/README.md, "The saturation criterion"):

- Each step is measured over its **steady window**: from 10 s after the step
  starts, so that the users still being spawned are left out, to the step's end.
- A step is **sustained** when its p99 is at most 1,000 ms and at most 1% of
  its requests failed. The **saturation point** is the first step that is not
  sustained. The capacity is the step before it.
- **Recovery** is measured in 10-second windows from the drop. It is the end of
  the first window whose p95 is within 1.2 times the first step's p95, with no
  failure.

## Configuration

| Variable | Default (local) | Meaning |
|---|---|---|
| `MOSAIQ_LOCUST_API_URL` | `http://127.0.0.1:18191` | The edge: `/api/<service>/...` |
| `MOSAIQ_LOCUST_WEB_URL` | `http://127.0.0.1:18192` | The web, behind its NGINX |
| `MOSAIQ_LOCUST_CONSOLE_URL` | `http://127.0.0.1:18193` | The ops console. On the instance, an SSH tunnel to its `127.0.0.1:5090` |
| `MOSAIQ_LOCUST_INSECURE_TLS` | off | Skips certificate checks. Only for a loopback hop whose certificate names another host |
| `MOSAIQ_LOCUST_{ANALYST,FLOOR,DESKTOP,ADMIN}_EMAIL` / `_PASSWORD` | the seeded accounts `user2`, `user31`, `user2`, `admin` | The four accounts. Outside the local stack they are required, and nothing falls back to a seeded password |
| `MOSAIQ_LOCUST_ACCOUNTS_FILE` | none | `accounts.py`'s pool file. Without it, each class has its one account |
| `MOSAIQ_LOCUST_STORE_IDS` | `6,14,22,30` (`user31`'s stores) | The only stores sales go to |
| `MOSAIQ_LOCUST_PRODUCT_IDS` | `1,2,3,4,5` | The only products sold. Each must have stock in each of those stores |
| `MOSAIQ_LOCUST_CUSTOMER_IDS` | the 30 seeded customers | The customers sales name and the analyst opens |
| `MOSAIQ_LOCUST_SEGMENT_IDS` | `1`–`6` | The segments the analyst opens |
| `MOSAIQ_LOCUST_CUSTOMER_PAGES` | `2` | Pages of 20 customers that exist (past the last one the web answers a redirect) |
| `MOSAIQ_LOCUST_CHANNEL_ID` | `3` (`physical_store`) | The sales' channel |
| `MOSAIQ_LOCUST_REPLAY_SHARE` | `0.05` | The share of sales that replay the previous key |
| `MOSAIQ_LOCUST_SIGN_IN_INTERVAL` | `10.5` | Seconds between sign-ins |
| `MOSAIQ_LOCUST_STEPS`, `_STEP_SECONDS`, `_SPAWN_RATE`, `_RECOVERY_USERS`, `_RECOVERY_SECONDS` | above | The shape |
| `MOSAIQ_LOCUST_RUN_ID`, `MOSAIQ_LOCUST_OUT` | UTC timestamp, set by `run.sh` | The run's id and directory |

Sale prices are read once, before the first step, from each product's
`list_price`, because a sale below the list price is refused.

## Run it locally

The stack needs the service images under one tag (built from the commit
under test, or pulled), plus the web and ops console images:

```bash
python3 -m venv ~/.venvs/mosaiq-locust && ~/.venvs/mosaiq-locust/bin/pip install -r bench/locust/requirements.txt
bash docker/distributed/auth-keys.sh
cp contracts/registry.yaml ops/console/registry.yaml
docker build -t mosaiq-web:p1191 .
docker build -t mosaiq-ops-console:p1191 ops/console
work=$(mktemp -d)
python3 bench/locust/stack.py render --project p1191 --tag <tag> >"$work/compose.json"
docker network create p1191-mosaiq
export MOSAIQ_LOCUST_OPS_CONSOLE_PASSWORD=$(openssl rand -hex 16)
docker compose -p p1191 -f "$work/compose.json" up -d --wait
```

The ops console's Redis password lives only in that variable. The rendered
file holds a reference to it, `${MOSAIQ_LOCUST_OPS_CONSOLE_PASSWORD:-}`, which
compose resolves at `up`. The `redis-acl` step writes the console's env file
for `assemble-acl.sh` into a tmpfs of its own container, so the password is
never written to disk, and `redis-acl` stops if the variable is empty.

The database is throwaway, so the test stores' stock is raised before the
run, which keeps a sale from being refused for stock:

```bash
docker exec -i p1191-db-1 psql -U postgres -d retail -c \
  "UPDATE inventory SET quantity_on_hand = 1000000 WHERE store_id IN (6,14,22,30) AND product_id IN (1,2,3,4,5)"
LOCUST=~/.venvs/mosaiq-locust/bin/locust bash bench/locust/run.sh local-<machine>
```

With the pools above, the run takes the sign-ins plus 6 × 60 s plus 180 s,
about 14 minutes. Then:

```bash
docker compose -p p1191 -f "$work/compose.json" down -v
docker network rm p1191-mosaiq
```

The instance run is a runbook in the evidence file, with the instance owner.
