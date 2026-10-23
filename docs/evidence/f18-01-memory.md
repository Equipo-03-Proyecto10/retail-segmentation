# F18-01 (#448): memory with the services running

The instance's memory with the web, the infrastructure and the services
running, compared with the budget in
[ADR-0040](../adr/0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md).
The services and how they were started are in
[f18-01-runtime.md](f18-01-runtime.md).

## How it was measured

On 2026-10-23, on `mosaiq-deployment-vm` (`e2-standard-2`, 7,675 MiB as `free`
reports it, with a 2 GB swapfile). It was measured twice: with the 13 core
services, and again after the second wave (campaigns and audit-trail) joined.

The figures come from `free -m` and
`docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}'`,
and the limits from `docker inspect -f '{{.HostConfig.Memory}}'`. The services
were idle: no traffic beyond readiness probes and their outbox relays.

## The host

| `free -m` | 13 core services | all 15 |
|---|---|---|
| total | 7,675 | 7,675 |
| used | 2,198 | 2,254 |
| free | 3,383 | 3,226 |
| shared | 63 | 63 |
| buff/cache | 2,455 | 2,556 |
| available | 5,477 | 5,421 |
| swap used | 0 | 0 |

`swapon --show` reported 512 KiB used on `/swapfile`, which `free -m` rounds
to 0. No container was OOM-killed at either point.

## The containers

| Container | `mem_limit` | Used, 13 core | Used, all 15 |
|---|---|---|---|
| auth | 384 MiB | 81.38 MiB | 81.38 MiB |
| users | 224 MiB | 84.32 MiB | 84.32 MiB |
| recommendations | 192 MiB | 81.45 MiB | 81.46 MiB |
| products | 160 MiB | 83.27 MiB | 83.28 MiB |
| categories | 160 MiB | 83.59 MiB | 83.60 MiB |
| customers | 160 MiB | 83.02 MiB | 83.02 MiB |
| sales | 160 MiB | 82.63 MiB | 82.63 MiB |
| engagement | 160 MiB | 87.11 MiB | 87.12 MiB |
| campaigns *(second wave)* | 160 MiB | | 78.29 MiB |
| segments | 64 MiB | 2.41 MiB | 2.42 MiB |
| stores | 48 MiB | 13.81 MiB | 13.83 MiB |
| stock | 48 MiB | 2.24 MiB | 2.25 MiB |
| customer-preferences | 48 MiB | 17.41 MiB | 17.42 MiB |
| check-ins | 48 MiB | 13.46 MiB | 13.47 MiB |
| audit-trail *(second wave)* | 48 MiB | | 1.69 MiB |
| infra `redis-state` | 96 MiB | 11.05 MiB | 8.32 MiB |
| infra `redis-cache` | 160 MiB | 8.06 MiB | 8.55 MiB |
| infra `mongodb` | 512 MiB | 114.30 MiB | 82.72 MiB |
| **Services** | 1,856 / 2,064 MiB | **716.1 MiB** | **796.2 MiB** |
| **Infrastructure** | 768 MiB | **133.4 MiB** | **99.6 MiB** |
| **All containers** | **2,624 / 2,832 MiB** | **849.5 MiB** | **895.8 MiB** |

The Flask services sit at 78 to 87 MiB, about half of a 160 MiB limit. The
Rust services and segments sit at 2 to 17 MiB of 48 or 64. MongoDB's figure
moves with its cache: it was lower at the second measurement, not because of
the second wave.

## Against ADR-0040

| | ADR-0040's estimate | Measured, all 15 |
|---|---|---|
| Containers | 2,832 MB of limits | 896 MiB in use, under the same 2,832 MiB of limits |
| The host without containers (the OS, Docker, PostgreSQL, the web, NGINX) | about 2,690 MB | about 1,360 MiB (2,254 used minus 896) |
| Committed with every container at its limit | about 5.5 GB of 8 GB | 2,832 + 1,360 ≈ 4.1 GiB of 7.5 GiB |
| CI gate on the sum of limits | 3,000 MB | 2,832, so 168 MB of headroom |

**The budget holds with room to spare.** No cut from ADR-0040's order was
needed (dropping the second wave, then lowering Flask limits to 128 MB), and
the second wave runs.

Two cautions keep the margin from being read as larger than it is:

- **The measurement is idle.** Under load, argon2id raises auth's and users'
  use toward their limits, and the Flask workers grow with request sizes. The
  limits, not today's use, are the ceiling.
- **PostgreSQL will grow.** `used` excludes the page cache and the shared
  memory not yet touched, and `shared` is 63 MiB. Its shared buffers fill as
  queries run, and ADR-0040 counts about 1,700 MB for it. Measured idle, the
  host's 1,360 MiB is therefore a floor. ADR-0040's 2,690 MB stays the figure
  to plan with.

Planned that way, with the ADR's host estimate and every limit full, about
5.5 GB of the 7.5 GiB is committed. The 2 GB swapfile remains a cushion and
was not used.
