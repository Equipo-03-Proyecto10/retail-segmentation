# Instance findings — fixes and verification

The 2026-09-06 browser review reported 17 findings. Its local findings report and
handoff were recovered when the [shared artifact](https://claude.ai/code/artifact/35d9b371-e25f-4246-b3e5-e15e983a29ce)
returned HTTP 403 for its content. This work is on `fix/76-instance-findings`,
based on `develop` at `22cf20d`, and contributes to the consistency and error
handling review in #76.

## Finding disposition

| Finding | Result |
|---|---|
| 01 — Published demo credentials | **Pending operator input.** Production `account-report` still reports 30 demonstration accounts that can sign in. The new administrator's name and email are required. Follow [the existing runbook](../runbook-instance-accounts.md); enter the password interactively, never in an issue or command argument. |
| 02 — Duplicate store ID returns 500 | Store writes roll back a uniqueness conflict and return an explanatory 409 form. |
| 03 — Rejected creates become edit forms | Store, category, channel and role forms use the route to determine create/edit state. A rejected create retains its ID input, values and message. |
| 04 — Fake image accepted | Upload validation checks the JPEG, PNG or WebP signature against the declared type after the size check, and rewinds the stream. Plain text and mismatched signatures are refused before any file or product is written. This is signature validation, not a full image decoder. |
| 05 — HSTS drift | Already resolved on the serving instance at `c192631`: the effective policy is `max-age=300`. |
| 06 — Orphaned product image | A confirmed product delete removes its image only after the database deletion succeeds. A refused delete retains the image. The previously reported orphan was separately checked for product references before removal from the served directory. |
| 07 — Dead write controls for readers | Listings keep `catalog.read` access; creation links, edit links, delete forms and their table headers require the existing `can('catalog.write')` template helper. Route authorization and the permission matrix are unchanged. |
| 08 — Blank segment window and accidental execution | Blank, whitespace-only and missing POST windows return 400. A valid first POST shows the selected window and consequences; only an explicit second confirmation POST runs the recalculation. Cancel returns to the form. The service's `None` default remains available to non-HTTP callers. |
| 09 — Raw relationship IDs | Product listings join category names; customer listings left-join segment names and retain unassigned customers. Search values remain SQL parameters. |
| 10 — One-click delete and no save feedback | All five catalog deletes first render the named record and a cancel link. Successful catalog saves/deletes and user account actions produce feedback after redirect. |
| 11 — Out-of-range pages | Catalog and administrator listings redirect to their last real page while preserving query and route arguments. Unreasonably large numeric page parameters fall back to page 1 before reaching PostgreSQL. |
| 12 — Invalid audit dates silently ignored | Invalid or reversed dates return an explanatory 400 form without reading unfiltered entries. |
| 13 — Missing response headers | The NGINX config adds `nosniff`, frame denial, a referrer policy and CSP. A narrow script-source exception preserves the published chart examples; application pages use same-origin scripts. Direct image responses also set `nosniff`. **NGINX deployment remains pending review.** |
| 14 — Generic form titles | All administrator forms and listings have meaningful browser titles. |
| 15 — Stale landing text | Landing copy describes the available modules and links to sign-in. |
| 16 — Missing favicon | The base template loads a local SVG; the conventional `/favicon.ico` URL redirects to it. |
| 17 — Browser artifacts untracked | `.playwright-mcp/` is ignored; the existing session files are retained locally. |

The previous test run's changes to customer segment assignments have not been
restored. Whether to keep the computed assignments or restore their audited
previous values remains a user decision. No production SQL, account, or segment
changes were made by this fix pass. The unreferenced production image was moved
to `/tmp/mosaiq-orphaned-upload-brepvrsl/7af69252c97943528332078c43ba0f82.png`
on the instance, in a private directory outside the upload root. The original
served path was confirmed absent; the file remains recoverable until the
instance clears its temporary files.

**Since this was written.** The three items this pass left open are resolved,
and the record of each is in
[`f6-07-proof-of-deployment.md`](f6-07-proof-of-deployment.md) rather than
edited into the table above. Finding 01 — the administrator was provisioned on
the instance and the thirty demonstration accounts deactivated, on 2026-09-07.
Finding 13 — the response headers were confirmed live on the published host,
with HSTS matching the repository's value. And the segment assignments were
restored to their audited previous values at 19:01:55 UTC that day, the restore
itself audited. The orphaned image in `/tmp` is not among them and may well be
gone; nothing references it.

## Verification

Baseline: `517 passed`, Black and Ruff passed. New regression tests first
reproduced the store 500, invisible conflict messages, fake PNG acceptance,
unconfirmed operations, invalid audit filters, out-of-range pages and dead
controls. The updated suite reports `573 passed`.

- `pytest -q`: passed.
- `black --check .`: passed.
- `ruff check .`: passed.
- `git diff --check`: passed.
- NGINX 1.30.4: the committed configuration passed `nginx -t` using temporary
  certificates, local ports and log paths. Actual responses through the local
  proxy carried all five expected headers.
- PostgreSQL 18.6: the three committed SQL scripts ran unchanged, in order,
  against a new isolated cluster. All 17 non-exempt tables had at least 30 rows.
  The existing `sql/seed-exempt.txt` exceptions remain: `role` has 7 rows and
  `channel` has 5. This is an existing discrepancy with the literal
  30-per-table rule, not a new table or seed change in this work.
- Real database checks: all five duplicate creates returned usable 409 forms;
  joins returned display names with and without searches; out-of-range requests
  reached the last page; successful saves showed feedback; deletion required
  confirmation; a valid PNG round-tripped intact and was removed after deletion.
- The application refused a second administrator with a readable message.
  A separate direct parameterized `INSERT` was refused by the actual
  `ux_app_user_single_administrator` index. Both checks used the isolated cluster.
- Chromium through the local HTTPS proxy: 16 affected pages checked at both
  375 px and 1440 px, with no page-level horizontal overflow. Duplicate form
  recovery, both confirmation steps and cancellation worked. All five analyst
  catalog listings retained read access and omitted write controls. The favicon
  loaded and no page JavaScript errors occurred.

## Screenshots

| Flow | 375 px | 1440 px |
|---|---|---|
| Duplicate category | [Mobile](instance-findings-conflict-375.png) | [Desktop](instance-findings-conflict-1440.png) |
| Read-only administrator listing | [Mobile](instance-findings-readonly-375.png) | [Desktop](instance-findings-readonly-1440.png) |
| Segment confirmation | [Mobile](instance-findings-segment-confirm-375.png) | [Desktop](instance-findings-segment-confirm-1440.png) |
| Delete confirmation | [Mobile](instance-findings-delete-confirm-375.png) | [Desktop](instance-findings-delete-confirm-1440.png) |
| Invalid audit filter | [Mobile](instance-findings-audit-date-375.png) | [Desktop](instance-findings-audit-date-1440.png) |

## Remaining delivery gates

Implementation and local verification are recorded here. The story is **not yet
Done** under [process §6](../process.md#6-definition-of-done): independent team
verification, an approved pull request into `develop`, release to `main`,
deployment and issue closure remain outstanding. No merge or deployment was
performed. No schema, dependency, or environment configuration was added.
