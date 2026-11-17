# `api_campaigns.campaign_excluded_customer_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_campaigns.campaign_excluded_customer_v1` |
| Kind | PostgreSQL view |
| Owning boundary | campaigns |
| Producer | campaigns-service |
| Consumer | experimentation |

The view publishes the customers a campaign excludes by name: one row for each customer excluded
from each campaign. experimentation reads it to take an experiment's frame (ADR-0068 section 1): a
customer a campaign excludes is not in the frame of an experiment attached to that campaign, and is
counted under the experimentation contract's `CAMPAIGN_EXCLUSION`. The campaign's target and
eligibility rules are in [`api_campaigns.campaign_target_v1`](api_campaigns.campaign_target.v1.md),
and its excluded labels in
[`api_campaigns.campaign_excluded_label_v1`](api_campaigns.campaign_excluded_label.v1.md). The three
are separate because each is its own multivalued fact of a campaign (4NF).

A campaign with no excluded customer has no row. The view holds no other column: it says nothing
about why a customer is excluded, who excluded them or when.

**As of when.** The view has no history: it shows the rows as committed when the reading
statement's snapshot was taken. A consumer reads it in the same `REPEATABLE READ` transaction as
the other objects of its frame, which fixes the instant `T` (see
[`api_campaigns.campaign_target_v1`](api_campaigns.campaign_target.v1.md)). campaigns-service
replaces a campaign's exclusions only while the campaign is `DRAFT`.

The producing table is `campaigns.campaign_excluded_customer`; this contract names no other.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `campaign_id` | `integer` | The campaign, as `campaign_id` of the campaigns contract. |
| `customer_id` | `uuid` | The excluded customer, as `customer_id` of the customers contract. |

`(campaign_id, customer_id)` is unique.

## Compatibility

Adding a column is compatible because consumers name their selected columns. Removing or renaming
a column, changing its PostgreSQL type, or changing which exclusions have a row is breaking. A
breaking revision is published as `api_campaigns.campaign_excluded_customer_v2` with a matching
`.v2.md` contract beside v1; v1 remains until experimentation has moved.
