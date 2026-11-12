# `api_preferences.restriction_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_preferences.restriction_v1` |
| Kind | PostgreSQL view |
| Owning boundary | customer-preferences |
| Producer | customer-preferences-service |
| Consumers | recommendations, campaigns |

The view publishes each customer's current restrictions, one row per customer and
target: a row exists while the latest row of the append-only restriction log for
that target is `IMPOSED`, and disappears when a later `LIFTED` row is recorded. A
restriction is a narrower fact inside a granted purpose: a channel the customer does
not want to be reached on, or an interest category they do not want offered
(ADR-0066 section 1). It is independent of the declared preference sets and of
inferred interests: a restricted category is not offered whether the customer
declared it, it was inferred, or neither. Consumers name only the columns they read.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `customer_id` | `uuid` | The customer who restricted. |
| `kind` | `character varying(20)` | `CHANNEL` or `INTEREST_CATEGORY`. |
| `target_id` | `smallint` | The `channel_id` for `CHANNEL`, the `category_id` for `INTEREST_CATEGORY`. |
| `decision_id` | `bigint` | The `IMPOSED` log row that makes the restriction current. |
| `imposed_at` | `timestamp with time zone` | When that row was recorded, on the server's clock. |

`(customer_id, kind, target_id)` is unique. Every `target_id` names an existing
channel or category.

## What a consumer does with it

A restriction on a category covers the categories below it. The view publishes only
the id the customer named; the consumer resolves the descendants through
`api_categories.category_tree_v1`, by following `parent_category_id`. A restriction
on a channel narrows the channels a campaign may contact the customer on.

| Consumer | Reads | Effect |
|---|---|---|
| recommendations | `INTEREST_CATEGORY` | No product of a restricted category, or of one below it, appears for the customer (ADR-0066 section 2) |
| campaigns | `CHANNEL`, `INTEREST_CATEGORY` | A restricted channel is not used to contact the customer; a restricted category is not the subject of an offer to them |

A consumer that cannot read this view cannot know what is restricted, so it does
not tailor or contact: recommendations treats `PERSONALIZATION` as withdrawn and
campaigns treats `MARKETING_CONTACT` as withdrawn, and each says so (ADR-0066
section 2). The restriction view is not consulted for `PROFILING`: rfm-engine reads
`api_preferences.consent_state_v1`, because no channel or category restricts the
computation of R, F and M.

## Writers

Only customer-preferences writes the log, and only by appending: `svc_preferences`
holds `SELECT` and `INSERT` on it and nothing else, and the service has no route that
edits or removes a row. A decision commits with its outbox row in one transaction,
and `stream:preferences.v1.consent_changed` announces it, so a consumer re-reads this
view for the customer and never takes a restriction from the event.

## The web

As for consent: the web consumes no service and reads no `api_preferences` object.
A restriction is not an input to its batch segmentation, so ADR-0066 gives it no
`public` view.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type, changing the
values of `kind`, or changing the rule that a row exists only while the latest
decision is `IMPOSED` is breaking. A breaking revision is published as
`api_preferences.restriction_v2` with a matching `.v2.md` contract beside v1; v1
remains until recommendations and campaigns have moved.
