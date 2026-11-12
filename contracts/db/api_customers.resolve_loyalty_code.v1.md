# `api_customers.resolve_loyalty_code_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_customers.resolve_loyalty_code_v1` |
| Kind | PostgreSQL function |
| Owning boundary | customers |
| Producer | customers-service |
| Consumer | check-ins |
| Signature | `api_customers.resolve_loyalty_code_v1(text)` |
| regprocedure | `api_customers.resolve_loyalty_code_v1(text)` |
| Result | `TABLE (card_id uuid, customer_id uuid)`: one row, or none |

It answers which customer an ACTIVE loyalty card belongs to, given the code a
scanner read from its QR. check-ins calls it to record an authorized customer
visit (`POST /v1/customer-visits`) without calling customers-service over HTTP
(ADR-0044) and without being able to list the codes: it is a function of one
code, not a view of all of them. It is a read, writes nothing, and the code
never leaves the call: check-ins does not store it and does not return it.

## Argument

| Position | Name | Type | Meaning |
|---:|---|---|---|
| 1 | `p_code` | `text` | The card's code: 26 Crockford base32 characters (digits and uppercase letters except I, L, O and U), as `CardCode` in `contracts/customers/openapi.yaml`. |

## What it returns

| Condition | Result |
|---|---|
| `p_code` is the code of an ACTIVE card | One row: the card and its customer. |
| `p_code` is the code of a REVOKED card | No row. |
| `p_code` matches no card, is not 26 Crockford base32 characters, or is `NULL` | No row. |

A revoked and an unknown code give the same answer, so the caller cannot tell
them apart and learns no customer from either, as `POST /v1/loyalty-cards/lookup`
names no customer for a revoked card. The function never raises for a code it
does not know. A customer holds at most one ACTIVE card, so it never returns
more than one row. A customer who was anonymized has no ACTIVE card, because
anonymization revokes loyalty identifiers (ADR-0066), so it returns nothing for
that customer's code either.

The function returns the card id and the customer id and nothing else: no name,
email, phone, issue time or revocation reason. A consumer that needs the customer
reads the customers contract, which applies the caller's role.

## Security and use

- SECURITY DEFINER, owned by `definer_customers`, with
  `search_path = public, pg_temp`. Its owner holds the privilege on the card
  table that the caller does not.
- EXECUTE is granted to `svc_checkins` only. `contracts/db/grants.csv` is not
  touched by this contract; the row arrives with the function, in the story that
  creates the loyalty-card table and this function in the customers section of
  `sql/01_schema.sql` (F21-14, #792). Until then check-ins' customer-visit
  operations are `x-mosaiq-status: planned`.
- check-ins applies its own request limit to the operation that calls it, per
  caller, so a code cannot be guessed by repetition.

## Compatibility

Changing the signature, the result columns' names or types, or the rule that
decides which card answers is breaking. Adding a result column is not, because
consumers name the columns they read. A breaking change ships
`api_customers.resolve_loyalty_code_v2` beside `_v1` with its own `.v2.md`
contract; v1 is removed only after check-ins has moved.
