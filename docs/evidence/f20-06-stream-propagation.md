# F20-06: Redis Streams propagation per consumer group

Story F20-06 ([#764](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/764)).
This report gives the time from the outbox commit to the consumer group's
acknowledgement, for every stream in [`contracts/streams/`](../../contracts/streams/)
and each of its consumer groups. It also gives the catch-up of a consumer that
restarts after a backlog, and the bound each contract now states. The tool,
and how it takes both times on one clock, are described in
[`ops/propagation/README.md`](../../ops/propagation/README.md).

| Environment | Status |
|---|---|
| Local stack (`compose.distributed.yaml`) | **Measured** on 2026-11-06. The figures are below. |
| GCP instance | **Measured** on 2026-11-24 (UTC), release #1188 at `50bf7d5c`. The figures are in [The instance](#the-instance). |

## Local stack

Setup:
- An Apple M2 laptop running Docker Engine 29.7.2.
- The streams' producers and consumers from `compose.distributed.yaml` at
  `develop` `ea45747`: sales, products, categories, stock, stores, users,
  auth, customer-preferences, segments and recommendations. Each ran as its
  own container, in an isolated compose project.
- Redis 7.4 as in `deploy/infra/redis/redis-state.conf`, including
  `appendfsync everysec`.

The load was 300 changes per stream at 20 changes per second, on every stream
at once. The poll interval was 5 ms. `preferences.changed` raises two events
per change, so it shows 600.

Two runs were made, `22:07Z` and `22:15Z`. The figures are from the second run,
which was run with `--check` and passed. The first run's p50 / p95 / max are
given beside them. The second run's raw report is
[`f20-06-stream-propagation-local.json`](f20-06-stream-propagation-local.json),
which `tests/test_stream_propagation.py` re-checks against the contracts on
every `pytest`.

| Stream | Group | Events | p50 (ms) | p95 (ms) | Max (ms) | Run 1: p50 / p95 / max | Unacked |
|---|---|---:|---:|---:|---:|---|---:|
| `stream:categories.v1.changed` | `categories` | 300 | 526.1 | 970.5 | 1022.7 | 529.8 / 980.2 / 1018.3 | 0 |
| `stream:categories.v1.changed` | `recommendations` | 300 | 548.7 | 976.1 | 1022.7 | 552.6 / 980.3 / 1018.3 | 0 |
| `stream:preferences.v1.changed` | `recommendations` | 600 | 600.2 | 976.4 | 1022.3 | 586.8 / 982.4 / 1024.0 | 0 |
| `stream:products.v1.changed` | `products` | 300 | 529.4 | 976.9 | 1020.9 | 530.1 / 980.0 / 1016.6 | 0 |
| `stream:products.v1.changed` | `recommendations` | 300 | 551.9 | 982.5 | 1020.9 | 549.6 / 981.7 / 1016.6 | 0 |
| `stream:sales.v1.recorded` | `recommendations` | 300 | 547.1 | 969.2 | 1021.7 | 549.6 / 979.5 / 1027.9 | 0 |
| `stream:segments.v1.run_recorded` | `recommendations` | 300 | 551.1 | 969.3 | 1034.2 | 550.0 / 980.4 / 1018.3 | 0 |
| `stream:segments.v1.run_recorded` | `segments` | 300 | 529.8 | 966.3 | 1022.9 | 524.9 / 979.6 / 1018.3 | 0 |
| `stream:stock.v1.changed` | `recommendations` | 300 | 555.6 | 975.0 | 1030.5 | 549.9 / 981.1 / 1016.3 | 0 |
| `stream:stores.v1.changed` | `stores` | 300 | 533.1 | 973.1 | 1036.2 | 535.2 / 980.3 / 1016.7 | 0 |
| `stream:users.v1.access_changed` | `auth` | 300 | 533.8 | 969.0 | 1016.0 | 529.9 / 979.3 / 1013.9 | 0 |

In both runs, no committed event was left unrelayed and no entry was left
unacknowledged.

Some streams or groups were not measured, because they do not exist yet:

| Stream | Groups | Why |
|---|---|---|
| `stream:engagement.v1.interaction_recorded` | `experimentation`, `recommendations` | The relay publishes from engagement's MongoDB notices (ADR-0075, #799), which `ops/propagation/` cannot load yet, and neither group consumes the stream yet. |
| `stream:rfm.v1.profile_changed` | `model-registry`, `analytics-kpis` | The producer is not deployed. The stream is contracted but not captured in `sql/01_schema.sql`. |
| `stream:models.v1.publication_recorded` | `rfm-engine` | The producer is not deployed. |
| `stream:preferences.v1.consent_changed` | `recommendations`, `campaigns`, `rfm-engine` | The stream is contracted (#794) but not captured yet. |
| `stream:sales.v1.reversed` | `recommendations`, `rfm-engine`, `experimentation` | The stream is contracted but not captured yet. |
| `stream:segments.v1.assignment_changed` | `recommendations`, `analytics-kpis` | The stream is contracted but not captured yet. |
| `stream:sales.v1.recorded` | `rfm-engine` | The consumer is not deployed, so Redis has no such group. |

Local-only touches:
- `segments.run_recorded` was measured with `--allow-append`, which appends
  empty segmentation runs.
- `users.access_changed` was measured with `--allow-session-revocation`,
  because the seed has no inactive account.

Neither option is used on the instance.

### What the figures say

The latency is spread across the first second after a commit: the p50 is
0.53–0.60 s, the p95 just under 1 s, and the maximum just over it, in every
group alike. Its cause is the relay, not the consumers:
1. The relay confirms each batch with `WAITAOF 1 0 2000` before it marks the
   batch relayed (ADR-0037, ADR-0056).
2. With `appendfsync everysec`, that call returns at the next fsync, which is
   up to one second later.
3. A batch therefore becomes visible to a group 0–1 s after its commit,
   depending on where the commit falls in the fsync second.

Delivery and acknowledgement add tens of milliseconds, which is the gap
between a group's p50 and the stream's fastest group. The maximum is about
1 s plus one poll interval and one handler.

### The bounds

Every group in every contract, measured or not, now states:

| Bound | Value | Why |
|---|---:|---|
| p95 | 3000 ms | Three times the local p95, about 980 ms, rounded up. Contention on the instance can stretch the fsync second but should not triple it. |
| Max | 5000 ms | The relay's fallback poll interval. An event that waits longer than a whole poll has missed its wake-up and something else as well. |

The bounds are in each contract's `## Propagation bound` table, which
`propagation.py` parses. Parity after a change (#768) reuses them. A group that
is not deployed yet holds the same default bound, marked "Not measured yet",
from its first run.

## Backlog and restart

Two runs: one where only the consumer stopped, and one where the stopped
service is also the stream's producer, so its relay stopped too.

| Run | Stopped service | Backlog | Entries | Acknowledged | Lost | Max deliveries | Catch-up |
|---|---|---|---:|---:|---:|---:|---:|
| `products.changed` → `recommendations` (Python kit) | the consumer only | 500 events relayed while it was down, 5 of them appended twice | 505 | 505 | 0 | 1 | **3.6 s** |
| `stores.changed` → `stores` (Rust kit) | the consumer and the stream's relay | 500 events left in the outbox | 500 | 500 | 0 | 1 | **29.3 s** |

Raw reports:
[`f20-06-backlog-recommendations-local.json`](f20-06-backlog-recommendations-local.json),
[`f20-06-backlog-stores-local.json`](f20-06-backlog-stores-local.json).

- **No event was lost.** Every event committed while the consumer was down was
  acknowledged after the restart.
- **Re-applying an event leaves the same state.** Criterion 3 was changed on
  2026-11-09 with the owner's agreement (see #764). It no longer says "none is
  applied twice (dedupe by `event_id`)". It now says "re-applying an event
  leaves the same state (idempotent by re-read, ADR-0044)".
  - Why: an event carries keys only, and every consumer re-reads state from
    the producer's contract. Applying the same event again therefore reaches
    the same state. Deduplication by `event_id` is in memory only, so a crash
    after the effect and before `XACK` re-applies the event.
  - In this run, no entry was delivered more than once (`Max deliveries` 1),
    and the five duplicated events were acknowledged without error.
  - Each consumer group has a test that applies the same event three times:
    1. in a process that dies after the effect and before `XACK`;
    2. in the restarted process, from its pending list, with an empty
       `event_id` memory;
    3. as a duplicate entry, the way a relay appends one when it crashed
       between `XADD` and marking its rows relayed.

    After each application, what a reader observes is identical.

    | Group | Streams | Test | State compared |
    |---|---|---|---|
    | `categories` | `categories.changed` | `services/categories/tests/test_redis.py::test_reapplying_an_event_leaves_the_same_state` | the category read |
    | `products` | `products.changed` | `services/products/tests/test_redis.py::test_reapplying_an_event_leaves_the_same_state` | the product read, with the production handler |
    | `recommendations` | all six of its streams | `services/recommendations/tests/test_redis.py::test_reapplying_an_event_leaves_the_same_state[...]` | the cache key, and the recommendation recomputed from PostgreSQL, without its computation instant |
    | `auth` | `users.access_changed` | `services/auth/tests/test_background.py::test_reapplying_an_access_change_leaves_the_same_sessions` | every live session and session key |
    | `stores` | `stores.changed` | `services/stores/tests/cache.rs::reapplying_an_event_leaves_the_same_directory` | the directory cache and the directory read |
    | `segments` | `segments.run_recorded` | `services/segments/tests/cache.rs::reapplying_an_event_leaves_the_same_run_and_list` | the tracked run and the segment list |

    The groups that are not deployed yet (`rfm-engine`, `model-registry`,
    `analytics-kpis`, `experimentation`) have no handler to test. Each one owes
    the same test with its implementation.
  - The kit's own restart test,
    `packages/kit-py/tests/test_streams.py::test_consumer_restarted_after_a_backlog_loses_nothing_and_applies_once`,
    shows the delivery side: after a restart nothing is lost, and a duplicate
    within one process is skipped.
- **Catch-up.** Catch-up is timed from the restart command until the last
  entry is acknowledged, so it includes container start-up.
  - When the backlog had been relayed, `recommendations` caught up in 3.6 s.
  - When the backlog waited in the outbox, `stores` took 29.3 s. See the
    findings.

### Findings

1. **An outbox backlog drains at about 100 rows per 5 seconds.**
   - What happens: both kits' relays wait up to 5 s for a wake-up before every
     pass, and a pass relays at most 100 rows. A service whose relay was down
     therefore catches up on 500 rows in about 25–30 s, and on 10 000 rows in
     about 8 minutes. A full batch does not trigger another pass at once.
   - Effect: the delay does not affect correctness or the steady-state
     figures above. It does affect recovery time.
   - A fix would change both kits and every service, so it is not part of this
     story. It is proposed as a follow-up.
2. **Deduplication by `event_id` lives in memory** (`StreamConsumer._handled`
   in both kits).
   - Within one process, duplicates are skipped.
   - After a crash between the effect and `XACK`, the event is applied again.
     That is why criterion 3 now asks for idempotence by re-read, not for a
     single application. The tests above prove idempotence for every deployed
     group.
   - A future consumer whose effect is not idempotent would need durable
     deduplication in its own schema, in the same transaction as its effect.

## Console

The ops console now shows, for each consumer group, the age of the oldest
entry the group has not acknowledged, beside its lag and pending count
(`ops/console/app/redis_stats.py`, ops-console 0.2.0).

It was checked on the local stack as the `ops_console` user with the new ACL
line (`+xpending +xrange +time`). `recommendations` was stopped and three
products were touched:

```text
stream:products.v1.changed products        lag 0 pending 0 oldest —
stream:products.v1.changed recommendations lag 3 pending 0 oldest 4.8 s
```

## The instance

Run on 2026-11-24 between 04:40Z and 04:55Z (the evening of 2026-11-23 in
Mexico City), on release #1188, `main` at `50bf7d5c`, with `ops/propagation`
from a `git archive` of that commit. Redis is 7.4.11 with `appendfsync
everysec`, `redis-state` at 64 MB with `noeviction` (2 MB used before the run).
The tool ran as the `ops_console` Redis user and as the OS user `postgres`.

**Why it did not run earlier.** The first attempt, an hour before, found no
`ops_console` user on `redis-state`: `deploy-service.sh` assembled the aclfile
without the `ops/*/redis` lines, so every service deploy dropped it
(fixed by [PR #1196](https://github.com/Equipo-03-Proyecto10/retail-segmentation/pull/1196)).
The user loaded the aclfile with `ACL LOAD` (no restart); `ops_console` then
held `+xpending +xrange +time` on both Redis instances.

Two things the runbook did not say, found by running it on the instance:
- The instance's PostgreSQL accepts `postgres` on its socket only from the OS
  user `postgres` (peer authentication), and the tool's container runs as root.
  `run.sh` now takes `PROPAGATION_USER` (`$(id -u postgres)`), runs `pip`
  with `--user`, and the report directory must be writable by that uid.
- SELinux is enforcing: the tool's directory, the contracts and the report
  directory needed the label `container_file_t` to be bind-mounted
  (`chcon -R -t container_file_t`, on the copy of the tree only).

```bash
export DATABASE_URL='postgresql://postgres@/retail?host=/run/postgresql'
export REDIS_URL="redis://ops_console:…@redis-state:6379/0"   # read from /etc/mosaiq/svc/ops-console.env
PROPAGATION_USER=$(id -u postgres) PROPAGATION_OUT=<dir> ops/propagation/run.sh measure --check
PROPAGATION_USER=$(id -u postgres) PROPAGATION_OUT=<dir> ops/propagation/run.sh backlog \
  'docker compose -p mosaiq-recommendations stop' \
  'docker compose -p mosaiq-recommendations start' \
  --stream stream:products.v1.changed --group recommendations --events 500
```

The load was 200 changes per stream at 20 per second (the tool's default;
`preferences.changed` raises two events per change, so 400). The poll interval
was 5 ms. `segments.run_recorded` was skipped (it appends a segmentation run),
and 16 groups were skipped because their producer is not captured yet, as
listed in `skipped` of the report. `users.access_changed` touched inactive
accounts only (72 of them).

### Propagation, every deployed group

Two runs were made. The second one passed `--check`
([`f20-06-stream-propagation-instance.json`](f20-06-stream-propagation-instance.json)):

| Stream | Group | Events | p50 (ms) | p95 (ms) | Max (ms) | Unacked |
|---|---|---:|---:|---:|---:|---:|
| `stream:categories.v1.changed` | `categories` | 200 | 629.7 | 984.3 | 1057.7 | 0 |
| `stream:categories.v1.changed` | `recommendations` | 200 | 662.8 | 1002.2 | 1057.7 | 0 |
| `stream:preferences.v1.changed` | `recommendations` | 400 | 697.1 | 1019.8 | 1132.8 | 0 |
| `stream:products.v1.changed` | `products` | 200 | 629.1 | 994.9 | 1093.9 | 0 |
| `stream:products.v1.changed` | `recommendations` | 200 | 661.4 | 1019.8 | 1112.9 | 0 |
| `stream:sales.v1.recorded` | `recommendations` | 200 | 663.7 | 1018.6 | 1112.1 | 0 |
| `stream:sales.v1.recorded` | `rfm-engine` | 200 | 727.4 | 1046.4 | 1125.9 | 0 |
| `stream:stock.v1.changed` | `recommendations` | 200 | 663.1 | 992.4 | 1115.3 | 0 |
| `stream:stores.v1.changed` | `stores` | 200 | 632.1 | 1007.4 | 1111.5 | 0 |
| `stream:users.v1.access_changed` | `auth` | 200 | 626.1 | 989.7 | 1097.8 | 0 |

Every p95 is about 1.0 s and every maximum about 1.1 s, against the contracts'
bound of **p95 3000 ms and max 5000 ms**. No group exceeds a bound, and no
entry was left unacknowledged. The local figures
([above](#local-stack)) are the same to within 10%: the cause is the relay's
`WAITAOF` and `appendfsync everysec`, not the machine.

**The first run failed `--check`**
([`f20-06-stream-propagation-instance-first-run.json`](f20-06-stream-propagation-instance-first-run.json)):
`FAIL: 2 committed events never relayed`. In it `stream:products.v1.changed` and
`stream:sales.v1.recorded` each report 199 events of 200. Read afterwards,
`products_events.outbox` and `sales_events.outbox` held all 200 rows of the run
each, with none unrelayed, so the relay did not lose them; the tool did not match
one event in each stream to its entry. The second run, a minute later with the
same load, matched all 200 in both. This is recorded as a finding, not retuned
away: it may be a race in the tool's first poll (the entries of that run were
all acknowledged, with 0 unacknowledged), but one run does not prove it. The
other figures of the first run are within 3% of the second's.

### Backlog on `recommendations`

`mosaiq-recommendations` was stopped with `docker compose -p mosaiq-recommendations stop`
and started again in the same step
([`f20-06-backlog-recommendations-instance.json`](f20-06-backlog-recommendations-instance.json)).
After it, the container was `Up`, the gateway served its contract (200), and
`check-services.sh` and `check-redis.sh` printed nothing.

| Backlog run | Entries | Acknowledged | Lost | Max deliveries | Catch-up |
|---|---:|---:|---:|---:|---:|
| `products.changed` → `recommendations` | 500 | 500 | 0 | 1 | **13.7 s** |

The stopped service was the consumer only (`producer_stopped: false`), so the
500 events were relayed while it was down, as in the local run, whose catch-up was
3.6 s. Catch-up is timed from the restart command until the last acknowledgement
in both, so it includes the container's start. The instance's 13.7 s is
about four times the local figure; this run does not isolate why (the
container's cold start is a candidate, not a measurement). No event was lost
and none was delivered twice. There is no contract bound on catch-up.

### What the run touched

Real writes by the tool, none changing a value a reader sees, in each of the
two measure runs of 200 changes per stream: no-op updates (`SET x = x`) of
40 products, 30 categories, 30 stores, 150 inventory rows (`quantity_on_hand`
unchanged; they include store 17's rows, and no stock movement was recorded),
200 sale rows and 72 inactive accounts; and, for each of the 30 customers, a
preference interest deleted and re-inserted in one transaction (800 events in
the two runs). The backlog run touched 500 products' rows the same way. No
sale, assignment or stock movement was recorded. Each touch raised a real
event that every group handled.
