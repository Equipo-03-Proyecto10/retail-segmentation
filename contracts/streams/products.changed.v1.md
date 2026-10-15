# `stream:products.v1.changed`

The **products** boundary publishes this key-only Redis Stream whenever the
product source of truth changes. It invalidates derived caches; it never carries
product state.

## Capture

An AFTER row trigger captures `INSERT`, `UPDATE`, and `DELETE` on `product` by
any writer. That includes products-service changes, frozen-web edits, and web or
maintenance hard deletes. The SECURITY DEFINER capture function writes the
transactional `products_events.outbox` and wakes only the owner's relay on the
private `products_outbox` channel. A rolled-back product change therefore
publishes no event.

The relay appends with `XADD stream:products.v1.changed MAXLEN ~ 10000 *` only
after reading the committed outbox row. Each entry has exactly these fields:

| Field | Encoding and meaning |
|---|---|
| `event_id` | UUID string generated for the outbox row; the deduplication key. |
| `event_type` | PostgreSQL `TG_OP`: `INSERT`, `UPDATE`, or `DELETE`. |
| `entity_id` | The changed row's `product_id`, encoded as an unsigned base-10 integer string. For `DELETE`, it comes from `OLD.product_id`; otherwise it comes from `NEW.product_id`. |
| `occurred_at` | RFC 3339 timestamp with an offset, emitted with `Z`. |
| `v` | The integer `1`. |

Consumers re-read current state through a versioned database contract when
they need it. In particular, the stream does not contain a SKU, name, category,
price, active flag, image path, or before/after document.

## Consumer groups and delivery

The consumer groups are:

- `products`, which invalidates products-service's own read cache, including
  entries made stale by web edits or hard deletes;
- `recommendations`, which invalidates recommendation inputs derived from
  product state.

Delivery is at least once. A consumer deduplicates by `event_id` before
acknowledging. On startup it handles its pending entries, claims entries idle
for more than 60 seconds, and then reads new entries. After five failed
deliveries an entry is logged and acknowledged; the producer's relayed outbox
row remains in the seven-day replay window. The approximate stream cap is
10,000 entries.

## Compatibility

Changing the stream key, captured table or operations, field names or
encodings, `entity_id` meaning, version value, or delivery promise is breaking.
A breaking revision creates `stream:products.v2.changed` and
`contracts/streams/products.changed.v2.md`; capture writes both versions until
the `products` and `recommendations` groups have moved.
