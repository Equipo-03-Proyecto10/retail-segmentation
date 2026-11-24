# F29-05 (#890): the acceptance tests of personalization and the A/B campaign

> **Status: held on the deployed instance on 2026-11-24 (03:54Z), release
> #1188 at `50bf7d5c`.** "Recomendaciones" 8 of 8 checks, "Campaña A/B" 21 of
> 21 and "Exposición y conversión" 11 of 11; the suites 2 of 2, at the commit
> whose tree is the release's. **One thing this run does not show:** an
> experiment planned, assigned, exposed and sold *in this run* (see [What is on
> the instance to run against](#what-is-on-the-instance-to-run-against)); that
> is the demonstration's own part.

This document is the runbook and the record of three of the instructor's sixteen
acceptance tests: AC-09 "Recomendaciones", AC-10 "Campaña A/B" and AC-11
"Exposición y conversión" ([`scope-delivery-3.md`](../scope-delivery-3.md) §10
and §11). It reuses what the stories behind them already proved and runs it
again, on the deployed system, as one set: F24-08 ([#831](f24-08-stock-recommendation-update.md))
for the recommendation that follows stock, F25-14 (#845) for the experiment
pipeline on known answers, and the two Android stories (#860, #861) for what the
phone records.

Governing decisions:
[ADR-0067](../adr/0067-a-recommendation-is-judged-at-a-reference-store-and-channel-and-its-explanation-is-recorded.md)
(F24-02's record: availability at the reference store, the recorded decision),
[ADR-0068](../adr/0068-an-experiment-is-planned-and-frozen-before-assignment-and-claims-an-effect-only-against-a-control.md)
(F25-01's record: the frame is frozen before anyone is assigned, and an
experiment is the only thing a customer is in at a time),
[ADR-0019](../adr/0019-experiment-measurement-separates-assignment-exposure-and-conversion.md),
[ADR-0026](../adr/0026-experiment-assignments-are-append-only-for-the-application-role.md),
[ADR-0027](../adr/0027-experiment-exposures-are-treatment-only-and-append-only.md),
[ADR-0075](../adr/0075-engagement-relays-interaction-facts-on-a-redis-stream-from-an-outbox-kept-on-the-event-document.md).

## The criteria, and where each is answered

| Criterion of #890 | Answered by | Section |
|---|---|---|
| "Recomendaciones": when a product has no stock at the store, then it is excluded | `f29_05 recommendations`: a live, read-only read at the store that offers none of the store's products with no stock, counts them as `out_of_stock`, and stores the same decision. F24-08's `cycle` on the instance (#831) is the proof that a product *counted to zero* leaves the answer after the propagation bound and comes back after a restock | [1](#1-recomendaciones) |
| "Campaña A/B": when clients are assigned, then no later change alters a group | `f29_05 ab` on an assigned experiment: the groups are read, **changes are tried**, and the groups are read again; plus F25-14's own cases, which change a segment and a consent after assignment on a throwaway database | [2](#2-campaña-ab) |
| "Exposición y conversión": when the run is read, assignment, exposure and sale are recorded separately | `f29_05 run` on an experiment with all three: three tables, three counts, each read back through the service as the database holds it; plus the phone's tests of what it records (#860, #861) | [3](#3-exposición-y-conversión) |

The collector is [`tests/acceptance/f29_05.py`](../../tests/acceptance/f29_05.py);
its pure parts are pinned in
[`tests/test_acceptance_evidence.py`](../../tests/test_acceptance_evidence.py).
No product code changes for this story, and no schema.

### What is on the instance to run against

The instance holds the 30 seeded experiments (`experiment` 1 to 30, four
assigned customers each, assignment rule V0, `data_origin` `SEEDED` or
`INJECTED`) and no experiment planned through the service. The seeded
assignments, exposures and sales are rows the seed inserted, so what they show
is the system's reading of them, not a campaign the team ran. That is the
reason for the choice below.

**This run plans and assigns no experiment.** An experiment cannot be edited or
deleted once planned (ADR-0068), it freezes a frame of every eligible customer
of the campaign's label, and a customer is in one experiment at a time among
overlapping periods. A plan made for an acceptance run would stay on the
instance, and would take its customers out of the frame of the demonstration
that follows. The A/B criterion is about what happens *after* assignment, so
an experiment that is already assigned answers it, and the collector tries to
change it. The instance planning, assigning, exposing and selling in one run is
the demonstration's own part
([`demo-distributed.md`](../demo-distributed.md) P6 to P8 and steps 14 to 16).

### What "a change" is here

`f29_05 ab` tries these on one assigned experiment, and each must be refused:

| Attempt | Through | Refused by |
|---|---|---|
| Assign the experiment again | `POST /v1/experiment-assignments` | the service: 409 `already_assigned` |
| Edit or delete the experiment | `PATCH`, `DELETE /v1/experiments/{id}` | the service: 405, there is no such operation |
| Edit or delete an assignment | `PATCH`, `DELETE /v1/experiment-assignments/{id}` | the service: 404, there is no such route |
| Move a customer to the other arm, or delete the assignment | `UPDATE`, `DELETE` as `retail_app` and as `svc_experiments` | PostgreSQL: no privilege, and the trigger `experiment_assignment_never_rewritten` for every role |
| The same, as the database owner | `UPDATE`, `DELETE` as `postgres` | the trigger |
| Put the customer in the other arm as well | `INSERT` as `svc_experiments` | `UNIQUE (experiment_id, customer_id)` (RN-23) |
| Rename an arm after its customers were assigned | `UPDATE experiment_group` as `postgres` | the trigger `experiment_group_definition_immutable` |

Every statement that reaches PostgreSQL runs inside a transaction that is
always rolled back, so even a statement that was wrongly accepted leaves
nothing behind, and the check for it fails. The collector also reads **what
changed since the assignment** (a customer's segment moved, a consent was
decided) and requires at least one, so the check cannot hold for want of a
change. The seeded groups are compared with the seed's own rule (assignee `k`
of experiment `n` is customer `1 + ((n-1)*4 + k) mod 30`, the first two in
control), written down in `f29_05.seeded_assignment` and checked against the
seed itself by a test.

What this does not show: that a *live* change through the services (a
withdrawn consent, a new segment run) leaves an arm alone. F25-14 does that on
a throwaway database, and the `suites` command re-runs it at the identified
commit. Doing it on the instance needs an experiment planned there.

## Who does what

| Step | Who | Where |
|---|---|---|
| 0. Preconditions, the version | Acceptance lead | workstation, GitHub |
| 1. `recommendations` | Instance operator (root), with the acceptance lead | the instance |
| 2. `ab` | Instance operator, with the acceptance lead | the instance |
| 3. `run` | Instance operator, with the acceptance lead | the instance |
| 4. `suites` | Acceptance lead (or anyone on the team) | a workstation, a clean PostgreSQL |

No step changes code. Steps 1 to 3 write no business data: the only writes are
the two accounts of step 0.3, and the refused attempts of step 2.

## 0. Before the run

1. **The identified version**: the commit every service is deployed at, the same
   one as F29-03 ([f29-03-transaction-path.md](f29-03-transaction-path.md)
   step 0).
2. **The tree on the instance** at that commit, with this branch's
   `tests/acceptance/f29_05.py`, and a virtualenv holding `pytest` and PyYAML
   (as for F24-08, [step 0.2](f24-08-stock-recommendation-update.md#0-before-the-run)).
3. **Two accounts**: an `ANALYST` to read and a `MARKETING` to try to assign
   again (the roles that may assign an experiment, ADR-0068). Create them as
   `@example.test` through users-service as the administrator, as F20-01
   step 1 does, and deactivate both at the end. Their credentials go into the
   shell's environment, read at a prompt; they are never arguments.
4. **A customer and a store for `recommendations`**: a customer with
   `PERSONALIZATION` granted, a segment, and a store whose answer has at least
   one `primary` item. Choose one whose current R score is **3 or higher**; the
   demonstration needs the customers with a low score untouched.
5. **An experiment for `ab` and `run`**: one that is `ASSIGNED`, has at least one
   exposure and one sale, and whose first assignee has a high R score (the
   attempts of step 2 name that customer; they change nothing, but name no one
   with a low score). Look for one first; it is read-only:

   ```sh
   runuser -u postgres -- psql -d retail -c \
     "SELECT e.experiment_id, e.data_origin,
             count(DISTINCT a.assignment_id) AS assigned,
             count(DISTINCT x.exposure_id) AS exposures,
             count(DISTINCT c.conversion_id) AS sales
        FROM experiment e
        JOIN experiment_assignment a USING (experiment_id)
        LEFT JOIN experiment_exposure x USING (assignment_id)
        LEFT JOIN experiment_conversion c USING (assignment_id)
       GROUP BY 1, 2 ORDER BY 1"
   ```
6. **F24-08's instance run** committed (`docs/evidence/f24-08/instance-*`),
   which `recommendations` reads.

## The procedure

On the instance, as root, from the tree of item 0.2:

```sh
cd <the tree> && . .venv/bin/activate
read -r MOSAIQ_ACCEPTANCE_EMAIL; read -rs MOSAIQ_ACCEPTANCE_PASSWORD
read -r MOSAIQ_MARKETING_EMAIL;  read -rs MOSAIQ_MARKETING_PASSWORD
export MOSAIQ_ACCEPTANCE_EMAIL MOSAIQ_ACCEPTANCE_PASSWORD \
  MOSAIQ_MARKETING_EMAIL MOSAIQ_MARKETING_PASSWORD
export MOSAIQ_BASE_URL=https://127.0.0.1 MOSAIQ_HOST=mosaiq.maxthecoder.online \
  MOSAIQ_INSECURE_TLS=1 MOSAIQ_DB_EXEC="runuser -u postgres --"
RUN=docs/evidence/f29-05/$(date -u +%Y%m%dT%H%MZ)
```

### 1. "Recomendaciones"

```sh
python -m tests.acceptance.f29_05 recommendations --out "$RUN/recommendations" \
  --customer <uuid> --store <id> --f24-08 docs/evidence/f24-08/<F24-08's instance run>
```

It holds when:
- the store has active products with no stock (otherwise the check proves
  nothing);
- the answer offers none of them, as a primary item or an alternative, and every
  offered item states at least one unit on hand;
- the decision counts them under `out_of_stock`, and the decision stored in
  schema `recommendations` is the answer's;
- F24-08's `cycle` and `exclusions` on the instance held every check, and the
  `cycle`'s "not offered after the bound" row is among them.

### 2. "Campaña A/B"

```sh
python -m tests.acceptance.f29_05 ab --out "$RUN/ab" --experiment <id> --seed-rule
```

It holds when the experiment is `ASSIGNED`; the service lists as many
assignments as it states, the same as the database holds; the groups are the
seed's; at least one segment moved or consent was decided since the assignment;
every attempt of [the table above](#what-a-change-is-here) is refused; and then
the service and the database give every customer the same arm and arm id as
before. Drop `--seed-rule` for an experiment the service planned: its
`assignment_key` lets anyone recompute the arms from the published salt.

### 3. "Exposición y conversión"

```sh
python -m tests.acceptance.f29_05 run --out "$RUN/run" --experiment <id>
```

It holds when the experiment has assignments, exposures and sales, each in its
own table; the service lists the assignments, the exposures each assignment
reports and the conversions as the database holds them; the result's two arms
count `assigned` and `exposed` apart and agree with the database; no control
customer was exposed (ADR-0027); every exposure follows its assignment and
every sale lies in its window; and the three do not imply one another: someone
was assigned and never exposed, and someone was exposed without buying or
bought without being exposed.

### 4. The suites

On a workstation, at the identified commit, with a throwaway PostgreSQL built by
the three scripts and Redis (as F29-04 step 1):

```sh
export TEST_DATABASE_URL=postgresql://postgres@127.0.0.1/retail
export MOSAIQ_TEST_OWNER_URL=$TEST_DATABASE_URL TEST_REDIS_URL=redis://127.0.0.1:6379/0
python -m tests.acceptance.f29_05 suites --out "$RUN/suites" --android
```

It runs `services/experimentation/tests/test_validation_pipeline.py` and
`test_validation_statistics.py` (F25-14, #845) with
`MOSAIQ_REQUIRE_POSTGRES=1`, and the Android unit tests
`ExplainedRecommendationsTest` and `PromotionsViewModelTest` (#860, #861)
through Gradle. A suite's check holds only when it exits 0, ran at least one
case, and failed, erred and skipped none. The service tests copy the
database they are given as a template, so no other connection may be open to
it.

### 5. Afterwards

Deactivate the two accounts of item 0.3. Commit `summary.md`, `exchanges.md` and
`rows.md` of each `$RUN/*`, the logs and JUnit files of `suites`, with the
filled record. Do not commit `record.json` (it holds every exchange in full).
No committed file holds a credential: `Recorder.write` refuses to write
anything shaped like a token.

---

## The record

### Where it ran

| Piece | Value |
|---|---|
| Identified version | `main` at `50bf7d5c` (release #1188), the tree of `develop` at `fdf0caed`. Contracts served: auth 1.1.0, experimentation 1.2.1, recommendations 2.0.4 (stock 1.3.0, products 1.4.0 as recorded by F24-08's run) |
| Date and time (UTC) | 2026-11-24, 03:54:13Z to 03:54:35Z (2026-11-23 evening in Mexico City) |
| Steps 1 to 3 | On the instance, as root, from a `git archive` of `50bf7d5c` with `tests/acceptance/f29_05.py` added. The collector's "checkout commit" row reads `(unavailable)` because the archive has no `.git` |
| Step 4 | On the author's workstation (WSL, PostgreSQL 18, Redis 8, JDK and the Android SDK), at `develop` `fdf0caed`, on a throwaway copy of the three SQL scripts' database. Its summary names `fdf0caed`, whose tree is the release's |
| Accounts | `s1-f2905-analyst@example.test` (`ANALYST`) and `s1-f2905-marketing@example.test` (`MARKETING`), created through users-service as the administrator and deactivated at the end |
| Customer, store, experiment | Customer `00000000-0000-0000-0000-000000000003` (R score 4), store 3; experiment 16, seeded (rule V0), campaign 16, four assignees |
| Left as found | Nothing was written that persists, apart from the two accounts (now inactive), the sessions of the three sign-ins each, and the decisions every recommendation read records. `check-services.sh` and `check-redis.sh` printed nothing afterwards |

### The results

| Criterion | Collector | Checks | Accepted |
|---|---|---|---|
| "Recomendaciones" | [`recommendations`](f29-05/instance-20261124T0355Z/recommendations/summary.md) | 8 of 8 | Yes |
| "Campaña A/B" | [`ab`](f29-05/instance-20261124T0355Z/ab/summary.md) | 21 of 21 | Yes, with the limit stated below |
| "Exposición y conversión" | [`run`](f29-05/instance-20261124T0355Z/run/summary.md) | 11 of 11 | Yes, with the limit stated below |
| The suites (F25-14, #860, #861) | [`suites`](f29-05/local-20261124T0355Z/suites/summary.md) | 2 of 2 | Yes |

### "Recomendaciones": what the read said

- Store 3 has 35 active products with no stock. The answer for the customer
  offers none of them: four `primary` items (products 1, 2, 3 and 5), each with
  at least 10 units on hand.
- Its decision counts `out_of_stock` 21, `already_bought` 14 and
  `PERSONALIZATION_WITHDRAWN` 1, and the decision stored in schema
  `recommendations` is the same.
- F24-08's run on the instance ([#831](f24-08-stock-recommendation-update.md),
  `docs/evidence/f24-08/instance-20261124T0333Z`) held 17 of 17 and 9 of 9:
  product 2 counted to zero at store 3 was no longer offered 164 ms after the
  movement, and offered again 73 ms after the receipt (bound: 5000 ms).

### "Campaña A/B": what was tried, and what it showed

Experiment 16 has four assignees, two in `CONTROL` (group 31) and two in
`TREATMENT` (group 32), assigned between 8 and 18 July 2026. The attempts
targeted the first assignment, customer `…0001` (R score 3). Since the
assignment, the segment of the assigned customers has moved 124 times and 13
consent decisions were recorded, and the groups are still the seed's.

| Attempt | Answer |
|---|---|
| Assign again | 409 `already_assigned` |
| `PATCH`, `DELETE` the experiment | 405, 405 |
| `PATCH`, `DELETE` an assignment | 404, 404 (the contract serves no such route; the collector accepts 404 or 405) |
| `UPDATE`, `DELETE` as `retail_app` and as `svc_experiments` | `permission denied for table experiment_assignment`, four times |
| `UPDATE`, `DELETE` as the owner `postgres` | the trigger: `An assignment is never rewritten` |
| `INSERT` into the other arm as `svc_experiments` | unique violation on `(experiment_id, customer_id)` |
| Rename an arm as the owner | the trigger: `Experiment group 31 is fixed after assignment` |

After them, the service lists the same four customers in the same arms and arm
ids, and the database holds the same four rows. The refusal is layered: on the
instance the application roles are stopped by a missing privilege, and the owner
is stopped by the trigger. On the dry run's database `retail_app` was
temporarily granted `UPDATE` to see which layer answered next, and the trigger
refused it (`An assignment is never rewritten`); that grant was revoked and is
not on the instance.

**The limit.** The instance has no experiment planned through the service, so
the groups compared are the seed's. What a *new* plan does to a frame is shown
by F25-14 on a throwaway database and by the demonstration. A live change of
segment or consent through the services was not tried against a frozen
experiment on the instance.

### "Exposición y conversión": what the run showed

Experiment 16 holds 4 assignments, 2 exposures (both in the treatment arm) and
14 sales counted as conversions by 4 assigned customers. The service reads the
three back as the database holds them; the result's arms count `assigned` 2 and
`exposed` 0 (control) and `assigned` 2 and `exposed` 2 (treatment); no one in
control was exposed; every exposure follows its assignment and every sale lies
inside the 28-day window. Two assigned customers were never exposed, and two
customers bought with no exposure at all: an assignment is not an exposure, and
an exposure is not a sale.

**The limit.** The exposures of the seeded experiments were inserted by the seed
(`source_event_id` is empty, `exposures from engagement events: 0`), not
delivered by engagement. A real `CAMPAIGN_EXPOSURE` from the phone, through
`stream:engagement.v1.interaction_recorded` into the projection, is covered by
the stream's own tests and #861's Android tests (below), and happens live in
the demonstration (step 14 of [`demo-distributed.md`](../demo-distributed.md)).

### The suites (step 4)

| Suite | Result |
|---|---|
| `services/experimentation/tests/test_validation_pipeline.py`, `test_validation_statistics.py` (F25-14, #845) | 14 passed, no skip |
| Android `ExplainedRecommendationsTest`, `PromotionsViewModelTest` (#860, #861) | 17 passed, no skip |

## Local dry run (not the acceptance)

> **Local dry run, not the acceptance.** It ran on the author's workstation
> before the instance, to shake out the collector.

Three services as real processes on a throwaway database from the three
scripts: auth, experimentation and recommendations, behind a small path-prefix
proxy standing in for NGINX (there is no Docker here), signed in as the
seed's `user2` (`ANALYST`) and `user4` (`MARKETING`). Redis users for the three
services were permissive, not the services' ACL lines.
[`f29-05/local-20261124T0355Z`](f29-05/local-20261124T0355Z/) holds
`recommendations` (8 of 8), `ab` (21 of 21), `run` (11 of 11) and `suites`
(2 of 2).

What the dry run found, and changed:
- The first `recommendations` run failed its own check: it compared two
  dictionaries by their printed form, and the stored decision lists its
  exclusions in the database's order. It now compares them as dictionaries.
- `ab` first expected only `permission denied`. With `UPDATE` granted to
  `retail_app` on purpose, the trigger answered instead, so a refusal may now say
  either, and the owner's two attempts were added.
- The service tests copy the database they are given as a template, so they
  failed while the dry run's services held it open; the `suites` step says so.

## Not run

- **An experiment planned, assigned and measured in this run.** See above.
- **A live exposure from the phone**, and a sale after it, on the instance:
  the demonstration's steps 14 and 15.
- **`suites` on the instance.** They are properties of the commit, not of the
  deployment.
