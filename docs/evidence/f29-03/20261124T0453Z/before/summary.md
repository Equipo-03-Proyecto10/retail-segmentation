# F29-03 snapshot before the sale

Started 2026-11-24T04:52:35Z, finished 2026-11-24T04:53:48Z.
2 checks, 2 held, 0 did not.

- **service reader:** `f29-03-store-manager@example.test`
- **portal reader:** `f29-03-store-manager@example.test`
- **portal:** `https://mosaiq.maxthecoder.online`
- **gateway:** `https://mosaiq.maxthecoder.online`

| Criterion | Case | Expected | Actual | Evidence | |
|---|---|---|---|---|---|
| Paridad web | before: product 1 (SKU-00001), portal = service = row | 40 = 40 = 40 | 40 = 40 = 40 | [1](exchanges.md#exchange-12), [2](exchanges.md#exchange-11), [3](rows.md#rows-1) | ✓ |
| Paridad web | before: product 2 (SKU-00002), portal = service = row | 41 = 41 = 41 | 41 = 41 = 41 | [1](exchanges.md#exchange-14), [2](exchanges.md#exchange-13), [3](rows.md#rows-2) | ✓ |

## Versions

| Piece | Version |
|---|---|
| checkout commit | `fdf0caedb301d5308ea804ff9cb9bb5b43ce73cb` |
| checkout describe | `kit-py/v0.1.8-776-gfdf0caed-dirty` |
| auth contract | `1.1.0` |
| users contract | `1.4.0` |
| stores contract | `1.4.0` |
| products contract | `1.4.0` |
| stock contract | `1.3.0` |
| customers contract | `1.8.1` |
| sales contract | `1.10.1` |
