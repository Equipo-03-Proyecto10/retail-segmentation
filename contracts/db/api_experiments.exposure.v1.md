# `api_experiments.exposure_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_experiments.exposure_v1` |
| Kind | PostgreSQL view |
| Owning boundary | experimentation |
| Producer | experimentation |
| Consumers | analytics-kpis |

The view publishes every campaign exposure experimentation holds that is not voided: one
row per exposure of a treatment assignment of an experiment that has a campaign.
analytics-kpis reads it to count the customers exposed in a bucket for `conversion`
(F26-04, #849, #1198). Consumers name only the columns they read. Its definition is in
the `experimentation` boundary section of `sql/01_schema.sql`. It needs nothing that a
later section creates, so an operator applies that section alone.

The rows are `public.experiment_exposure`, the table F25-10 (#841) writes and F25-12
(#843) attributes sales from; the view adds no second copy of an exposure. An exposure
reaches that table from `CAMPAIGN_EXPOSURE` notices of
`stream:engagement.v1.interaction_recorded` (consumer group `experimentation`,
ADR-0075), or from the frozen web's own write (E-09).

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `exposure_id` | `bigint` | The exposure. One row each, and it never changes. |
| `campaign_id` | `integer` | The campaign whose offer the customer saw: the campaign of the assignment's experiment. Never `NULL`. |
| `experiment_id` | `integer` | The experiment the customer is assigned in. |
| `customer_id` | `uuid` | The exposed customer, as assigned. |
| `store_id` | `smallint` | The store where the exposure happened, as engagement recorded it. `NULL` for an exposure the web wrote, which records no store. |
| `exposed_at` | `timestamp with time zone` | When the customer saw the offer: the event's `occurred_at`. |
| `data_origin` | `text` | The experiment's origin: `OBSERVED`, `SEEDED` or `INJECTED` (ADR-0052). Anything but `OBSERVED` is synthetic. |

## What is a row

- A row is an exposure whose source event has no `VOIDED` notice in
  `experiments.exposure_notice`. A void that arrives before its `RECORDED` keeps the
  `RECORDED` from ever writing an exposure, so the exposure never appears; a void that
  arrives after hides the exposure without rewriting it (ADR-0050).
- A redelivered `event_id` adds no row: the source event id is unique on the table.
  RN-49's 60-second rule also folds a second sighting of the same assignment inside 60
  seconds into the first, so two rows of one customer are two separate sightings.
- A control assignment is never exposed (RN-27, ADR-0050). The consumer records a
  `CAMPAIGN_EXPOSURE` of a control customer as a `CONTROL` notice and writes no exposure,
  `trg_experiment_exposure_treatment_only` refuses one from any writer, and the view
  keeps only treatment arms as well, so no control row can be published.
- A customer may have several rows. To count exposed customers, count distinct
  `customer_id`.

## What it is not

Exposure is descriptive. It decides nothing in an effect: an experiment's result is
intent to treat over every assigned customer, exposed or not, and the effect columns of
`api_experiments.campaign_effect_v1` are computed from no exposure (ADR-0068). Exposure
feeds only attribution (F25-12, ADR-0069), which is named apart from effect. A
conversion counted after an exposure is observational and is never called an effect.

## Compatibility

Adding a column is compatible, because consumers name the columns they read. Removing
or renaming a consumed column, changing its type, or changing what a row or a column
means is breaking. Such a change is published as `api_experiments.exposure_v2`, with a
matching `.v2.md` contract beside this one, and v1 stays until analytics-kpis has moved.
