# Datastore designs

> [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md)
> and [ADR-0038](../adr/0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md)
> govern Redis and MongoDB. Where those records and these documents disagree,
> the records win.

**Redis is specified and its instances are built.** F13-05 (#424) rewrote
[`redis-design.md`](redis-design.md) as ADR-0037's specification and built
`redis-state` and `redis-cache` in [`deploy/infra/`](../../deploy/infra/README.md).
Only the services connect to them, and none does yet.

**MongoDB is specified and its server is built.** F13-06 (#428) rewrote
[`mongodb-design.md`](mongodb-design.md) as ADR-0038's specification and added
the server to the same project. It holds no application database until
engagement-service's own story, F14-24 (#466), creates `engagement`; only that
service will connect.

| Document | What it covers |
|---|---|
| [MongoDB](mongodb-design.md) | The server and its accounts, the one database `engagement` and its two collections, the operations, live counters, failure behaviour and checks |
| [Redis](redis-design.md) | The two instances, every key family with its type and TTL, the per-service ACL rules, the failure behaviour and the checks |

The PostgreSQL model is [`docs/data-model.md`](../data-model.md). Its §7 also
records the two MongoDB collections, and why they sit outside the relational
model.
