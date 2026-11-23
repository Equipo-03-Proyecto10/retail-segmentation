# F29-04 (#889): the acceptance tests of the segmentation path, on one version

> **Status: not yet run on the identified version.** This document is the runbook
> and the template of five of the instructor's sixteen acceptance tests (AC-04
> "RFM", AC-05 "Migración", AC-06 "Incremental", AC-07 "K-means", AC-08 "Drift";
> [`scope-delivery-3.md`](../scope-delivery-3.md) §10 and §11). Nothing blocks
> them: #782, #808, #816, #822, #886 and #887 are closed. Every `‹fill: …›` below
> is filled during the run. The only results recorded so far are the
> [local dry run](#local-dry-run-not-the-acceptance), which is **not the
> acceptance**.

Governing decisions: F22-01's record
([ADR-0063](../adr/0063-segments-change-on-events-and-on-a-schedule-scored-against-the-published-version.md)),
F23-07's record
([ADR-0065](../adr/0065-drift-is-claimed-in-three-separate-kinds-and-concept-drift-only-from-a-predictive-outcome.md)),
[RN-58](../business-rules.md) (a K-means run is fixed by its seed and configuration).

## What "on one version" means here

Each of the five was already validated by the story that built it, each on its own
commit and its own database. This run accepts all five **together, on one commit,
from a clean database** built by the three SQL scripts, in one evidence
directory that names the commit. That is the section 10 condition: "one
identified version ... from a clean environment".

Three of the five, RFM, K-means and Drift, are properties of the code and of
fixed inputs: the hand-worked fixture, the registered seed and configuration,
and the planted shifts. The deployed system's own data cannot plant them, and
planting them there would put test rows in production. They are accepted at the
commit that the instance runs. The other two, Incremental and Migración, are
also behaviour of the live chain from a sale to a segment. They are checked a
second time on the instance, after F29-03's sale: the same sale proves "Venta
Android" and then flows through the segmentation path.

## The criteria, and where each is answered

| Criterion of #889 | Answered by | Section |
|---|---|---|
| "RFM": the automatic calculation matches the known manual values | F29-01's fixture, 14 customers worked out on paper, compared with the web (`tests/test_rfm_hand_computed.py`) and with rfm-engine (`test_hand_computed.py`) | [1](#1-the-five-at-the-identified-commit) |
| "Migración": a provoked change of segment keeps the previous assignment | rfm-engine's `test_assignment.py` (a change closes the previous assignment and opens a new one, F22-15) and segments' `migration_validation` and `assignment_cause` (six customers moved by each cause, the matrix compared with a table worked out on paper, F29-02); on the instance, the F29-03 customer's migrations | [1](#1-the-five-at-the-identified-commit), [2](#2-incremental-and-migración-on-the-instance) |
| "Incremental": after a recorded sale, the incremental update equals the full recalculation | rfm-engine's `test_verification.py` (F22-08: a sale, the profile before and after, a full recomputation at the same instant); on the instance, a verification over every profile after F29-03's sale | [1](#1-the-five-at-the-identified-commit), [2](#2-incremental-and-migración-on-the-instance) |
| "K-means": reproduced with the registered seed and configuration, the groups are the same | F23-04's registered result (k = 4, seed 11, 100 iterations, tolerance 0.0001, 180 days) fitted three times from scratch, and its partition digest compared with the registered one; model-registry's `test_training.py`, whose golden fixtures pin the registry's fit to the web's (F22-12) | [1](#1-the-five-at-the-identified-commit) |
| "Drift": a controlled change is detected and the alert is explained | F23-10's study through the production monitor on its own throwaway database: every planted spend and frequency shift that alerts is detected in every pair, and every alert names the planted variable and direction; no-shift windows stay within 1%; the figures' digest equals F23-10's. model-registry's `test_monitor.py` plants a shift through `run_once` and reads the alert back | [1](#1-the-five-at-the-identified-commit) |
| The evidence names the version and links what supports each check | `f29_04` writes `summary.md` (the commit, then every check with its log or exchange), one log and JUnit file per suite, `kmeans-reproduction.txt` and `drift-figures.json` | [The record](#the-record) |

## Who does what

| Step | Who | Where |
|---|---|---|
| 0. Preconditions, the version | Acceptance lead | workstation, GitHub |
| 1. The five at the identified commit | Acceptance lead (or anyone on the team) | a workstation or a CI runner, a clean PostgreSQL |
| 2. Incremental and Migración on the instance | Acceptance lead, with an ADMIN account | workstation, the public edge |
| 3. Afterwards | Acceptance lead | GitHub |

No step changes code.

## 0. Before the run

1. **The identified version** is the same commit as F29-03 and F29-06: one commit
   of `develop` (or a tag) that the instance runs, with the F20-03 runner passed
   on it ([f29-03-transaction-path.md](f29-03-transaction-path.md) step 0).
2. **F29-03's sale is recorded** before step 2. Note its customer's uuid and a
   UTC time before the sale.
3. **The tools** for step 1:
   - Python 3.12 with `web/requirements-dev.txt`, and `uv`.
   - The Rust toolchain, with `libclang-dev`, `libxml2-dev` and `pkg-config`, as
     CI installs them.
   - PostgreSQL. The instance runs 18.
   - **Redis 7.2 or later.** The services' ACL files grant `WAITAOF`, which
     Redis 7.0 rejects, so with 7.0 every test that sets up a service's Redis
     user errors.

   The run takes about 15 minutes, 10 of them the drift study.

## The procedure

### 1. The five at the identified commit

From a clean checkout at the identified commit, build two throwaway databases with
the three scripts. One is for the suites. The other is for the drift study, which
writes its reports and alerts to it. Then start Redis:

```sh
git checkout <identified commit>
python -m venv .venv && . .venv/bin/activate && pip install -r web/requirements-dev.txt
export PGHOST=127.0.0.1 PGUSER=postgres
psql -v ON_ERROR_STOP=1 -f sql/00_create_database.sql
psql -v ON_ERROR_STOP=1 -d retail -f sql/01_schema.sql
psql -v ON_ERROR_STOP=1 -d retail -f sql/02_seed_30_per_table.sql
psql -d postgres -c "CREATE DATABASE retail_drift TEMPLATE retail"
redis-server --port 6379 --daemonize yes

export TEST_DATABASE_URL=postgresql://postgres@127.0.0.1/retail
export MOSAIQ_TEST_OWNER_URL=$TEST_DATABASE_URL
export TEST_REDIS_URL=redis://127.0.0.1:6379/0
export DRIFT_STUDY_DATABASE_URL=postgresql://postgres@127.0.0.1/retail_drift
RUN=docs/evidence/f29-04/$(date -u +%Y%m%dT%H%MZ)
python -m tests.acceptance.f29_04 suites --out "$RUN/suites"
```

It runs each suite with `MOSAIQ_REQUIRE_POSTGRES=1` and `MOSAIQ_REQUIRE_REDIS=1`,
so a missing database fails instead of skipping. A suite's check holds only
when the suite exits 0, ran at least one case, and failed, erred and skipped
none. The command exits 0 only when every check holds.

### 2. Incremental and Migración on the instance

After F29-03's sale, from a workstation:

```sh
export MOSAIQ_BASE_URL=https://mosaiq.maxthecoder.online
read -r  -p 'ADMIN email: ' MOSAIQ_ACCEPTANCE_EMAIL
read -rs -p 'Password: '    MOSAIQ_ACCEPTANCE_PASSWORD; echo
export MOSAIQ_ACCEPTANCE_EMAIL MOSAIQ_ACCEPTANCE_PASSWORD
python -m tests.acceptance.f29_04 instance --out "$RUN/instance" \
  --customer <F29-03's customer uuid> --since <UTC before F29-03's sale>
```

It reads the served contract versions of rfm-engine, segments and model-registry.
Then it signs in as ADMIN and starts a verification recalculation
(`POST /api/rfm-engine/v1/rfm-recalculations`). The verification recomputes every
customer from the net purchases at one instant and compares each with the
profile held incrementally. It writes no profile and changes no segment
(ADR-0063). The command polls until the verification ends and expects
`COMPLETED`, `EQUIVALENT` and 0 different. If any customer differs, it records
which ones. It then reads the F29-03 customer's migrations since `--since`, and
expects every change to name the assignment before it. At the end it signs out.

Only one verification runs at a time, so a 409 `recalculation_in_progress` means
the weekly one is running: wait, and run the command again. If the sale did not
change the customer's segment, the migrations check has nothing to compare. The
record then says so, and "Migración" rests on step 1.

### 3. Afterwards

Commit `summary.md`, the logs, the JUnit files, `kmeans-reproduction.txt` and
`drift-figures.json` of `$RUN/suites`, and `summary.md` and `exchanges.md` of
`$RUN/instance`, with the filled record. `record.json` stays outside the
repository. No file holds a credential: `Recorder.write` refuses to write
anything shaped like a token.

---

## The record

*Everything below is filled during the run.*

### Where it ran

| Piece | Value |
|---|---|
| Identified version | ‹fill: commit or tag›, F20-03 report ‹fill: link› |
| Date and time (UTC) | ‹fill: start – end› |
| Step 1 machine | ‹fill: OS, PostgreSQL version, Python, uv, rustc› |
| Instance | `https://mosaiq.maxthecoder.online/`, versions ‹fill: from `$RUN/instance/summary.md`› |
| F29-03's sale | ‹fill: sale id, customer, time› |
| Who ran each step | ‹fill› |

### The results

| Criterion | Step 1 (commit) | Step 2 (instance) | Accepted |
|---|---|---|---|
| RFM | ‹fill› | — | ‹fill› |
| Migración | ‹fill› | ‹fill› | ‹fill› |
| Incremental | ‹fill› | ‹fill› | ‹fill› |
| K-means | ‹fill› | — | ‹fill› |
| Drift | ‹fill› | — | ‹fill› |

---

## Local dry run (not the acceptance)

> **Local dry run, not the acceptance.** It ran on a throwaway local database,
> not on the identified version's machine, and step 2 did not run (no instance
> access). It shows that the collector runs every suite, refuses a skip, and
> compares the two registered results. It also shows what a wrong environment
> looks like.

### Where it ran

| Piece | Here |
|---|---|
| Date (UTC) | 2026-11-23, 04:43 – 04:55 |
| Tree | `develop` at `0a39131b` with this pull request's collector committed on top (`6e435419`) |
| Databases | PostgreSQL 16 (CI's version; the instance runs 18). Two databases from the three scripts, the second a copy of the first for the drift study |
| Redis | 7.0.15, **too old**: see below |

### What it gave

`python -m tests.acceptance.f29_04 suites` ran 13 checks: 10 held, 3 did not.

| Criterion | Result here |
|---|---|
| RFM | Held. Web 4 of 4 and rfm-engine 2 of 2 against the 14 hand-worked customers, none skipped |
| K-means | Held. The registered configuration was fitted 3 times with no difference, and the partition digest `040e8e1a…26e0` equals F23-04's. model-registry's golden tests passed 35 of 35 |
| Drift | Held. The study ran through the production monitor in 593 s, and its figures' digest `cfb2ddcf…0f25` equals F23-10's. Spend ×2 and frequency −50% were each detected in 50 of 50 pairs, and every alert explained the planted change. With no shift, 0 alerts in 200 pairs, 95% upper bound 0.75%. The monitor tests passed 40 of 40 |
| Migración | Partly held. segments' `migration_validation` passed 3 of 3, including `a_change_keeps_the_previous_assignment_and_names_its_cause` and the matrix against the table worked out on paper. The cases that set up a Redis user errored (see below): 5 in rfm-engine's `test_assignment.py` and 2 in segments' `assignment_cause` |
| Incremental | Not shown. 17 of rfm-engine's `test_verification.py` cases passed, but the 18 that set up a Redis user errored, among them `test_the_acceptance_run_shows_the_customer_before_after_and_the_reference` |

**Why three did not hold.** Every one of the 25 errors is the same: `ACL SETUSER`
refused `+waitaof`, because this machine's Redis is 7.0 and the services' ACL
files name a command that Redis 7.2 introduced. None is a failure of a
criterion. The collector counted them as failures and not as passes, as it
must. CI and the instance run a Redis that accepts the ACL, and step 0.3 now
names the version. The first attempt at the Rust suite also failed to build
without `libxml2-dev`. Step 0.3 now names it too, and the collector stages the
contracts for the Rust suite as CI does.

### Not run here

- Step 2, on the instance: it needs F29-03's sale and an ADMIN account there.
- Incremental and the Redis-dependent Migración cases, on a Redis that accepts the
  ACL.
