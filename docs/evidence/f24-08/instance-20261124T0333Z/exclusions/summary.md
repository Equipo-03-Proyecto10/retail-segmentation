# F24-08 discontinued and restricted products

Started 2026-11-24T03:34:20Z, finished 2026-11-24T03:34:22Z.
9 checks, 9 held, 0 did not.

- **customer:** `00000000-0000-0000-0000-000000000003`
- **store:** `3`
- **discontinued product:** `5`
- **reader:** `s1-f2408-analyst@example.test`
- **gateway:** `https://127.0.0.1`
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
| checkout commit | `(unavailable)` |
| checkout describe | `(unavailable)` |
| auth contract | `1.1.0` |
| stock contract | `1.3.0` |
| products contract | `1.4.0` |
| recommendations contract | `2.0.4` |
| auth release | `/opt/mosaiq/services/auth/releases/0.1.4` |
| stock release | `/opt/mosaiq/services/stock/releases/0.3.0` |
| products release | `/opt/mosaiq/services/products/releases/0.3.0` |
| recommendations release | `/opt/mosaiq/services/recommendations/releases/0.5.0` |
| web commit | `50bf7d5c48336a39f89f34f9bb8d44a78057fb06` |
