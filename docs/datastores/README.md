# Datastore designs

> **Decided, not yet built.** [ADR-0037](../adr/0037-redis-holds-sessions-cache-limits-and-events-for-the-services-only.md)
> and [ADR-0038](../adr/0038-mongodb-holds-customer-activity-owned-by-activity-service-alone.md)
> now govern Redis and MongoDB. Where those records and these designs
> disagree, the records win. In particular:
> - Redis key names follow ADR-0037's namespace.
> - MongoDB holds only `customer_activity`; the six collections below
>   duplicate PostgreSQL tables and are not built.
>
> F13-05 (#424) and F13-06 (#428) rewrite these documents as specifications
> and remove the banner below.

> **Nothing in this directory is implemented, and nothing in it runs.** The
> delivery has one database engine, PostgreSQL, on one instance
> ([ADR-0001](../adr/0001-flask-monolith-on-a-single-vm.md)). These documents
> record designs that belong to later deliveries, so that the work already done
> on them is not redone from scratch — and so that a reader does not mistake a
> design for a component.
>
> Why they are documented but not built: [ADR-0005](../adr/0005-document-mongodb-and-redis-designs-without-implementing-them.md).

| Document | What it designs |
|---|---|
| [MongoDB](mongodb-design.md) | Collections for consumption profiles, RFM snapshots, segment migrations, clustering runs, recommendations and experiment events |
| [Redis](redis-design.md) | Keys and TTLs for sessions, caching, counters, distributed locks and temporary data |

The PostgreSQL model — the one that exists — is
[`docs/data-model.md`](../data-model.md).
