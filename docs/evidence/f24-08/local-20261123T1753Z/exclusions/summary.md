# F24-08 discontinued and restricted products

Started 2026-11-23T17:53:41Z, finished 2026-11-23T17:53:44Z.
9 checks, 9 held, 0 did not.

- **customer:** `00000000-0000-0000-0000-000000000003`
- **store:** `3`
- **discontinued product:** `5`
- **reader:** `user2@mosaiq-demo.com`
- **gateway:** `http://127.0.0.1:18831`
- **restricted products in stock:** `[4]`
- **read:** `product 5: absent; exclusions: already_bought 14, not_sellable 1, out_of_stock 21, PERSONALIZATION_WITHDRAWN 1`
- **earlier decision:** `product 5: primary at position 4 (store 3); exclusions: already_bought 14, out_of_stock 21, PERSONALIZATION_WITHDRAWN 1`

| Criterion | Case | Expected | Actual | Evidence | |
|---|---|---|---|---|---|
| 3. Excluidos con motivo | product 5 is not sellable (is_active false) | f | f | [1](rows.md#rows-1) | ✓ |
| 3. Excluidos con motivo | the customer restricted a category that has products in stock here | at least one | 1 | [1](rows.md#rows-2) | ✓ |
| 3. Excluidos con motivo | the discontinued product 5 is not offered, nor reported elsewhere | absent | absent | [1](exchanges.md#exchange-6) | ✓ |
| 3. Excluidos con motivo | no product of a restricted category is offered | none | none | [1](exchanges.md#exchange-6), [2](rows.md#rows-2) | ✓ |
| 3. Excluidos con motivo | the decision counts `not_sellable` | at least 1 | 1 | [1](exchanges.md#exchange-6) | ✓ |
| 3. Excluidos con motivo | the decision counts `PERSONALIZATION_WITHDRAWN` | at least 1 | 1 | [1](exchanges.md#exchange-6) | ✓ |
| 3. Excluidos con motivo | the stored decision records the same exclusions | {'already_bought': 14, 'not_sellable': 1, 'out_of_stock': 21, 'PERSONALIZATION_WITHDRAWN': 1} | {'already_bought': 14, 'not_sellable': 1, 'out_of_stock': 21, 'PERSONALIZATION_WITHDRAWN': 1} | [1](exchanges.md#exchange-6), [2](rows.md#rows-3) | ✓ |
| 3. Excluidos con motivo | before, product 5 was offered here | offered | offered | [1](rows.md#rows-4) | ✓ |
| 3. Excluidos con motivo | the later decision counts one more `not_sellable` | 1 | 1 | [1](rows.md#rows-4), [2](exchanges.md#exchange-6) | ✓ |

## Versions

| Piece | Version |
|---|---|
| checkout commit | `3a8b72b8a32de074c12fffe3d2de3c32d281a34c` |
| checkout describe | `kit-py/v0.1.8-721-g3a8b72b8-dirty` |
| auth contract | `1.1.0` |
| stock contract | `1.3.0` |
| products contract | `1.4.0` |
| recommendations contract | `2.0.3` |
