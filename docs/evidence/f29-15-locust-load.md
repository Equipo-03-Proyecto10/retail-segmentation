# F29-15 (#1191): the platform's real profiles under load, with Locust

> **Status: local runs recorded; the instance run is not done yet.** The
> local stack was measured three times on 2026-11-24 at commit `7d0cc705`.
> The instance run needs the instance owner: he switches the instance on,
> agrees on the test store, and provides the administrator's credentials.
> It is written below as a [runbook](#the-instance-run-runbook) with every
> `‹fill: …›` left to fill in.

The tooling is in [`bench/locust/`](../../bench/locust/README.md). The raw
results, one directory per run, are in [`bench/locust/results/`](../../bench/locust/results/).
Running `python3 bench/locust/report.py bench/locust/results/<run>` reproduces
every table below.

## The criteria, and where each is answered

| Criterion of #1191 | Answered by |
|---|---|
| A `locustfile.py` under `bench/locust/` simulates the four weighted profiles | [`bench/locust/locustfile.py`](../../bench/locust/locustfile.py): `WebAnalyst` (5), `FloorSeller` (3, JSON with `Idempotency-Key`), `DesktopReports` (2, XML), `OpsAdmin` (1). See [the profiles](#the-profiles) |
| The report gives concurrent users, req/s, mean, p95, p99, errors, the slowest endpoints, saturation and recovery | [The local results](#the-local-results). Each run's `report.md` has the same tables |
| Seeded test accounts from the environment, within the edge's sign-in limit | `MOSAIQ_LOCUST_*_EMAIL` / `_PASSWORD`, or `accounts.py`'s pool file. The sign-ins are paced. See [sign-in](#sign-in-accounts-and-the-limits) |
| The local stack and the instance, each committed with the version and the machine | Local: three runs, each with its own `facts.txt` (commit, machine, Docker, each container's image and limits). Instance: the [runbook](#the-instance-run-runbook), not yet run |
| How the Locust results relate to the k6 bench | [Locust and the k6 bench](#locust-and-the-k6-bench) |

## The profiles

| Class | Weight | Account | Requests (task weight) |
|---|---:|---|---|
| `WebAnalyst` | 5 | ANALYST, one web session | `/catalog/customers` (4), `?q=` (2), `/catalog/customers/{id}` (3), `/catalog/segments` (2), `/catalog/segments/{id}` (2), `/segmentation-dashboard/` (1) |
| `FloorSeller` | 3 | SALES_ASSOCIATE | `POST /api/sales/v1/sales` (3), with a fresh `Idempotency-Key`; 5% of them replay the previous key and must answer `200` with `Idempotent-Replayed: true`. Also `GET /api/products/v1/products` (1) and `GET /api/stock/v1/stock-levels` (1) |
| `DesktopReports` | 2 | ANALYST | `/api/analytics-kpis/v1/kpi-reports/` `net-sales` by store (3), by segment and month (2), `spend` (2), `retention`, `migrations`, `demand-coverage`, `definitions` (1 each), all with `Accept: application/xml`. An `<Error>` body fails the request |
| `OpsAdmin` | 1 | ADMIN | the ops console's `/status.json` (3) and `/` (1), `GET /api/users/v1/users` (1), `GET /api/audit-trail/v1/audit-entries` (1) |

Every user waits 1–3 s between tasks. Locust's model is closed: a user waits
for each answer before it goes on.

**Test data.** Sales go only to the stores of the seeded floor account
`user31` (6, 14, 22, 30). They sell only products 1–5, at each product's
`list_price`, to the 30 seeded customers, on channel 3 (`physical_store`).
Every request carries `X-Correlation-ID: locust-<run id>-<hex>`. Locally the
database is thrown away after each run. Before each run the stock of those
stores and products is raised to 1,000,000 by an `UPDATE` on the throwaway
database, so that no sale is refused for stock.

## Sign-in, accounts and the limits

Three limits shape the run. All three are in the code, and none of them
changed for it:

| Limit | Where | Value | How the run respects it |
|---|---|---|---|
| The edge's sign-in limit, per IP | `deploy/nginx/mosaiq.conf`: `/login` and `POST /api/auth/v1/sessions` | 6 a minute, burst 5 | One sign-in per account, before the first step, `MOSAIQ_LOCUST_SIGN_IN_INTERVAL` = 10.5 s apart. The refresh is not counted |
| auth-service's own sign-in limit, per IP | `services/auth/app/__init__.py`, `LOGIN_LIMIT` | 30 a minute | The local gateway has no edge limit, so the overload run used 2.5 s. A 1 s interval was refused with `429 rate_limited` at the 30th sign-in |
| Every service's request limit, per account (`sub`) | `packages/kit-py`, `packages/kit-rs`: `RATE_LIMIT_REQUESTS` / `_WINDOW_SECONDS` | 120 a minute, per service | Floor and desktop accounts come from pools (`bench/locust/accounts.py`). The admin has one account, because there is exactly one administrator |

The per-account limit is the first thing the load met. In the first run, every
floor user signed in as `user31` and every desktop user as `user2`. That run
saturates at **40 users**, entirely on `429 rate_limited`, while the latency
stays low ([shared accounts](#run-1-one-account-per-class)). One person cannot
send more than two requests a second to one service, and a real floor has one
account per person. So the other two runs spread the floor and desktop users
over pools of accounts, which `accounts.py` creates through users-service
(`POST /api/users/v1/users`, then `PUT /api/users/v1/store-assignments/{id}`
for the floor accounts):

| Run | Floor accounts | Desktop accounts | Sign-ins | Interval |
|---|---:|---:|---:|---:|
| `2026-11-24-local-shared-accounts` | 1 (`user31`) | 1 (`user2`) | 4 | 10.5 s |
| `2026-11-24-local-account-pools` | 12 | 12 | 26 | 10.5 s |
| `2026-11-24-local-overload` | 40 | 30 | 72 | 2.5 s |

## The load shape and the definitions

The definitions are the k6 bench's ([`bench/README.md`](../../bench/README.md),
"The saturation criterion"), so the two can be read side by side:

- **Steps.** Each step holds a number of users for 60 s, with users spawned
  at 10 a second (20 in the overload run). Each step is measured over its
  steady window: from 10 s after it starts to its end, which leaves the
  users still being spawned out.
- **Sustained.** A step is sustained when its p99 is at most 1,000 ms and at
  most 1% of its requests failed. The **saturation point** is the first step
  that is not sustained. The capacity is the step before it.
- **Recovery.** After the last step the load drops to 10 users for 180 s.
  Recovery is the time from the drop to the end of the first 10-second window
  whose p95 is within 1.2 times the first step's p95, with no failure.

## The machine and the stack

| Item | Value |
|---|---|
| Machine | MacBook Air, Apple M2 (`Mac14,2`), 8 CPUs, 8 GiB, macOS (Darwin 25.6.0, arm64) |
| Docker | Docker Desktop's VM: 8 CPUs, 4.8 GiB |
| Locust | 2.46.7 (`bench/locust/requirements.txt`), one process, on the same machine as the stack |
| Commit | `7d0cc7056968af151333966c4344dcdf81441a1e` (develop `fdf0caed` plus the tooling), nothing modified (`dirty=0`, `locust_files_dirty=0`); locustfile sha256 `51d4e8ff…5905a` |
| Stack | `bench/locust/stack.py render --project p1191 --tag p831 --image analytics-kpis=p1191`: compose project `p1191`, network `p1191-mosaiq`, the edge on `127.0.0.1:18191`, the web's NGINX on `:18192`, the ops console on `:18193` |
| Services | auth 0.1.4, users 0.2.0, products 0.3.0, stock 0.3.0, sales 0.7.0 (and its import worker), audit-trail 0.1.5, analytics-kpis 0.4.0. Each runs with its own compose file's memory and CPU limits, which are the instance's (for example sales 160 MB and 0.5 CPU; analytics-kpis 64 MB and 0.25 CPU). `facts.txt` lists each container's image id and limits |
| Images | `:p831` were built on this machine on 2026-11-23 at about 11:45 CST. No later commit touches auth, users, products, stock, sales or audit-trail. analytics-kpis changed after that time (`8e013881`), so it was rebuilt from this commit as `:p1191` |
| The web | `docker build .` from this commit, gunicorn with 2 workers (the instance's `deploy/systemd/mosaiq.service` uses 2), behind `deploy/nginx/mosaiq.compose.conf` |
| `facts.txt` edit | After the runs, the one `container=` line of the document store was renamed from its compose name to `p1191-document-store`, because `guards.sh` refuses that engine's name outside engagement (ADR-0038). Its image digest, id and limits are unchanged, and `run.sh` now writes that name itself |
| PostgreSQL, Redis | `postgres:18`, built by the three scripts. `redis:7.4.11-alpine`, both instances. Every run starts from an empty database (`down -v`) |

The other services were not started: nothing in the four profiles reaches
them. The ops console polls every registered service, so it shows the missing
ones as down, as the instance's console does for any service that is down.

## The local results

### Run 1: one account per class

`bench/locust/results/2026-11-24-local-shared-accounts/`. The default shape:
10 to 160 users.

| Phase | Users | Requests | req/s | Mean ms | p50 ms | p95 ms | p99 ms | Max ms | Errors | Sustained |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| step-1 | 10 | 252 | 5.0 | 30 | 22 | 67 | 163 | 281 | 0 (0.00%) | yes |
| step-2 | 20 | 495 | 9.8 | 25 | 19 | 55 | 150 | 439 | 0 (0.00%) | yes |
| step-3 | 40 | 973 | 19.3 | 20 | 13 | 44 | 150 | 213 | 117 (12.02%) | no |
| step-4 | 80 | 1,949 | 39.5 | 20 | 12 | 90 | 177 | 402 | 503 (25.81%) | no |
| step-5 | 120 | 2,971 | 59.1 | 33 | 12 | 146 | 713 | 1,139 | 942 (31.71%) | no |
| step-6 | 160 | 3,949 | 78.4 | 20 | 10 | 98 | 196 | 484 | 1,361 (34.46%) | no |
| recovery | 10 | 837 | 4.9 | 32 | 25 | 63 | 191 | 227 | 64 (7.65%) | no |

The run saturates at **40 users** on errors alone. Every error is a `429
rate_limited` from the floor's and the desktop's one account. The web never
failed, because its pages carry no per-account limit. Recovery took **60 s**:
the limit's window is a fixed minute, so an account that went over it stays
refused until that minute ends.

### Run 2: account pools, 10 to 160 users

`bench/locust/results/2026-11-24-local-account-pools/`.

| Phase | Users | Requests | req/s | Mean ms | p50 ms | p95 ms | p99 ms | Max ms | Errors | Sustained |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| step-1 | 10 | 245 | 4.9 | 34 | 24 | 126 | 195 | 222 | 0 (0.00%) | yes |
| step-2 | 20 | 493 | 9.8 | 25 | 19 | 50 | 160 | 201 | 0 (0.00%) | yes |
| step-3 | 40 | 984 | 20.0 | 21 | 13 | 51 | 153 | 216 | 0 (0.00%) | yes |
| step-4 | 80 | 2,001 | 39.7 | 17 | 11 | 37 | 150 | 382 | 0 (0.00%) | yes |
| step-5 | 120 | 2,997 | 59.5 | 17 | 10 | 43 | 172 | 292 | 0 (0.00%) | yes |
| step-6 | 160 | 3,891 | 78.8 | 20 | 9 | 94 | 204 | 356 | 0 (0.00%) | yes |
| recovery | 10 | 846 | 5.0 | 35 | 26 | 81 | 248 | 280 | 1 (0.12%) | yes |

Every step is sustained, up to **160 users and 79 req/s**, with no failed
request under load. Throughput grows linearly with the users (about 0.49 req/s
per user, which the 1–3 s think time sets). The platform did not saturate
within this ramp. After the drop it was back under the threshold in the first
window, within **10 s**. The only two failures in the whole run were ops
console connections that failed (status 0): one while step 4 was spawning,
outside its steady window, and one during the recovery phase.

| The 5 slowest endpoints (whole run, by p95) | Requests | Mean ms | p95 ms | p99 ms | Errors |
|---|---:|---:|---:|---:|---:|
| `GET /segmentation-dashboard/` | 430 | 173 | 245 | 302 | 0 |
| `GET /catalog/customers?q=` | 846 | 24 | 63 | 145 | 0 |
| `GET /catalog/customers/{id}` | 1,278 | 27 | 60 | 134 | 0 |
| `GET /catalog/segments` | 867 | 22 | 55 | 109 | 0 |
| `GET /catalog/customers` | 1,704 | 22 | 53 | 112 | 0 |

| Class | Requests | req/s | Mean ms | p95 ms | p99 ms | Errors |
|---|---:|---:|---:|---:|---:|---:|
| desktop-xml | 2,565 | 4.7 | 7 | 18 | 29 | 0 |
| floor-android | 3,786 | 7.0 | 10 | 25 | 47 | 0 |
| ops-admin | 1,302 | 2.4 | 10 | 27 | 94 | 2 (0.15%) |
| web-analyst | 5,978 | 11.1 | 34 | 155 | 211 | 0 |

`POST /api/sales/v1/sales` answered 2,134 sales with p50 8 ms, p95 29 ms and
p99 62 ms. All 113 replays answered `200` with `Idempotent-Replayed: true`.

### Run 3: overload, 160 to 560 users

`bench/locust/results/2026-11-24-local-overload/`. The same pools, larger (40
and 30). Steps of 160, 240, 320, 400, 480 and 560 users.

| Phase | Users | Requests | req/s | Mean ms | p50 ms | p95 ms | p99 ms | Max ms | Errors | Sustained |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| step-1 | 160 | 3,979 | 79.2 | 23 | 11 | 112 | 235 | 1,098 | 0 (0.00%) | yes |
| step-2 | 240 | 5,863 | 116.7 | 39 | 10 | 204 | 356 | 774 | 3 (0.05%) | yes |
| step-3 | 320 | 7,505 | 149.2 | 133 | 10 | 651 | 878 | 1,239 | 33 (0.44%) | yes |
| step-4 | 400 | 8,097 | 164.2 | 442 | 9 | 2,001 | 2,388 | 2,956 | 107 (1.32%) | no |
| step-5 | 480 | 8,480 | 168.5 | 842 | 10 | 4,919 | 6,493 | 7,074 | 180 (2.12%) | no |
| step-6 | 560 | 9,344 | 185.7 | 972 | 8 | 6,410 | 7,660 | 9,101 | 258 (2.76%) | no |
| recovery | 10 | 838 | 4.9 | 50 | 28 | 86 | 756 | 1,418 | 0 (0.00%) | yes |

**Saturation at 400 users** (p99 2,388 ms and 1.32% errors). The last
sustained step was **320 users at 149 req/s**. Throughput stops growing with
the users at the same point: from 320 to 560 users it gains 24%, against 75%
more users. The p50 stays at 8–10 ms throughout, so most requests are still
fast and the tail is what grows.

**Recovery: 20 s.** The first 10-second window after the drop still holds
the overload's backlog (p95 8,096 ms). The next one is at 84 ms. Of the 16
windows after it, 14 are within the threshold, and two are above it (260 and
171 ms), as single spikes. No window after the drop had a failure.

**Where the latency builds.** It builds in the web. By class over the whole
run:

| Class | Requests | req/s | Mean ms | p95 ms | p99 ms | Errors |
|---|---:|---:|---:|---:|---:|---:|
| desktop-xml | 11,875 | 22.0 | 11 | 21 | 68 | 11 (0.09%) |
| floor-android | 17,794 | 32.9 | 17 | 31 | 313 | 0 |
| ops-admin | 5,961 | 11.0 | 9 | 25 | 96 | 572 (9.60%) |
| web-analyst | 17,281 | 32.0 | 1,384 | 6,031 | 7,424 | 0 |

The five slowest endpoints are all web pages, at p95 6.0–6.5 s
(`/segmentation-dashboard/`, `/catalog/segments/{id}`, `/catalog/customers?q=`,
`/catalog/customers/{id}`, `/catalog/customers`). In the same run, every API the
clients use stays at p95 10–45 ms: sales 43 ms, products 18 ms, stock 14 ms, the
XML reports 10–35 ms. The web runs two synchronous gunicorn workers, as on the
instance, so its requests queue behind them. `docker stats`, sampled every
25 s, puts PostgreSQL at 0.7 CPU at 160 users and about 2 CPUs from 320 users
up, and the web at about 0.45 CPU. sales reached 0.46 CPU at 160 users and
0.1–0.25 CPU after that. Every other container stayed under 0.1 CPU. The web's queue explains the latency. PostgreSQL's rise is consistent
with the web's queries, which this run did not isolate.

**The errors under overload** are all expected refusals, not crashes:

| Endpoint | Error | Count | What it is |
|---|---|---:|---|
| `GET /api/users/v1/users`, `GET /api/audit-trail/v1/audit-entries` | `429 rate_limited` | 309, 255 | The one administrator account, used by 51 virtual admins at 560 users: the per-account limit |
| `GET /api/analytics-kpis/v1/kpi-reports/*` | `429 rate_limited` | 11 | The desktop pool's 30 accounts, at about 3.6 users each by 560 users |
| `GET console /status.json`, `GET console /` | connection reset (status 0) | 8 | The ops console: one process, 0.25 CPU and 96 MB, as on the instance |

No sale failed in the whole run (10,156 sales and 521 replays).

### Findings to follow up

These are observations from the runs. None of them is fixed here.

1. **The web is the platform's first bottleneck** under these profiles. With
   two gunicorn workers, its pages pass 1 s at p99 between 320 and 400
   concurrent users, while the services do not. The worker count is the
   instance's, and the next lever is the number of workers.
2. **The per-account limit sets the throughput of one person**: 120 requests
   a minute per service. A shared kiosk or shared account on the floor would
   meet it at about 40 users of a class. In run 1 the fixed window kept the
   refusals going for up to 60 s after the load dropped.
3. **A sale dated even a fraction of a second after the service's clock is
   `422 future_dated`.** An earlier draft of the locustfile sent `occurred_at`
   at the client's current time, and 40 of its sales were refused this way.
   The Docker VM's clock and the Mac's differed by less than a second. The
   locustfile now dates a sale 2 s before it sends it. An Android device
   whose clock runs ahead would meet the same refusal. #867's offline queue
   and the phone's clock are where that matters.
4. **analytics-kpis failed closed with `503 auth_unavailable`** under the
   same draft's overload run: 87 requests, plus 1 to stock. Its liveness check against Redis
   has a 200 ms timeout (`packages/kit-rs/src/auth.rs`, `STATE_TIMEOUT`), and
   the service runs at 0.25 CPU. This is the correct behaviour (ADR-0037), but
   the timeout plus CPU throttling makes it visible under load. It did not
   recur in the recorded overload run. The draft runs are not committed,
   because they ran before the tooling was committed.

## Locust and the k6 bench

They measure different things, and their numbers are not interchangeable.

| | k6 bench (ADR-0042, F15-03) | Locust (F29-15) |
|---|---|---|
| Question | Is the Rust twin of products' reads faster than Flask, on identical work? | How does the whole platform behave under its real, mixed profiles? |
| Target | One service, called directly, with its cache and rate limit off | The web, the edge and seven services, through NGINX, with every limit on |
| Load model | Open: constant arrival rates | Closed: users who wait for each answer and think for 1–3 s |
| Saturation | The highest sustained arrival rate of one endpoint | The user count at which the mix stops being sustained |
| Criterion | p99 ≤ 1,000 ms, errors ≤ 1%, dropped arrivals ≤ 1% | The same p99 and error limits. A closed model drops no arrivals: it slows down |

Where they meet: products-service's `GET /v1/products` sustained 320 req/s in
JSON, on its own, at 0.5 CPU (F15-03, GitHub-hosted x86). In the Locust
overload run the same read went through the edge, with the token and liveness
check on. It averaged 6.5 req/s over the run, in a mix whose total peaked at
186 req/s, and its p95 over the whole run, 560 users included, was 18 ms. So the products read was never near its own limit here. The two results
agree that the service is not the bottleneck: in the mix, the web's two workers
and the per-account limit come first. The k6 bench stays the tool for comparing
two builds of one service. Locust is the tool for the platform as its users
meet it.

## Threats to validity

- **One machine for everything.** Locust, the stack and PostgreSQL shared the
  M2's 8 CPUs, and Docker's VM had 4.8 GiB. Locust did not print its
  CPU-usage warning in any run (`locust-summary.txt`). Still, the instance (one GCP VM, Locust remote) will
  give its own numbers, and they will differ.
- **No network.** Everything is on loopback. The instance adds Cloudflare and
  the public internet, which raise every latency and change nothing in the
  definitions.
- **Small data.** The seed has 30 customers, 30 segments and 150 stock rows.
  The web's pages and the reports scan more on the instance.
- **Fresh accounts.** The pool accounts are created for the run. Each is a
  SALES_ASSOCIATE or ANALYST like the seeded ones, with the same rules.
- **One repetition per run.** Each run is one pass. The saturation step is
  only as fine as the steps (80 users apart in the overload run).

## The instance run (runbook)

**Not yet run.** It needs Marcelo, who switches the instance on, agrees on the
window and the test store, and holds the administrator's credentials. Locust
runs on a laptop, never on the instance, so that it does not take the
instance's CPU. Nothing below changes the instance by hand: accounts and stock
go through the services' APIs.

**Before.** These are agreed with the instance owner and filled in here:

| Item | Value |
|---|---|
| Date and window | ‹fill: date, start and end, UTC› |
| Commit and the services' versions deployed | ‹fill: `git rev-parse HEAD` of the deploy; each `services/*/VERSION`› |
| Test store(s), `MOSAIQ_LOCUST_STORE_IDS` | ‹fill: agreed with the instance owner› |
| Products with stock in them, `MOSAIQ_LOCUST_PRODUCT_IDS` | ‹fill› |
| Customers the sales name, `MOSAIQ_LOCUST_CUSTOMER_IDS` | ‹fill: test customers› |
| Customer pages that exist, `MOSAIQ_LOCUST_CUSTOMER_PAGES` | ‹fill: ceil(customers / 20)› |
| Shape | the default, 10 to 160 users. The overload steps only if the owner agrees, outside any demonstration |
| The machine Locust runs on | ‹fill: `run.sh` records it› |

The test store needs stock for the products it sells, enough for about 4 sales
a second for 10 minutes. The stock owner sets it through stock-service, never
by SQL.

**Run.** From the repository root, on the laptop, with this branch checked out:

```bash
python3 -m venv ~/.venvs/mosaiq-locust && ~/.venvs/mosaiq-locust/bin/pip install -r bench/locust/requirements.txt
export MOSAIQ_LOCUST_API_URL=https://mosaiq.maxthecoder.online
export MOSAIQ_LOCUST_WEB_URL=https://mosaiq.maxthecoder.online
export MOSAIQ_LOCUST_CONSOLE_URL=http://127.0.0.1:5090
export MOSAIQ_LOCUST_STORE_IDS=‹fill› MOSAIQ_LOCUST_PRODUCT_IDS=‹fill›
export MOSAIQ_LOCUST_CUSTOMER_IDS=‹fill› MOSAIQ_LOCUST_CUSTOMER_PAGES=‹fill›
export MOSAIQ_LOCUST_ADMIN_EMAIL=‹fill› MOSAIQ_LOCUST_ADMIN_PASSWORD=‹fill: typed, never written to a file›
export MOSAIQ_LOCUST_ANALYST_EMAIL=‹fill› MOSAIQ_LOCUST_ANALYST_PASSWORD=‹fill›
```

The ops console listens on the instance's loopback only. Leave this tunnel
open in another terminal:

```bash
gcloud compute ssh mosaiq-deployment-vm --zone=northamerica-south1-a -- -N -L 5090:127.0.0.1:5090
```

Create the pools (24 accounts, one administrator sign-in), run, and retire
them:

```bash
pool=$(mktemp -d)/accounts.json
python3 bench/locust/accounts.py create "$pool" --floor 12 --desktop 12
MOSAIQ_LOCUST_ACCOUNTS_FILE="$pool" BENCH_LOCUST_PROJECT= LOCUST=~/.venvs/mosaiq-locust/bin/locust bash bench/locust/run.sh instance
python3 bench/locust/accounts.py retire "$pool" && rm -f "$pool"
```

The 26 sign-ins take about 4.5 minutes at 10.5 s apart, which is within the
edge's 6 a minute. The run then takes about 9 minutes. `BENCH_LOCUST_PROJECT=`
leaves the local containers out of `facts.txt`. Record the deployed versions
in the table above instead.

**After.**
- The sales stay in the test store, as test rows that their correlation ids
  name (`locust-<run id>-…`, in `bench/locust/results/<run>/settings.txt`).
  ‹fill: the owner's decision: keep them, or reverse them through
  `POST /api/sales/v1/sale-reversals`›.
- The pool accounts are deactivated by `retire`. Deactivation keeps the rows,
  so the run stays traceable.
- Commit `bench/locust/results/<date>-instance/` and add its tables to a new
  section here, beside the local runs, with the comparison.
