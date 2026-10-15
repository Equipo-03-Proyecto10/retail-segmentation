# `api_users.credential_v1`

`api_users.credential_v1` is a PostgreSQL view published by the **users**
boundary. It is the only cross-boundary database contract that carries a
credential (accepted exception E-15).

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `user_id` | `uuid` | Stable account identifier. |
| `email_lower` | `text` | Account email lower-cased for case-insensitive credential lookup. |
| `password_hash` | `character varying(255)` | The account's argon2id hash. It is used only to verify a submitted credential. |
| `is_active` | `boolean` | Whether the account may authenticate. |
| `role_code` | `character varying(40)` | Immutable role code copied into a successful session's token. |

## Access and confidentiality

**auth is the only consumer.** `svc_auth` holds `USAGE` on `api_users` and
`SELECT` on this view. `svc_users` also holds `SELECT` because users is the
producer. No other service role may read it, and neither role receives access
to another boundary's base table through this contract.

The hash never appears in an HTTP payload, a Redis Stream event, or a log.
Keeping it in this narrowly granted view avoids creating a second credential
store while letting auth perform sign-in and session reconciliation.

## Compatibility

Consumers name the columns they read, so adding an unconsumed column is
non-breaking. Removing or renaming a listed column, changing its PostgreSQL
type or meaning, widening access beyond auth and users, or weakening the hash
confidentiality promise is breaking. A breaking revision ships
`api_users.credential_v2` and `api_users.credential.v2.md` beside v1 until
auth has moved and its v1 grant is removed.
