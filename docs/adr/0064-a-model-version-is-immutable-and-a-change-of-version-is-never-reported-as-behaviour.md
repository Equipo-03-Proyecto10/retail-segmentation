# ADR-0064 — A model version is immutable, and a change of version is never reported as behaviour

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #776
**Supersedes:** —
**Superseded by:** —

---

## Context

The brief names consistent history as a key capability: migrations are kept per model
version, and if the RFM limits or K-means change, the past is not rewritten and a
methodological difference is not presented as a change in customer behaviour.

`segmentation_run` records `method`, `window_days` and `parameters`, and nothing
groups runs into a version, so nothing can be published or rolled back. RN-39 compares
two runs by label code and treats the method as a description only, so a change of
algorithm that relabels half the customers reads as half the customers moving.

Incremental scoring raises the stakes: it needs a frozen set of boundaries or centroids
to score against (ADR-0063), and something has to own them.

The web complicates this. Its administrator run changes every customer's assignment,
and it cannot reach any schema but `public` (ADR-0046), so it cannot name a version
that model-registry owns. Whether web runs should remain possible at all is a fair
question; this record keeps them, so the web does not change.

## Decision

A model version is an immutable record, and exactly one version is published at a
time, because a customer holds one current segment (RN-20). Publication and rollback
add facts and never edit history. A comparison across versions is marked methodological
and never counts as behaviour.

| A version holds | Detail |
|---|---|
| Method | `RFM_RULES` or `KMEANS` |
| Definition | Rule set, or k, seed, iteration limit and tolerance |
| Features and normalization | Which variables, and how they were scaled |
| Window | The days it measures |
| Frozen scoring | Quintile boundaries, or centroids and normalization bounds |
| Reference distribution | What drift is judged against (F23-07) |
| Dataset | A fingerprint of the data it was fitted on, and its origin (ADR-0052) |
| Metrics | Inertia, silhouette, stability, as they apply |
| Owner and state | Who registered it; `DRAFT`, `PUBLISHED` or `RETIRED` |

- **Immutable.** Once published, a version cannot be edited or deleted by any role. A change is a new version. A partial unique index allows one `PUBLISHED` row.
- **Publication and rollback.** `models.model_publication` is append-only: version, action, author, time and reason. A rollback publishes the previous version again, as a new row.
- **Moving customers.** After a publication, every customer is scored under the new version, in a `FULL` run whose changed rows carry the cause `VERSION_CHANGE`. A rollback does the same. Rows already written never change, so the past stays as it was scored.
- **Every run has a version.** `models.run_version` links `run_id` to its version, in model-registry's schema, and `segmentation_run` does not change. model-registry consumes `stream:segments.v1.run_recorded`. When a run comes from the web, it registers the run's method and parameters as a version unless one matches, and publishes it, because that run has just reassigned everyone. So the web's button keeps working unchanged, and nothing escapes the registry.
- **Reading a comparison.** Two assignments under one version show behaviour. Across versions, the response carries `methodological: true`, `VERSION_CHANGE` rows are never counted as migrations, and behaviour KPIs exclude them. The web, which cannot read `models.*`, marks a comparison methodological when the two runs differ in method, window or parameters.
- **RN-39 narrows.** Runs are still compared by label code, and the method still never changes how a label is read, but a comparison now states whether it crosses a method or a version.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Treat each run as a version, as today | There would be nothing to publish or roll back, and incremental batches would each be a version |
| Rewrite history under the new version | It presents the past as if today's model had scored it, which the instructor forbids |
| One published version per method | A customer would hold two current segments, against RN-20 |
| Add `model_version_id` to `segmentation_run` | The web would have to fill it, and `retail_app` cannot read the table it would point to |
| Stop web runs and publish only through the registry | It is cleaner, but it changes the web's main process and its evidence, and the stream gives the same guarantee |
| Version only the RFM rules | K-means is the one whose parameters change results most, and the brief names it |

## Consequences

**What this makes easy.** "Who changed the model, when, and on what data" has one
answer. A bad model is undone without losing the record. A migration report can be
believed.

**What this makes hard.** The web's run reaches the registry only through a stream, so
there is a moment when a run has no version yet; readers must treat that as pending. A
publication writes a row for each customer who moves. One published version means that
comparing K-means with RFM happens on drafts, not side by side in production.

**What must now be true elsewhere.**
- F22-10 and F22-11 publish and build the registry, F22-13 publication and rollback, and F22-16 the version-aware migrations.
- ADR-0063's scoring reads a version's frozen scoring through a published view.
- F21-25's audit records every publication and rollback.
- F28-13 compares drafts, and RN-39 keeps saying that disagreement is not a verdict.

## Compliance

A test tries to edit and delete a published version as each role. A test publishes and
rolls back, then compares `customer_segment_history`: rows that existed are identical,
and only new rows appear. The partial unique index refuses a second `PUBLISHED` row.
A test compares two versions and asserts the response is marked methodological and is
absent from behaviour KPIs.
