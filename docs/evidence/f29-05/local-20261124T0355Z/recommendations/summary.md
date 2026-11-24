# F29-05 Recomendaciones

Started 2026-11-24T03:55:45Z, finished 2026-11-24T03:55:46Z.
8 checks, 8 held, 0 did not.

- **customer:** `00000000-0000-0000-0000-000000000003`
- **store:** `3`
- **reader:** `user2@mosaiq-demo.com`
- **gateway:** `http://localhost:18129`
- **products with no stock here:** `35`
- **read:** `product 6: absent; exclusions: already_bought 14, out_of_stock 21, PERSONALIZATION_WITHDRAWN 1`

| Criterion | Case | Expected | Actual | Evidence | |
|---|---|---|---|---|---|
| Recomendaciones | the store has active products with no stock (the check is not empty) | at least one | 35 | [1](rows.md#rows-1) | ✓ |
| Recomendaciones | no product without stock here is offered (primary or alternative) | none | none | [1](exchanges.md#exchange-6), [2](rows.md#rows-1) | ✓ |
| Recomendaciones | every offered item states at least one unit on hand | none below 1 | none below 1 | [1](exchanges.md#exchange-6) | ✓ |
| Recomendaciones | the decision counts the exclusions as `out_of_stock` | at least 1 | 21 | [1](exchanges.md#exchange-6) | ✓ |
| Recomendaciones | the stored decision records the same items and exclusions | {'items': [(1, 2, 'primary', 2, 10, None, None), (2, 1, 'primary', 1, 34, None, None), (3, 3, 'primary', 3, 61, None, None), (4, 5, 'primary', 5, 18, None, None)], 'exclusions': {'already_bought': 14, 'out_of_stock': 21, 'PERSONALIZATION_WITHDRAWN': 1}} | {'items': [(1, 2, 'primary', 2, 10, None, None), (2, 1, 'primary', 1, 34, None, None), (3, 3, 'primary', 3, 61, None, None), (4, 5, 'primary', 5, 18, None, None)], 'exclusions': {'PERSONALIZATION_WITHDRAWN': 1, 'already_bought': 14, 'out_of_stock': 21}} | [1](exchanges.md#exchange-6), [2](rows.md#rows-2) | ✓ |
| Recomendaciones | F24-08 `cycle` on the instance (#831): every check held | 17 of 17 | 17 of 17 | [1](../../../f24-08/local-20261123T1753Z/cycle/summary.md) | ✓ |
| Recomendaciones | F24-08: a product counted to zero was no longer offered after the bound | held | held | [1](../../../f24-08/local-20261123T1753Z/cycle/summary.md) | ✓ |
| Recomendaciones | F24-08 `exclusions` on the instance (#831): every check held | 9 of 9 | 9 of 9 | [1](../../../f24-08/local-20261123T1753Z/exclusions/summary.md) | ✓ |

## Versions

| Piece | Version |
|---|---|
| checkout commit | `fdf0caedb301d5308ea804ff9cb9bb5b43ce73cb` |
| checkout describe | `kit-py/v0.1.8-776-gfdf0caed-dirty` |
| auth contract | `1.1.0` |
| stock contract | `404` |
| products contract | `404` |
| recommendations contract | `2.0.4` |
