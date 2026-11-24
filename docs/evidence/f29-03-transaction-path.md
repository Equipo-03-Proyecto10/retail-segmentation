# F29-03 (#888): the acceptance tests of the transaction path, on the instance

> **Status: run on 2026-11-24 (04:52 - 04:56 UTC, the evening of 2026-11-23 in
> Mexico City) on the instance's release #1188 (`main` at `50bf7d5c`, same tree as
> `fdf0caed`). The three tests hold, with one deviation from the runbook: the
> sale the collectors checked was recorded through the gateway in the floor
> app's JSON, not on the phone.** The phone's own sale is
> [F20-01's sale 307](f20-01-android-sale.md), and Electron's read of it in XML
> is [F20-02's](f20-02-electron-sale-campaign.md); both are linked here, not
> copied. The "before" snapshot, which "Paridad web" needs, did not exist for
> sale 307, so sale 308 was recorded with a snapshot taken first. #462 (the
> demonstration script) is still open and was not waited for. The run was made by
> the team's automation. The [local dry run](#local-dry-run-not-the-acceptance)
> below is not the acceptance.

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

### Where it ran

| Piece | Value |
|---|---|
| Identified version | `fdf0caedb301d5308ea804ff9cb9bb5b43ce73cb` of `develop` (tree of `main` at `50bf7d5c`, what the instance runs). The collectors' `after` summary names `85e109cb`, `fdf0caed` plus the portal reader's `User-Agent` fix. No F20-03 runner report was made for this evening's release |
| Date and time (UTC) | 2026-11-24 04:52:25 (account) - 04:55:55 (last collector); the account deactivated at about 04:57 |
| Instance | `https://mosaiq.maxthecoder.online/`, web commit `50bf7d5c48336a39f89f34f9bb8d44a78057fb06` |
| Android build | The phone's sale is [F20-01's](f20-01-android-sale.md): MOSAIQ Floor 0.1.0, release build of `fdf0caed`, run-only signing key. No phone took part in sale 308 |
| Desktop build | [F20-02's](f20-02-electron-sale-campaign.md): desktop 0.1.0, checkout `fdf0caed`, read sale 307 |
| Account | `f29-03-store-manager@example.test`, `STORE_MANAGER`, user `b0111a08-bc6f-4638-8e49-5b3bf10fdd92`, store 30 (correlation ids `f29-03-create-user`, `f29-03-assign-store`); deactivated afterwards (`f29-03-deactivate`, 200, `is_active` false) |
| Who ran each step | Every step: the team's automation, from a WSL2 workstation against the edge, the database read over SSH |
| Approval | The owner approved the live acceptance runs of 2026-11-23 evening before they started (the batch brief records it) |

### Versions

From [`f29-03/20261124T0453Z/after/summary.md`](f29-03/20261124T0453Z/after/summary.md) (contracts) and the releases read on the instance for [F20-01 step 7](f20-01-android-sale.md#7-read-the-versions-on-the-instance):

| Service | Release (`current`) | Contract (`info.version`) |
|---|---|---|
| auth | `releases/0.1.4` | 1.1.0 |
| users | `releases/0.2.0` | 1.4.0 |
| stores | `releases/0.2.0` | 1.4.0 |
| products | `releases/0.3.0` | 1.4.0 |
| stock | `releases/0.3.0` | 1.3.0 |
| customers | `releases/0.4.0` | 1.8.1 |
| sales | `releases/0.7.0` | 1.10.1 |

### AC-01 "Venta Android"

The phone's sale is [F20-01's sale 307](f20-01-android-sale.md#4-the-sale-over-https) (store 30, customer `00000000-0000-0000-0000-000000000028`, 41.25), recorded from the release build over HTTPS with its screenshot, its masked request and 201, and its rows read with parameters: that is this criterion's phone half, and it is not repeated here.

This run's own sale, **308**, was recorded through the gateway in the same JSON the app sends (`POST /api/sales/v1/sales`, `channel_id` 3, `Idempotency-Key` `99a05079-6950-48f5-b3d8-f592b12a63ce`, `X-Correlation-ID` `f29-03-sale-045407`), by the step 1 account at 04:54:07, with `occurred_at` 15 s before the call (the phone's sale had met a 422 `future_dated` for a clock a fraction ahead; see F20-01). The emulator was not started for this run.

| What | Value | Evidence |
|---|---|---|
| `POST /api/sales/v1/sales` -> 201 | sale 308, 41.25, `X-Correlation-ID` `f29-03-sale-045407`, `Idempotency-Key` `99a05079-6950-48f5-b3d8-f592b12a63ce` | the response, below |
| The sale reads back in JSON | 200 | [`after/exchanges.md#exchange-11`](f29-03/20261124T0453Z/after/exchanges.md#exchange-11) |
| `transaction` row, key matches | `key_matches` true, `OBSERVED`, total 41.25 | [`after/rows.md#rows-1`](f29-03/20261124T0453Z/after/rows.md#rows-1) |
| `transaction_line` rows | product 1 x 1 at 18.75, product 2 x 1 at 22.50 | same |
| `sales.sale_capture`, recorded by the account | exactly one row | same |

```http
HTTP/2 201
location: /v1/sales/308
x-correlation-id: f29-03-sale-045407

{"sale_id":308,"customer_id":"00000000-0000-0000-0000-000000000028","store_id":30,"channel_id":3,
 "occurred_at":"2026-11-24T04:53:54.206689Z","total":"41.25",
 "lines":[{"product_id":1,"quantity":1,"unit_price":"18.75"},{"product_id":2,"quantity":1,"unit_price":"22.50"}]}
```

### AC-02 "Paridad web"

[`before/summary.md`](f29-03/20261124T0453Z/before/summary.md) at 04:52:35 - 04:53:48, [`after/summary.md`](f29-03/20261124T0453Z/after/summary.md) at 04:54:19 - 04:55:55. The portal was read as `STORE_MANAGER` (`catalog.read`, `segment.read`).

| Product | Portal before -> after | Service before -> after | `inventory` before -> after | Sold |
|---|---|---|---|---|
| 1 (SKU-00001), store 30 | 40 -> 39 | 40 -> 39 | 40 -> 39 | 1 |
| 2 (SKU-00002), store 30 | 41 -> 40 | 41 -> 40 | 41 -> 40 | 1 |

| Customer's profile (customer `...0028`) | Before | After |
|---|---|---|
| Purchases (30 days), portal | 1 | 2 |
| Spend (MXN), portal | 41.25 | 82.50 |
| Purchases (database) | 12 | 13 |
| Spend (database) | 5340.75 | 5382.00 |

Checks: before 2 checks, 2 held; after 15 checks, 15 held.

### AC-03 "Consulta escritorio"

The desktop app's read is [F20-02's](f20-02-electron-sale-campaign.md): the Electron client read sale 307 (`GET /api/sales/v1/sales/307`, 200 `application/xml`, correlation id `f313cff2-3207-4f8b-95be-361f4abf560a`), validated it against the bundled `sales.xsd` and showed it on the Sales screen. It is linked, not copied.

| What | Value | Evidence |
|---|---|---|
| The desktop app's read | sale 307 in XML, accepted by its own validation | [F20-02](f20-02-electron-sale-campaign.md) |
| The same read from the instance for this run's sale 308, `xmllint` | `GET /api/sales/v1/sales/308` in XML: 200 `application/xml`, `contracts/sales/sales.xsd: - validates` | [`after/exchanges.md#exchange-12`](f29-03/20261124T0453Z/after/exchanges.md#exchange-12), [`after/sale.xml`](f29-03/20261124T0453Z/after/sale.xml) |

### Found on the way

1. **Cloudflare error 1010 refused the portal reader** (`Portal` sends urllib's default `Python-urllib/3.x`). `tests/acceptance/evidence.py` now sends `User-Agent: mosaiq-acceptance/1` (the gateway reader uses `http.client`, which sends none, and was not affected).
2. Sale 308 was recorded with `occurred_at` 15 s in the past because sales-service refuses a sale dated after its own clock with no tolerance (the F20-01 finding), not because the criterion needs it.
3. Customer 28's segment was already `CHAMPION`; sale 308 did not change it.

### What was not done

- No phone took part in sale 308, and no desktop window was driven in this run: the phone's and the desktop's halves are F20-01's and F20-02's, linked above. #462 (the demonstration script) is not closed; the acceptance was run without waiting for it.
- The portal's HTML (`pages/`) and `record.json` stay outside the repository, as step 6 says.
- Sale 308's inventory (store 30, products 1 and 2) was left decremented.

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
