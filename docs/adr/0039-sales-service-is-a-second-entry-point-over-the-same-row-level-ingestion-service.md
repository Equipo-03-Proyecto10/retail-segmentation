# ADR-0039 — sales-service is a second entry point over the same row-level ingestion service

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** [ADR-0020](0020-csv-is-the-sole-sales-ingestion-entry-point-for-this-delivery.md), in part: its "sole entry point" clause only
**Superseded by:** —

---

## Context

[ADR-0020](0020-csv-is-the-sole-sales-ingestion-entry-point-for-this-delivery.md)
made CSV the only way sales enter the system. It deliberately left room for
what comes next: "A later microservice or ERP adapter calls the same row-level
service", and a later ingestion record "may not create a second implementation
of the same rules".

The end-to-end demonstration now requires the Android client to record a sale
in JSON, and the web to display it. That is a second entry point, so the
"sole" clause of ADR-0020 no longer holds. Its other clause, one
implementation of the rules, must survive unchanged.

[ADR-0031](0031-sales-ingestion-decrements-inventory-atomically.md) adds a
constraint. Accepted sales decrement stock **in the same transaction** as the
sale. A design where sales-service asks inventory-service to decrement stock
over HTTP would break that atomicity.

## Decision

sales-service records a sale by calling `web.services.ingestion.ingest_row` on
its own `svc_sales` connection. That is the same function, rules, rejection
reasons and atomic stock decrement that CSV ingestion uses.

It adds only what the HTTP transport needs:

- **Idempotency.** A required `Idempotency-Key` is held in Redis for 24 hours.
  A repeated key with the same body returns the original result. A repeated
  key with a different body gets 409 `idempotency_conflict`.
- **JSON and XML translation** of the row and its typed rejections.
- **An event.** After commit, sales-service appends a `sales.recorded` event
  to the stream.

A rule change in `ingest_row` applies to both entry points at once, and must
keep both test suites green.

ADR-0020's version-checked CSV header, rejection report and load records stay
exactly as they are. The CSV path remains the bulk entry point.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| A sales-service with its own validation, written for HTTP | Exactly the "second implementation of the same rules" ADR-0020 forbids. The two paths would disagree on the first edge case: future-dated sales, rounding, unknown products |
| sales-service posts a one-row CSV to the web's import page | Couples a service to the web's HTML form, CSRF token and session, and changes nothing about the rules |
| inventory-service owns the decrement and sales-service calls it | Breaks ADR-0031: a crash between the two calls leaves a sale without its stock movement |
| Leave ADR-0020 as is and treat the service as "not ingestion" | ADR-0020's title says CSV is the *sole* entry point. Saying nothing would leave an accepted record contradicted by running code, which is what superseding exists for |

## Consequences

**What this makes easy.**
- A phone sale and a CSV row are judged by the same code, with the same
  rejection wording.
- Stock stays exact.
- The web shows phone sales with no change, because they are ordinary
  `transaction` rows.

**What this makes hard.**
- **sales-service depends on the `web` package at runtime.** Its image copies
  `web/`, and a refactor of `web/services/ingestion.py` is now a change to two
  deployables.
- **Idempotency and retries are new.** Concurrent duplicate posts must be
  serialized by the key, which needs a `SET NX` before the insert.

**What must now be true elsewhere.**
- ADR-0020's status line reads "Partially superseded by ADR-0039 (sole entry
  point only)", the one edit an accepted record may receive.
- The sales contract (F14-11) reuses ingestion's rejection reasons.
- `sale.record` exists in `web/middleware/authz.py` (ADR-0034).

## Compliance

```bash
# sales-service calls ingest_row and contains no validation of its own.
grep -rn "ingest_row" services/sales-service/ || echo "sales-service does not use ingest_row"
grep -rnE 'INSERT INTO (transaction|transaction_line)|UPDATE inventory' services/ \
  && echo "a service writes sales SQL directly" || true
```

A test posts the rows from `tests/test_ingestion_service.py`'s rejection cases
to sales-service, and asserts the same reasons as 422 responses.
