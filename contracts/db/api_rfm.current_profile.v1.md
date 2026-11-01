# `api_rfm.current_profile_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_rfm.current_profile_v1` |
| Kind | PostgreSQL view |
| Owning boundary | rfm-engine |
| Producer | rfm-engine |
| Consumers | model-registry, analytics-kpis |

The view publishes exactly one row for every customer in
`api_customers.customer_ref_v1`, so a customer is never missing and never
repeated. It reports what rfm-engine last computed for the customer and fills in
nothing from any other source. It publishes the profile as `rfm-profiles` serves
it, minus `next_change_at`, which is the producer's own scheduling state.
Consumers name only the columns they read.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `customer_id` | `uuid` | The customer. |
| `state` | `text` | One of `scored`, `unassigned` or `pending`. |
| `rule_set_id` | `bigint` | The rule set the profile was computed under; `NULL` when `state` is `pending`. |
| `r_days` | `integer` | Whole days from the customer's latest net purchase in the window to `computed_at`; `NULL` unless `state` is `scored`. |
| `f_count` | `integer` | Net purchases in the window; `0` when `unassigned`, `NULL` when `pending`. |
| `m_total` | `numeric(12,2)` | The sum of net amounts in the window; `0.00` when `unassigned`, `NULL` when `pending`. |
| `r_score` | `smallint` | Recency score 1 to 5; `NULL` unless `scored`. |
| `f_score` | `smallint` | Frequency score 1 to 5; `NULL` unless `scored`. |
| `m_score` | `smallint` | Monetary score 1 to 5; `NULL` unless `scored`. |
| `label_code` | `character varying(40)` | rfm-engine's own label for the scores; `NULL` unless `scored`. |
| `via_fallback` | `boolean` | True when no band matched and the rule set's fallback label was taken (RN-46); `NULL` unless `scored`. |
| `computed_at` | `timestamp with time zone` | The instant the profile was computed; `NULL` when `pending`. |

"Net" means accepted purchases less reversals (ADR-0062). The scores are measured
against the boundaries frozen in the rule set, never against the population
(ADR-0063). The label is rfm-engine's own result. The customer's current segment
is segments' resource, published as `api_segments.current_label_v1`; a consumer
that needs it reads that view and not this one.

## The three states

| State | Meaning | Measures, scores, label | `rule_set_id`, `computed_at` |
|---|---|---|---|
| `scored` | The customer has at least one net purchase in the window. | set | set |
| `unassigned` | The customer has no net purchase in the window (RN-21): they never bought, or every sale was reversed, or every purchase aged out. | `f_count` 0 and `m_total` 0.00; the rest `NULL` | set |
| `pending` | No profile has been computed yet, for instance a customer registered after the last pass. | `NULL` | `NULL` |

A customer with no profile is reported as `pending`; the view never substitutes
another rule set's profile, an earlier row or a default.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type, adding a
state, changing the meaning of a state or of a measure, or changing the
one-row-per-customer rule is breaking. A breaking revision is published as
`api_rfm.current_profile_v2` with a matching `.v2.md` contract beside v1; v1
remains until model-registry and analytics-kpis have moved.
