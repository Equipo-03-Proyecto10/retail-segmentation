# `api_campaigns.campaign_target_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_campaigns.campaign_target_v1` |
| Kind | PostgreSQL view |
| Owning boundary | campaigns |
| Producer | campaigns-service |
| Consumer | experimentation |

The view publishes, for every campaign that exists, who the campaign targets and
the eligibility rules a customer must meet to be in its audience. experimentation
reads it to take an experiment's frame (ADR-0068 section 1): the customers who hold
the campaign's target label, meet its eligibility rules and are not excluded. It also
reads `status`, to refuse a plan for a finished or cancelled campaign as the
experimentation contract's `campaign_closed` (RN-49). The customers a campaign
excludes are in
[`api_campaigns.campaign_excluded_customer_v1`](api_campaigns.campaign_excluded_customer.v1.md)
and [`api_campaigns.campaign_excluded_label_v1`](api_campaigns.campaign_excluded_label.v1.md),
not here, because an exclusion is a list of any length and this view has one row per campaign
(ADR-0044, 4NF).

The view holds one row for each campaign, whatever its status. It carries no customer, no
budget, no promotion and no cap on the audience's size: the cap says how many customers
campaigns-service will contact, not who is eligible, and which customers it keeps is
campaigns-service's own choice. It carries no channel either, because the campaigns boundary holds
none for a campaign (a promotion's channels belong to the promotion).

The three eligibility columns are the campaign's one eligibility rule, as campaigns-service
stores it (`campaigns.campaign_eligibility`): a campaign with no rule has `NULL` in all three,
a campaign with a registration cutoff only has `NULL` in the two purchase columns, and a
purchase rule always has both of its columns (`min_purchases` and `purchase_window_days` are
given together or not at all).

**As of when.** The view has no history: it shows the rows as committed when the reading
statement's snapshot was taken. A consumer that needs one instant `T` for the target, the
exclusions and every other published object it reads (ADR-0068 section 1) reads them all in one
transaction at `REPEATABLE READ`, so each read is of the same snapshot, and takes `T` from the
PostgreSQL clock in that same transaction. A rule or label that campaigns-service changes after
the snapshot is not seen. campaigns-service changes a campaign's rules only while it is `DRAFT`,
and a plan is not refused for a `DRAFT` campaign, so a plan may be taken from rules that are
later replaced; the plan stores what it read.

The producing tables are the ones campaigns-service created for audiences and eligibility
(`campaigns.campaign_eligibility`) and `public.campaign`; this contract names no other.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `campaign_id` | `integer` | The campaign, as `campaign_id` of the campaigns contract. Unique. |
| `label_code` | `varchar(40)` | The campaign's target segment label, as `label_code` of the segmentation contract. Never `NULL`. |
| `status` | `varchar(20)` | `DRAFT`, `ACTIVE`, `FINISHED` or `CANCELLED`. |
| `registered_on_or_before` | `date` | Only customers registered on or before this date are eligible. `NULL` when the campaign has no such rule. |
| `min_purchases` | `integer` | The least number of net purchases (ADR-0062) a customer made within the window. `NULL` when the campaign has no purchase rule. |
| `purchase_window_days` | `integer` | The length in days of the window `min_purchases` counts in, ending at the instant the rule is judged. `NULL` exactly when `min_purchases` is. |

## Compatibility

Adding a column is compatible because consumers name their selected columns. Removing or renaming
a column, changing its PostgreSQL type, or changing which campaigns have a row or what a rule's
`NULL` means is breaking. A breaking revision is published as `api_campaigns.campaign_target_v2`
with a matching `.v2.md` contract beside v1; v1 remains until experimentation has moved.
