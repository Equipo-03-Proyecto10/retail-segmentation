# F19-08 (#758): memory under load, and the gate at 3,500 MB

This measures the instance's memory with the existing services under load. It
follows [f18-01-memory.md](f18-01-memory.md), which measured them idle. It also
checks the budget that
[ADR-0059](../adr/0059-four-new-services-join-the-fifteen-and-six-capabilities-fold-into-the-rule-they-extend.md)
moves to 3,500 MB, which `.github/scripts/memory_gate.py` now enforces.

## How it was measured

The measurement ran on 2026-11-13 from 19:50:50 to 19:55:50 UTC, on
`mosaiq-deployment-vm`: an `e2-standard-2` with 2 vCPUs, 7,675 MiB as `free`
reports it, and a 2 GB swapfile. Everything ran at the versions deployed that day.

- A load generator ran on the instance itself, as root, with Python's standard
  library only. Eight threads ran for 300 seconds and called the services
  directly on their loopback ports (`127.0.0.1:5002` to `5015`).
- 75% of the calls were `GET`s spread over every deployed service except auth
  and audit-trail: 39 read operations, taken from the contracts, alternating
  JSON and XML. The other 25% were the web's pages through
  `127.0.0.1:8000`: home, catalogs, stock, run history, segmentation dashboard,
  model comparison, migration matrix, reports, campaigns, experiments,
  notifications and the administration lists.
- The run signed in as the instance's administrator: four service sessions
  through auth and two web sessions, all signed out at the end. It wrote no
  sale, stock movement, segment run or campaign, and it did not read
  audit-trail, so it recorded no domain events.
- `docker stats --no-stream` and `free -m` were sampled every 3 seconds: three
  samples before the load, and 100 during it. The limits were read with
  `docker inspect -f '{{.HostConfig.Memory}}'`.

What the load was, and was not:

| | |
|---|---|
| Requests | 19,196 in 300 s, **64 per second** |
| Web pages | 4,798, every one 200 |
| Service calls | 14,398: 5,140 answered 200, 4,977 404, 1,000 422 and 3,281 429 |

- **429.** Each service's own per-caller limit (120 a minute by default)
  answered a quarter of the service calls. The load therefore cannot say what
  a service holds above that rate. One administrator coming from one address is
  the worst case for that limit.
- **404 and 422.** The ids in the paths were drawn from 1 to 30, and a
  customer id is a UUID. Some lists also need a query parameter the generator
  did not send. These answers still pass through authentication, the liveness
  check, the database and the serializer. They do not build large bodies.
- **Sign-ins** happened before the sampled window, so argon2id's memory in
  auth and users under repeated sign-ins is **not** in these figures.

The demonstration itself is a few people at a few screens
(`docs/demo-system-guide.md` §13), well under 64 requests a second. This run
is heavier than that, but it is reads only.

## The host

| `free -m` (MiB) | Before the load | Under load (peak `used`) |
|---|---:|---:|
| total | 7,675 | 7,675 |
| used | 2,460 | 2,652 |
| free | 3,997 | 3,801 |
| shared | 171 | 171 |
| buff/cache | 1,635 | 1,639 |
| available | 5,215 | 5,023 (lowest) |
| swap used | 0 | 0 |

`used` rose by 192 MiB, almost all of it outside the containers, whose sum moved
by 4 MiB: the web's gunicorn workers and PostgreSQL. No container was
OOM-killed, and swap stayed unused.

## The containers

| Container | `mem_limit` | Before the load (MiB) | Peak under load (MiB) | Peak / limit |
|---|---:|---:|---:|---:|
| infra `mongodb` | 512 MiB | 212.0 | 212.0 | 41% |
| auth | 384 MiB | 102.8 | 102.8 | 27% |
| users | 224 MiB | 105.1 | 105.5 | 47% |
| recommendations | 192 MiB | 102.5 | 102.6 | 53% |
| engagement | 160 MiB | 108.5 | 109.1 | 68% |
| sales | 160 MiB | 106.9 | 107.0 | 67% |
| categories | 160 MiB | 103.8 | 104.1 | 65% |
| customers | 160 MiB | 103.6 | 103.9 | 65% |
| products | 160 MiB | 103.1 | 103.7 | 65% |
| campaigns | 160 MiB | 102.0 | 102.1 | 64% |
| infra `redis-cache` | 160 MiB | 18.5 | 18.6 | 12% |
| **ops-console** | 96 MiB | 86.7 | 86.8 | **90%** |
| infra `redis-state` | 96 MiB | 8.7 | 8.8 | 9% |
| segments | 64 MiB | 10.4 | 10.5 | 16% |
| customer-preferences | 48 MiB | 25.9 | 26.0 | 54% |
| check-ins | 48 MiB | 22.6 | 23.0 | 48% |
| stores | 48 MiB | 22.6 | 22.9 | 48% |
| stock | 48 MiB | 10.3 | 10.4 | 22% |
| audit-trail | 48 MiB | 9.1 | 9.1 | 19% |
| **All 19 containers** | **2,928 MiB** | **1,365.1** | **1,369.0** (sum of peaks) | |

Since F18-01 the Flask services have grown from 78–87 MiB to 102–109 MiB at
rest, and they barely move under this load. The largest is engagement, at
109.1 MiB, 68% of its 160 MiB. The Rust services stay at 9 to 26 MiB.
ops-console is the container nearest its limit, at 90%, and it was already
there at rest.

## Against the budget

The gate's default set now includes `ops/console/compose.yaml`. Its container
runs on the instance, and its 96 MB had been outside the gate.

| | `mem_limit` sum |
|---|---:|
| Today, as the gate now counts it (17 compose projects) | 2,928 MB |
| + sales' import worker, ADR-0070 (#853) | 3,024 MB |
| + ADR-0059's four new services (544 MB) | **3,568 MB** |
| Gate (ADR-0059) | 3,500 MB |

ADR-0059 and ADR-0070 planned 3,472 MB because they did not count the ops
console. Counted, the full plan is 68 MB over the gate. Nothing deployed today
breaks it: the sum stays at 3,024 MB until a new service's pull request adds its
limit.

**No limit failed under load.** Every container stayed below 70% of its limit,
except ops-console, which sits at 90% at rest as well. So criterion 3 has
nothing to correct today. The correction the plan needs is the one ADR-0059
names first: "lower the Flask limits to 128 MB after measuring". This run is
that measurement for the six Flask services at 160 MB:

- Their peak is 102 to 109 MiB, which leaves 19 to 26 MiB (15% to 20%) under
  128 MB.
- Lowering all six saves 192 MB. The full plan then commits 3,376 MB, under the
  gate with 124 MB of margin.
- The three new Flask services would start at 128 MB on the same evidence,
  which is ADR-0059's "up to 288 MB across nine services".

This pull request does **not** change any limit. The first new service whose
pull request would take the sum over 3,500 MB has to carry the cut, and the gate
fails it until it does. Two cautions apply to that cut:

- **What this did not exercise.** Above-limit request rates, large bodies and
  argon2id sign-ins were not in the load. A 128 MB Flask limit should be
  watched on the next measured run, and auth (384 MB) and users (224 MB),
  which hash passwords, are not candidates.
- **ops-console.** At 90% of 96 MiB it has the least headroom of any container,
  and it must not be lowered. If its use grows, it is the container to raise
  first.

With today's 2,928 MB of limits and the host's 2,652 MiB peak `used` (which
already includes what the containers use), every limit full commits about
2,928 + (2,652 − 1,369) ≈ 4.1 GiB of 7.5 GiB. ADR-0040's planning estimate for
the host is larger and remains the figure to plan with, as F18-01 concluded.
