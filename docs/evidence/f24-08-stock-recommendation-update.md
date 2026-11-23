# F24-08 (#831): a stock change, and the recommendation that follows it

> **Status: not yet run, locally or on the instance.** The local run of
> criteria 1 to 3 is still to come ([local run](#local-run)). Criterion 4, "the same
> result on the deployed instance", is **pending**: the team agreed not to touch
> the instance for now, and only its operator can start it. This document is
> the runbook for that run, and every `‹fill: …›` below is filled during it.

Governing decisions:
[ADR-0067](../adr/0067-a-recommendation-is-judged-at-a-reference-store-and-channel-and-its-explanation-is-recorded.md)
(F24-02's record: the reference store, availability, exclusions and the recorded
decision),
[ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)
(stock reaches recommendations only through `api_stock.*_v1` and
`stream:stock.v1.changed`),
[ADR-0061](../adr/0061-stock-keeps-one-quantity-per-store-and-changes-only-through-a-sale-a-reversal-or-a-recorded-movement.md),
[ADR-0084](../adr/0084-a-paused-or-discontinued-product-is-not-sellable-and-the-elsewhere-store-is-the-nearest-by-great-circle-distance.md).

## The criteria, and where each is answered

| Criterion of #831 | Answered by |
|---|---|
| 1. A movement or a sale leaves a recommended product at zero in a store; the next read after the propagation bound no longer offers it, and the measured delay is in the evidence | `f24_08 cycle`, its first half: a `COUNT` to zero, then reads every 100 ms until the product is no longer offered at the store, and one more read once the bound has passed |
| 2. After a restock, once the bound has passed, it can be offered again | `f24_08 cycle`, its second half: a `RECEIPT` of the counted quantity, measured the same way |
| 3. A discontinued product and a product not authorized for the customer are both excluded, and the decisions say why | `f24_08 exclusions`: read-only, the answer and its stored decision |
| 4. The same result on the deployed instance | **Pending**: [On the instance](#on-the-instance) |

The collector is [`tests/acceptance/f24_08.py`](../../tests/acceptance/f24_08.py);
its pure parts are pinned in
[`tests/test_acceptance_evidence.py`](../../tests/test_acceptance_evidence.py).
No product code changes for this story, and no schema.

### What "the bound" is

The collector writes no figure of its own. It reads the `recommendations` row of
the `## Propagation bound` table in
[`contracts/streams/stock.changed.v1.md`](../../contracts/streams/stock.changed.v1.md),
merged by F20-06
([#764](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/764),
[`f20-06-stream-propagation.md`](f20-06-stream-propagation.md)), with
`ops/propagation/propagation.py`'s own parser. Today the bound is **p95 3000 ms,
max 5000 ms**. It runs from the commit of stock's outbox row to the
`recommendations` group's acknowledgement, and the consumer acknowledges only
after it has emptied the cache (`services/recommendations/app/service.py`,
`_start_consumers`). So a read sent once the maximum has passed cannot be served
an answer cached before the movement. The "read after the bound" is sent at
least `max` ms after the movement's 201.

### What "offered" means

`GET /v2/recommendations?customer_id=…&store_id=…` answers items of three kinds
(ADR-0067):

| Kind | Counts as offered at the store? |
|---|---|
| `primary` | yes |
| `alternative` (a substitute sold here) | yes |
| `elsewhere` (the product held at the nearest *other* store, with `unavailable_reason`) | **no**: it tells the seller the product is not here |

A product left at zero normally comes back as an `elsewhere` entry with
`unavailable_reason: out_of_stock` when another store holds it. The collector
records that entry in the summary and counts it as **not offered at this
store**. That reading was agreed with the acceptance lead before the run.

### What the delay measures

On the collector's monotonic clock, from the moment the movement's `201` is
received to the moment the first read that has changed (no longer offers the
product, or offers it again) is received. It overstates the propagation by at
most one poll interval plus one read; the summary also gives when the last
unchanged read was sent, so the true figure lies between the two. Every read is
recorded, with its `decision_id`, in `exchanges.md`, and the delays in
`delays.json`.

### Why the reads are warmed first

`/v2` answers are cached for 120 s on redis-cache. The collector reads the
recommendation twice before the movement and checks the second answer is served
from the cache (the same `decision_id`). Without the stream, the change would
only be seen when that entry expired, up to 120 s later. A delay under the bound
is therefore the stream emptying the cache, not a cold read.

### Writes

Only the two movements, through stock-service as an `INVENTORY_PLANNER`, each
with its own `Idempotency-Key`: a `COUNT` to zero and a `RECEIPT` of the same
quantity. On-hand ends where it started. Movements are append-only (ADR-0061):
both stay in `stock.stock_movement`, with the note `F24-08 (#831)`. Every query
runs in a read-only transaction with bind parameters.

Criterion 3 needs a discontinued product. The seed has none: every seeded
product is active (`sql/02_seed_30_per_table.sql`, `product`). The local run
discontinued one through products-service as a precondition (below). On the
instance, see step 0.3.

## Local run

> **Local, not the acceptance of criterion 4.** It ran on a throwaway stack,
> not on the instance.

‹results›

## On the instance

**Pending.** Run it only once the operator has started the instance and the team
has agreed. Nothing here has run there.

### Who does what

| Step | Who | Where |
|---|---|---|
| 0. Preconditions | Acceptance lead (Raquel) | workstation |
| 1. `cycle` (criteria 1 and 2) | Instance operator (root), with the acceptance lead | the instance |
| 2. `exclusions` (criterion 3) | Instance operator, with the acceptance lead | the instance |

### 0. Before the run

1. **The identified version**: the commit every service is deployed at, the
   same one as F29-03 ([f29-03-transaction-path.md](f29-03-transaction-path.md)
   step 0). The collector records each service's served contract version.
2. **The tree on the instance** at that commit, readable by `postgres`, with a
   virtualenv holding PyYAML (as for F18-04,
   [tests/negative/README.md](../../tests/negative/README.md) "The instance").
3. **A discontinued product** for criterion 3. Look for one first; it is
   read-only:

   ```sh
   runuser -u postgres -- psql -d retail -c \
     "SELECT product_id, sku, commercial_status FROM products.product_status
      WHERE commercial_status = 'discontinued' ORDER BY product_id"
   ```

   When there is none, discontinuing one is a business change, not a test
   movement. It needs the team's agreement and the administrator
   (`PATCH /api/products/v1/products/{id}/details` with
   `{"commercial_status": "discontinued"}`). Run step 2 first, without
   `--before-decision`, if nobody agrees, and record that the product was
   already discontinued.
4. **Two accounts**: an `ANALYST` to read, and an `INVENTORY_PLANNER` to record
   the two movements. Their credentials go into the shell's environment, read at
   a prompt; they are never arguments.
5. **The customer and the store.** A customer with `PERSONALIZATION` granted, an
   open segment assignment and a restricted category, and a store whose
   recommendation for them has at least one `primary` item. Pick them with a read
   of `/v2/recommendations` (the summary of the local run shows what one looks
   like). The `cycle` takes the first primary item unless `--product` is given.

### The procedure

On the instance, as root, from the tree of item 0.2:

```sh
cd <the tree> && . .venv/bin/activate
read -r MOSAIQ_ACCEPTANCE_EMAIL; read -rs MOSAIQ_ACCEPTANCE_PASSWORD
read -r MOSAIQ_PLANNER_EMAIL; read -rs MOSAIQ_PLANNER_PASSWORD
export MOSAIQ_ACCEPTANCE_EMAIL MOSAIQ_ACCEPTANCE_PASSWORD \
  MOSAIQ_PLANNER_EMAIL MOSAIQ_PLANNER_PASSWORD
export MOSAIQ_BASE_URL=https://127.0.0.1 MOSAIQ_HOST=mosaiq.maxthecoder.online \
  MOSAIQ_INSECURE_TLS=1 MOSAIQ_DB_EXEC="runuser -u postgres --"
RUN=docs/evidence/f24-08/$(date -u +%Y%m%dT%H%MZ)
```

#### 1. Criteria 1 and 2

```sh
python -m tests.acceptance.f24_08 cycle --out "$RUN/cycle" \
  --customer <uuid> --store <id>
```

It holds when:
- before the movement the product is a primary item, read twice (the second
  from the cache), and its quantity agrees with stock-service and `inventory`;
- the `COUNT` to zero is `201`; the first read that no longer offers the product
  arrives within the bound's maximum, and that is the measured delay;
- the read after the bound does not offer it, its decision counts one more
  `out_of_stock`, the decision read back is the answer's, and stock-service and
  `inventory` read 0;
- the `RECEIPT` is `201`; the product is offered again within the maximum, and
  after the bound it is a primary item with the restocked quantity.

#### 2. Criterion 3

```sh
python -m tests.acceptance.f24_08 exclusions --out "$RUN/exclusions" \
  --customer <uuid> --store <id> --discontinued <product id> \
  [--before-decision <decision id of a read made while it was active>]
```

Read-only. It holds when the product reads `is_active` false; the customer has a
restricted category with products in stock at the store; neither the
discontinued product nor any restricted product is among the items (of any
kind); and the decision, as answered and as stored, counts `not_sellable` and
`PERSONALIZATION_WITHDRAWN`. With `--before-decision`, the earlier decision
offered the product and the later one counts one more `not_sellable`.

### The record

| Item | Value |
|---|---|
| Date (UTC) | ‹fill› |
| Identified version | ‹fill: commit, and the contract versions from `summary.md`› |
| Customer, store, product | ‹fill› |
| Delay to "not offered" | ‹fill› ms (bound max 5000 ms) |
| Delay to "offered again" | ‹fill› ms |
| `exclusions` | ‹fill: checks held› |

Commit `summary.md`, `exchanges.md`, `rows.md`, `record.json` and `delays.json`
of each `$RUN/*` with the filled record.
