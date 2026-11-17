# `api_models.published_version_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_models.published_version_v1` |
| Kind | PostgreSQL view |
| Owning boundary | model-registry |
| Producer | model-registry |
| Consumers | rfm-engine, analytics-kpis |

The view publishes the current published version of each method: one row per
method that has a version whose state is `PUBLISHED`, and no row for a method
that has none. ADR-0064 allows exactly one `PUBLISHED` version at a time, so the
view holds at most one row today, and a consumer never has to choose between two.
A consumer that needs the published version reads this view and not
`models.model_version`, which it cannot reach. The view reports what model-registry
stored when the version was created and when it was published, and fills in
nothing from any other source. It carries the version as `model-versions` serves
it, minus the reference distribution and the metrics, which only drift judgement
and the registry's own readers use. Consumers name only the columns they read.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `version_id` | `bigint` | The published version. |
| `method` | `character varying(20)` | `RFM_RULES` or `KMEANS`. |
| `name` | `character varying(80)` | The version's unique name. |
| `window_days` | `integer` | The days of sales the version measures. |
| `seed` | `bigint` | The seed the fit used; `NULL` for `RFM_RULES`. |
| `rule_set_id` | `bigint` | The rfm-engine rule set the version froze; `NULL` for `KMEANS`. |
| `k` | `integer` | The number of clusters; `NULL` for `RFM_RULES`. |
| `features` | `text[]` | The variables used, from `R`, `F` and `M`. |
| `normalization` | `character varying(20)` | `NONE` or `MIN_MAX`. |
| `frozen_scoring` | `jsonb` | What a customer is scored against, in the shape of the API's `FrozenScoring`: `quintile_boundaries` for `RFM_RULES`, `centroids` and `normalization_bounds` for `KMEANS`. |
| `data_origin` | `character varying(20)` | `OBSERVED` or `SEEDED`: the origin of the data the version was fitted on (ADR-0052). |
| `publication_id` | `bigint` | The publication or rollback that made the version current. |
| `published_at` | `timestamp with time zone` | The instant of that publication. |

The scoring is frozen: a consumer scores a customer against `frozen_scoring` and
never against the population (ADR-0063). The row changes only when a later
publication or rollback appends a row to `models.model_publication`; a version's
own definition never changes (ADR-0064). The stream that tells a consumer a
publication happened is `stream:models.v1.publication_recorded`
(`contracts/streams/models.publication_recorded.v1.md`); the consumer then re-reads
this view and does not take the version from the event.

## When no version is published

The view has no row. It never substitutes a draft, a retired version or a default.
rfm-engine's `POST /v1/rfm-recalculations` without a rule set answers 409
`no_published_rule_set` in that case.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type, changing the
shape of `frozen_scoring`, or changing the one-row-per-method rule is breaking. A
breaking revision is published as `api_models.published_version_v2` with a matching
`.v2.md` contract beside v1; v1 remains until rfm-engine and analytics-kpis have
moved.
