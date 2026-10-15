# `api_preferences.interest_category_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_preferences.interest_category_v1` |
| Kind | PostgreSQL view |
| Owning boundary | customer-preferences |
| Producer | customer-preferences-service |
| Consumers | recommendations |

The view publishes one row for every interest a customer has named, from
`public.customer_interest_category`. A customer with no interests has no rows.
Consumers name only the columns they read.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `customer_id` | `uuid` | The customer who named the interest. |
| `category_id` | `smallint` | The category of interest. |

`(customer_id, category_id)` is unique: an interest appears once. Every row
names an existing customer and an existing category; a category with interests
is never deleted through services (E-13).

## Writers

Only customer-preferences writes the table, and only by replacing a customer's
whole set. The replace takes
`pg_advisory_xact_lock(290001, hashtext(customer_id::text))` before its DELETE
and INSERT (see [`advisory-locks.md`](advisory-locks.md)), so a reader never
sees a mixture of two concurrent replaces.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, or changing its PostgreSQL type, is
breaking. A breaking revision is published as
`api_preferences.interest_category_v2` with a matching `.v2.md` contract beside
v1; v1 remains until recommendations has moved.
