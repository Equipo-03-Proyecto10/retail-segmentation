# `api_experiments.exposure_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_experiments.exposure_v1` |
| Kind | PostgreSQL view |
| Owning boundary | experimentation |
| Producer | experimentation |
| Consumers | analytics-kpis |

The view publishes the campaign exposures that still count: one row for each exposure
of a customer to a campaign that experimentation projected from engagement's
`CAMPAIGN_EXPOSURE` notices (F25-10, ADR-0075) and that no `VOIDED` notice has removed.
analytics-kpis reads it for `conversion` (F26-04, #849), to know which customers were
exposed and when. Exposure is descriptive: it is not a conversion and not an effect
(ADR-0069), and the view carries no arm, no result and no sale. Consumers name only the
columns they read. Its definition is in the `experimentation` boundary section of
`sql/01_schema.sql`. It reads only tables of experimentation, so an operator applies
that section alone.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `exposure_id` | `bigint` | The exposure. |
| `campaign_id` | `integer` | The campaign of the experiment the customer was assigned to. |
| `experiment_id` | `integer` | The experiment. A campaign measured twice can expose the same customer in two experiments. |
| `customer_id` | `uuid` | The exposed customer. |
| `exposed_at` | `timestamp with time zone` | When the exposure happened, as engagement recorded it. |
| `data_origin` | `text` | The experiment's origin: `OBSERVED`, `SEEDED` or `INJECTED` (ADR-0052). A consumer marks a figure built on any other origin as synthetic. |

`exposure_id` is unique.

## What is a row

- An exposure of a customer assigned to a treatment arm of an experiment that has a
  campaign. The control arm is never exposed, and an experiment without a campaign has no row.
- An exposure whose source event was voided has no row, whichever of the two notices
  arrived first. An exposure written by the web, which has no source event, counts.
- Exposures inside the web's 60-second repeat window are one row, as the projection keeps them.
- The figures are eventually consistent, within seconds, because the exposures travel on
  a stream (ADR-0075).

## Compatibility

Adding a column is compatible, because consumers name the columns they read. Removing
or renaming a consumed column, changing its type, or changing what a row or a column
means is breaking. Such a change is published as `api_experiments.exposure_v2`, with a
matching `.v2.md` contract beside this one, and v1 stays until analytics-kpis has moved.
