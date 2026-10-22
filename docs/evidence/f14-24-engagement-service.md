# F14-24 (#466): engagement-service on MongoDB

What was run, against what, and what was not. The service is
`services/engagement/`; the rule is RN-55 and the design is
[ADR-0038](../adr/0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md).

## Where it was run

| Piece | Used here | CI / instance |
|---|---|---|
| MongoDB | `mongod` 7.0.34 (conda-forge build), standalone, `--auth`, as `deploy/infra/compose.yaml` runs it | `mongo:7.0.41`, pinned by digest |
| mongosh | 2.13.0 | the same version, installed by the new `services.yml` step |
| PostgreSQL | 16, loaded from the three scripts of this commit; the service connects as `svc_engagement` | `postgres:16` |
| Redis | 7.4.7 with an `aclfile` holding the committed `engagement.acl` line | `redis:7.4.11`, pinned |
| Python | 3.12, `uv sync --frozen`, kit `kit-py/v0.1.4` | the same, in the image |

## Results

- `pytest` in `services/engagement`: **143 passed** with PostgreSQL, MongoDB, Redis and mongosh present; 41 passed and 102 skipped with none of them
  (a test that needs one skips, and CI sets `MOSAIQ_REQUIRE_MONGO`, `MOSAIQ_REQUIRE_REDIS` so it fails there instead).
- `black --check` (26.5.1) and `ruff check` clean; `bash .github/scripts/guards.sh` prints nothing; `bash deploy/templates/service/check.sh` prints nothing;
  `python3 docs/check.py` passes.
- The shared conformance suite (`tests/conformance`, 28 cases: byte-equal contract files, 406, 415, 413, XML attack bodies, token cases, `auth_unavailable`
  with redis-state stopped, XSD-valid XML) **passed against the running service**, with `docker` stood in by a small script that stops and starts
  two local Redis servers. It did not run against the image (see below).
- `contracts/db/check_grants.sh` prints nothing; the ADR-0038 `role_table_grants` query prints nothing; `svc_engagement` cannot read `public.store`.

## Acceptance, case by case

| Criterion | Evidence |
|---|---|
| Insert checks store, product, customer in the three views | `test_events.py`: unknown/inactive store, unknown product, unknown customer, in that order; ids outside int32 never reach PostgreSQL (`test_units.py`) |
| `occurred_at` more than 5 minutes ahead is 422 | `test_an_event_more_than_five_minutes_ahead_is_refused` (6 minutes refused, 4 accepted) |
| Replay answers 200 with the original, a different type/store/product 409 | `test_a_replay_*`, `test_a_client_event_id_reused_*`; eight simultaneous posts of one `client_event_id` leave one document and one 201 |
| Void: `voided:false` guard, `$inc -1`, second void 409 with one history entry, unknown id 404, STORE_MANAGER 403 | `test_a_void_*`, `test_two_voids_at_once_count_once` (six at once: one 200, five 409, rollup -1 once) |
| List by store, type or customer uses its index, `totalDocsExamined == nReturned` | `test_each_filter_is_an_index_scan_that_reads_only_what_it_returns` explains the query the service ran, for each of the three |
| INVENTORY_PLANNER 403 on events, 200 on summaries | `test_the_events_are_read_by_the_roles_the_contract_names`, `test_summaries_are_read_by_the_inventory_planner_too` |
| Summaries from `engagement_daily`, voided not counted | `test_summaries_count_only_what_is_not_voided` |
| `rebuild-rollup` agrees with the incremental counts and repairs drift | `test_rollup.py`: equality, a drifted/stale rollup repaired, only its days touched, the CLI, a bad range refused |
| Live counters: INCR/DECR, repaired from the rollup, 48 h TTL, summary reads the counter, redis-cache stopped answers from MongoDB | `test_rollup.py` (a deleted counter between two inserts equals `engagement_daily.count`; a planted value 41 is what the summary returns; a service whose redis-cache is a dead address still answers 201 and 200) |
| `init.js`: twice with no change, converges a drifted shape, refuses without the password | `test_init.py`, run in mongosh as root: snapshots of collections, validator, indexes and roles are equal after a second run and after repairing a validator turned off, a non-unique `client_event_id` index, a stray index, a missing daily index and a weakened role |
| Validator refuses what is not an event | `test_the_validator_refuses_what_is_not_an_event`: 26 documents, all code 121 "Document failed validation" (a scan with `rank`, a click without one, extra fields, bad ids, doubles for ints, ...) |
| `seed.js` twice, 40 `QR_SCAN` and 36 `RECO_CLICK`, ends with the rollup | `test_the_seed_inserts_once_and_agrees_with_its_rollup` |
| Readiness | `test_readiness_needs_every_collection_and_index`: dropping one of the four indexes turns `/health/ready` 503 `schema_not_ready` and leaves `/health/live` 204; the service never alters what is missing |
| MongoDB stopped: 503 `database_unavailable`, never 500; PostgreSQL stopped: the post only | `test_outages.py`; `serverSelectionTimeoutMS` is clamped to 2000 even when the URI asks for 30000 |
| Redis ACL | `test_acl.py`: the line is exactly the expected one; with Redis 7.4, `ACL DRYRUN` allows `INCR engagement:live:1:20261013:QR_SCAN` for `svc_engagement` and denies it for the nine other services' lines and `XADD stream:products.v1.changed`; the counters run signed in as that user |

The ACL has no `MULTI`/`EXEC` (`-@all +@connection +@read +@write`), so the counter's `INCR` and `EXPIRE NX` go out as a non-transactional
pipeline, like the kit's rate limiter. A pipeline with `transaction=True` fails under the real ACL; `test_acl.py` catches it.

## Compliance block of ADR-0038, in mongosh as `svc_engagement`

```text
authenticatedUserRoles  [ { role: 'readWrite', db: 'engagement' } ]
collections             [ 'engagement_daily', 'engagement_event' ]
validator present       true
indexes                 _id_, client_event_id (unique), store_id+occurred_at, type+occurred_at, customer_id+occurred_at
QR_SCAN with a rank     Document failed validation
winning plan uses       store_id_1_occurred_at_-1
```

`explain("executionStats")` of the three list queries on the seeded data:

```text
{"store_id":3}                                          LIMIT > FETCH > IXSCAN(store_id_1_occurred_at_-1)       nReturned=13 totalDocsExamined=13
{"type":"QR_SCAN"}                                      LIMIT > FETCH > IXSCAN(type_1_occurred_at_-1)           nReturned=20 totalDocsExamined=20
{"customer_id":"00000000-0000-0000-0000-000000000005"}  LIMIT > FETCH > IXSCAN(customer_id_1_occurred_at_-1)    nReturned=1  totalDocsExamined=1
```

## A request by request run

Against the running service (`POST` then replay, conflict, void, and the reads):

```text
POST             201  voided=False  recorded_by=<the token's sub>
POST (replay)    200  same event_id
POST (conflict)  409  client_event_conflict
POST bad store   422  unknown_store
VOID             200  voided=True  history[0] = VOIDED by <the administrator>
VOID again       409  already_voided
VOID as manager  403
VOID unknown     404  event_not_found
LIST (inventory planner) 403   SUMMARY (inventory planner) 200
```

## Mutations the tests catch

Each of these was applied to the service or to `init.js`/`seed.js` and the tests run; every one failed a test: the conflict check removed;
the future check removed; the void guard `voided:false` removed; the void counting up; the live counter never repaired; the summary ignoring days;
the rebuild not zeroing stale groups; a connection failure not mapped; the list oldest first; the list unsorted; the page without `xsi:type`;
the counter pipeline made a transaction; the ACL without `~engagement:live:*`; a 30 s selection timeout; readiness ignoring indexes;
`init.js` without the scan/rank rule; `init.js` keeping stray indexes; `seed.js` inserting twice; a replay writing; `recorded_by` taken from
something other than the token; the inactive-store check removed; a forbidden word in `app/` (the guard prints it).

## Not run here

- **The image.** There is no Docker daemon in the workspace, so `docker build`, `docker compose up` of the distributed stack, and
  `tests/conformance/stack.sh` were not run. The service was run from the same `uv.lock` with `uv sync --frozen --no-dev` (production
  dependencies only) and the same `gunicorn "app:create_app()"` command; the Dockerfile and compose files are the rendered ones
  (`deploy/templates/service/check.sh` prints nothing).
- **The new `services.yml` step** that starts MongoDB, Redis and mongosh for a service with `mongo/init.js`. It follows `stack.sh` but is untested until
  CI runs it.
- The MongoDB used is 7.0.34, not the pinned 7.0.41; the Redis is 7.4.7, not 7.4.11.
