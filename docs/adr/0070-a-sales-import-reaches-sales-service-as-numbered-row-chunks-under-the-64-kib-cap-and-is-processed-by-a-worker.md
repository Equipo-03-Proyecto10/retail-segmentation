# ADR-0070 — A sales import reaches sales-service as numbered chunks of rows under the 64 KiB cap, and a worker processes it row by row, resumable and safe to repeat

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #851
**Supersedes:** — *(it extends ADR-0039 and ADR-0020 and replaces no clause of either; see Decision)*
**Superseded by:** —

---

## Context

The web loads a sales CSV through one page (RF-18, ADR-0020). Phase 26 wants the same
load from the desktop console, through sales-service, with data-quality folded into
sales (ADR-0059, capability 24: "a file's rows pass the same rule as a recorded sale
(RN-37)"). The issue (#851) blocks the sales contract (#852), so this record has to be
concrete enough for an OpenAPI author. Facts that constrain it:

- **Every service caps a body at 64 KiB.** `deploy/templates/service/nginx.conf` sets
  `client_max_body_size 64k` on `location /api/<name>/` and answers 413
  `payload_too_large` in the shared envelope, JSON or XML (ADR-0035); the kit refuses the
  same body again, and `deploy/check-api-edge.sh` asserts it for every service. The web's
  own upload limit is 5 MiB (`MAX_UPLOAD_BYTES`, `client_max_body_size 6m`). A CSV of
  tens of thousands of lines is therefore 50 to 100 times over the service cap.
- **Content negotiation is JSON and XML only** (ADR-0035): any other `Content-Type` gets
  415. ADR-0041 makes the Electron client consume XML only, validated against the
  service's XSD, and Android JSON only. A raw `text/csv` body fits neither rule.
- **Only `POST /v1/sales` exists** (ADR-0039): one sale, all or nothing, in one
  PostgreSQL transaction, with a durable `Idempotency-Key`. ADR-0062 narrowed that
  record to "a sale as the only sales write" for the sales table, which leaves open where
  the control state of an import (who started it, which lines are done) may live.
- **ADR-0020 and ADR-0039 split the paths on purpose.** The CSV path keeps `ingest_row`
  (one row per transaction, a header found or created by `source_transaction_id`, the
  web's per-row rejection report and reconciled counts). The service path validates a
  whole sale. The reserved namespace `svc-sales:` stops a CSV row from appending to a
  phone sale, by two BEFORE INSERT triggers. Nothing yet stops the *same file* being
  loaded once through each path: the ids would not collide (the service would write
  `svc-sales:...`, the web the bare id), so the sales would be recorded twice.
- **RN-37 is a rule of rows.** The web adapter rejects extra columns, a future
  `occurred_at`, a formula-like source id, a non-positive price, more than two decimals,
  a sale before the customer's registration, an inactive product, and a row naming a
  `SEEDED` sale. Of these, ADR-0039 ports five to the service for `POST /v1/sales`.
- **The web consumes no service and runs no Redis** (AGENTS.md). Services never call
  each other over HTTP, import `web/` or share a database role (ADR-0044, ADR-0045).
  A new deployable unit of any kind needs an ADR (AGENTS.md), and a long import cannot
  run inside a request: a Cloudflare-fronted request is bounded at about 100 seconds,
  and a row costs a transaction, a header lock and a `consume_v1` call.

Judgement calls the reviewer should look at first: the chunk size (150 rows, section 2),
the 100,000-row ceiling (section 2), the cross-path duplicate guard (section 5), and the
second process of the sales compose project (section 3), whose memory has not been
measured. The record was written without a load test.

## Decision

A sales import is a resource of sales-service, `/v1/sale-imports`, and the CSV never
travels. The desktop client reads the file locally and sends its rows in numbered chunks
of at most 150 rows and 60,000 bytes, each an ordinary JSON or XML request body under the
existing 64 KiB cap; no NGINX limit changes. Each chunk is staged in PostgreSQL and
acknowledged with 202; a worker, a second process of the same image and compose project,
applies the rows one at a time through the same rule as `POST /v1/sales`, writing each
row's outcome in the same transaction as the row, so a crash or a retry neither loses nor
repeats a line. Creating an import and sending a chunk are idempotent, a duplicate of a
file loaded through the web page is refused in both directions by the database, and
importing is an `ADMIN` operation. The web page stays as it is.

### 1. How the rows reach the service

| | |
|---|---|
| Create | `POST /v1/sale-imports`, header `Idempotency-Key` (UUID, required). Body: `contract_version` (1), `header` (the eight column names, in order), `total_rows` (1 to 100,000), `chunk_count` (1 to 667). 201 with `Location: /v1/sale-imports/{import_id}`; a replay of the same key and body answers 200 with the original and `Idempotent-Replayed: true`; the same key with another body answers 409 `idempotency_key_reused` |
| Send a chunk | `PUT /v1/sale-imports/{import_id}/chunks/{sequence}`, `sequence` from 0. 202 with the import's counts. The same sequence and the same body (compared by SHA-256 of the canonical form) answers 200 with `Idempotent-Replayed: true`; the same sequence with another body, 409 `chunk_conflict`; a sequence above the next expected, 409 `chunk_out_of_order` with `details[]` naming `next_sequence`; an import that is not `RECEIVING`, 409 `import_not_receiving` |
| Resume | `GET /v1/sale-imports/{import_id}` returns `status`, `next_sequence`, and the counts below. A client that lost its connection asks, then continues at `next_sequence` |
| Report | `GET /v1/sale-imports/{import_id}/rejections?limit=&cursor=` (a paged collection with `PageInfo`, ordered by `line`), and `GET /v1/sale-imports` (recent imports, newest first) |
| Cancel | `PUT /v1/sale-imports/{import_id}/status` with `{"status":"CANCELLED"}`. Rows already accepted stay accepted (ADR-0020: no rollback); unprocessed chunks are dropped. 409 `import_finished` if it is already complete |

A chunk body is the rows as positional cells, so the service, not the client, decides
what a row is, and RN-37's "extra columns" rule keeps its meaning:

```json
{"rows":[{"line":2,"cells":["T-1001","17","3","2","2026-10-02T14:03:00-06:00","501","2","19.90"]}]}
```
```xml
<ImportChunk xmlns="https://mosaiq.maxthecoder.online/xml/sales/v1"><rows><row line="2"><cell>T-1001</cell>...</row></rows></ImportChunk>
```

Rules the contract (#852) carries:

- `line` is the row's line number in the file (the header is line 1). It must be unique
  and increasing within the import, else 422 `invalid_body` (`details[].field` = `rows[i].line`).
- A chunk is **structurally** validated and nothing else: 1 to 150 rows, a positive `line`,
  at most 64 cells per row, each cell a string of at most 256 characters. Values are
  never typed at this layer. A price of `19.999`, a future date or nine cells is a
  **row** rejection later, not a 422 on the chunk, because one bad row must never reject
  its neighbours (ADR-0020).
- The client stops a chunk at 150 rows or 60,000 bytes of serialized body, whichever
  comes first, so the 64 KiB cap is never the thing it discovers. A larger body still gets
  413 `payload_too_large` from NGINX, unchanged.
- The sum of the rows of all chunks must equal `total_rows`, and the chunks must number
  `chunk_count`. The last chunk that does not reconcile gets 422 `row_count_mismatch`
  and the import stays `RECEIVING` until it is cancelled or expires.
- `Accept` and `Content-Type` follow ADR-0035: JSON and XML both, 406 and 415 otherwise.
  The XSD for `ImportChunk`, `Import`, `Rejection` and their collections ships with the
  contract, and the examples include a chunk with a malformed cell, a replay and a
  `chunk_conflict`.

### 2. Limits

| Limit | Value | Why |
|---|---|---|
| Rows per chunk | 150 | 150 rows of eight typical cells is 30 to 45 KB serialized, in XML as well; 60,000 bytes is the client's byte ceiling |
| Rows per import | 100,000 (667 chunks) | The web's 5 MiB file holds about 65,000 lines of the CSV v1 shape; the ceiling leaves headroom and keeps the staging table bounded at roughly 25 MB of JSON per import |
| Open imports | One `RECEIVING` or `PROCESSING` import at a time; another create gets 409 `import_in_progress` | One worker, one writer of the stock rows an import locks, and no pile of half-staged files |
| Idle import | 24 hours without a chunk: status `EXPIRED`, unprocessed payload nulled | Staged rows hold customer ids and are not kept for ever |
| Worker rate | At most 100 rows per second (`SALES_IMPORT_MAX_ROWS_PER_SECOND`, in `.env.example`) | Phone sales and the import lock `inventory` rows in the same order (ADR-0039); the ceiling keeps their latency, and 100,000 rows still finish in under 20 minutes |
| Request rate | NGINX `limit_req` on `location /api/sales/v1/sale-imports/`: 10 r/s per client, burst 20, 429 `rate_limited` with `Retry-After` | A chunk is cheap to send and expensive to stage |

These are first values to measure in F19-08 and tune, not guarantees; changing one is a
configuration change, not a new record, so long as the shape above holds.

### 3. Processing is asynchronous, and the worker is part of sales

A chunk is written to `sales.import_chunk` (a service schema, ADR-0046) with status
`PENDING` and answered 202; nothing is applied inside the request. The worker is a second
process of sales-service's image and compose project (`mosaiq-sales`, command
`python -m sales.import_worker`), connecting as `svc_sales` like the API (ADR-0040).
It is not a new service, rule or port. It wakes by polling every 2 seconds, with the
service's own `pg_notify` as an optional private wake-up (ADR-0044); no Redis is used,
because a queue that can be flushed cannot be the record of what was imported.

The worker takes the lowest-sequence `PENDING` chunk with `FOR UPDATE SKIP LOCKED`, under
a `pg_try_advisory_lock` keyed by the import, so two workers never interleave one import
and rows of one source transaction are applied in file order. For each row it runs **one
PostgreSQL transaction**, the row seam of ADR-0020:

1. parse the cells (count, types) and apply the rule of section 4;
2. find the header by `source_transaction_id` and lock it, or create it; refuse a later row
   whose customer, store, channel or date disagrees with the accepted header;
3. append the line, call `api_stock.consume_v1` once, recompute the total from the lines;
4. `INSERT INTO sales.import_row_outcome (import_id, line, outcome, code, message)`;
5. `COMMIT`.

A rejected row writes only step 4 (in its own short transaction), so the rejection report
holds the source line and the reason of every rejection, as RF-18 asks. Because the
outcome row and the sale commit together, **resume is exact**: after a crash, a restart or
a retry, the worker skips every `line` that already has an outcome and continues at the
first that has not. A row is never applied twice, and a row repeated in the file is
rejected as a duplicate by the unique source-transaction-and-product rule, exactly as on
the web. When all of a chunk's lines have outcomes, its `rows` payload is set to null and
its status to `DONE`: the staging copy of customer ids does not outlive the work.

The import is `COMPLETED` when every chunk is `DONE`. Its counts reconcile, and the
service refuses to report an import complete unless they do:

`received` = rows staged = `accepted` + `rejected` + `pending`, and `pending` = 0 at `COMPLETED`.

`GET /v1/sale-imports/{id}` returns the status (`RECEIVING`, `PROCESSING`, `COMPLETED`,
`CANCELLED`, `EXPIRED`), `next_sequence`, `received`, `accepted`, `rejected`, `pending`,
`created_at`, `finished_at` and `created_by` (the token's `sub`, never taken from a body).

The worker takes its authority from the upload, not from a live token: the importer was
checked on every chunk, `created_by` is stored, and the sales it writes carry that user in
`sales.sale_capture.recorded_by`. A session revoked after the last chunk therefore does not
stop the work; cancelling does (see Consequences).

### 4. One rule, shared by the two sales paths and the web's page

Rows pass the **same rule** as `POST /v1/sales` and the web's `ingest_row`, and it stays
one rule in the sense ADR-0039 and ADR-0045 allow:

- The checks the database carries apply to every writer and are not repeated: foreign
  keys, `quantity > 0`, `unit_price >= 0`, the unique source-transaction-and-product key,
  `quantity_on_hand >= 0`, and `data_origin` (ADR-0052).
- The five ported checks (future date, registration date, active product, positive
  price, two decimals) are **one module inside sales-service**, called by `POST /v1/sales`
  and by the worker. There is no second port for imports. The CSV-only checks of RN-37 (extra or
  missing cells, a formula-like source id, a `SEEDED` duplicate, the header contract) are
  applied to the cells before this module.
- A row's rejection `code` is the code the same fault would get on `POST /v1/sales`
  (`future_dated`, `product_inactive`, and so on) plus the CSV codes `wrong_cell_count`,
  `formula_like_id`, `duplicate_row`, `header_mismatch`. `message` echoes at most 64
  characters of the offending cell, with control characters removed.
- The golden fixtures of ADR-0045 gain an import corpus: the web's CSV test files run
  through `ingest_row` and through the worker must produce the same accepted lines and the
  same rejected lines with the same reason, and the nightly drift job runs it. Import rows
  write `data_origin = OBSERVED` and `recorded_by` the importer.
- **The unit differs on purpose.** A phone sale is whole-or-nothing; an import row is one
  line, as on the web. A sale spread over several lines of a file can therefore be
  partly accepted. The report says so, by line.

ADR-0020's CSV contract v1 (the eight columns, the version check, per-row outcomes, the
reconciled counts, the report that survives) is preserved, not replaced, and the "later
ingestion record" it asks for is this one: it keeps the row seam and the contract and
creates no second implementation of the rules.

### 5. Coexistence with the web's page

Both stay. The web page is the web system's way (RF-18) and is not changed; the service
import is the console's. The only new coupling is the identity of the sale:

- Imported sales use the source id `svc-sales:imp:` followed by the file's
  `transaction_id`, which therefore may be at most 50 characters (a longer id is the row
  rejection `id_too_long`). The prefix is inside the namespace ADR-0039 reserves, so its
  guard triggers stay true as written, and `imp:` can never equal a phone sale's key,
  which is a UUID.
- To stop the *same file* being recorded through both paths, the sales section of
  `sql/01_schema.sql` extends the guard that ADR-0039 already installs on `transaction`:
  when the session user is `svc_sales`, a row with the `svc-sales:imp:` prefix is refused
  if a header with the bare id exists, and when it is any other role, a bare id is refused
  if a header with the prefixed id exists. Both take `pg_advisory_xact_lock` on a hash of
  the bare id before looking, and both raise SQLSTATE 22023 with the message
  `duplicate: <id> was already loaded through the other path`. `ingest_row` already
  catches 22023 and the worker maps it to `duplicate_row`, so **the web changes not at
  all**: its report shows the row as rejected, with the reason.

Whoever loads a file should still pick one path; the guard is a net, not a workflow. It
does not unify a file whose ids were re-keyed between loads.

### 6. How a CSV upload fits "Electron consumes XML only"

The rule is about the representation of the API's bodies, and no CSV crosses the wire. The
Electron main process opens the file the user chose in a native dialog, parses it, checks
the header against `contract_version` 1 and the 100,000-line ceiling, serializes the rows
into `ImportChunk` XML, **validates each chunk against the sales XSD before sending**, and
validates each response against it too, as ADR-0041 requires of every call. The
renderer never reads the file and receives only the parsed counts and the report
(`contextIsolation: true`, `nodeIntegration: false`, `sandbox: true` unchanged). A
rejection that the console shows is rendered as text, and one it exports to CSV is
prefixed against formula injection. Android is not an importing client; sending
`Accept: application/json` for the same routes is allowed and tested, but no Android story
uses them.

### 7. Who may import

`ADMIN` only, on every route of the resource, in sales-service's own role policy
(`x-mosaiq-roles`), matching the web page's administrator-only upload. Store-scoped
profiles (ADR-0060) cannot import, because a file spans stores and a per-row store
check would add a second rule. Widening this is a new record.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| **Raise `client_max_body_size` to about 6m on one route and post the CSV as `text/csv`** | The 64 KiB cap is deliberately the same on every service and `check-api-edge.sh` asserts it; one exception makes the template per-service, and the 413 envelope with it. `text/csv` is a third media type, so ADR-0035's 415 rule and Electron's XML-only rule both break. A Flask service held to a small memory limit (ADR-0059) would buffer megabytes per request, a single abusive caller could hold such a request open, and a dropped connection at 99% restarts the whole file. Processing would still need to be asynchronous: tens of thousands of rows do not fit one request |
| **Send the raw CSV in 60 KiB byte chunks** (`application/octet-stream`, `Content-Range`) | Cuts rows in half, so the service must reassemble before parsing, keep parser state between requests and defend a custom range protocol; the media type breaks the same two rules. Rows as cells keep the cap, the content types and the XSD, and make a chunk meaningful on its own |
| **Process each chunk synchronously and return the outcomes (200)** | Simpler, and no worker. But the AC asks for an asynchronous import; 150 rows each taking a header lock and a stock call can outlast a gateway timeout under contention; and progress lives in the request, so a client that dies mid-chunk cannot tell which lines were applied. The staged chunk plus a durable outcome per line is what makes resume exact |
| **Queue the rows in Redis and have a consumer apply them** | Redis can be flushed, expire or restart inside its AOF window; a lost chunk would be an unrecorded sale with no trace. ADR-0039 already put sales idempotency in PostgreSQL for the same reason. The staging table is the queue |
| **Run the work on a thread inside the API process** | The API process is restarted by every deploy and may run several workers; a thread is killed mid-import, and two processes could drive one import. A separate process under the same image can be sized, restarted and observed alone, and the advisory lock makes it single per import |
| **A new `imports-service`** | Each service implements one rule (ADR-0043), and ADR-0059 folded data-quality into sales on exactly that argument: an import is a batch of sales, each accepted or refused by the sales rule. A separate service would copy RN-37 or call sales over HTTP, which ADR-0035 and ADR-0044 forbid |
| **Let the web forward the file to the service** | The web consumes no microservice (AGENTS.md); the web page already works and is frozen |
| **Drop the file on the server and have the service read the path** | Bypasses authentication and the role policy, couples the service to a host path outside its build context, and leaves an uploaded dataset on disk where `data/` rules say none should be committed or kept |
| **Give imported sales the bare source id, as the web does, so duplicates collide naturally** | The `svc_sales` guard in ADR-0039 forbids a service write without the prefix, so that a CSV row can never append to a phone sale. Removing it for imports would change the guard that ADR-0039 depends on. The prefixed id plus a symmetric database check keeps the guard and still refuses the duplicate |
| **Treat one import as a single transaction** | A 100,000-row transaction would hold locks for minutes and block phone sales, and ADR-0020 chose per-row acceptance so one bad row does not discard valid sales |

## Consequences

**What this makes easy.**
- The console loads a large file with the 64 KiB cap, the two content types and the XSD
  unchanged; no NGINX, template or `check-api-edge.sh` change.
- A broken connection, a deploy or a worker crash costs at most the current row's
  transaction, and the client resumes from `next_sequence`; a duplicate request is
  harmless at every level (create, chunk, row).
- One rule serves `POST /v1/sales`, the worker and, through the database, the web.

**What this makes hard.**
- **A second process in the sales project.** It adds memory to a budget already gated at
  3,500 MB (ADR-0059). Estimate: the same limit as a Flask service; unmeasured, and F19-08
  must include it before sales deploys with it.
- **Three tables and a worker are new state**: `sales.import`, `sales.import_chunk`,
  `sales.import_row_outcome`. Each ships with 30 seed rows or a named entry in
  `sql/seed-exempt.txt` (AGENTS.md), and the 4NF note says why outcomes are keyed by
  `(import_id, line)` and carry no copy of the row.
- **Events.** Each accepted row raises the events ADR-0039 counts for a CSV row (two for
  a row that creates a header, one per appended line). 100,000 rows can add over 150,000
  outbox rows and stream entries in 20 minutes; the relay and stream length (ADR-0037) must be
  checked at that volume, and consumers already dedupe by `event_id`.
- **A revoked session does not stop an accepted import.** The administrator cancels it,
  which stops unprocessed chunks only. This is deliberate (the work must outlive a 12-hour
  token) and is the one place authority is not re-checked.
- **A sale in a file can be partly accepted**, which a phone sale cannot. Reports are by line
  and say so.
- **The cross-path guard is a net.** It takes an advisory lock and one indexed lookup per
  row on both paths, and it cannot see a file that was re-keyed.
- **Rejections echo cell text** (64 characters, sanitized) to the administrator, which can
  confirm that a customer id exists. Acceptable for the single administrator; not for a
  wider role.

**What must now be true elsewhere.**
- `contracts/registry.yaml` registers the resource `sale-imports` to sales, beside `sales`
  and `channels`; `contracts/sales/openapi.yaml` and `sales.xsd` carry section 1, with the
  codes of sections 1, 3 and 4 and `Idempotency-Key`/`Idempotent-Replayed`, in #852. No
  other service's path contains `sale-imports`.
- The sales section of `sql/01_schema.sql`, idempotent (AGENTS.md), adds the three
  tables, the extended guard of section 5 and an `import_id` link from `sales.sale_capture`
  so the one-capture-per-`svc-sales:` header query of ADR-0039 still returns zero rows.
  `svc_sales` gains INSERT and UPDATE on the new tables only. `docs/data-model.md` §4
  records the second namespace prefix and the CSV contract reserves `svc-sales:imp:`.
- `deploy/templates/service/` gets a variant or flag for sales' second process and the
  `limit_req` of section 2; `.env.example` gains `SALES_IMPORT_MAX_ROWS_PER_SECOND` and
  the chunk and import limits with these defaults.
- `docs/business-rules.md` states, in RN-37's text, that the service import is a second
  enforcer; `docs/requirements.md` gains the requirement for the console import; the
  Electron story (ADR-0041's table gains sales imports) follows #852.
- ADR-0020 and ADR-0039 keep their status lines; nothing in them is replaced.

## Compliance

From the repository root, each of the first three prints nothing, the fourth prints the one
module both paths call:

```bash
grep -rnE '^\s*(from|import)\s+web(\.|\s|$)' services/sales/
grep -rniE 'redis' services/sales/ --include='*.py' | grep -iE 'import|chunk|outcome'
grep -rnE 'client_max_body_size' services/sales/nginx/ | grep -v '64k'
grep -rlE 'validate_sale_rules\(' services/sales/ --include='*.py'
```

(The module name is the implementation's; the check is that `POST /v1/sales` and the worker import the same one.)

As the database owner after the three scripts, each returns zero rows:

```sql
SELECT 1 FROM sales.import i
 WHERE i.status = 'COMPLETED' AND i.total_rows <>
   (SELECT count(*) FROM sales.import_row_outcome o WHERE o.import_id = i.import_id);   -- counts reconcile
SELECT 1 FROM sales.import_chunk WHERE status = 'DONE' AND rows IS NOT NULL;            -- staging copy is gone
SELECT 1 WHERE has_table_privilege('svc_sales','public.inventory','SELECT,INSERT,UPDATE,DELETE');
```

Tests in `services/sales/tests/`: a chunk of 151 rows gets 422; a body over 64 KiB gets 413
from NGINX; the same chunk twice gives 202 then 200 `Idempotent-Replayed: true`, and the
same sequence with another body gives 409 `chunk_conflict`; killing the worker between a
row's commit and the next row and restarting yields no second sale and the same counts;
the web's CSV fixtures give identical accepted and rejected lines through `ingest_row`
and through the worker; a file loaded through the web and then through the service has
every row rejected `duplicate_row`, and the reverse likewise (a probe joins the
`APPLICATION ROLE VERIFICATION` block, only 22023 passes); a `STORE_MANAGER` token gets
403 `forbidden_role` on every route; an XML chunk validates against `sales.xsd`, and the
Electron test sends no `text/csv` and rejects an unvalidated response.
