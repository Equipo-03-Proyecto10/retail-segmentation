# Stream propagation

F20-06 ([#764](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/764)).
This tool measures, for every stream in [`contracts/streams/`](../../contracts/streams/)
and each of its consumer groups, the time from the commit of an outbox row to
the moment the group acknowledges the entry that the row became. It reports
p50, p95 and the maximum, and it fails a run that exceeds the bound the
stream's contract states under `## Propagation bound`.

It is an operator's tool, not a deployable. It implements no business rule,
has no image, and holds no credentials. It runs in a throwaway `python` container
on the services' network, because that network is the only place where both
Redis and the PostgreSQL socket can be reached, on a laptop and on the instance
alike.

| File | What it is |
|---|---|
| `propagation.py` | `measure`, `backlog` and `check`. `check` uses only the standard library. |
| `run.sh` | Runs `measure` or `backlog` in the container, and drives the stop and restart around `backlog`. |
| `requirements.txt` | The container's two pinned dependencies. |

## How it measures

- **Commit.** The tool makes the change itself and reads its own outbox rows in
  the same transaction: they share its `now()`. It takes the commit time when
  `COMMIT` returns.
- **Acknowledgement.** The tool polls each group's `last-delivered-id`
  (`XINFO GROUPS`) and pending list (`XPENDING`) every `--poll-ms` (default
  5 ms). An entry is acknowledged at the first poll where the group has
  delivered it and it is no longer pending.
- **One clock.** Both times are read on the tool's monotonic clock. A latency is
  overstated by at most one poll interval, and understated by at most one
  commit round trip.
- **Load.** Each change is a touch that fires the capture trigger and changes
  no business value, such as `UPDATE product SET name = name`. Three streams
  need more care:
  - `preferences.changed` captures only inserts and deletes, so one touch
    deletes and re-inserts an interest. That is two events.
  - `users.access_changed` touches inactive accounts only, because auth ends
    every session of a touched account.
  - `segments.run_recorded` captures only inserts, and a run is a business
    record (RN-05). Its touch appends an empty run, so it runs only with
    `--allow-append`, on the local stack.
- **What it skips.** A stream whose producer has no outbox yet, and a group that
  does not exist in Redis yet, are listed with the reason. They are not
  failures.

`check` fails a run in these cases:
- a measured group has no bound;
- its p95 or maximum exceeds the bound;
- an entry was never acknowledged;
- a committed event was never relayed;
- the load hit an error;
- a contract has no bound table.

The `backlog` subcommand does this:
1. It stops one consumer.
2. It raises `--events` events while the consumer is down, and makes sure none
   of them is acknowledged in that time.
3. With `--duplicates N`, it marks N of their outbox rows unrelayed again, so
   the relay appends them twice, as a relay that crashed between `XADD` and its
   `UPDATE` would.
4. It starts the consumer again.
5. It reports the catch-up time, from the restart until the last
   acknowledgement, along with every event that was never acknowledged.

That every event is applied once, even when it is duplicated, is a property of
the consumer kit. It is tested in
`packages/kit-py/tests/test_streams.py::test_consumer_restarted_after_a_backlog_loses_nothing_and_applies_once`.

## Before a run

- **What it writes.** The tool writes as `postgres`. Its writes are test
  writes, not business data:
  - no-op updates such as `SET name = name`;
  - a preference interest deleted and inserted again in one transaction;
  - with the local-only options, empty segmentation runs, and touches of active
    accounts that end their sessions.

  No value a reader sees changes. When #764 says "Writes: none", it means
  business data. Each touch still raises a real event that every consumer
  group handles.
- **Redis memory.** Look at redis-state's memory on the ops console's Redis
  panel first. redis-state does not evict (ADR-0037), so when it is full, writes
  fail, sessions included. A run adds about `--events` entries to each stream,
  and the relays cap every stream at `MAXLEN ~ 10000`. Do not start a run on an
  instance that is already close to its `maxmemory`.

## Run it on the instance

From the checkout, as an operator who can run Docker:

```bash
export DATABASE_URL='postgresql://postgres@/retail?host=/run/postgresql'
export REDIS_URL="redis://ops_console:$(sudo sed -n 's/^REDIS_OPS_CONSOLE_PASSWORD=//p' /etc/mosaiq/svc/ops-console.env)@redis-state:6379/0"
PROPAGATION_OUT=~/propagation ops/propagation/run.sh measure --check
```

`DATABASE_URL` must name a role that can update the captured tables and read
every `<suffix>_events.outbox`. `ops_console` can send every command the tool
sends (`EXISTS`, `XRANGE`, `XREVRANGE`, `XINFO GROUPS`, `XPENDING`) once this
change's ACL line has been assembled into the aclfile.
`ops/console/tests/test_acl.py` drives the tool's real Redis calls and fails
if one is not granted.

The backlog test against `recommendations`, whose unit is `mosaiq-recommendations`:

```bash
PROPAGATION_OUT=~/propagation ops/propagation/run.sh backlog \
  'docker compose -p mosaiq-recommendations stop' \
  'docker compose -p mosaiq-recommendations start' \
  --stream stream:products.v1.changed --group recommendations --events 500
```

Then write both reports into
[`docs/evidence/f20-06-stream-propagation.md`](../../docs/evidence/f20-06-stream-propagation.md).

## Run it on a laptop

The local stack of `compose.distributed.yaml` publishes no PostgreSQL port.
Its socket is the `mosaiq-pgsocket` volume, its network is `mosaiq`, and its
`ops` Redis user's password is `local-ops-only`:

```bash
export DATABASE_URL='postgresql://postgres@/retail?host=/run/postgresql'
export REDIS_URL='redis://ops:local-ops-only@redis-state:6379/0'
export PROPAGATION_NETWORK=mosaiq PROPAGATION_PG_SOCKET=mosaiq-pgsocket PROPAGATION_ENV=local
ops/propagation/run.sh measure --check --allow-append
ops/propagation/run.sh backlog \
  'docker compose -f compose.distributed.yaml stop recommendations' \
  'docker compose -f compose.distributed.yaml start recommendations' \
  --stream stream:products.v1.changed --group recommendations --events 500 --duplicates 5
```

`check` needs no container: `python3 ops/propagation/propagation.py check report.json`.
