# ADR-0020 — CSV is the sole sales ingestion entry point for this delivery

**Status:** Proposed
**Owner:** Marcelo
**Issue:** —
**Supersedes:** —
**Superseded by:** —

---

## Context

The analytics phase needs sales before RFM, segmentation or revenue reporting,
so [`roadmap.md`](../roadmap.md) puts transaction ingestion first. The existing
model has `transaction` headers and `transaction_line` items linked to the four
catalog entities, but the application writes neither table. This decision
populates [`sql/01_schema.sql`](../../sql/01_schema.sql), not a new sales store.

This work is the monolith's analytics phase inside the same second delivery
that [ADR-0016](0016-the-second-delivery-reinstates-the-distributed-architecture.md)
governs. It is not a return to a monolith-only plan. Constraints C-1 and C-2 in
[`scope.md`](../scope.md) describe the first delivery; they do not prohibit an
ingestion endpoint, JSON or XML in the second delivery.

The reason not to build an endpoint now is sequence. ADR-0016 requires contracts
before their consumers. An endpoint would fix a route, request, response and
retry contract before that decision; waiting for it would block analytics on
data that does not exist. CSV gives the monolith an entry point without
claiming an HTTP contract for a future service or ERP.

The boundary matters more than the transport. [ADR-0003](0003-layered-architecture-with-an-explicit-service-layer.md)
puts HTTP and file handling in routes, business rules in services and SQL in
`web/db`. [ADR-0014](0014-service-owned-transactions-and-typed-write-failures.md)
requires write services to own transactions and translate expected database
refusals. Copying the product-image service's `FileStorage` input would bind
sales validation to Flask and to a file.

A CSV row represents one `transaction_line` and repeats its header values. The
service rejects a later row whose customer, store, channel or date disagrees
with the accepted header. It preserves the positive quantity, non-negative
price and foreign-key rules. The total derives from accepted lines; the CSV
does not supply a second total that can disagree.

Two points remain uncertain. A future contract may need transaction-level atomic
acceptance instead of the row seam. CSV may remain after a service owns
ingestion, or that service may retire it; the later contract decides both.

The schema generates `transaction.transaction_id`, while the contract supplies
a transaction identifier. The schema and data model must define its durable
mapping and duplicate rule before implementation. This record leaves the
physical design to that 4NF analysis and does not change the segment-history
obligation predicted by [ADR-0004](0004-model-ahead-of-the-deferred-segmentation-modules.md).

## Decision

Sales enter the monolith in this delivery only through a versioned CSV
contract whose first version contains `transaction_id`, `customer_id`,
`store_id`, `channel_id`, `occurred_at`, `product_id`, `quantity` and
`unit_price`; the CSV view validates the contract version and header, parses
each record, and passes one transport-free row value to the ingestion service,
while that service validates business fields, owns one database transaction
for the row, calls only `web/db` for persistence, and returns acceptance or
raises a typed rejection. Each load reports counts of received, accepted and
rejected rows, those counts reconcile, and every rejected row retains its row
number and reason in the rejection report. A later microservice or ERP adapter
calls the same row-level service, so changing transport does not duplicate
validation or persistence rules.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Add a JSON or XML ingestion endpoint now | Its route version, authentication, payload, error response and retry semantics would become an integration contract before the contract decision required by ADR-0016. A client could then depend on an unreviewed shape that the later service ADR has to preserve or break |
| Parse, validate and persist inside the CSV view | The rules would depend on Flask request and file objects, contrary to ADR-0003, and a future endpoint would have to call a view or reimplement customer, catalog, duplicate and total checks. The view would also control commits, contrary to ADR-0014 |
| Reject the whole file when one row fails | One malformed product or unknown customer would roll back unrelated valid sales. Operators would have to edit and resubmit the full file, and the system could not produce the required accepted and rejected counts for a mixed load |
| Infer the contract from whatever header a file contains | Renamed, missing or extra columns could silently bind values to different meanings. There would be no version to select parsing rules, no deterministic error for an unsupported producer, and no stable fixture for later adapters to match |

## Consequences

**What this makes easy.** Analytics can start with repeatable sales fixtures
before any external service contract exists. The same service tests exercise
CSV imports and later adapters because they construct row values directly.
Per-row outcomes let an operator retain valid sales and correct only the
rejected rows. The report makes the invariant `received = accepted + rejected`
visible for every load.

**What this makes hard.** A load is not one atomic transaction. Earlier rows
remain accepted when a later row fails, and a transaction spread across rows
can temporarily contain only its accepted lines. The implementation must keep
header values and the derived total consistent after every row, translate
duplicate and foreign-key failures, and define safe retry behaviour. Parsing a
large file row by row also needs bounded memory and limits in the transport
adapter. The generated database identifier does not yet provide the source-ID
mapping the contract needs.

**What must now be true elsewhere.** The ingestion implementation stories must
put CSV parsing and rejection-report rendering in a route adapter, business
validation and transaction ownership in `web/services`, and parameterized SQL
in `web/db`. Any required source-identifier schema change belongs in
[`sql/01_schema.sql`](../../sql/01_schema.sql), with the 4NF argument and data
dictionary updated in [`data-model.md`](../data-model.md) before application
code depends on it. The RFM, clustering, segment-history and dashboard stories
in [`roadmap.md`](../roadmap.md) consume only accepted sales. A later ingestion
contract ADR must say whether it preserves the row seam and the CSV contract or
supersedes this record; it may not create a second implementation of the same
rules.

## Compliance

```bash
# The service has no Flask, CSV, request, stream or file dependency.
test -f web/services/ingestion.py
! grep -nEi 'flask|csv|filestorage|request|filename|stream' \
    web/services/ingestion.py

# Direct-row tests assert one commit on acceptance, rollback after a partial
# header/line write, and a raised typed rejection with a reason.
pytest -q tests/test_ingestion_service.py

# Four uploaded rows, two valid and two invalid, must report 4/2/2; retain a row
# number and reason per rejection; persist only accepted rows; and reconcile
# each transaction total with quantity times unit_price.
pytest -q tests/test_sales_csv.py
```
