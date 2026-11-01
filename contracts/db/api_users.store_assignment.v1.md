# `api_users.store_assignment_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_users.store_assignment_v1` |
| Kind | PostgreSQL view |
| Owning boundary | users |
| Producer | users-service |
| Consumers | sales, check-ins, engagement |

The view publishes one row for each store a user is assigned to, for the users
whose assignment currently counts. It exists so that a service can narrow a
store-bound operation to the caller's stores on each request without calling
users-service and without carrying stores in the token (ADR-0036, ADR-0060).
The write side is `PUT /v1/store-assignments/{user_id}` in
`contracts/users/openapi.yaml`; the table behind the view is users-service's
own and is not part of this contract. Consumers name only the columns they
read.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `user_id` | `uuid` | The user, as `api_users.credential_v1.user_id` and the token's subject name them. |
| `store_id` | `smallint` | A store the user is assigned to, as `api_stores.store_directory_v1.store_id` names it. |

A pair `(user_id, store_id)` appears at most once. The view carries no store
attributes, and a consumer that needs a store's name or location reads the
stores contract, never this view.

## Which rows appear

A row appears only while all of these hold:

- the user is active;
- the user's role is `SALES_ASSOCIATE`, `STORE_MANAGER`, `LOYALTY_OFFICER` or
  `COMMERCIAL_SUPERVISOR`; and
- the store is in the user's assignment set.

`ADMIN` is not confined and has no rows. A user with none of these has no
rows, and a consumer reads that as "assigned to no store": a floor role with no
row can work in no store (ADR-0060). Deactivating a user, or changing the role
to one that is not store-bound, therefore removes the user's rows from the view
at the same commit, with no further write.

## How a consumer uses it

A consumer reads the rows for the caller's `user_id` inside the request it is
serving, with a parameterised statement, and keeps neither the rows nor a
derived set beyond that request: no cache, no Redis key, no copy in a token. No
stream announces assignments; the only stream of the users boundary,
`stream:users.v1.access_changed`, is for auth and says nothing about stores.

## Staleness bound

A change is visible to every request that starts after the change commits. A
request already in flight completes under the rows it read. So a removed
assignment can still be honoured only by requests in flight at the commit, and
by none that starts later. The access token's ten-minute life does not extend
this, because the token holds no store.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming `user_id` or `store_id`, changing a column's PostgreSQL
type, adding or removing a store-bound role from the list above, relaxing the
active-user condition, or publishing a row for a user the rules above exclude
is breaking. A new consumer needs a row in `contracts/db/grants.csv` and a line
in the Consumers field above in the same pull request. A breaking revision is
published as `api_users.store_assignment_v2` with a matching `.v2.md` contract
beside v1; v1 remains until its consumers have moved.
