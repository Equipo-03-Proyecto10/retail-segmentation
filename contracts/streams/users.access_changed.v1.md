# `stream:users.v1.access_changed`

The **users** boundary publishes this key-only Redis Stream so auth can end
every session after an account's access changes, regardless of which permitted
writer made the change.

## Capture and fields

The transactional capture is on `public.app_user` for:

- `UPDATE OF is_active, role_id, password_hash`; and
- `DELETE`.

It captures writes made by users-service and by the frozen web. The trigger
writes the users outbox in the same PostgreSQL transaction, so a rollback
publishes nothing and neither writer can bypass the signal.

`XADD` carries exactly these fields, all encoded as Redis strings:

| Field | Contract |
|---|---|
| `event_id` | UUID that identifies the outbox event and the delivery. |
| `event_type` | PostgreSQL trigger operation `TG_OP`: `UPDATE` or `DELETE`. |
| `entity_id` | The changed account's `user_id`, encoded as its canonical UUID string. |
| `occurred_at` | RFC 3339 timestamp with an offset, emitted as UTC with `Z`. |
| `v` | Literal `1`. |

No account state and no credential hash appears in the event. Auth re-reads
current state through `api_users.credential_v1` when it needs state.

## Relay and consumption

The users relay appends with `XADD stream:users.v1.access_changed MAXLEN ~
10000 *`. Delivery is **at least once**. The only consumer group is `auth`;
each auth consumer deduplicates by `event_id`, ends all sessions for
`entity_id`, and acknowledges only after handling succeeds. Pending entries
are replayed on startup and entries idle for more than 60 seconds may be
claimed. An outbox row remains available for the seven-day replay window even
after relay.

## Compatibility

Changing the stream name, field set, field meaning or encoding, capture
operations, `entity_id` identity, or delivery guarantee is breaking. A
breaking change creates `stream:users.v2.access_changed` and
`users.access_changed.v2.md`; capture writes both versions until the `auth`
consumer group has moved. Adding state to this key-only event is also breaking
and is not permitted by the boundary convention.
