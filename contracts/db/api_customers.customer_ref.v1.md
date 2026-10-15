# `api_customers.customer_ref_v1`

`api_customers.customer_ref_v1` is a PostgreSQL view produced by the
**customers** boundary. It is the published customer reference used across
service boundaries; consumers do not read the `customer` base table.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `customer_id` | `uuid` | Stable identity of the customer. |
| `registered_on` | `date` | Date on which the customer was registered. |

The view deliberately carries no customer name, email or phone. Those fields
are personally identifying directory data and none of the consumers needs
them to apply its rule.

## Consumers

- **sales** reads `customer_id` and `registered_on` to validate a sale's
  customer and date.
- **customer-preferences**, **segments**, **recommendations** and
  **engagement** read `customer_id` to validate or enumerate customer
  references.

Consumers name only the columns they read. Adding a column is therefore not a
breaking change. Removing either v1 column, changing its PostgreSQL type or
meaning, or changing which customer rows the view represents is breaking and
requires `api_customers.customer_ref_v2` with a parallel `.v2.md` contract.
