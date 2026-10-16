# Datastore designs

> [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md)
> and [ADR-0038](../adr/0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md)
> govern Redis and MongoDB. Where those records and these documents disagree,
> the records win.

**Redis is specified and its instances are built.** F13-05 (#424) rewrote
[`redis-design.md`](redis-design.md) as ADR-0037's specification and built
`redis-state` and `redis-cache` in [`deploy/infra/`](../../deploy/infra/README.md).
Only the services connect to them, and none does yet.

> **The MongoDB document is still a design, and nothing in it runs.** MongoDB
> will hold only engagement-service's collections (`engagement_event` and
> `engagement_daily`, in the database `engagement`); the six collections the
> document lists duplicate PostgreSQL tables and are not built. F13-06 (#428)
> rewrites it as a specification. Why it was documented before it was built:
> [ADR-0005](../adr/0005-document-mongodb-and-redis-designs-without-implementing-them.md).

| Document | What it covers |
|---|---|
| [MongoDB](mongodb-design.md) | A design, pending F13-06: collections for consumption profiles, RFM snapshots, segment migrations, clustering runs, recommendations and experiment events |
| [Redis](redis-design.md) | The two instances, every key family with its type and TTL, the per-service ACL rules, the failure behaviour and the checks |

The PostgreSQL model — the one that exists — is
[`docs/data-model.md`](../data-model.md).
