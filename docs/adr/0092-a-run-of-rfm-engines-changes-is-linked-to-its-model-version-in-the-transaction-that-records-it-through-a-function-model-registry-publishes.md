# ADR-0092 — A run of rfm-engine's changes is linked to its model version in the transaction that records it, through a function model-registry publishes

**Status:** Proposed
**Owner:** Maximiliano
**Issue:** #1208
**Supersedes:** —
**Superseded by:** —

---

## Context

[ADR-0064](0064-a-model-version-is-immutable-and-a-change-of-version-is-never-reported-as-behaviour.md)
gives every segmentation run a version in `models.run_version`, model-registry's table,
and publishes it as `api_models.run_version_v1`. segments-service reads a run's version
only from that view, and reports a migration as methodological when either side's version
is unknown.

[ADR-0087](0087-an-incremental-change-is-applied-to-the-segment-history-in-the-profile-transaction-under-the-published-version.md)
lets `api_segments.apply_assignment_v1` record a run for each batch of rfm-engine's
changes, with the version it was scored under in the run's parameters, and says those runs
"have no `models.run_version` link until model-registry links runs". Nothing links them.
On the instance, both `INCREMENTAL` runs were unlinked and all 31 `FULL` runs were linked,
so every migration caused by a sale came out `methodological: true` with
`new_model_version_id: null`, and was never counted as behaviour (#1208).

The run is recorded inside the segments boundary's function, owned by `definer_segments`.
The link belongs to model-registry: its table, its immutability trigger, its grants.
AGENTS.md forbids a boundary to touch another's table and services to call each other over
HTTP. The issue offers two remedies: segments falls back to the version in the run's
parameters, or the link is written with the run.

## Decision

model-registry publishes `api_models.link_run_version_v1(run_id, version_id)`, a SECURITY
DEFINER function owned by `definer_models` that inserts the link, is a no-op for the same
link, and refuses an unknown version (`unknown_model_version`) or a second version for a
run (`run_version_conflict`). `api_segments.apply_assignment_v1` calls it right after it
inserts its batch's run, in the same transaction and sub-transaction, so a run of
rfm-engine's changes (`INCREMENTAL`, or the `FULL` run of a `VERSION_CHANGE`) and its link
commit together or not at all. EXECUTE is granted to `definer_segments` alone: rfm-engine
neither calls it nor needs a grant, and its code does not change. The version stays in the
run's parameters too, as ADR-0087 records it. model-registry's section of
`sql/01_schema.sql` backfills the runs recorded before this, with an
`INSERT ... SELECT ... ON CONFLICT DO NOTHING` over the runs that carry a `batch_id` and a
`model_version_id` naming a version that exists, so the operator's reapply of the section
links them and a second reapply adds nothing. Segments keeps reading one source,
`api_models.run_version_v1`, unchanged.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| segments reads `COALESCE(rv.version_id, (r.parameters ->> 'model_version_id')::bigint)` (the issue's option 1) | Two sources for one fact, which can disagree, and every reader of a run's version (segments' four reads, analytics-kpis, recommendations, the outcome test) must repeat the fallback or be wrong. The link is the fact ADR-0064 names |
| rfm-engine calls the function after `apply_assignment_v1`, with a grant to `svc_rfm` | rfm-engine does not know the run: `apply_assignment_v1` returns `opened`, `changed` or `unchanged`, and returning the run id would change its signature and need a `v2`. The function that records the run is the one place that knows it, in the transaction that records it |
| model-registry links the run from `stream:segments.v1.run_recorded` | It is not built, and an event links after commit: the run would be unlinked for a while, and every migration read in that while would be methodological. The registry would also have to read the run's version from segments' table, or from a view that publishes it, to link a version it did not choose |
| `definer_segments` inserts into `models.run_version` with a grant | A boundary role would write another boundary's table, which AGENTS.md forbids. The function keeps the registry's rules (one link per run, a known version, never changed) in the registry |
| Link only a version that exists, and skip an unknown one silently | A run would claim a version it is not linked to, and the defect of #1208 would return unnoticed. rfm-engine reads the version from `api_models.published_version_v1`, and versions are never deleted, so a refusal can only mean a defect |
| Require the version to be the published one | A publication can land between rfm-engine's read of the published version and the call, and the change was scored under the version it read. Existence is what the link needs |

## Consequences

**What this makes easy.** A sale-driven migration whose two assignments were written under
one version has both version ids and is behaviour. Every reader of a run's version keeps
reading `api_models.run_version_v1` alone, and `version_at`, which picks the latest linked
run, now also sees incremental runs, all under the version that was published. The live
data is repaired by the reapply the operator already does before a deploy.

**What this makes hard.** `apply_assignment_v1` now refuses a call that would create a run
under a version the registry does not hold. No caller sends one, but it is a new error row
in its contract, recorded there. A run's `run_id` is linked by value, so a run deleted by
maintenance leaves its link behind, as ADR-0064 already accepts for web runs. The
operator applies model-registry's section before segments' (segments' function calls a
function the other section creates), and applies model-registry's section once more after
segments' to link any run recorded between the two; the backfill makes that safe. The backfill reads `public.segmentation_run` as the script's owner, once, the way the scripts already read across sections; no boundary role gains a privilege on that table. The
coalesce in model-registry's outcome test (ADR-0089) stays for runs not yet backfilled.

**What must now be true elsewhere.**
- `contracts/db/api_models.link_run_version.v1.md` describes the function, and
  `contracts/db/grants.csv` lists its grants to `definer_segments` and the privileges of
  `definer_models`.
- `contracts/db/api_segments.apply_assignment.v1.md` says the run is linked and lists
  `unknown_model_version`.
- `contracts/db/api_models.run_version.v1.md` says who writes the links it publishes.
- This complements ADR-0087 and does not supersede it: ADR-0087's decision (the version in
  the run's parameters) stands, and its expectation that incremental runs would be linked
  later is what this record fulfils, by the segments function rather than by model-registry's
  consumer.

## Compliance

- `services/rfm-engine/tests/test_assignment.py` checks that an incremental run under the
  published version is linked to it, and that a rolled-back change leaves no link.
- `services/segments/tests/migration_validation.rs` checks that a sale between two
  incremental runs under one version has both version ids and `methodological: false`.
- `tests/test_run_version_view.py` runs the backfill twice and checks it links a pending
  run once, skips an unknown version and a web run, and that only `definer_segments` (and
  the owner) may execute the function.
- `contracts/db/check_grants.sh` checks the grants of `definer_models` and `definer_segments`.
