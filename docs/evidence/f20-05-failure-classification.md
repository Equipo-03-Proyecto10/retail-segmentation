# F20-05 — every failure of the consolidated run, classified

Issue [#763](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/763),
answering RT-02 in [`../scope-delivery-3.md`](../scope-delivery-3.md). Every
result below comes from F20-03 ([#761](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/761)),
`ops/verify/run.sh`, run on GitHub Actions as **Verify one commit**
(`.github/workflows/verify.yml`). Each run's report is its artifact
`verify-<sha>`.

## The result

On `develop` at **`f6c7bc74`** (the merge of #953), F20-03 ran three times. 17,
16 and 17 cases failed:
- **The campaign suites have no failure.** `service-campaigns` held 124 of 124,
  `conformance-campaigns` 29 of 29, all three times, and in each of the ten
  reports listed below. The web's campaign and
  experiment tests (in `web` and `database`), `golden` (which pins the
  campaigns port), `e2e` and `parity` also passed. The two campaign failures
  that RT-02 cites do not appear in a run from an empty environment.
- **No failure is a product defect.** Each one is either a test defect or an
  environment fault.
- **The 16 cases that repeat in every run** are one test defect in
  `tests/negative`, tracked by #977 (`type:bug`), with its fix in PR #978. The
  same is true of the one case that failed in a single run.
- **The one case that differed between two runs of the same commit**
  (`service-categories`) is a second test defect, raised as its own issue.

Classes:
- **Defect.** The product does something its contract, ADR or rule forbids.
- **Test defect.** The test, its fixture or the runner is wrong. The product
  behaves as documented.
- **Environment fault.** Something outside the commit failed: the network, the
  host or a registry.

## The runs

| Run | Commit | Event | Runs | Failed cases | Report |
|---|---|---|---|---|---|
| [42252895763](https://github.com/Equipo-03-Proyecto10/retail-segmentation/actions/runs/42252895763) | `ba003bb5` (#953 branch) | push | 2 | 23, 23 | `verify-ba003bb5…` |
| [42301744760](https://github.com/Equipo-03-Proyecto10/retail-segmentation/actions/runs/42301744760) | `d87fcb36` (#953 branch) | push | 2 | 16, 16 | `verify-d87fcb36…` |
| [42574882343](https://github.com/Equipo-03-Proyecto10/retail-segmentation/actions/runs/42574882343) | `c687ea2c` (#953 branch, now in develop) | push | 2 | 17, 20 | `verify-c687ea2c…` |
| [42658824949](https://github.com/Equipo-03-Proyecto10/retail-segmentation/actions/runs/42658824949) | **`f6c7bc74` (develop)** | dispatch | 1 | 17 | `verify-f6c7bc74…` |
| [42750359330](https://github.com/Equipo-03-Proyecto10/retail-segmentation/actions/runs/42750359330) | **`f6c7bc74` (develop)** | dispatch, `twice` | 2 | 17, 16 | `verify-f6c7bc74…`, with `comparison.md` |
| [42716817920](https://github.com/Equipo-03-Proyecto10/retail-segmentation/actions/runs/42716817920) | `037a8cfd` (#978 branch) | dispatch | 1 | **0** | `verify-037a8cfd…` |

The runner (`ops/verify`) was the dispatching ref's: `f6c7bc74` for the last
three rows. To reproduce:

```bash
gh workflow run verify.yml -R Equipo-03-Proyecto10/retail-segmentation --ref develop -f commit=f6c7bc7422daa457568670d3fa3f1453f8861709 -f twice=true
```

```bash
gh run download 42750359330 -R Equipo-03-Proyecto10/retail-segmentation -D verify-42750359330
```

Locally, the command is `bash ops/verify/run.sh f6c7bc74` (two to three hours
on an Apple Silicon laptop, under amd64 emulation).

`comparison.md` of 42750359330 (both runs of `f6c7bc74`):

> 6144 cases compared, 1 differ.
>
> | Suite | Case | First | Second |
> |---|---|---|---|
> | service-categories | `tests.test_rules::test_a_write_is_audited_with_the_callers_identity` | failed | passed |

## Failures on develop (`f6c7bc74`)

Each case's outcome in the three runs of `f6c7bc74` (42658824949, then the two
runs of 42750359330), and on #978's branch (42716817920).

| # | Case | f6c7bc74 ×3 | 037a8cfd | Class | Tracked |
|---|---|---|---|---|---|
| 1–3 | `negative` `test_representations::test_an_unsupported_accept_is_not_acceptable[rfm-engine\|model-registry\|analytics-kpis]` | ✗ ✗ ✗ | skipped | Test defect | #977 |
| 4–5 | `negative` `test_representations::test_bodies_that_must_be_refused_are[rfm-engine\|model-registry]` | ✗ ✗ ✗ | skipped | Test defect | #977 |
| 6–8 | `negative` `test_representations::test_pagination_outside_its_range_is_invalid_pagination[…3]` | ✗ ✗ ✗ | skipped | Test defect | #977 |
| 9–11 | `negative` `test_roles::test_a_role_outside_each_operation_is_forbidden_role[…3]` | ✗ ✗ ✗ | skipped | Test defect | #977 |
| 12–14 | `negative` `test_tokens::test_a_missing_or_forged_token_is_invalid_token[…3]` | ✗ ✗ ✗ | skipped | Test defect | #977 |
| 15 | `negative` `test_revocation::test_a_signed_out_session_is_refused_everywhere` | ✗ ✗ ✗ | ✓ | Test defect | #977 |
| 16 | `negative` `test_infrastructure::test_redis_state_stopped_fails_closed` | ✗ ✗ ✗ | ✓ | Test defect | #977 |
| 17 | `negative` `test_auth::test_sign_in_is_rate_limited_by_ip` | ✗ ✓ ✓ | ✓ | Test defect (timing) | #977 |
| 18 | `service-categories` `tests.test_rules::test_a_write_is_audited_with_the_callers_identity` | ✓ ✗ ✓ | ✓ | Test defect (id reuse) | its own issue |

### 1–16. `tests/negative` probes three services that are not built

**Message** (42750359330, first run, `report.md`):

```
tests.negative.test_representations::test_an_unsupported_accept_is_not_acceptable[rfm-engine]:
assert False + where False = check('rfm-engine', 'Accept: text/csv',
Reply(status=502, ... <h1>502 Bad Gateway</h1> ... nginx ...), 406, 'not_acceptable')
tests.negative.test_representations::test_bodies_that_must_be_refused_are[rfm-engine]:
ConnectionRefusedError: [Errno 111] Connection refused
```

`negative-evidence.md` of the same run:

```
| rfm-engine | Accept: text/csv | 406 not_acceptable | 502 | ✗ |
| model-registry | Accept: text/csv | 406 not_acceptable | 502 | ✗ |
| analytics-kpis | Accept: text/csv | 406 not_acceptable | 502 | ✗ |
```

**Why it is a test defect, not a gap in the product:**
- **What the suite calls.** `tests/negative/stack.py:202` reads every service
  in `contracts/registry.yaml`.
- **The registry.** It lists rfm-engine (5016), model-registry (5019) and
  analytics-kpis (5022) as `wave: third`.
- **The contracts come first, by design.** Their contracts were merged ahead of
  their code, as ADR-0035 requires.
- **Nothing implements them.** At `f6c7bc74` there is no `services/rfm-engine`,
  `services/model-registry` or `services/analytics-kpis`, and
  `compose.distributed.yaml` does not mention them (`grep -c` gives 0).
  `logs/stack/ps.txt` of the run shows the 15 implemented services and no
  others. So the gateway answers 502 for them, and a direct connection is
  refused.
- **Nothing promised them.** The third-wave services are scheduled work in
  [`../scope-delivery-3.md`](../scope-delivery-3.md), not delivered features
  that broke.
- **The cases that loop.** Cases 15 and 16 loop over every registered service,
  so the same three make them fail too, although the 15 deployed services held.
- **Why ADR-0073 does not help.** None of the three contracts carries
  `x-mosaiq-status: planned`: `grep -c` gives 0, against 13, 13 and 12
  operations. ADR-0073's marker is per operation and is read by
  `tests/conformance`, not by `tests/negative`. So nothing tells the negative
  suite to leave them out.

**Repeated.** The same 16 cases failed in all nine reports of the commits
before the fix (`ba003bb5`, `d87fcb36`, `c687ea2c` and `f6c7bc74`), with the same 502 or refused
connection.

**Corrected.** #978 (`037a8cfd`) skips a registered service with no
`services/<name>/compose.yaml`, and leaves it out of the looping cases. Its
dispatched run, 42716817920, reports `negative` 91 passed, 0 failed, 42
skipped, and **0 failed cases** in every suite.

### 17. The per-IP sign-in limit case depends on where the window starts

**Message** (42658824949):

```
tests.negative.test_auth::test_sign_in_is_rate_limited_by_ip:
AssertionError: The per-IP sign-in limit accepted 35 attempts. assert None is not None
```

**What auth does.** `services/auth/app/__init__.py:23–24` and `:90–96`:

```python
LOGIN_LIMIT = 30
LOGIN_WINDOW_SECONDS = 60
...
pipe.incr(login_key)
pipe.expire(login_key, LOGIN_WINDOW_SECONDS, nx=True)
...
if int(count) > LOGIN_LIMIT:
    raise _rate_limited(...)
```

This is a fixed 60-second window, opened by the first attempt from an IP.
ADR-0036 promises only that "a per-IP limit applies". It does not promise a
sliding window. The local gateway (`deploy/nginx/mosaiq.compose.conf`) has no
`limit_req` zone for auth's sign-in. Only the instance's `mosaiq.conf` defines
`mosaiq_auth_signin`.

**Why it is a test defect.**
- **What the test does.** `tests/negative/test_auth.py:121` makes 35 attempts
  and expects one of them to be refused.
- **Why that can fail.** Earlier cases in the suite sign in from the same IP.
  If their window closes partway through the loop, the count starts again, and
  35 attempts can stay under 30 in both windows.
- **What the product does.** It behaves as written and as the ADR describes.

**Repeated.** 1 failure in the nine runs before the fix:
- passed in both runs of `ba003bb5`, `d87fcb36` and `c687ea2c`;
- failed in 42658824949;
- passed in both runs of 42750359330, on the same commit.

**Corrected.** #978 retries until a refusal or two whole windows have passed.
It passed in 42716817920.

### 18. categories' audit test can draw an id an earlier test already used

**Message** (42750359330, first run,
`logs/service-categories/pytest.log`):

```
tests/test_rules.py:243
>       assert rows == [("INSERT", actor), ("UPDATE", actor), ("DELETE", actor)]
E         At index 1 diff: ('DELETE', None) != ('UPDATE', '11111111-1111-1111-1111-000000000001')
E         Left contains 2 more items, first extra item: ('UPDATE', '11111111-1111-1111-1111-000000000001')
```

**What the five rows are.** The test reads `audit_log` for one `entity_pk`.
The five rows are two lives of one category id:
- `INSERT` by the actor, then `DELETE` with no user: an earlier test created
  the category, and the autouse `cleanup` fixture deleted it as the owner
  connection, which sets no `user_id`;
- then this test's own `INSERT`, `UPDATE` and `DELETE`.

**Why the id was drawn again.**
- **What the allocator skips.** `new_id`
  (`services/categories/tests/conftest.py:204`) draws a random id in
  30000–32000. It skips ids that are still in `category` or still in
  `categories_events.outbox` (the fix for #600).
- **What it does not skip.** Ids that already have `audit_log` rows.
- **How an id loses its outbox rows.** `tests/test_redis.py:79` deletes the
  outbox rows that were not relayed. `test_redis.py` runs before
  `test_rules.py`, so an id used there leaves the outbox. The cleanup removes
  its category row, so the id can be drawn again, while its audit rows remain.

**Why it is a test defect.** The product audited every write correctly: the
three writes of this test carry the actor, and so does the earlier `INSERT`.
The ownerless `DELETE` is the fixture's own cleanup. The assertion read
another test's history.

**Repeated.**
- It passed in the second run of 42750359330, on the same commit.
- It passed in every other run listed above: 9 of the 10 runs.
- `comparison.md` lists it as the only case that differs between the two runs.

**Not corrected here.** F20-05 is timeboxed to classification. The fix is
small, and it is described in its issue.

## Failures on earlier commits, already fixed

These failed on commits of #953's branch before `f6c7bc74`, and no longer
fail.

| Case | Commits | Class | Evidence | Fixed by |
|---|---|---|---|---|
| `database` `tests.test_create_database_script::test_script_runs_twice_on_one_cluster`, `::test_creates_the_service_and_definer_roles` | `ba003bb5` ×2 | Test defect (runner) | `initdb … returned non-zero exit status 1`, under `/tmp/pytest-of-root/`: `initdb` refuses root | `d87fcb36`: the database job's steps run as user `runner` |
| `android` `checks::lint`, `unit-tests::run` | `ba003bb5` ×2 | Test defect (runner) | The toolbox held no `android-sdk-license` | `d87fcb36`: installing platform-tools writes it |
| `e2e` `test_demonstration::test_a_sale_lands_in_postgresql_and_reads_back_as_valid_xml`, `::test_a_signed_out_token_is_refused_by_another_service`, `::test_authentication_fails_closed_without_redis_state` | `ba003bb5` ×2 | Test defect (runner) | `429 == 201`, `{"code":"rate_limited","message":"Too many sign-in attempts; retry later."…}`. `tests/negative` ran first and used up auth's per-IP sign-in limit | `d87fcb36`: `e2e` runs first on the fresh stack, as in `nightly.yml`. `e2e` passed 3 of 3 in every run since |
| `negative` `test_rules::test_sales_refuses_a_reused_key_with_another_request` | `c687ea2c` ×2 | Test defect (shared state) | `Expected 201, got 409 stock_unavailable … "lines[0].quantity","code":"insufficient_stock"`. `tests/parity`'s last-unit case set a product's stock to 1 by hand and never gave it back | `9ee7e908` (#765) restores the units. Passed in all four runs of `f6c7bc74` and `037a8cfd` |
| `android` `checks::ktlint`, `checks::lint`, `unit-tests::run` | `c687ea2c`, second run only | **Environment fault** | `java.io.IOException: Server returned HTTP response code: 504 for URL: https://github.com/gradle/gradle-distributions/releases/download/v8.13.0/gradle-8.13-bin.zip`, and `Could not download kotlin-compiler-embeddable-2.1.20.jar` | See below |

**The environment fault, shown passing.**
- **The fault.** github.com and Maven Central did not serve the Gradle
  distribution and a Kotlin jar.
- **The same commit passing.** The first run of `c687ea2c`, in the same
  workflow run, passed `android`.
- **Passing after the fault.** `git diff c687ea2c f6c7bc74 -- android ops/verify`
  is empty, so `android` ran the same tree in every later run. It passed 96 of
  96 in 42658824949, in both runs of 42750359330 and in 42716817920.
- **What was corrected.** Nothing in the repository. The downloads succeeded
  once the hosts answered.

## Outside this run

Fifteen tests in `tests/test_guards.py` and `tests/test_service_matrix.py` fail
on a clean `develop` on macOS only. F20-03 runs on `linux/amd64` on every host,
and there the `web` suite passed 3584 cases with 0 failures, so they are not
failures of the consolidated run. They are left for their own issue.

## Issues

- **#977** (`type:bug`, opened by Marcelo): failures 1–17. Fix: PR #978.
- **Failure 18** gets its own `type:bug` issue, for a test defect.
- **No product defect was found**, so no product `type:bug` issue is needed.
