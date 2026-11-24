# F20-12 (#784): one result for release #1188, the instance's half

Story F20-12 ([#784](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/784)).
Release #1188 is `main` at `50bf7d5c48336a39f89f34f9bb8d44a78057fb06`, whose tree is the
one of `develop` at `fdf0caed`. This page holds what was observed **on the instance** on
2026-11-24 (04:06Z to 04:17Z, the evening of 2026-11-23 in Mexico City). The
consolidated document `ops/verify/publish.py` writes also needs F20-03's report at
this commit, which does not exist yet: see [What is missing](#what-is-missing).

## Result

**Not yet one result.** The instance's half stands at **failed**: the negative suite
has two failed cases, and 13 of 24 containers carry a revision other than `50bf7d5c`
(see [the images](#the-images-the-instance-runs)).

## The negative suite on the instance

`tests/negative` at `50bf7d5c`, run as root on the instance against `127.0.0.1:443`
under the published host name, with the administrator's credential read as data from
its root-only file, `MOSAIQ_DIRECT_PORTS=1`, `MOSAIQ_CHECK_REDIS=1` and
`MOSAIQ_DB_EXEC="runuser -u postgres --"`, as
[`tests/negative/README.md`](../../tests/negative/README.md) "The instance" says. The suite
created one active account for each other role and deactivated every one at the end (no
`f18-04-*@example.test` account is active afterwards). It ran 616 s.

Totals: **145 passed, 2 failed, 28 skipped** (175 cases).
Raw: [`negative-instance.xml`](f20-12-50bf7d5c/negative-instance.xml),
[`negative.log`](f20-12-50bf7d5c/negative.log),
[`negative-evidence.md`](f20-12-50bf7d5c/negative-evidence.md) (its own record of every case).

| Module | Passed | Failed | Skipped |
|---|---:|---:|---:|
| `test_auth` | 15 | 0 | 0 |
| `test_infrastructure` | 3 | 1 | 20 |
| `test_privacy` | 12 | 0 | 0 |
| `test_representations` | 52 | 0 | 5 |
| `test_revocation` | 1 | 0 | 1 |
| `test_roles` | 19 | 0 | 0 |
| `test_rules` | 2 | 1 | 1 |
| `test_store_scope` | 21 | 0 | 1 |
| `test_tokens` | 20 | 0 | 0 |

### Failures

| # | Case | Message | Class | Issue |
|---|---|---|---|---|
| 1 | `test_infrastructure::test_the_grant_and_boundary_checks_print_nothing` | `check_grants.sh` printed 636 lines: `tail: cannot open '/var/tmp/mosaiq-1124-S1/tree/contracts/db/grants.csv' for reading: Permission denied` | The run's own fault: the copy of the tree sat under a directory only its owner could enter, so `postgres` could not read it. Not a finding about the deployment | none needed; **re-run below** |
| 2 | `test_rules::test_sales_refuses_a_sale_its_stock_cannot_cover` | `AssertionError: 422 discount_without_promotion` (expected `409 stock_unavailable`) | **Unclassified.** The sale the suite sends charges `unit_price` 1.00; sales-service refuses a line below its list price with `discount_without_promotion` before it looks at stock. Either the fixture should use the product's list price, or the order of the two rules is not the one the test expects | None yet. Suggested: "negative: the stock-cannot-cover sale is refused for its price, not its stock" |

**Re-run of case 1.** After the directory was made enterable, that one case was run alone,
as root on the instance, the same way: **1 passed**
([`negative-rerun.xml`](f20-12-50bf7d5c/negative-rerun.xml)). Case 2 was not re-run: it is
deterministic from its fixture. The totals above are the full run's, not a mix.

### Skips

| Cases | Reason the runner gave |
|---:|---|
| 18 | `MOSAIQ_PROBE_HOST is not set; the outside port probe is disabled.` (probing the internal ports from outside needs a second machine) |
| 2 | `local stack only` / `local stack only, with its container name`: the direct INSERT of a second administrator writes to the live accounts table; stopping `redis-state` stops a shared container |
| 1 | `local stack only, with its container names`: the revoked-session case that stops containers |
| 1 | `an accepted sale persists; local stack only` (a reused `Idempotency-Key`) |
| 1 | `rows in another store persist; local stack only` |
| 1 | `segments takes no body` |
| 1 | `recommendations takes no body` |
| 1 | `audit-trail takes no body` |
| 1 | `analytics-kpis takes no body` |
| 1 | `auth has no paginated read` |

The suite's own record also lists, as not run on the instance, the wrong `iss` and `aud`
tokens (they need auth's private key) and the customers logging case.

## The images the instance runs

`ops/verify/instance-images.sh` on the instance, as root
([`instance-images.tsv`](f20-12-50bf7d5c/instance-images.tsv)). 11 of 24 containers carry
`50bf7d5c` as `org.opencontainers.image.revision`.

| Container | Image | Registry digest | Revision | Version | At 50bf7d5c |
|---|---|---|---|---:|:-:|
| `mosaiq-analytics-kpis-analytics-kpis-1` | `mosaiq-analytics-kpis:0.4.0` | `sha256:deb863e0cd5dd123272d9e099ae6180b162778384f3bb7051c13d0291abddca6` | `50bf7d5c` | 0.4.0 | ✓ |
| `mosaiq-audit-trail-audit-trail-1` | `mosaiq-audit-trail:0.1.5` | `sha256:b001002d893cb5d1aabac4713b355d793e81556596ef0693dd589d8062afa16c` | `50bf7d5c` | 0.1.5 | ✓ |
| `mosaiq-auth-auth-1` | `mosaiq-auth:0.1.4` | `sha256:c5bde30c46e990bfea9c7a5065222166508a753a9bb646bf7157b8eeaad18284` | `3c085829` | 0.1.4 | ✗ |
| `mosaiq-campaigns-campaigns-1` | `mosaiq-campaigns:0.4.0` | `sha256:fc8c55ee2040046c31bc374bf706b9534a79c780e54301420271d05101596bc1` | `50bf7d5c` | 0.4.0 | ✓ |
| `mosaiq-categories-categories-1` | `mosaiq-categories:0.1.9` | `sha256:29987abc3bd3af52d8f09d988531922edc78f4b4976c8f190eeb92b03de36828` | `3c085829` | 0.1.9 | ✗ |
| `mosaiq-check-ins-check-ins-1` | `mosaiq-check-ins:0.3.0` | `sha256:e60fb7e5ed8a4f1b02151b3608ac9f2e22681b2aee817be5f24c716fb9020d7e` | `3c085829` | 0.3.0 | ✗ |
| `mosaiq-customer-preferences-customer-preferences-1` | `mosaiq-customer-preferences:0.3.0` | `sha256:7ba757a03387ec9686dcd9ffcf89d502d04e6e09de56721845dfe4e88ddbfda5` | `3c085829` | 0.3.0 | ✗ |
| `mosaiq-customers-customers-1` | `mosaiq-customers:0.4.0` | `sha256:668a38a219399559ba30c7893367e25d8c01a2ca3609d1e3711941c822babc8f` | `3c085829` | 0.4.0 | ✗ |
| `mosaiq-engagement-engagement-1` | `mosaiq-engagement:0.4.0` | `sha256:835c4e9ee916cf065f3c608f90b2ff7c8d98b00602e36c6359e658f93eec8e82` | `3c085829` | 0.4.0 | ✗ |
| `mosaiq-experimentation-experimentation-1` | `mosaiq-experimentation:0.8.1` | `sha256:79d322f60e65b5b18aff8db13d4c4d9ae36875a8994df321d4a38d0e30521df2` | `50bf7d5c` | 0.8.1 | ✓ |
| `mosaiq-infra-mongodb-1` | `mongo:7.0.41@sha256:b096b4cb9269f3ebcf363be63f1c50920f786879d03a1890347a3bf33f1f0df0` | `sha256:b096b4cb9269f3ebcf363be63f1c50920f786879d03a1890347a3bf33f1f0df0` | `22.04` | — | ✗ |
| `mosaiq-infra-redis-cache-1` | `redis:7.4.11-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499` | `sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499` | `none` | — | ✗ |
| `mosaiq-infra-redis-state-1` | `redis:7.4.11-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499` | `sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499` | `none` | — | ✗ |
| `mosaiq-model-registry-model-registry-1` | `mosaiq-model-registry:0.5.0` | `sha256:6fb221dc4db009b6f6d34f09ea77e17bee0ded1e231a83c42b831c2eefc04563` | `50bf7d5c` | 0.5.0 | ✓ |
| `mosaiq-ops-console-ops-console-1` | `mosaiq-ops-console:0.1.0` | `sha256:ad097f708502b3af111d1649a7301896046b5ebd30e61ba100d7d82b0aa2b9a5` | `2cd641e9` | 0.1.0 | ✗ |
| `mosaiq-products-products-1` | `mosaiq-products:0.3.0` | `sha256:1b395a2108f54d664e5286c46e7ad9f4faa34a8683c1044a8159e42d8b50ced1` | `50bf7d5c` | 0.3.0 | ✓ |
| `mosaiq-recommendations-recommendations-1` | `mosaiq-recommendations:0.5.0` | `sha256:9d45c24b37825d20d3d2d3a6dc952fcbf63d5000ffdf9080836e7484312014c7` | `50bf7d5c` | 0.5.0 | ✓ |
| `mosaiq-rfm-engine-rfm-engine-1` | `mosaiq-rfm-engine:0.8.0` | `sha256:b0976394eb4ffd9d68ccea75f8969c569bfc609e41f55cd99644b6c0c3d46987` | `50bf7d5c` | 0.8.0 | ✓ |
| `mosaiq-sales-sales-1` | `mosaiq-sales:0.7.0` | `sha256:b0d2b38c398e5cd1975e196393dff25744c5495ba277364adddeac203e944757` | `50bf7d5c` | 0.7.0 | ✓ |
| `mosaiq-sales-sales-import-worker-1` | `mosaiq-sales:0.7.0` | `sha256:b0d2b38c398e5cd1975e196393dff25744c5495ba277364adddeac203e944757` | `50bf7d5c` | 0.7.0 | ✓ |
| `mosaiq-segments-segments-1` | `mosaiq-segments:0.6.0` | `sha256:05430790af20077220e2eb49479a912a95c75682b5c5b38d249a43079059f8a1` | `50bf7d5c` | 0.6.0 | ✓ |
| `mosaiq-stock-stock-1` | `mosaiq-stock:0.3.0` | `sha256:60f26716298c6a26fef1eeaa681a8d6bae9859d3cd93d7aa38bfdc0055edcfdc` | `3c085829` | 0.3.0 | ✗ |
| `mosaiq-stores-stores-1` | `mosaiq-stores:0.2.0` | `sha256:5c4821537d4fb569edffe0abac1f930cb43a1de86b6f9600f4e67f23464d09c8` | `3c085829` | 0.2.0 | ✗ |
| `mosaiq-users-users-1` | `mosaiq-users:0.2.0` | `sha256:c9fc9a518252a75b260c5680879e056b644d3e49413b0137b9078474cf0d4c89` | `3c085829` | 0.2.0 | ✗ |

What the ✗ rows are, from the repository and not from the instance:
- **Nine services** (auth, categories, check-ins, customer-preferences, customers, engagement,
  stock, stores, users) run an image built at release #1079 (`3c085829`). Nothing under
  `services/<name>`, `packages/` or `contracts/<name>` differs between `3c085829` and
  `50bf7d5c` (`git diff --stat` is empty for each), so the code is the same; the label is not.
  `publish.py` counts them as not at the commit, and this page does the same. Whether a
  release must rebuild every image, or the rule should accept an image whose source did not
  change, is for the team to decide.
- **The ops console** runs `0.1.0` built at `2cd641e9`; the tree has `0.2.0`
  (`ops/console` differs in 10 files, among them F20-06's oldest-entry age). It is not
  deployed at this version.
- **MongoDB and the two Redis instances** are upstream images: they carry no revision label
  (MongoDB's `22.04` is Ubuntu's).

## What is missing

F20-03 at `50bf7d5c`: **no run exists.** The last "Verify one commit" run is of `43d67d0d`
(2026-11-17, failed); none was dispatched for this commit. F20-03 rebuilds every image from the
commit and runs every suite in a Docker daemon of its own (a full run is hours), so it cannot
run on the instance and there is no Docker on the workstation that collected this. It is
dispatched with `gh workflow run verify.yml --ref develop -f commit=50bf7d5c48336a39f89f34f9bb8d44a78057fb06 -f twice=true`.

Then the consolidated document is one command from this directory's files:

```bash
python3 ops/verify/publish.py --verify verify-run/first \
  --negative-junit docs/evidence/f20-12-50bf7d5c/negative-instance.xml \
  --negative-evidence docs/evidence/f20-12-50bf7d5c/negative-evidence.md \
  --instance-images docs/evidence/f20-12-50bf7d5c/instance-images.tsv \
  --issues issues.tsv
```

`issues.tsv` needs one line for each failed case of the junit it is given, and `publish.py`
stops until every failure has an issue, so none is explained away. The full run's junit has
two failed cases (above). Case 1 needs a line too, unless the junit is edited, which this
page does not do: the re-run is recorded beside it, not merged into it. Add the F20-03
failures, if any.

## The other results of this night

The acceptance runs on this release, each with its own record (links are to the evidence
as each pull request adds it):

| Story | Result | Record |
|---|---|---|
| F20-06 (#764) stream propagation | every group measured, 500-event backlog caught up in 13.7 s, none lost | [`f20-06-stream-propagation.md`](f20-06-stream-propagation.md) |
| F20-01 (#759) Android sale | done: every criterion met, with two findings in its record | `f20-01-android-sale.md` |
| F20-02 (#760) Electron | partial: criterion 3's deviation awaits Marcelo | `f20-02-electron-sale-campaign.md` |
| F24-08 (#831) stock and recommendation | 26 of 26 checks | [`f24-08-stock-recommendation-update.md`](f24-08-stock-recommendation-update.md) |
| F29-03 (#888) transaction path | partial: sale through the gateway, not the phone | `f29-03-transaction-path.md` |
| F29-04 (#889) segmentation path | done | [`f29-04-segmentation-path.md`](f29-04-segmentation-path.md) |
| F29-05 (#890) personalization and A/B | 8, 21 and 11 checks held, on seeded experiments | `f29-05-personalization-campaign.md` |
| F29-06 (#891) privacy and revoked session | "Privacidad" and "Sesión revocada" held; "Desconexión" pending | [`f29-06-privacy-revocation-disconnection.md`](f29-06-privacy-revocation-disconnection.md) |
