# ADR-0054 — What happens to the outbox rows the seed writes before the relays first start

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #597
**Supersedes:** — *(once accepted with any option below, ADR-0046's consequence "The outboxes hold the seed's own events" in part)*
**Superseded by:** —

---

## Context

A clean run of the three scripts leaves events in four outboxes, written by the
capture triggers ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md))
while `02_seed_30_per_table.sql` loads the owner tables: `categories_events.outbox` 30,
`products_events.outbox` 40, `stock_events.outbox` 150, `stores_events.outbox` 60
and `users_events.outbox` 0, 280 rows in all. The instance was rebuilt this way
on 2026-10-20, so the rows are there now. All have `relayed_at IS NULL`, so
when each service first starts, its relay publishes them to Redis Streams.
More outboxes will join as the remaining boundaries land (preferences, sales,
segments); sales alone would add about 600 rows (an INSERT and an UPDATE OF
`total` per seeded sale).

[ADR-0046](0046-service-owned-data-lives-in-service-schemas.md) exempts the
outboxes from the 30-row rule because they are runtime state, and the
ADR-0051 review kept that exemption on the argument that seeding an outbox
would publish invented events once its relay starts. The seed now does exactly
that, indirectly, so the argument and the behaviour disagree. ADR-0046 also
calls the result "harmless, since consumers only invalidate caches"; that
claim has not been checked against how consumers are created.

What is known:

- Consumer groups are created with `XGROUP CREATE <stream> <group> $ MKSTREAM`
  (ADR-0044), so a group sees only entries added after it exists. A consumer
  that starts after the relay publishes the seed events never reads them; one
  that starts before reads them once. Which of the two happens is a deployment
  ordering accident, not a design.
- A consumer's handling of these events is idempotent and re-reads state from
  the producer's views; for the cache streams the effect is one extra
  invalidation per entity. For `stream:users.v1.access_changed` the effect
  would be ending every session of the user, but the users outbox is empty
  today only because that stream captures UPDATE and DELETE and the seed only
  inserts users. That safety is incidental: a seed that updates a user would
  end a session on first start.
- The events describe the database as it was seeded, which is also the state
  every consumer reads on first start. They carry no information a cold cache
  does not already get.
- The seed's `occurred_at` is the load time. After a rebuild a relay started
  days later stamps genuine-looking old events into the streams.

Genuinely uncertain: whether a consumer will ever rely on a stream being
populated at first start. Nothing in ADR-0044 says so, and no story asks for it.

## Decision

*Proposed, not decided: the team chooses. The recommendation is option B1.*

## Alternatives considered

| Option | What it is | For | Against |
|---|---|---|---|
| A. Keep the events | No change. Relays publish the seed events on first start. This record states that as a deliberate choice, superseding ADR-0046's consequence in part | Nothing to build. Consumers that start first get a populated stream | The "populated stream" is not reliably delivered (groups start at `$`). Publishes events about changes nobody made; grows with every new outbox (about 600 more for sales). Whether the users stream is safe depends on seed order. Contradicts the reason the outboxes are exempt |
| B1. The seed ends by deleting the rows its own load captured | The last statements of `02_seed_30_per_table.sql` run `DELETE FROM <suffix>_events.outbox` for each outbox, one explicit statement per outbox, parameter-free | The three scripts leave every outbox empty, so the exemption means what it says. No relay or consumer change. No DDL, no superuser, no trigger toggling. A boundary adds its own line with its own outbox. The rule is checkable with one query | The seed knows each outbox by name; a new outbox that forgets its line is caught only by the check below. The live instance still holds its 280 rows until rebuilt or cleaned once |
| B2. The seed marks the rows relayed | Same place, `UPDATE ... SET relayed_at = now()` instead of `DELETE` | Keeps the rows as an apparent replay log | Records a publication that never happened. The hourly purge deletes them after 7 days anyway. Replay of a stream would hand a consumer events the relay never sent |
| B3. Load with the capture disabled | `SET session_replication_role = replica`, or `ALTER TABLE ... DISABLE TRIGGER` around the seed | Events are never written | Replica mode needs a superuser and also switches off every other trigger the seed relies on (guards, history, stock). `ALTER TABLE` is DDL outside `sql/01_schema.sql`, which AGENTS.md forbids |
| C. Relays skip what predates their first start | The kit's relay helper marks rows older than the service's first boot as relayed without publishing | Fixes it for every present and future outbox in one place, and also for a rebuild followed by a late start | Changes ADR-0044's relay and its replay guarantee (an outage longer than the cut would lose events), in code every service shares. "First start" needs durable state to define. Larger than the problem |

## Consequences

*If option B1 is accepted:*

**What this makes easy.** `SELECT count(*)` over all outboxes after a clean run
is 0, a check CI can run. The exemption in `sql/seed-exempt.txt` is consistent
with the seed. No relay publishes a fact nobody caused. F18 can deploy the
first service without a burst of replayed history.

**What this makes hard.** Each new outbox needs its own `DELETE` line in the
seed, in the same pull request that creates it. The instance's current 280 rows
are not removed by this record: either the database is rebuilt, or the operator
runs the same statements once before the first service starts, which is the
single hand-applied step "nothing else is applied by hand" would otherwise
forbid, so it needs the team's explicit approval in the issue.

**What must now be true elsewhere.**
- ADR-0046's consequence about the seed's own events is superseded in part; its
  status line gains `Partially superseded by ADR-0054`.
- `sql/02_seed_30_per_table.sql` ends with one `DELETE` per outbox.
- The CI step that runs the three scripts gains the query below.
- Each later boundary that adds an outbox adds its line and keeps the check green.

*If option A is accepted,* this record states the choice made on purpose and
that a first start publishes the seed. ADR-0046 and ADR-0044 are accepted and
stay as written (AGENTS.md): only ADR-0046's status line changes, as under B1.

## Compliance

After the three scripts on an empty database, this returns 0 (extended with each
new outbox):

```sql
SELECT (SELECT count(*) FROM categories_events.outbox)
     + (SELECT count(*) FROM products_events.outbox)
     + (SELECT count(*) FROM stock_events.outbox)
     + (SELECT count(*) FROM stores_events.outbox)
     + (SELECT count(*) FROM users_events.outbox);
```

A service created later is checked by its own PR: a clean run followed by the
same count for its outbox.
