# One commit, every suite, one command

F20-03 ([#761](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/761)).
Today `ci.yml`, `services.yml` and `nightly.yml` each set up their own
environment, so no single result covers one version. This command does. It
rebuilds everything from one commit, in an empty environment, and runs every
suite against it. The result is one report that names that commit.

```bash
bash ops/verify/run.sh                 # HEAD
bash ops/verify/run.sh <commit>        # any commit, branch or tag
bash ops/verify/run.sh --out <dir> <commit>
bash ops/verify/run.sh --compare <dir-a> <dir-b>
```

The exit status is non-zero when any case failed. A suite that could not run
counts as failed, never as skipped.

On GitHub Actions, dispatch **Verify one commit** (`.github/workflows/verify.yml`)
with the commit to verify. It runs the same command and uploads the report as
the artifact `verify-<sha>`. With `twice`, it also runs a second time and
compares the two runs.

## What the host needs

Docker and this git checkout, nothing else: no Python, Node, Rust, JDK or
PostgreSQL. `run.sh` does three things on the host:
- bundles the repository with `git bundle`;
- builds the toolbox image, [`Dockerfile`](Dockerfile);
- starts that image privileged.

The toolbox runs its own Docker daemon, and every container of the run lives on
that daemon: the stack, each throwaway database and every image built. Nothing
reaches the host's containers, networks, volumes or ports. A run therefore
never collides with a stack already running on the machine, and it never
reuses a cache. The daemon's storage is discarded with the container.

The toolbox is `linux/amd64` on every host, like the GitHub runners. The
contract tools are amd64 binaries, `xmllint` is pinned to Ubuntu 24.04's
libxml, and the Android build tools are x86_64 only. On an Apple Silicon Mac it
runs under emulation: it uses the same tools, but runs slower.

A full run needs about 8 GB of memory for Docker and 30 GB of disk. On a
smaller machine, `VERIFY_BUILD_SERIAL=1` builds the service images one at a
time.

## What it runs, in order

Each step is the step a workflow already runs (named in
[`suites.sh`](suites.sh)), and leaves JUnit under `results/<suite>/`.

| Suite | What | Mirrors |
|---|---|---|
| `toolbox` | the commit's Python requirements and `contracts/install-tools.sh` | `ci.yml` |
| `images` | `stage-contracts.sh --all`, `auth-keys.sh`, and `docker compose -f compose.distributed.yaml build`: every image at this commit | `nightly.yml` |
| `contracts` | `contracts/check.sh`, `check_streams.sh`, `guards.sh`, the memory gate | `ci.yml` `contracts`, `guards` |
| `web` | `black`, `ruff`, the repository `pytest` without the four directories below | `ci.yml` `quality` |
| `database` | every `run` step of `ci.yml`'s `database` job, read from the workflow: the three scripts from empty, the seed's compliance queries, 30 rows per table, the boundary sections, the grants, and the web's database-backed tests | `ci.yml` `database` |
| `golden` | `tests/golden`, then the capture and drift comparison | `golden-drift.yml` |
| `service-<name>` | per service, on its own fresh PostgreSQL, Redis and MongoDB: VERSION, then `uv sync`, `black`, `ruff`, `pytest`, or `fmt`, `clippy`, `cargo test` | `services.yml` `service` |
| `conformance-<name>` | `tests/conformance/run.sh` against the image the stack runs | `services.yml` `service` |
| `kit-py`, `kit-rs` | each kit's suite, its example image, the example's conformance, and the proof that the suite is not vacuous | `ci.yml` `kit-*`, `services.yml` `kits` |
| `desktop` | `npm ci`, lint, Prettier, `tsc`, the contract bundle, Vitest, the ADR-0041 checks | `desktop.yml` `check` |
| `android` | `ktlintCheck`, `lint`, `testDebugUnitTest`, the ADR-0041 checks | `android.yml` `check` |
| `stack` | the local stack up from those images, with PostgreSQL built by the three scripts, both Redis instances on the instance's configuration and ACL, MongoDB and the gateway, then every boundary section re-applied | `nightly.yml` |
| `e2e` | `tests/e2e`, the demonstration chain | `nightly.yml` |
| `negative` | `tests/negative` against the gateway, as its README's "The local stack" | — |

The conformance stack and the local stack would both join network `mosaiq`
under the same names, so the local stack comes up only after the conformance
runs. `tests/e2e` runs on the fresh stack, as in `nightly.yml`: after
`tests/negative`, auth's sign-in rate limit would refuse its sign-ins.
`tests/e2e` stops `redis-state`, so every service is restarted before
`tests/negative`.

The database job's steps run as the unprivileged user `runner`, as on a GitHub
runner, because `initdb` refuses root.

Not run: the desktop's Playwright window tests, packaging, and the Android
instrumented tests. All three need a display or an emulator, and they keep
their own workflows.

## The report

A run writes one directory, by default `test-results/verify/<sha>-<UTC time>/`:

| File | What |
|---|---|
| `report.md` | the report below, for a person |
| `report.json` | the same data, for `--compare` and for tools |
| `results/<suite>/*.xml` | every step's JUnit, as the report read it |
| `logs/<suite>/<step>.log` | every step's output; `logs/stack/compose.log` holds the stack's logs |
| `negative-evidence.md` | what `tests/negative` records for this run |
| `meta.json`, `tools.txt`, `images.txt` | the raw inputs of the header |

`report.md` holds, in order:
1. **The result** (`passed` or `failed`) and the number of failed cases.
2. **The commit**: its SHA and subject, the start and end times, the commit of
   `ops/verify` that ran it, and the toolbox image's ID.
3. **Suites**: one row per suite, with its passed, failed and skipped counts.
4. **Failures**: each failed case, `<classname>::<name>`, with its message.
5. **Skips and their reasons**: each skipped case, with the reason its runner
   gave. A test marked `#[ignore]` without a reason is listed as such.
6. **Images**: every image on the toolbox's daemon. A pulled image shows its
   registry digest; an image built from the commit shows its image ID.
7. **Tool versions**: Python, pytest, black, ruff, uv, Node, npm, mongosh, the
   JDK, Gradle, rustup and every Rust toolchain installed, Docker, Compose,
   psql, initdb, jq, yq, oasdiff, openapi-spec-validator, xmllint, and the
   kernel.

`report.json` has these keys: `commit`, `subject`, `started`, `finished`,
`runner_commit`, `toolbox`, `images`, `tools`, `result`, and `summary` (suite
→ `{passed, failed, skipped}`). Its `cases` key maps each suite to a list of
`{id, outcome, message}`.

**A case that could not run.** A command that is not a test runner (a lint, a
build, a `uv sync`) is recorded as one case, passed when it exits 0. Two more
rules apply to test runners:
- When a runner exits non-zero and wrote no JUnit file, the step gets the
  failed case `could not run`.
- When a runner exits non-zero but its JUnit file shows no failure, the step
  gets the failed case `exit status`.

The log's last lines are kept in the failure either way.

## Two runs of one commit

```bash
bash ops/verify/run.sh --out test-results/verify/a <sha>
bash ops/verify/run.sh --out test-results/verify/b <sha>
bash ops/verify/run.sh --compare test-results/verify/a test-results/verify/b
```

`--compare` writes `comparison.md` into the second directory. It lists every
case whose outcome differs between the two runs, including a case present in
only one of them, and exits 1 if there is any. Those cases are flaky. Each one
is raised as a defect: a GitHub issue that links both reports. A UUID in a
case's id is read as `<uuid>`, because a test parametrized with a random UUID
gets a new id each run.
