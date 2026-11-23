# `api_experiments.campaign_effect_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_experiments.campaign_effect_v1` |
| Kind | PostgreSQL view |
| Owning boundary | experimentation |
| Producer | experimentation |
| Consumers | analytics-kpis |

The view publishes, for every experiment of a campaign whose result experimentation has
stored, the sales attributed to the campaign and the incremental effect measured against
its control. The two are separate columns and are never merged: attribution says which
sales followed an exposure, and the effect says what the campaign changed (ADR-0069,
F25-02's record). analytics-kpis reads it for `campaign-performance` (F26-04, #849).
Consumers name only the columns they read. Its definition is in the `experimentation`
boundary section of `sql/01_schema.sql`. It needs nothing that a later section creates,
so an operator applies that section alone.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `campaign_id` | `integer` | The campaign the experiment measures. |
| `experiment_id` | `integer` | The experiment. A campaign measured twice has two rows. |
| `window_start` | `date` | The experiment's first day (`starts_on`). |
| `window_end` | `date` | The experiment's last day (`ends_on`), or the UTC day its result was computed when it has none. |
| `treated_customers` | `integer` | Customers assigned to a treatment arm. |
| `control_customers` | `integer` | Customers assigned to the control arm; 0 without one. |
| `attributed_sales_amount` | `numeric(14,2)` | The net amount, after reversals, of the sales attributed to the campaign through its treatment assignments (F25-12). A fully reversed sale adds nothing. 0.00 when none. |
| `incremental_effect` | `double precision` | Mean net spend per analysed customer of the treatment arm less that of the control: the stored `REVENUE_PER_CUSTOMER` comparison (F25-13). `NULL` without a control, with more than one treatment arm, or when the comparison was withheld. |
| `effect_lower` | `double precision` | The lower end of the effect's interval; `NULL` exactly when `incremental_effect` is. |
| `effect_upper` | `double precision` | The upper end; `NULL` exactly when `incremental_effect` is. |
| `confidence` | `numeric(12,10)` | The interval's level, `1 - comparison_alpha` of the experiment's plan (0.95 for the usual plan). `NULL` exactly when `incremental_effect` is, and when the experiment has no plan. |
| `computed_at` | `timestamp with time zone` | When the result was computed. |
| `synthetic` | `boolean` | True when the result was computed over synthetic data, the experiment is not `OBSERVED`, or an attributed sale is `SEEDED` (ADR-0052). |
| `data_origin` | `text` | The experiment's origin: `OBSERVED`, `SEEDED` or `INJECTED`. |

## What is a row

- A row is an experiment with a `campaign_id` and a stored `experiments.experiment_result`.
  An experiment not yet measured has no row.
- The effect is one number only when the experiment has exactly one control arm and one
  treatment arm, so one comparison and one interval describe it. With two treatment arms
  the stored comparisons are one per arm at a split alpha, so no single effect or 95%
  interval exists, and the view shows none rather than choose one.
- The attributed amount and the effect never feed each other. Attribution counts the
  treated customers' qualifying sales; the effect compares the treated with the control.

## Compatibility

Adding a column is compatible, because consumers name the columns they read. Removing
or renaming a consumed column, changing its type, or changing what a row or a column
means is breaking. Such a change is published as `api_experiments.campaign_effect_v2`,
with a matching `.v2.md` contract beside this one, and v1 stays until analytics-kpis
has moved.
