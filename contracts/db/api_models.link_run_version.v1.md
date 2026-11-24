# `api_models.link_run_version_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_models.link_run_version_v1` |
| Kind | PostgreSQL function |
| Owning boundary | model-registry |
| Producer | model-registry |
| Consumer | segments (`definer_segments`) |
| Signature | `api_models.link_run_version_v1(bigint, bigint)` |
| regprocedure | `api_models.link_run_version_v1(bigint,bigint)` |
| Result | `void` |

It records that a segmentation run was scored under a model version, in
`models.run_version`, the table [`api_models.run_version_v1`](api_models.run_version.v1.md)
publishes. It is the one write of that link from outside model-registry
(ADR-0092, #1208). `api_segments.apply_assignment_v1` calls it in the transaction
that records a run of rfm-engine's changes (an `INCREMENTAL` run, or the `FULL`
run of a `VERSION_CHANGE`), with the version the run was scored under, so the run
and its link commit together or not at all, and segments reads the run's version
from the same view as any other run's. The version also stays in the run's
parameters, as ADR-0087 records it.

It is a function in the database and never an HTTP call. No service role calls it:
the run is recorded inside the segments boundary's function, whose owner is the
only grantee.

## Arguments

| Position | Name | Type | Meaning |
|---:|---|---|---|
| 1 | `p_run_id` | `bigint` | The segmentation run, by value: the registry holds no foreign key to the run (ADR-0064), and does not read it. |
| 2 | `p_version_id` | `bigint` | The version the run was scored under. It must exist in the registry; it need not be the published one, since a publication may land between the caller's read of `api_models.published_version_v1` and the call. |

## What it writes

| Condition | Effect |
|---|---|
| The run has no link | Inserts `(p_run_id, p_version_id)`, with `linked_at` the transaction time. |
| The run is linked to `p_version_id` | Nothing: a repeated call is harmless. |
| The run is linked to another version | Refused (`run_version_conflict`): a link never changes. |

## Errors

| SQLSTATE | Constraint name | Meaning |
|---|---|---|
| 22023 | none | An argument is `NULL`. |
| 23503 | `unknown_model_version` | `p_version_id` is not a version of the registry. |
| 23514 | `run_version_conflict` | The run is already linked to another version. |

`api_segments.apply_assignment_v1` lets each refusal through, so the run, the
history row and the profile roll back with it.

## Security and use

- SECURITY DEFINER, owned by `definer_models`, with
  `search_path = pg_catalog, pg_temp`. Its owner holds `USAGE` on `models`,
  `SELECT` on `models.model_version` and `SELECT, INSERT` on `models.run_version`;
  it can neither change nor delete a link (`trg_run_version_immutable`).
- EXECUTE is granted to `definer_segments` only, with `USAGE` on `api_models`
  (`contracts/db/grants.csv`). No `svc_*` role holds it.
- The runs recorded before this function existed are linked by the backfill in
  model-registry's section of `sql/01_schema.sql`, which the operator's reapply of
  that section runs; it is idempotent.

## Compatibility

Changing the signature, an error code or constraint name, or the rule that a link
is written once and never changed is breaking, and ships
`api_models.link_run_version_v2` beside this function, with its own `.v2.md`
contract.
