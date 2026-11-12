# `stream:engagement.v1.interaction_recorded`

## Contract

| Field | Value |
|---|---|
| Stream | `stream:engagement.v1.interaction_recorded` |
| Producer | engagement |
| Captured collection | MongoDB `engagement.engagement_event` (ADR-0038), not a PostgreSQL table |
| Captured operations | A new event of a carried kind (`RECORDED`), and the void of one (`VOIDED`) |
| Outbox | `relay_pending` on the event document itself (ADR-0075) |
| Private wake-up channel | None: the relay polls every second |
| Consumer groups | `experimentation`, `recommendations` |
| Retention | `XADD MAXLEN ~ 10000` |
| Delivery | At least once |

A standalone `mongod` cannot write an event and a second document atomically,
so the outbox lives on the event (ADR-0075). The insert of a carried kind
writes `relay_pending: ["RECORDED"]` in the same document. A void appends
`"VOIDED"` in the same single-document update that sets `voided` and adds the
`history` entry. An insert or void that fails therefore announces nothing.

Carried kinds: `RECO_IMPRESSION`, `RECO_CLICK` and `CAMPAIGN_EXPOSURE`, whether
recorded through `/v1` or `/v2`. `QR_SCAN`, `STORE_VISIT`, `CART_ADD`,
`CART_REMOVE` and `NAVIGATION` are never published: no consumer acts on them.
None of these entries is a sale; a confirmed transaction exists only in
sales-service.

## Entry fields

**This stream carries the fact, not only keys.** It is the one exception to
the key-only rule of `README.md` (ADR-0075): engagement publishes no PostgreSQL
view to re-read, so the fact is all a consumer can get. The fact holds no
names, emails or coordinates.

A field whose value is absent on the event is left out of the entry, since a
stream field cannot be null.

| Field | Encoding and meaning |
|---|---|
| `event_id` | `<id>:recorded` or `<id>:voided`, where `<id>` is the engagement event's UUID (`event_id` in the API); the deduplication key. |
| `v` | The literal `1`. |
| `notice` | `RECORDED` or `VOIDED`. |
| `type` | `RECO_IMPRESSION`, `RECO_CLICK` or `CAMPAIGN_EXPOSURE`. |
| `store_id` | Base-10 store id. |
| `product_id` | Base-10 product id. Present on `RECO_IMPRESSION` and `RECO_CLICK`. |
| `customer_id` | Lower-case UUID. Always present on `CAMPAIGN_EXPOSURE`; on the other two only when the client sent one. |
| `campaign_id` | Base-10 campaign id. Present on `CAMPAIGN_EXPOSURE` only. |
| `label_code` | The segment label of the recommendation. Present on `RECO_IMPRESSION` and `RECO_CLICK`. |
| `rank` | Base-10 position of the recommendation, from 1. Present on `RECO_IMPRESSION` and `RECO_CLICK`. |
| `occurred_at` | When the interaction happened: RFC 3339, UTC with `Z`, millisecond precision. |

A `VOIDED` entry repeats the fact of the event it voids, so a consumer that
never saw the `RECORDED` entry still knows what to stop counting.

## Consumer behaviour

| Group | Effect |
|---|---|
| `experimentation` | Projects `CAMPAIGN_EXPOSURE` into its own schema as the exposures of a campaign per customer, and ignores the other kinds. A `VOIDED` notice removes the exposure from the count. An exposure is never a conversion. |
| `recommendations` | Projects `RECO_IMPRESSION` and `RECO_CLICK`, the interactions with its own recommendations, into its own schema, and ignores `CAMPAIGN_EXPOSURE`. A `VOIDED` notice removes the interaction from the count. |

analytics-kpis never consumes this stream (ADR-0059): it reads the owner of
each projection through that owner's `api_*` view.

Each group deduplicates by `event_id` before acknowledging an entry. On restart
it handles pending entries first, may claim entries idle for more than 60
seconds, and acknowledges only after its effect succeeds. After five failed
deliveries it logs and acknowledges the entry. Notices apply idempotently: a
`VOIDED` can arrive before its `RECORDED` after a redelivery, and the consumer
keeps the void (ADR-0075). A projection is eventually consistent, within
seconds: the relay polls every second.

## Propagation bound

The time from the write that sets the event's `relay_pending` notice to the
group's acknowledgement of its entry. `ops/propagation/propagation.py check`
fails a run whose p95 or maximum exceeds the figure below, or that leaves an
entry unacknowledged (F20-06, #764). The measured figures are in
`docs/evidence/f20-06-stream-propagation.md`.

| Group | p95 (ms) | Max (ms) |
|---|---:|---:|
| `experimentation` | 3000 | 5000 |
| `recommendations` | 3000 | 5000 |

Not measured yet: `experimentation`, `recommendations`.
Their bound is the default, and it holds from the first run that measures it.

## Failure and compatibility

The relay is a thread of the engagement container. Every second it reads up to
100 events whose `relay_pending` is non-empty, in `occurred_at` order, through
a partial index, and publishes their notices through the kit's
`StreamPublisher` (`XADD`, then one `WAITAOF 1 0 2000`). Only then does it
`$pull` the published notices. A crash between the two duplicates an entry but
cannot lose one, and a relay or Redis outage leaves the notices on the
documents until it returns. The request path never runs `XADD`.

There is no seven-day replay log: a relayed notice is removed from its
document. A consumer that falls behind the stream's retention, or a consumer
deployed later, whose group starts at `$`, is brought up to date by a
maintenance re-queue of a day range (ADR-0075); consumers dedupe the
redelivered entries by `event_id`.

Removing a field, changing a field's meaning or encoding, or changing how
`event_id` is formed is breaking. So is publishing a kind a group does not
expect to ignore. Adding a carried kind or an optional field is not breaking,
because every group ignores kinds and fields it does not use. A breaking
revision creates `stream:engagement.v2.interaction_recorded` and a matching
`.v2.md` contract, and the relay writes both until both groups have moved.
