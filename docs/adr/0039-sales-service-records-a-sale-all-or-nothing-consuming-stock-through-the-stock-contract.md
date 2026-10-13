# ADR-0039 — sales-service records a sale all or nothing, consuming stock through the stock contract

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** [ADR-0020](0020-csv-is-the-sole-sales-ingestion-entry-point-for-this-delivery.md), in part: its "sole entry point" clause and its "one implementation of the rules" clause
**Superseded by:** —

---

## Context

[ADR-0020](0020-csv-is-the-sole-sales-ingestion-entry-point-for-this-delivery.md)
made a versioned CSV the only way sales enter the system, and it bound any
later entry point to it: "A later microservice or ERP adapter calls the same
row-level service", and a later ingestion record "may not create a second
implementation of the same rules".

The end-to-end demonstration now needs the Android client to record a sale in
JSON, and the frozen web to show it with no change. That is a second entry
point, so the "sole" clause cannot stand. The "one implementation" clause
cannot stand either, for reasons that are not about sales:

- [ADR-0045](0045-services-never-import-the-web-system.md) forbids a service to
  import `web/`. Importing `web.services.ingestion` pulls in `web.db`, whose
  package import loads Flask and `web.config`, and the web has no version a
  service could pin.
- The web is frozen. `ingest_row` cannot be lifted into a shared library.
- `ingest_row` is a row seam. Each call validates one line, finds or creates
  the header by `source_transaction_id`, appends the line, and decrements its
  stock (`web/services/ingestion.py`). A phone sale is one multi-line unit that
  must be accepted or refused whole. ADR-0020 named this exact doubt: "A future
  contract may need transaction-level atomic acceptance instead of the row
  seam."

Three more facts constrain the design:

- [ADR-0031](0031-sales-ingestion-decrements-inventory-atomically.md) and RN-14
  require the stock decrement in the sale's own transaction. sales-service does
  not own `inventory`. stock-service publishes the decrement as the database
  function `api_stock.consume_v1`
  ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)).
- `transaction.source_transaction_id` is globally `UNIQUE`. The comment on it
  in `sql/01_schema.sql`, and `docs/data-model.md` §4, say that is enough only
  while CSV is the sole producer. The web's `get_transaction_by_source_id`
  looks a header up by that value alone, and appends to whatever it finds.
- `transaction` and `transaction_line` carry no audit trigger. The audit
  strategy in `sql/01_schema.sql` excludes individual sales on purpose. Setting
  `mosaiq.user_id` therefore records nothing for a sale, and phone sales need
  their own attribution.

What is genuinely uncertain is how far two implementations of the sale rules
will drift. The database carries part of them. The rest is ported, and only
golden fixtures hold the port to the web's behaviour.

## Decision

sales-service records a phone sale with its own code, in one PostgreSQL
transaction per `POST /v1/sales`. Inside it, the service validates the whole
sale, writes the header and every line, and calls `api_stock.consume_v1` once
per line, in ascending `product_id` order. It then writes its provenance row,
`sales.sale_capture`. Any refusal rolls back the whole sale, so neither a
partial sale nor a partial decrement survives. Idempotency is durable in
PostgreSQL. The `Idempotency-Key` becomes `source_transaction_id`, and no
Redis key is involved. The service does not import, call or copy
`web/services/ingestion.py`. The rules it shares with the CSV path are carried
by the database where they can be, and by a port pinned with golden fixtures
where they cannot ([ADR-0045](0045-services-never-import-the-web-system.md)).
The CSV path keeps its own identical decrement, and both paths raise
`stream:sales.v1.recorded` through the same capture trigger. ADR-0020 keeps
everything else it decided: the versioned CSV contract, the row seam for CSV,
the rejection report, the reconciled load counts, and a total derived from the
lines.

### The transaction

`READ COMMITTED`, one connection from the service's pool:

1. `SELECT set_config('mosaiq.user_id', <sub>, true)`. This is the uniform
   service rule ([ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)).
   No audited table is written here today, so it records nothing, but it keeps
   attribution right if one ever is.
2. Validate the body against the service's own rules. These rules mirror
   RN-12, RN-13 and the sales RN-37 ("Accepted sales are dated, attributable
   and safe to reload"):
   - at least one line, and no `product_id` twice;
   - `quantity` > 0;
   - `unit_price` is the price actually charged, never copied from
     `list_price` (RN-13). It is parsed as a decimal, never a binary float,
     and must be finite, > 0 and have at most two decimal places, as
     `ingest_row` checks for CSV;
   - `occurred_at` carries an offset and is not after `now()`;
   - the customer exists in `api_customers.customer_ref_v1`, and the calendar
     date of `occurred_at`, in the offset the client sent, is on or after its
     `registered_on`. That is how `ingest_row` compares them. A cast in the
     database session's time zone could move the date across midnight;
   - every product exists in `api_products.product_ref_v1` and `is_active` is
     true.
3. `INSERT INTO transaction`, with `source_transaction_id = 'svc-sales:' ||
   <Idempotency-Key>` and `total` computed from the lines. There is no later
   `UPDATE` of the total.
4. `INSERT INTO transaction_line`, one row per line.
5. For each line, in ascending `product_id` order,
   `SELECT api_stock.consume_v1(store_id, product_id, quantity)`.
6. `INSERT INTO sales.sale_capture (transaction_id, request_sha256,
   recorded_by)`.
7. `COMMIT`.

A 40P01 (deadlock) or 40001 (serialization failure) restarts the whole
transaction from step 1, at most three times. A restart is safe, because
nothing committed. Ascending `product_id` order means two phone sales at one
store take their inventory row locks in the same order. A CSV row holds at
most one inventory row lock at a time. Neither can wait in a cycle on
inventory, and the retry covers whatever the analysis missed.

The service writes the `Idempotency-Key` in its canonical lowercase form, 36
characters, so `source_transaction_id` is 46, within `VARCHAR(64)`.

### Durable idempotency

- `Idempotency-Key` is required and must be a UUID. Otherwise the answer is
  400 `idempotency_key_required`.
- `request_sha256` is the SHA-256 of a canonical form of the request: the
  caller's `sub`, `customer_id`, `store_id`, `channel_id`, `occurred_at`
  normalised to UTC, and the lines sorted by `product_id`, with quantities
  and two-decimal prices. A JSON retry and an XML retry of the same sale
  therefore hash the same.
- **Before step 1,** the service looks the key up:

  ```sql
  SELECT c.transaction_id, c.request_sha256
    FROM transaction AS t
    JOIN sales.sale_capture AS c USING (transaction_id)
   WHERE t.source_transaction_id = 'svc-sales:' || $1;
  ```

  The same hash returns 200 with the original sale and
  `Idempotent-Replayed: true`. A different hash returns 409
  `idempotency_key_reused`.
- **Concurrent duplicates.** When two requests with one key race, the second
  `INSERT` in step 3 waits on the unique index until the first commits, then
  fails with 23505 on `transaction_source_transaction_id_key`. It has
  consumed no stock yet. The service rolls back and runs the lookup again,
  which now finds the committed sale.
- **Only accepted sales are remembered.** A refused request leaves no row, so
  the same key may be retried and may succeed later, for example once stock
  has arrived. The phone's queue treats 409 and 422 as final, and retries only
  on a network error, a 503 or a timeout
  ([ADR-0041](0041-android-and-electron-clients-run-different-processes-over-different-formats.md)).

Redis plays no part. A Redis flush, an eviction, or a retry from the phone's
WorkManager queue days later cannot create a second sale.

### The reserved namespace

A reserved prefix keeps the global `UNIQUE` on `source_transaction_id`, and
with it the exact lookup the web's CSV path depends on. A convention alone
would let a CSV file name `svc-sales:<uuid>` and append a line to a phone
sale, because `ingest_row` appends to any header it finds whose customer,
store, channel and time agree. So the sales section of `sql/01_schema.sql`
adds a guard:

- A BEFORE INSERT trigger on `transaction` refuses a row when
  `(source_transaction_id LIKE 'svc-sales:%')` differs from
  `(session_user = 'svc_sales')`.
- A BEFORE INSERT trigger on `transaction_line` refuses a line whose header is
  in the namespace, unless `session_user = 'svc_sales'`.
- Both functions are SECURITY INVOKER: every writer of these tables can
  already read `transaction`, so no definer role is needed.
- Both raise SQLSTATE 22023. psycopg maps that to a `DataError`, which
  `ingest_row` already catches on both inserts. A CSV row in the namespace is
  therefore rejected as a malformed row, with no web change.

### Error codes

The service answers with its own stable codes, never the web's sentences.

| Condition | CSV reason in `ingest_row` | sales-service |
|---|---|---|
| No lines | — (a CSV row is one line) | 422 `no_lines` |
| A `product_id` twice | `duplicate: … already has a line for product …` | 409 `duplicate_product_line` |
| `quantity` ≤ 0 | `quantity must be positive` | 422 `invalid_quantity` |
| `unit_price` not finite, ≤ 0, or more than two decimals | `unit price must be a finite number`, `... must be positive`, `... at most 2 decimal places` | 422 `invalid_price` |
| `occurred_at` after `now()` | `occurred_at cannot be in the future` | 422 `future_dated` |
| Unknown customer | `unknown customer` | 422 `unknown_customer` |
| Sale before registration | `sale occurred before customer registration` | 422 `before_registration` |
| Unknown or inactive product | `unknown product`, `product is inactive` | 422 `unknown_product`, `inactive_product` |
| Unknown store or channel (FK 23503, by constraint name) | `unknown store`, `unknown channel` | 422 `unknown_store`, `unknown_channel` |
| `consume_v1` raises 23514 `no_stock_record` | `no inventory for store … and product …` | 422 `no_stock_record`, naming the line |
| `consume_v1` raises 23514 `stock_unavailable` | `insufficient stock for store … and product …` | 409 `stock_unavailable`, naming the line |
| Key reused with a different request | — | 409 `idempotency_key_reused` |

The store's own status is not checked, on either path. That is parity, not an
omission.

### The CSV path, unchanged

The web's CSV path keeps `ingest_row` and `web.db.inventory.decrement_stock`,
one row per transaction. Its conditional `UPDATE` is the same statement
`consume_v1` runs. Both paths serialize on the inventory row lock, and
`CHECK (quantity_on_hand >= 0)` backstops both. No trigger decrements stock:
the web's explicit decrement would then run twice, and removing it is a web
change.

### The event

`stream:sales.v1.recorded` is captured by an AFTER INSERT trigger on
`transaction`, which writes one row to `sales_events.outbox`, and the sales
relay publishes it ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)).
The trigger fires for the CSV path as well, so every new sale raises the event,
whoever wrote it. sales-service never runs `XADD` from its request path.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Call `web.services.ingestion.ingest_row` from the service (the earlier draft of this record) | Forbidden by ADR-0045. It loads Flask and `web.config` into the service, and ties its build and rollback to an unversioned web. It is also the wrong shape: it accepts one line at a time and appends to an existing header, so a three-line phone sale would need a wrapper that reimplements atomicity around it |
| Move the sale rules into triggers on `transaction` and `transaction_line`, so both writers share one implementation | `unit_price` is `NUMERIC(10,2)`, so a value with three decimals is already rounded when a trigger sees it. The two-decimal rule cannot live there. The registration date, the active product and the stock decrement would then fire for the web too. That changes which exceptions the frozen web receives, and some of them it does not catch, so web pages would fail with no web code change. And the decrement would run twice |
| Decrement through stock-service over HTTP, or as a saga with a compensating increment | Breaks ADR-0031. Between the two commits, stock can be sold twice, or a sale can exist without its decrement. Services never call each other over HTTP |
| Keep idempotency keys in Redis with `SET NX` and a 24-hour TTL (the earlier draft) | A key lost to a flush, a restart inside the AOF's one-second window, or plain expiry lets a retried sale be recorded twice. Phones retry from an offline queue, possibly days later. The database already holds a unique key that cannot be lost |
| A composite key `(producer, source_transaction_id)`, as `docs/data-model.md` §4 anticipated for a second producer | The web looks headers up by `source_transaction_id` alone. Two producers could then share a value, and the web would append a CSV line to a phone sale. Fixing that means changing the frozen web's query |
| Store the key in a `sales.sale_capture.idempotency_key` column, separate from `source_transaction_id` | The same fact would sit in two tables, written once each and free to disagree. The key is already the suffix of a unique column |
| Leave ADR-0020 unsuperseded and call the service "not ingestion" | ADR-0020's title says CSV is the sole entry point, and its decision says no second implementation. Running code would contradict an accepted record, which is what supersession exists to prevent |

## Consequences

**What this makes easy.**
- A phone sale is all or nothing, including its stock. A two-line sale whose
  second line is short leaves no header, no line, and the first line's stock
  untouched.
- The web shows phone sales with no change. They are ordinary `transaction`
  rows, which `web/db/customer_timeline.py` already reads, and the stock
  consultation sees the decrement.
- Retries are safe without Redis. The key is a unique column, and the
  replayed answer is the original sale.
- sales-service builds, versions and rolls back on its own, with no `web/`
  in its image.
- Every sale raises `sales.v1.recorded`, from either path, with no web change.

**What this makes hard.**
- **The sale rules have two implementations, and that is a real loss.**
  ADR-0020's clause was right that two paths will disagree on some edge case.
  This record does not make that risk go away. It trades it for independence,
  and narrows it:
  - The database carries what it can for both writers: the foreign keys,
    `quantity > 0`, `unit_price >= 0`, `total >= 0`,
    `quantity_on_hand >= 0`, the unique source id, and the
    `(transaction_id, product_id)` key.
  - Five rules are ported: future date, registration date, active product,
    positive price and two decimals. Golden fixtures captured from
    `ingest_row` pin each of them, and a nightly drift job re-captures them
    (ADR-0045).
  - A difference on an input the fixtures do not contain goes unnoticed. The
    fixtures are evidence of parity, not proof.
  - Positivity of the price is a ported rule only. The schema allows 0
    (RN-11).
- **The paths differ on purpose,** and the fixtures record each difference
  rather than hiding it:
  - a phone sale is refused whole, while a CSV file is accepted row by row;
  - the service answers with codes, the web with sentences;
  - the service never appends to an existing header, because a known key
    is a replay.
- **The decrement also has two implementations:** `decrement_stock` in Python,
  and `consume_v1` in plpgsql. They are one statement each, and the CHECK
  backstops both, but a change to one must be made to the other.
- **`consume_v1` is a hard dependency.** If stock-service ships a broken `_v2`,
  or the function is missing, every phone sale fails. sales-service cannot
  deploy around it.
- **Validation reads are not locks.** A product deactivated in the web
  milliseconds before the commit can still be sold. The CSV path has the same
  window.
- **The namespace guard is new behaviour for the web.** A CSV row whose id
  starts with `svc-sales:` is now rejected, with the generic "malformed row"
  wording. Every inserted line also pays one primary-key lookup of its header.
  The prefix also packs a producer into an identifier, a choice made to keep
  the web's lookup exact.
- **Only accepted sales are remembered.** A sale refused with
  `stock_unavailable` can succeed on a later retry with the same key, if stock
  has arrived. That is intended, but the phone's outbox screen must show the
  refusal until someone acts on it.
- **A multi-row CSV sale raises one event.** The capture fires on the header's
  INSERT, which happens on the CSV transaction's first accepted row. Lines
  added by later rows raise none, and recommendations' cache picks them up
  only when its 120 s TTL expires.
- **The phone's clock is trusted.** A clock running ahead gets
  `future_dated`. There is no skew tolerance, matching the CSV rule.

**What must now be true elsewhere.**
- ADR-0020's status line reads `Partially superseded by ADR-0039`, set in the
  pull request that accepts this record. That is the one edit an accepted
  record may receive.
- `sql/01_schema.sql`, in the sales section:
  - creates `sales.sale_capture`
    ([ADR-0046](0046-service-owned-data-lives-in-service-schemas.md));
  - creates the namespace guard triggers and the capture trigger on
    `transaction`;
  - updates the comment on `source_transaction_id` to name the namespace.
- `docs/data-model.md` §4 replaces "a later multi-producer contract would need
  a composite key" with the reserved namespace.
- The CSV contract documentation reserves the `svc-sales:` prefix.
- [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)
  publishes `api_stock.consume_v1` and `stream:sales.v1.recorded`.
  [ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)
  gives `svc_sales` SELECT and INSERT on `transaction`, `transaction_line` and
  `sales.sale_capture`, SELECT on `channel`, and EXECUTE on `consume_v1`. It
  gives no privilege on `inventory`.
- `contracts/sales/openapi.yaml` lists the codes above per operation, and
  declares `Idempotency-Key` and `Idempotent-Replayed`
  ([ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)).
- `services/sales/tests/golden/` holds the sales line outcomes captured from
  `ingest_row`, before the service's first release
  ([ADR-0045](0045-services-never-import-the-web-system.md)).
- The Android queue keeps one `Idempotency-Key` per tapped sale, and treats 409
  and 422 as final
  ([ADR-0041](0041-android-and-electron-clients-run-different-processes-over-different-formats.md)).

## Compliance

Static checks, from the repository root. Each must print nothing:

```bash
# No web import in the sales service.
grep -rnE '^\s*(from|import)\s+web(\.|\s|$)' services/sales/

# Idempotency never touches Redis.
grep -rniE 'idempot' services/sales/ --include='*.py' | grep -iE 'redis|setnx|\bnx\s*='

# The service never writes inventory itself; it calls the stock contract.
grep -rniE '(update|insert\s+into|delete\s+from)\s+(public\.)?inventory\b' services/sales/
```

And this must print the file that calls the stock contract:

```bash
grep -rlE 'api_stock\.consume_v1\(' services/sales/ --include='*.py'
```

Run as the database owner after the three scripts. Every query must return zero
rows:

```sql
-- 1. Sales stay unaudited, and no trigger on them touches inventory.
SELECT t.tgname
  FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
 WHERE t.tgrelid IN ('public.transaction'::regclass, 'public.transaction_line'::regclass)
   AND NOT t.tgisinternal
   AND (p.proname = 'fn_audit' OR p.prosrc ILIKE '%inventory%');

-- 2. Every phone sale has exactly one capture, and every capture is a phone sale.
SELECT t.transaction_id, t.source_transaction_id, c.transaction_id
  FROM public.transaction t
  FULL JOIN sales.sale_capture c USING (transaction_id)
 WHERE (t.source_transaction_id LIKE 'svc-sales:%') IS DISTINCT FROM (c.transaction_id IS NOT NULL);

-- 3. A phone sale's total equals its lines, as ADR-0020 requires of every sale.
SELECT t.transaction_id
  FROM public.transaction t
  JOIN public.transaction_line l USING (transaction_id)
 WHERE t.source_transaction_id LIKE 'svc-sales:%'
 GROUP BY t.transaction_id, t.total
HAVING t.total <> sum(l.quantity * l.unit_price);

-- 4. svc_sales reaches inventory only through consume_v1.
SELECT 1 WHERE has_table_privilege('svc_sales', 'public.inventory', 'SELECT,INSERT,UPDATE,DELETE')
            OR NOT has_function_privilege('svc_sales', 'api_stock.consume_v1(smallint,integer,integer)', 'EXECUTE');
```

The namespace guard joins the `APPLICATION ROLE VERIFICATION` block of
`sql/01_schema.sql`, which CI already runs as `retail_app`. Any outcome other
than SQLSTATE 22023 fails the step, and the block rolls back:

```sql
BEGIN
    INSERT INTO public.transaction
        (source_transaction_id, customer_id, store_id, channel_id, occurred_at, total)
    VALUES ('svc-sales:00000000-0000-0000-0000-000000000000',
            '00000000-0000-0000-0000-000000000000', 1, 1, now(), 0);
    -- The guard is a BEFORE trigger, so it refuses the row before any
    -- foreign-key check runs, on a seeded or an empty database alike.
    RAISE EXCEPTION 'FAIL: retail_app wrote into the svc-sales namespace';
EXCEPTION WHEN invalid_parameter_value THEN
    RAISE NOTICE 'PASS: svc-sales namespace refused (SQLSTATE 22023)';
END;
```

Tests in `services/sales/tests/`, against a database built by the three
scripts:
- A two-line sale whose second line is short returns 409 `stock_unavailable`
  naming that line, leaves no `transaction` row, and leaves the first line's
  `quantity_on_hand` unchanged.
- Two concurrent posts with one key produce one 201, one 200 with
  `Idempotent-Replayed: true`, one `transaction` row and one decrement.
- A replay after `FLUSHALL` on both Redis instances still returns the original
  sale.
- The same key with a different body returns 409 `idempotency_key_reused`.
- Each fixture in `tests/golden/` yields the recorded outcome mapped to the
  service's code.
- A sale posted through the service and a CSV row each add one entry to
  `sales_events.outbox`.
