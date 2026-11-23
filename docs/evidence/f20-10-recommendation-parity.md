# F20-10: recommendations parity after a change, within the propagation bound

Story F20-10 ([#768](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/768)).
This report shows that a customer's recommendations from the web and from
recommendations-service agree again after each of four changes. Each time,
they agree within the bound of the stream that carries the change. It also
shows that the service's 120 s result cache never serves the answer from
before the change. The cases are in
[`tests/parity/test_recommendation_parity.py`](../../tests/parity/test_recommendation_parity.py).

| Environment | Status |
|---|---|
| Local stack (`compose.distributed.yaml`) | **Measured** on 2026-11-23. The figures are below. |
| GCP instance | **Not run.** The team is not using the instance for now. The same command runs there once #764's instance measurement is done. |

## What each case does

The web computes the recommendations from PostgreSQL on every request
(`/catalog/customers/<id>/recommendations`, `Cache-Control: no-store`). The
service computes the same rule (RN-40) through the `api_*` views and caches
each answer for 120 s. The cache is emptied by the eight streams the service
consumes (`services/recommendations/app/config.py`). So the web is right at
once, and the service is right once the event reaches it.

Each case follows the same steps:

1. Both paths agree. The service's answer is cached: two reads 0.2 s apart
   return the same `computed_at`, which has microsecond precision, so only a
   cache hit repeats it.
2. One change is made, chosen so that it changes the web's answer.
3. The service is read until it agrees with the web: same status, same
   products in the same order, and the same units on hand for each one. This
   must happen within the bound.
4. Once the bound has passed, one more read must agree with the web, and its
   `computed_at` must be later than the change. That is acceptance criterion 2:
   the next read after the bound is never the stale cached result.

| Change | How it is made | Stream | Bound (max, `recommendations` group) |
|---|---|---|---:|
| Preference | `PUT /v1/preference-profiles/{id}/interest-categories` adds the category of an eligible product that no signal chose | `stream:preferences.v1.changed` | 5000 ms |
| Sale | `POST /v1/sales`: the customer buys a recommended product at their usual store | `stream:sales.v1.recorded` | 5000 ms |
| Stock | `POST /v1/stock-movements` `COUNT` to 0 of a recommended product at the usual store | `stream:stock.v1.changed` | 5000 ms |
| New run | The web's `/segment-run/` form | `stream:segments.v1.run_recorded` | 5000 ms |

Each bound is read when the case runs, from the `## Propagation bound` table of
that stream's contract in [`contracts/streams/`](../../contracts/streams/)
(F20-06, #764, PR #961). The tests hold no copy of the figure. F20-06 measured
these bounds under load. Here each change is a single event.

The customer is the first seeded one who meets all of these:
- `PERSONALIZATION` is granted and no category is restricted. The web reads
  neither consent nor restrictions, so for any other customer the two paths
  differ by design (ADR-0066).
- They have 2 to 7 recommendations.
- Some eligible product is chosen by no signal yet.

On the fresh seed this was customer `…0014`.

## Local stack

Setup:
- An Apple M2 laptop running Docker Engine 29.7.2.
- An isolated compose project, `p768`, rendered from `compose.distributed.yaml`
  with its own network, volumes and ports. It ran on `develop` `4d5fd0cc` plus
  this branch.
- The services: auth, sales, stock, customer-preferences, segments and
  recommendations, with the infrastructure they need. Every boundary section
  was re-applied as `nightly.yml` does.
- The web ran on the host with gunicorn, against the stack's PostgreSQL.

Run 2, on fresh volumes, passed 4 of 4 with no difference:

| Change | Service agreed after | Outbox commit → relayed | Before (cached) `computed_at` | First read after the bound `computed_at` |
|---|---:|---:|---|---|
| Preference | 46 ms | 9 ms | 17:41:58.942254Z | 17:41:59.447946Z |
| Sale | 56 ms | 14 ms | 17:41:59.447946Z | 17:42:05.803410Z |
| Stock | 61 ms | 9 ms | 17:42:05.803410Z | 17:42:11.135055Z |
| New run | 60 ms | 35 ms | 17:42:11.135055Z | 17:42:16.579341Z |

How the columns were measured:
- **Agreed after:** from the write's answer (which follows its commit) to the
  first read of the service that agreed with the web.
- **Relayed:** `relayed_at - occurred_at` of the change's row in its owner's
  outbox.

The service's log confirms the cache behaviour. Before each change, the
service logged `Served cached decision` for the earlier reads. After each
change, the first read was a cache miss, computed again from the views. The sale also wrote a
`stream:stock.v1.changed` row (stock is consumed by the sale), and that row
empties the same cache.

The golden fixtures stayed green on the same stack before the cases wrote
anything: `services/recommendations/tests/test_golden.py`, 34 passed, with
`MOSAIQ_REQUIRE_POSTGRES=1`.

### The first run, and why its run case failed

Run 1 failed 3 of 4. Each failure was a fault in the test, and the service was
not at fault in any of them:

- **Preference:** the chosen customer had no eligible product to add. The
  customer is now chosen only if such a product exists.
- **Sale:** the `STORE_MANAGER` account is not assigned to the customer's
  usual store, so `403 forbidden_store`. The sale is now made as `ADMIN`.
- **New run:** reported as stale, but it was not. The test compared the
  host's clock with `computed_at`, and during that run Docker's VM clock was
  about 92 s behind the host's. The service log shows the answer was computed
  after the change. The change time is now read from PostgreSQL
  (`clock_timestamp()`), which runs on the same clock as the service.

So no product change was needed. The cache is emptied by every writer of the
four changes, the web included, because the capture triggers in
`sql/01_schema.sql` run for every writer. A new run also gives a new cache key,
because the key names the run behind the customer's label.

## What these cases do not cover

- **The golden fixtures** are static: they capture the web's answers once, at
  the seed's anchor. They cannot express:
  - a change, or the time it takes to arrive;
  - the cache;
  - consent and restrictions, which the web does not read (`service_differs`).

  Those are covered by the cases in this report.
- **Which path makes each change.** The preference, sale and stock changes are
  made through the services. The web's own writers for them (the CSV sales
  import, the administrator's forms) fire the same capture triggers, but these
  cases do not exercise them.
- **`/v2`** is not compared, because the web has no counterpart. Neither are
  the streams for products, categories, consent and assignments, which are
  outside #768's four changes.
- **Load.** F20-06 measured the bounds under load. These cases make one change
  at a time.
