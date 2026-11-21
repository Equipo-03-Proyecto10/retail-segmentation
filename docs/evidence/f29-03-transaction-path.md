# F29-03 (#888): the acceptance tests of the transaction path, on the instance

> **Status: not yet run on the instance.** This document is the runbook and the
> template of three of the instructor's sixteen acceptance tests (AC-01
> "Venta Android", AC-02 "Paridad web", AC-03 "Consulta escritorio";
> [`scope-delivery-3.md`](../scope-delivery-3.md) §10 and §11). It is blocked
> by [#462](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/462)
> (the demonstration script) and
> [#760](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/760)
> (F20-02, Electron reads the sale in XML). Every `‹fill: …›` below is filled
> during the live run. The only results recorded so far are the
> [local dry run](#local-dry-run-not-the-acceptance), which is **not the
> acceptance**.

Governing decisions:
[ADR-0039](../adr/0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md),
[ADR-0041](../adr/0041-android-and-electron-clients-run-different-processes-over-different-formats.md).

## The criteria, and where each is answered

| Criterion of #888 | Answered by | Section |
|---|---|---|
| "Venta Android": a real test sale recorded in JSON shows in PostgreSQL | F20-01's capture of the phone's HTTPS request, and `f29_03 sale`'s rows | [3](#3-venta-android-the-sale-on-the-phone), [4](#4-the-sale-its-rows-and-the-parity-after-it) |
| "Paridad web": the portal is read, and the sale and the inventory agree | `f29_03 snapshot` before and `f29_03 sale` after: the portal's `/catalog/stock`, stock-service and `inventory` agree, each fell by the quantity sold, and the customer's profile counts one more purchase | [2](#2-the-snapshot-before-the-sale), [4](#4-the-sale-its-rows-and-the-parity-after-it) |
| "Consulta escritorio": Electron reads the sale in XML, and it validates against its XSD | The desktop app itself (#760), plus `f29_03 sale`'s XML read validated by `xmllint` against `contracts/sales/` | [5](#5-consulta-escritorio-the-sale-in-electron) |
| The evidence names the version and links its requests, responses and rows | Every collector writes `summary.md` (checks, each linked), `exchanges.md` (each request and response with its `X-Correlation-ID`), `rows.md` and the versions | [The record](#the-record) |

## Who does what

| Step | Who | Where |
|---|---|---|
| 0. Preconditions, the version | Acceptance lead (Raquel) | workstation, GitHub |
| 1. The account | Instance operator (a sudoer), as F20-01 step 1 | workstation |
| 2. Snapshot before | Acceptance lead, with the instance operator | the instance, as root |
| 3. The sale on the phone | Floor tester (the Android owner), **by hand** with the release build | emulator behind mitmproxy, F20-01 steps 2–4 |
| 4. The sale, its rows, the parity | Acceptance lead, with the instance operator | the instance, as root |
| 5. The sale in Electron | Desktop tester (#760's owner), by hand | workstation, desktop release build |
| 6. Clean-up | Instance operator | workstation |

No step changes code. Android steps use the release build as it is; nothing in
`clients/android/` is edited for this run.

## 0. Before the run

Each of these must hold, and the record says when it was checked.

1. **#462 and #760 are closed**, and the identified version is chosen: one
   commit of `develop` (or a tag) that the instance runs. Every service in
   [the versions table](#versions) must be deployed from it. The F20-03 runner
   has passed on that commit (`bash ops/verify/run.sh <commit>`,
   [ops/verify/README.md](../../ops/verify/README.md)).
2. **The edge answers.** From a workstation:

   ```sh
   bash deploy/check-api-edge.sh
   ```

   It prints nothing when every check holds. Its check 3 spends the caller's
   sign-in budget for about a minute: run it at least a minute before step 2.
3. **The collectors' tools on the instance.** A copy of this tree at the
   identified commit that the `postgres` user can read (as for F18-04,
   [tests/negative/README.md](../../tests/negative/README.md) "The instance"),
   a virtualenv with `pytest` and PyYAML, and `xmllint` (`xmllint --version`).
4. **The F20-01 tooling** on the floor tester's workstation: the emulator image,
   mitmproxy and the signed release APK ([F20-01](f20-01-android-sale.md)
   "Before the run" item 4 and steps 2–3).

## The procedure

Shared settings for steps 2 and 4, on the instance as root, from the tree of
item 0.3. The service reader and the portal reader can be the step 1 account:
`STORE_MANAGER` reads sales and stock levels (`contracts/sales`,
`contracts/stock`) and holds `catalog.read` and `segment.read` on the portal
(`web/middleware/authz.py`). Passwords are read at a prompt and only kept in
this shell's environment.

```sh
cd <the tree> && . .venv/bin/activate
export MOSAIQ_BASE_URL=https://mosaiq.maxthecoder.online   # the public edge
export MOSAIQ_DB_EXEC="runuser -u postgres --"
read -r  -p 'Account email: ' MOSAIQ_ACCEPTANCE_EMAIL
read -rs -p 'Account password: ' MOSAIQ_ACCEPTANCE_PASSWORD; echo
export MOSAIQ_ACCEPTANCE_EMAIL MOSAIQ_ACCEPTANCE_PASSWORD
RUN=docs/evidence/f29-03/$(date -u +%Y%m%dT%H%MZ)
```

From a workstation instead, the reads go to the database over SSH:
`MOSAIQ_DB_EXEC="ssh <host> sudo -n -u postgres"` (each argument is quoted for
the remote shell; `tests/acceptance/evidence.py`, `Database`). The versions
table then lacks the instance's release paths, which step 4's
[F20-01 step 7](f20-01-android-sale.md#7-read-the-versions-on-the-instance)
commands supply.

### 1. The account

As [F20-01 step 1](f20-01-android-sale.md#1-create-the-account): a
`STORE_MANAGER` created through users-service, assigned to the sale's store
(`PUT /api/users/v1/store-assignments/{user_id}`, F19-07). If F20-01's account
is still active, it is reused and the record says so.

### 2. The snapshot before the sale

Choose the store, one or two active products with stock there, and the customer
the floor tester will select. Then:

```sh
python -m tests.acceptance.f29_03 snapshot --out "$RUN/before" \
  --store <store_id> --product <product_id> [--product <product_id>] \
  --customer <customer uuid>
```

It signs in, reads each product's stock level from stock-service, the
portal's `/catalog/stock` row and the `inventory` row, and the customer's
profile page (30-day window) and transactions, then signs out. It exits 0
only when, for every product, the three readers agree.

Nothing else may sell those products at that store between steps 2 and 4.

### 3. "Venta Android": the sale on the phone

The floor tester follows [F20-01 steps 3–4](f20-01-android-sale.md#3-prepare-the-device-and-the-capture):
the release build behind mitmproxy, signed in as the step 1 account, the step 2
store, customer and products, then **Confirm**. Capture:

- the screenshot of the recorded sale, `f29-03-android-sale-recorded.png`;
- the masked exchanges:
  `python clients/android/evidence/sale_evidence.py har ~/f20-01/capture.har --path /api/sales/v1/sales`,
  saved as `$RUN/android-exchanges.md`;
- from the 201, the `sale_id`, the request's `Idempotency-Key` and the
  response's `X-Correlation-ID`.

### 4. The sale, its rows and the parity after it

```sh
python -m tests.acceptance.f29_03 sale --out "$RUN/after" --before "$RUN/before" \
  --sale-id <sale_id> --idempotency-key <Idempotency-Key>
```

It reads the sale back from sales-service in JSON and in XML, validates the
XML with `xmllint` against `contracts/sales/` (the namespace's XSD, as the
conformance harness does), runs
[`sale_evidence.sql`](../../clients/android/evidence/sale_evidence.sql) for the
sale's rows, and takes the step 2 snapshot again for the sale's store, products
and customer. It checks:

- the `transaction` row holds the request's `Idempotency-Key`
  (`source_transaction_id = 'svc-sales:' || lower(key)`), with the response's
  total, and one `sales.sale_capture` row;
- for each line, the portal, stock-service and `inventory` agree after the sale,
  and each fell by the quantity sold;
- the customer's profile shows one more purchase and the sale's total added to
  the spend.

Every query is a bind parameter in a read-only transaction. It exits 0 only
when every check holds.

### 5. "Consulta escritorio": the sale in Electron

Once #760 is merged and released: the desktop tester signs in to the desktop
release build against `https://mosaiq.maxthecoder.online/` with a role that may
read sales, opens the step 3 sale and records:

- the screenshot of the sale's detail, `f29-03-desktop-sale.png`, showing the
  same `sale_id`, lines and total as the 201;
- the app's own validation of the XML against `sales.xsd` (#760 defines where it
  shows or logs this), and the request's `X-Correlation-ID`;
- the desktop build's version and commit.

Step 4's XML read is the same request made from the instance, and its
`xmllint` result is the protocol's half of this criterion; the app's own read
is the other half and is what the instructor sees.

### 6. Afterwards

- Deactivate the account through
  `POST /api/users/v1/users/{user_id}/deactivation` (RN-04), unless another
  acceptance test still needs it.
- Delete the HAR, as F20-01 step 8 says.
- Commit `summary.md`, `exchanges.md`, `rows.md`, `snapshot.json` and
  `sale.xml` of `$RUN/before` and `$RUN/after` with the filled record. No file
  holds a credential: `Recorder.write` refuses to write anything shaped like a
  token, and masks passwords, tokens and CSRF fields. `pages/` (the portal's
  HTML, with the customer's profile) and `record.json` stay with the run
  outside the repository.

---

## The record

*Everything below is filled during the live run.*

### Where it ran

| Piece | Value |
|---|---|
| Identified version | ‹fill: commit or tag› of `develop`, F20-03 report ‹fill: link› |
| Date and time (UTC) | ‹fill: start – end› |
| Instance | `https://mosaiq.maxthecoder.online/`, web commit ‹fill: from `summary.md` "web commit"› |
| Android build | MOSAIQ Floor ‹fill: versionName›, APK SHA-256 ‹fill› |
| Desktop build | ‹fill: version, commit› |
| Account | ‹fill: email›, `STORE_MANAGER`, store ‹fill› |
| Who ran each step | ‹fill: step → name› |
| Approval | ‹fill: who approved the live run, and where› |

### Versions

‹fill: the "Versions" table of `f29-03/<run>/after/summary.md`, and the
release of each service from F20-01 step 7.›

| Service | Release (`current`) | Contract (`info.version`) |
|---|---|---|
| auth | ‹fill› | ‹fill› |
| users | ‹fill› | ‹fill› |
| stores | ‹fill› | ‹fill› |
| products | ‹fill› | ‹fill› |
| stock | ‹fill› | ‹fill› |
| customers | ‹fill› | ‹fill› |
| sales | ‹fill› | ‹fill› |

### AC-01 "Venta Android"

![The sale recorded on the phone](f29-03-android-sale-recorded.png)

*‹fill: caption — store, customer, lines and total as the phone shows them›*

| What | Value | Evidence |
|---|---|---|
| `POST /api/sales/v1/sales` → 201 | sale ‹fill›, `X-Correlation-ID` ‹fill›, `Idempotency-Key` ‹fill› | ‹fill: `android-exchanges.md` link› |
| `transaction` row, key matches | ‹fill: true› | ‹fill: `after/rows.md#rows-1`› |
| `transaction_line` rows | ‹fill› | same |
| `sales.sale_capture`, recorded by the account | ‹fill› | same |

### AC-02 "Paridad web"

| Product | Portal before → after | Service before → after | `inventory` before → after | Sold |
|---|---|---|---|---|
| ‹fill› | ‹fill› | ‹fill› | ‹fill› | ‹fill› |

| Customer's profile | Before | After |
|---|---|---|
| Purchases (30 days) | ‹fill› | ‹fill› |
| Spend (MXN) | ‹fill› | ‹fill› |

Checks: ‹fill: "N checks, N held" from `before/summary.md` and `after/summary.md`, linked›.

### AC-03 "Consulta escritorio"

![The sale in the desktop app](f29-03-desktop-sale.png)

*‹fill: caption›*

| What | Value | Evidence |
|---|---|---|
| The desktop app's read | ‹fill: status, `X-Correlation-ID`, XSD validation as the app reports it› | ‹fill› |
| The same read from the instance, `xmllint` | ‹fill: valid against `contracts/sales/sales.xsd`› | ‹fill: `after/exchanges.md` link, `after/sale.xml`› |

### Found on the way

‹fill, or "Nothing."›

### What was not done

‹fill›

---

## Local dry run (not the acceptance)

> **Local dry run, not the acceptance.** It ran on a throwaway local stack, not
> on the instance and not on an identified version. No phone and no desktop app
> took part: the sale was recorded by a script through the gateway, in the same
> JSON the floor app sends, as `tests/e2e` records one. It shows that the
> collectors work and what they report.

### Where it ran

| Piece | Here |
|---|---|
| Date (UTC) | 2026-11-19, 03:29 – 03:35 |
| Tree | `develop` at `46433fc7` plus this pull request's files, uncommitted at the time (`checkout describe` reads `kit-py/v0.1.8-453-g46433fc7-dirty`) |
| Stack | `compose.distributed.yaml`, all nineteen services built from that tree on an Apple Silicon Mac, readiness 204 from every one |
| Isolation | the project renamed `p888`: `docker compose -p p888 config`, then the external network `mosaiq`, the volumes `mosaiq-pgsocket` and `mosaiq-redis-acl` and the image tag `local` renamed, every service port but the gateway's dropped, the gateway on `127.0.0.1:18080`. Brought down with `down -v` afterwards; it removed only its own volumes |
| Accounts | the seed's demonstration accounts (root README), on that throwaway database only |
| Web | gunicorn on `127.0.0.1:15888`, as the F20-03 runner starts it, reading the stack's PostgreSQL through a TCP proxy container on the project's own network |

Settings:

```sh
export MOSAIQ_BASE_URL=http://127.0.0.1:18080 MOSAIQ_WEB_URL=http://127.0.0.1:15888 \
  MOSAIQ_DB_EXEC="docker exec -i -e PGUSER=postgres p888-db-1" \
  MOSAIQ_ACCEPTANCE_EMAIL=user3@mosaiq-demo.com \
  MOSAIQ_ACCEPTANCE_PASSWORD=<the root README's demonstration password>
```

1. `f29_03 snapshot --store 1 --product 2 --product 3 --customer
   00000000-0000-0000-0000-000000000004`: **2 checks, 2 held.** Portal,
   stock-service and `inventory` agreed: 13, 14.
2. The sale: `POST /api/sales/v1/sales` as `user3@` (`STORE_MANAGER`),
   2 × product 2 and 1 × product 3, total 71.25: 201, sale 308.
3. `f29_03 sale --sale-id 308 --idempotency-key <its key>`: **15 checks, 15
   held.**

| Criterion | What held |
|---|---|
| "Venta Android" | The sale reads back in JSON; the `transaction` row carries the request's key (`key_matches` true) and the response's total, 71.25; one `sales.sale_capture` row |
| "Paridad web" | After the sale the portal, stock-service and `inventory` agreed for both products (11, 13), and each of the three fell by the quantity sold (2, 1). The customer's profile page counted one more purchase (1 → 2) and 71.25 more spend |
| "Consulta escritorio", the protocol's half | `GET /api/sales/v1/sales/308` in XML: 200 `application/xml`, valid against `contracts/sales/sales.xsd` (`xmllint`) |

No file the collectors wrote held the demonstration password or anything
shaped like a token.

A first attempt at the sale, at store 2, was refused 403: `user3@` is assigned
to stores 1, 9, 17 and 25 only (F19-07). Step 1 of the runbook assigns the
account to the sale's store for that reason.

### Not run here

- The phone (steps 1 and 3) and the desktop app (step 5): #760 is open, and
  the phone's run is F20-01's procedure on the instance.
- Anything on the instance.
