# `api_campaigns.campaign_ref_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_campaigns.campaign_ref_v1` |
| Kind | PostgreSQL view |
| Owning boundary | campaigns |
| Producer | campaigns-service |
| Consumer | check-ins |

The view publishes the identity of every campaign, so that another boundary can
check that a campaign it is about to point at exists, without calling
campaigns-service over HTTP (ADR-0044) and without reading the campaign tables.
check-ins reads it to validate the optional `campaign_id` of an authorized
customer visit (`POST /v1/customer-visits`); an id that is not in the view is
refused as `unknown_campaign`. It is the same kind of reference view as
`api_customers.customer_ref_v1` and `api_products.product_ref_v1`.

The view holds one row for each campaign that exists, whatever its status:
`DRAFT`, `ACTIVE`, `FINISHED` or `CANCELLED`. It carries no status, dates,
audience, budget or promotion, so it says nothing about whether a campaign is
running; a consumer that needs more asks for a column in a later, additive
revision. check-ins tests existence and nothing else.

The producing tables are created by the schema story that implements the
campaigns contract; this contract names none.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `campaign_id` | `integer` | The campaign, as `campaign_id` of the campaigns contract and of `api_campaigns.active_promotion_v1`. |

`campaign_id` is unique.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming `campaign_id`, changing its PostgreSQL type, or changing
which campaigns have a row is breaking. A breaking revision is published as
`api_campaigns.campaign_ref_v2` with a matching `.v2.md` contract beside v1; v1
remains until check-ins has moved.
