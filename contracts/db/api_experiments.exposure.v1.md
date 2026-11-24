# `api_experiments.exposure_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_experiments.exposure_v1` |
| Kind | PostgreSQL view |
| Owning boundary | experimentation |
| Producer | experimentation |
| Consumers | analytics-kpis |

The view publishes every campaign exposure that counts: one row per recorded exposure of a
treatment assignment in an experiment of a campaign, less the voided ones. analytics-kpis
reads it to count exposed customers per bucket and store, and to report conversion after
exposure (F26-04, #849). Consumers name only the columns they read. Its definition is in the
`experimentation` boundary section of `sql/01_schema.sql`. It needs nothing that a later
section creates, so an operator applies that section alone.

Exposures reach experimentation as `CAMPAIGN_EXPOSURE` notices on
`stream:engagement.v1.interaction_recorded`, read in consumer group `experimentation`
(ADR-0075), and are projected by F25-10 (#841). The web also writes exposures directly,
without a source event. The view does not consume anything itself; it publishes what the
projection keeps.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `exposure_id` | `bigint` | The exposure. Unique in the view. |
| `source_event_id` | `uuid` | The engagement event that recorded it. `NULL` for an exposure the web wrote. Unique when not `NULL`. |
| `campaign_id` | `integer` | The campaign whose experiment the customer was exposed in. Never `NULL`. |
| `experiment_id` | `integer` | The experiment. |
| `group_id` | `integer` | The arm of the assignment. Always a `TREATMENT` arm. |
| `customer_id` | `uuid` | The exposed customer. |
| `store_id` | `smallint` | The store where the exposure happened, as engagement's event carries it. `NULL` when its writer recorded none, as the web does. |
| `exposed_at` | `timestamp with time zone` | When the exposure occurred: the event's `occurred_at` for a projected one. |
| `data_origin` | `text` | The experiment's origin: `OBSERVED`, `SEEDED` or `INJECTED` (ADR-0052). |

## What is a row

- One row per exposure fact. A redelivery of the same engagement `event_id` never adds a
  row: the projection keeps one fact per source event.
- A voided exposure has no row, whether the `VOIDED` notice arrived after its `RECORDED`
  notice or before it. The fact itself is never deleted (ADR-0027); the view leaves it out.
- The control arm is never exposed (RN-27, ADR-0050). No row names a control arm, an
  experiment without a campaign, or a customer outside the experiment.
- An exposure inside RN-49's 60 seconds of an earlier one of the same assignment was never
  recorded, so it has no row. A customer may still have more than one row in an
  experiment, one per later exposure; a consumer counting exposed customers counts
  distinct `customer_id`.
- Exposure is descriptive. It decides nothing in an experiment's effect, and attribution
  reads the exposure table itself, not this view.

## Compatibility

Adding a column is compatible, because consumers name the columns they read. Removing or
renaming a consumed column, changing its type, or changing what a row or a column means is
breaking. Such a change is published as `api_experiments.exposure_v2`, with a matching
`.v2.md` contract beside this one, and v1 stays until analytics-kpis has moved.
