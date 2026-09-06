# Redis — design

**Status: design only. Not implemented, not installed, not connected to.**
See [ADR-0005](../adr/0005-document-mongodb-and-redis-designs-without-implementing-them.md).

This design covers the performance and security infrastructure of a later,
multi-client delivery: sessions, caching, counters, distributed locks and
temporary data.

> Parts of it assume things this delivery does not have. It describes JWTs and
> a revocation list, whereas F3-03 (#63) authenticates with server-side
> sessions; it describes rate limiting an API, and this delivery exposes no API
> (`docs/scope.md` C-1); it describes mobile and desktop clients, which are out
> of scope. Read it as the design for the delivery it was written for, not as a
> plan for this one.

## Keys and TTLs

| Use | Key pattern | Redis type | TTL |
|---|---|---|---|
| User session | `session:user:{user_id}` | `HASH` (token, role, device, logged_in_at) | 30 min renewable; 7 days for a mobile "remember me" |
| Revoked token | `token:revoked:{jti}` | `STRING` (`"1"`) | The original token's remaining lifetime |
| Customer's current segment | `cache:segment:customer:{customer_id}` | `STRING` (JSON) | 10 min |
| Product catalog per store | `cache:catalog:store:{store_id}` | `STRING` (JSON) | 15 min |
| Inventory availability | `cache:inventory:{store_id}:{product_id}` | `STRING` (int) | 2 min — very volatile |
| Coupon activations | `counter:coupon:{campaign_id}` | `STRING` (`INCR`) | None, or expires with the campaign |
| API rate limit | `ratelimit:api:{user_id}` | `STRING` (`INCR`) | 1 min sliding window |
| Segment recalculation lock | `lock:segmentation:customer:{customer_id}` | `STRING` (`SETNX`) | 30 s |
| Clustering job lock | `lock:job:clustering:{model_id}` | `STRING` (`SETNX`) | 5 min |
| Login OTP | `otp:login:{phone_or_email}` | `STRING` | 5 min |
| Unconfirmed preferences | `temp:preferences:{customer_id}` | `HASH` | 24 h |
| Async job progress | `job:status:{job_id}` | `HASH` (progress, state) | 1 h after completion |

## Sessions

On login a token is issued and mirrored into `session:user:{user_id}` with its
metadata — device, role, IP. The mirror is what makes "sign out everywhere"
possible from the administrator panel: without it, a token stays valid until it
expires no matter what the panel says. Short TTL on web, longer on a mobile
client with a refresh token.

## Revocation list

On logout or when access is withdrawn, `token:revoked:{jti}` is written with a
TTL equal to the token's own remaining lifetime. The list therefore cleans
itself and cannot grow without bound — a revocation list without that TTL is a
memory leak with a security label on it.

## Caching

Cached reads are the frequent and expensive ones: a customer's current segment,
which otherwise costs a PostgreSQL query plus a MongoDB lookup on every
request; the catalog; availability. **Transactions are never cached.** A stale
sale is a wrong sale.

## Counters

Promotion activations, recommendation clicks, and failed login attempts for
temporary account lockout — all through `INCR`/`EXPIRE`, which keeps
usage metrics out of PostgreSQL's write path.

## Distributed locks

Incremental profile updates and concept-drift detection can both be triggered by
concurrent events — two near-simultaneous transactions from the same customer.
The lock stops two instances recomputing and writing the same profile at once.

## Temporary data

OTPs, unconfirmed preferences, clustering progress, bulk import state. All
carry a TTL, and none of it is ever the source of truth.
