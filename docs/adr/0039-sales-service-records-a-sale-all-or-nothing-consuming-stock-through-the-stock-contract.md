# ADR-0039 — sales-service records a sale all or nothing, consuming stock through the stock contract

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** [ADR-0020](0020-csv-is-the-sole-sales-ingestion-entry-point-for-this-delivery.md), in part: its "sole entry point" clause and its "one implementation of the rules" clause
**Superseded by:** —

---

## Context

[ADR-0020](0020-csv-is-the-sole-sales-ingestion-entry-point-for-this-delivery.md) made a versioned CSV the only way sales enter
the system, and bound later entry points to it: "A later microservice or ERP adapter calls the same row-level service", and a
later ingestion record "may not create a second implementation of the same rules". The Android client must now record a sale in
JSON for the frozen web to show, a second entry point, so the "sole" clause falls. The "one implementation" clause falls too:
- [ADR-0045](0045-services-never-import-the-web-system.md) forbids a service to import `web/`, whose package import loads Flask
  and `web.config`, and which has no version a service could pin. The web is frozen, so `ingest_row` cannot become a library.
- `ingest_row` is a row seam: it validates one line, finds or creates the header by `source_transaction_id`, appends the line
  and decrements its stock. A phone sale must be accepted or refused whole, the doubt ADR-0020 itself named.

Three facts constrain the design. [ADR-0031](0031-sales-ingestion-decrements-inventory-atomically.md) and RN-14 put the
decrement in the sale's own transaction, but sales does not own `inventory`; stock publishes it as `api_stock.consume_v1`
([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)).
`transaction.source_transaction_id` is globally `UNIQUE`, and the web's `get_transaction_by_source_id` looks a header up by it
alone. `transaction` carries no audit trigger, so phone sales need their own attribution. What is uncertain is how far two
implementations of the sale rules will drift: the database carries part of them, and only golden fixtures hold the ported rest.

## Decision

sales-service records a phone sale with its own code, in one PostgreSQL transaction per `POST /v1/sales`. Inside it, the service
validates the whole sale, writes the header and every line, calls `api_stock.consume_v1` once per line in ascending `product_id`
order, and writes its provenance row, `sales.sale_capture`. Any refusal rolls back the whole sale, so neither a partial sale nor
a partial decrement survives. Idempotency is durable in PostgreSQL: the `Idempotency-Key` becomes `source_transaction_id`, and
no Redis key is involved. The service does not import, call or copy `web/services/ingestion.py`; the rules it shares with the
CSV path are carried by the database where they can be, and by a port pinned with golden fixtures where they cannot
([ADR-0045](0045-services-never-import-the-web-system.md)). The CSV path keeps its own identical decrement, and both paths raise
`stream:sales.v1.recorded` through one capture trigger. ADR-0020 keeps the rest: the versioned CSV contract, the row seam for
CSV, the rejection report, the reconciled load counts, and a total derived from the lines.

### The transaction (`READ COMMITTED`, one pooled connection)

1. `SELECT set_config('mosaiq.user_id', <sub>, true)`, the uniform service rule (ADR-0034); it records nothing today.
2. Validate the body against RN-11 to RN-13 and RN-37 (accepted sales): at least one line, no `product_id` twice, positive
   quantities; `unit_price` the price charged, a finite positive decimal with at most two places; `occurred_at` with an offset,
   not after `now()`, and on or after the customer's `registered_on` (in `api_customers.customer_ref_v1`) as a date in the
   client's offset, as `ingest_row` compares them; every product active in `api_products.product_ref_v1`.
3. `INSERT INTO transaction` with `source_transaction_id = 'svc-sales:' || <Idempotency-Key>` (lowercase, so 46 characters,
   within `VARCHAR(64)`) and `total` computed from the lines, never updated later.
4. `INSERT INTO transaction_line`, one row per line.
5. For each line, in ascending `product_id` order, `SELECT api_stock.consume_v1($1::smallint, $2, $3)`. The store id is cast,
   because the contract's first parameter is `smallint` (ADR-0044).
6. `INSERT INTO sales.sale_capture (transaction_id, request_sha256, recorded_by)`.
7. `COMMIT`.

A 40P01 or 40001 restarts the whole transaction from step 1, at most three times; nothing committed, so that is safe. Ascending
`product_id` order makes phone sales lock inventory rows in one order, and a CSV row holds at most one, so neither waits in a
cycle; the retry covers the rest.

### Durable idempotency

`Idempotency-Key` is required and must be a UUID, else 400 `idempotency_key_required`. `request_sha256` hashes a canonical form
(the caller's `sub`, the ids, `occurred_at` in UTC, the lines sorted by `product_id` with two-decimal prices), so JSON and XML
retries hash the same. Before step 1 the service looks up `source_transaction_id = 'svc-sales:' || key`, joined to
`sales.sale_capture`: the same hash answers 200 with the original sale and `Idempotent-Replayed: true`; a different hash, 409
`idempotency_key_reused`. A concurrent duplicate fails step 3 with 23505 before consuming stock, rolls back and looks up again.
Only accepted sales are remembered; the phone retries on a network error, 503 or timeout (ADR-0041). Redis plays no part.

### The reserved namespace

The prefix keeps the global `UNIQUE` on `source_transaction_id`, and the web's exact lookup with it. A convention alone would
let a CSV row name `svc-sales:<uuid>` and append to a phone sale, so the sales section of `sql/01_schema.sql` adds two BEFORE
INSERT triggers: on `transaction`, refusing a row unless it has the `svc-sales:` prefix exactly when `session_user` is `svc_sales`; on
`transaction_line`, refusing a line whose header is in the namespace unless the session user is `svc_sales`. Both are SECURITY
INVOKER and raise SQLSTATE 22023, a `DataError` that `ingest_row` already catches, so a CSV row in the namespace is rejected as
a malformed row, with no web change.

### Refusals, the CSV path and the event

Every refusal has a stable code, listed per operation in `contracts/sales/openapi.yaml`, and the golden fixtures map each
`ingest_row` sentence to it: among them 409 `stock_unavailable`, 422 `no_stock_record`, 422 `future_dated` and 409
`idempotency_key_reused`. The store's status is checked on neither path; that is parity. The CSV path keeps `ingest_row` and
`decrement_stock`, one row per transaction; it runs the same conditional `UPDATE` as `consume_v1`, and the `quantity_on_hand`
CHECK backstops both paths. No trigger decrements stock.

`stream:sales.v1.recorded` is captured by an AFTER INSERT OR UPDATE OF `total` trigger on `transaction`, which writes one row to
`sales_events.outbox` per firing; the sales relay publishes it (ADR-0044). A service sale raises exactly one event, because its
total is never updated. A CSV row that creates a header raises two (the INSERT, then `recompute_total`'s update), and each CSV
line appended to an existing header raises one, so appended lines reach consumers too. Consumers dedupe by `event_id` and are
idempotent. sales-service never runs `XADD` from its request path.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Call `web.services.ingestion.ingest_row` from the service (the earlier draft of this record) | Forbidden by ADR-0045. It loads Flask and `web.config` into the service, and ties its build and rollback to an unversioned web. It is also the wrong shape: it accepts one line at a time and appends to an existing header, so a three-line phone sale would need a wrapper that reimplements atomicity around it |
| Move the sale rules into triggers on `transaction` and `transaction_line`, so both writers share one implementation | `unit_price` is `NUMERIC(10,2)`, so a value with three decimals is already rounded when a trigger sees it. The two-decimal rule cannot live there. The registration date, the active product and the stock decrement would then fire for the web too. That changes which exceptions the frozen web receives, and some of them it does not catch, so web pages would fail with no web code change. And the decrement would run twice |
| Decrement through stock-service over HTTP, or as a saga with a compensating increment | Breaks ADR-0031. Between the two commits, stock can be sold twice, or a sale can exist without its decrement. Services never call each other over HTTP |
| Keep idempotency keys in Redis with `SET NX` and a 24-hour TTL | A key lost to a flush, a restart inside the AOF's one-second window, or plain expiry lets a retried sale be recorded twice. Phones retry from an offline queue, possibly days later. The database already holds a unique key that cannot be lost |
| A composite key `(producer, source_transaction_id)`, as `docs/data-model.md` §4 anticipated for a second producer | The web looks headers up by `source_transaction_id` alone. Two producers could then share a value, and the web would append a CSV line to a phone sale. Fixing that means changing the frozen web's query |
| Store the key in a `sales.sale_capture.idempotency_key` column, separate from `source_transaction_id` | The same fact would sit in two tables, written once each and free to disagree. The key is already the suffix of a unique column |
| Leave ADR-0020 unsuperseded and call the service "not ingestion" | ADR-0020's title says CSV is the sole entry point, and its decision says no second implementation. Running code would contradict an accepted record, which is what supersession exists to prevent |

## Consequences

**What this makes easy.**
- A phone sale is all or nothing, stock included: a short second line leaves no header and the first line's stock untouched.
- Phone sales are ordinary `transaction` rows: the reports index revenue summary (`web/db/revenue_summary.py`) counts them at
  once, and the stock consultation (`web/db/inventory.py`) shows the decrement, with no web change.
- Retries are safe without Redis; the replayed answer is the original sale. sales-service builds, versions and rolls back on its
  own, with no `web/` in its image.

**What this makes hard.**
- **The sale rules, and the decrement, have two implementations, and that is a real loss.** The database carries what it can for
  both writers: the foreign keys, `quantity > 0`, `unit_price >= 0`, `quantity_on_hand >= 0`, the unique source id and the line
  key. Five rules are ported (future date, registration date, active product, positive price, two decimals), pinned by golden
  fixtures and a nightly drift job ([ADR-0045](0045-services-never-import-the-web-system.md)); fixtures are evidence of parity,
  not proof. The paths also differ on purpose, and the fixtures record it: a whole sale against row by row, codes against
  sentences, no append to an existing header. `decrement_stock` and `consume_v1` must change together.
- **`consume_v1` is a hard dependency.** If it is missing or broken, every phone sale fails, and sales cannot deploy around it.
- **Validation reads are not locks.** A product deactivated milliseconds before the commit can still be sold, as on CSV.
- **The namespace guard is new web behaviour.** A `svc-sales:` CSV row is rejected as malformed; each line pays a header lookup.
- **Only accepted sales are remembered.** A `stock_unavailable` sale may succeed on a retry with the same key, so the phone
  shows the refusal until someone acts.
- An N-line CSV sale raises N + 1 events (two for the row that creates the header, one per appended line); consumers dedupe by `event_id` and are idempotent.
- **The phone's clock is trusted.** A clock running ahead gets `future_dated`, with no skew tolerance, as for CSV.

**What must now be true elsewhere.**
- ADR-0020's status line reads `Partially superseded by ADR-0039`, set in the pull request that accepts this record.
- The sales section of `sql/01_schema.sql` creates `sales.sale_capture` (ADR-0046), the namespace guard triggers and the capture
  trigger AFTER INSERT OR UPDATE OF `total`, and names the namespace in the comment on `source_transaction_id`.
- `docs/data-model.md` §4 replaces its anticipated composite key with the namespace; the CSV contract reserves `svc-sales:`.
- ADR-0044 publishes `consume_v1` and the stream. ADR-0034 grants `svc_sales` SELECT and INSERT on `transaction`,
  `transaction_line` and `sales.sale_capture`, SELECT on `channel`, EXECUTE on `consume_v1`, and nothing on `inventory`.
- `contracts/sales/openapi.yaml` lists the codes per operation and declares `Idempotency-Key` and `Idempotent-Replayed`.
- `services/sales/tests/golden/` holds the line outcomes captured from `ingest_row` before the first release (ADR-0045), and the
  Android queue keeps one `Idempotency-Key` per tapped sale and treats 409 and 422 as final (ADR-0041).

## Compliance

From the repository root, the first three must print nothing and the last must print the file that calls the stock contract:

```bash
grep -rnE '^\s*(from|import)\s+web(\.|\s|$)' services/sales/
grep -rniE 'idempot' services/sales/ --include='*.py' | grep -iE 'redis|setnx|\bnx\s*='
grep -rniE '(update|insert\s+into|delete\s+from)\s+(public\.)?inventory\b' services/sales/
grep -rlE 'api_stock\.consume_v1\(' services/sales/ --include='*.py'
```

As the database owner after the three scripts, each query returns zero rows:

```sql
SELECT t.transaction_id, c.transaction_id FROM public.transaction t FULL JOIN sales.sale_capture c USING (transaction_id)
 WHERE (t.source_transaction_id LIKE 'svc-sales:%') IS DISTINCT FROM (c.transaction_id IS NOT NULL);   -- one capture per phone sale
SELECT 1 WHERE has_table_privilege('svc_sales', 'public.inventory', 'SELECT,INSERT,UPDATE,DELETE')
            OR NOT has_function_privilege('svc_sales', 'api_stock.consume_v1(smallint,integer,integer)', 'EXECUTE');
```

A namespace probe joins the `APPLICATION ROLE VERIFICATION` block (run as `retail_app`); only SQLSTATE 22023 passes.

Tests in `services/sales/tests/`: a two-line sale whose second line is short returns 409 `stock_unavailable`, with no header and
the first line's stock untouched; two concurrent posts with one key give one 201, one replayed 200, one row and one decrement; a
replay after `FLUSHALL` returns the original. A service sale adds exactly one `sales_events.outbox` row; each accepted CSV row
adds at least one row for its `transaction_id`, and appending a second line to an existing header adds one more.
