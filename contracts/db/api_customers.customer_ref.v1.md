# `api_customers.customer_ref_v1`

`api_customers.customer_ref_v1` is a PostgreSQL view produced by the
**customers** boundary. It is the published customer reference used across
service boundaries; consumers do not read the `customer` base table.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `customer_id` | `uuid` | Stable identity of the customer. |
| `registered_on` | `date` | Date on which the customer was registered. |
| `anonymized` | `boolean` | True once customers-service has anonymized the customer (ADR-0066 section 3); never null, and never true again false. Appended last so the view can be replaced in place. |

The view deliberately carries no customer name, email or phone. Those fields
are personally identifying directory data and none of the consumers needs
them to apply its rule.

## An anonymized customer

A customer is never deleted, so an anonymized customer keeps its row, its
`customer_id` and its `registered_on` here, and `anonymized` is true. Every
sale, segment assignment and consent record that names the key still resolves.
A consumer that serves, targets or contacts a customer, or records something new
about one, reads `anonymized` and treats a customer for whom it is true as one it
may not act on: campaigns counts it under `not_in_customer_reference` and never
puts it in an audience, and sales refuses a new sale for it. A consumer that only
validates a reference, or reads history, is unaffected. The flag is set once, in
the transaction that removes the identity, and is announced on
`stream:customers.v1.anonymized` (`contracts/streams/customers.anonymized.v1.md`);
a consumer re-reads this view and never trusts the event for state.

## Consumers

- **sales** reads `customer_id` and `registered_on` to validate a sale's
  customer and date.
- **customer-preferences**, **segments**, **recommendations** and
  **engagement** read `customer_id` to validate or enumerate customer
  references.
- **campaigns** reads `customer_id` and `registered_on` to judge a campaign's
  audience (registration cutoff, customers the reference does not list).

Consumers name only the columns they read. Adding a column, as `anonymized`
was, is therefore not a breaking change. Removing either v1 column, changing its PostgreSQL type or
meaning, or changing which customer rows the view represents is breaking and
requires `api_customers.customer_ref_v2` with a parallel `.v2.md` contract.
