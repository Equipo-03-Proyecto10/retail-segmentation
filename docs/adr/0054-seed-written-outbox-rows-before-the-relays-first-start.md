# ADR-0054 — What happens to the outbox rows the seed writes before the relays first start

**Status:** Accepted (2026-10-21)
**Owner:** Marcelo
**Issue:** #597
**Supersedes:** [ADR-0046](0046-service-owned-data-lives-in-service-schemas.md) in part (publication of seed-written outbox events only)
**Superseded by:** —

---

## Context

A clean run of the three scripts leaves events in four outboxes, written by the
capture triggers ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md))
while `02_seed_30_per_table.sql` loads the owner tables: `categories_events.outbox` 30,
`products_events.outbox` 40, `stock_events.outbox` 150, `stores_events.outbox` 60
and `users_events.outbox` 0, 280 rows in all. The instance was rebuilt this way
on 2026-10-20, so these were the rows reported by the issue. All have `relayed_at IS NULL`, so
when each service first starts, its relay publishes them to Redis Streams.
The current schema also includes preferences, sales and segments: all eight
outboxes are covered by this decision. The counts above describe the original
2026-10-20 observation, not the current state of the running database.

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

**Accept B1.** The seed deletes every row in each of the eight outboxes after
loading the demonstration data and **before its existing `COMMIT`**. Capture
triggers and integrity checks remain enabled throughout. Until that commit,
relays cannot see the seed's uncommitted events; after it, none remain.

This is exclusively an empty-database bootstrap operation. The seed is not a
maintenance or live-cleanup script and must not run against an in-use database.
Relays continue to publish every pending runtime event regardless of its age.

The requester accepted B1 and authorized its implementation and the verified
live cleanup on 2026-10-21. For the existing instance only, that authorization
permits a one-time data cleanup: identify the seed transaction from its exact
`occurred_at` and corroborating audit records, archive the candidate event rows,
then delete only those event IDs while still unrelayed, in one transaction.
Verify expected counts and preserve every other event, including later user
access changes. If provenance or counts cannot be established, do not delete.
This narrowly scoped exception does not authorize a blanket live outbox purge
or a database rebuild.

## Alternatives considered

| Option | What it is | For | Against |
|---|---|---|---|
| A. Keep the events | No change. Relays publish the seed events on first start. This record states that as a deliberate choice, superseding ADR-0046's consequence in part | Nothing to build. Consumers that start first get a populated stream | The "populated stream" is not reliably delivered (groups start at `$`). Publishes events about changes nobody made; grows with every new outbox (about 600 more for sales). Whether the users stream is safe depends on seed order. Contradicts the reason the outboxes are exempt |
| B1. The seed ends by deleting the rows its own load captured | The last statements of `02_seed_30_per_table.sql` run `DELETE FROM <suffix>_events.outbox` for each outbox, one explicit statement per outbox, parameter-free | The three scripts leave every outbox empty, so the exemption means what it says. No relay or consumer change. No DDL, no superuser, no trigger toggling. A boundary adds its own line with its own outbox. The rule is checkable with one query | The seed knows each outbox by name; a new outbox that forgets its line is caught only by the check below. The live instance still holds its 280 rows until rebuilt or cleaned once |
| B2. The seed marks the rows relayed | Same place, `UPDATE ... SET relayed_at = now()` instead of `DELETE` | Keeps the rows as an apparent replay log | Records a publication that never happened. The hourly purge deletes them after 7 days anyway. Replay of a stream would hand a consumer events the relay never sent |
| B3. Load with the capture disabled | `SET session_replication_role = replica`, or `ALTER TABLE ... DISABLE TRIGGER` around the seed | Events are never written | Replica mode needs a superuser and also switches off every other trigger the seed relies on (guards, history, stock). `ALTER TABLE` is DDL outside `sql/01_schema.sql`, which AGENTS.md forbids |
| C. Relays skip what predates their first start | The kit's relay helper marks rows older than the service's first boot as relayed without publishing | Fixes it for every present and future outbox in one place, and also for a rebuild followed by a late start | Changes ADR-0044's relay and its replay guarantee (an outage longer than the cut would lose events), in code every service shares. "First start" needs durable state to define. Larger than the problem |

## Consequences

**What this makes easy.** `SELECT count(*)` over all outboxes after a clean run
is 0, a check CI can run. The exemption in `sql/seed-exempt.txt` is consistent
with the seed. No relay publishes a fact nobody caused. F18 can deploy the
first service without a burst of replayed history.

**What this makes hard.** Each new outbox needs its own `DELETE` line in the
seed, in the same pull request that creates it. The database test discovers
outboxes from the PostgreSQL catalog, so forgetting a new boundary fails CI.
Existing live events require the separately scoped cleanup above; accepting
this record alone does not remove them.

**What must now be true elsewhere.**
- ADR-0046's consequence about the seed's own events is superseded in part; its
  status line gains `Partially superseded by ADR-0054`.
- `sql/02_seed_30_per_table.sql` ends with one `DELETE` per outbox.
- The CI database job checks every discovered outbox after the three scripts
  and proves that a subsequent web write still captures an event transactionally.
- Each later boundary that adds an outbox adds its line and keeps the check green.

## Compliance

Run `pytest tests/test_seed_outboxes_postgres.py -m postgres` against an empty
database loaded with the three scripts, with `MOSAIQ_REQUIRE_POSTGRES=1` and
`MOSAIQ_TEST_OWNER_URL` set. The database CI job runs this check immediately
after installing its Python dependencies, before other database tests.

The test discovers all `*_events.outbox` tables, requires every exact count to
be zero, and exercises a product update as `retail_app`: its event is pending
inside the writing transaction, invisible to another connection, and removed
by rollback. The seed cleanup must not disable ordinary capture.
