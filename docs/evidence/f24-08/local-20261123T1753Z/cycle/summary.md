# F24-08 a stock change and the recommendation

Started 2026-11-23T17:53:04Z, finished 2026-11-23T17:53:21Z.
17 checks, 17 held, 0 did not.

- **bound:** `contracts/streams/stock.changed.v1.md, group `recommendations`: p95 3000 ms, max 5000 ms`
- **customer:** `00000000-0000-0000-0000-000000000003`
- **store:** `3`
- **channel:** `(any)`
- **poll interval ms:** `100`
- **reader:** `user2@mosaiq-demo.com`
- **planner:** `user5@mosaiq-demo.com`
- **gateway:** `http://127.0.0.1:18831`
- **product:** `2`
- **before:** `product 2: primary at position 1 (store 3); exclusions: already_bought 14, out_of_stock 21, PERSONALIZATION_WITHDRAWN 1`
- **COUNT Idempotency-Key:** `c6fc46fd-250b-467f-bb77-2fae1a6f43e9`
- **1. Agotado: after the bound:** `product 2: elsewhere at position 4 (store 13), out_of_stock; exclusions: already_bought 14, out_of_stock 22, PERSONALIZATION_WITHDRAWN 1`
- **1. Agotado: elsewhere entry:** `product 2 reported held at store 13 (25 units), unavailable_reason out_of_stock: not offered at store 3`
- **RECEIPT Idempotency-Key:** `b67f51bd-cd9e-4c80-ba9f-6c067d158740`
- **2. Reabastecido: after the bound:** `product 2: primary at position 1 (store 3); exclusions: already_bought 14, out_of_stock 21, PERSONALIZATION_WITHDRAWN 1`

| Criterion | Case | Expected | Actual | Evidence | |
|---|---|---|---|---|---|
| 1. Agotado | before: the product is a primary item at the store | primary | primary | [1](exchanges.md#exchange-7) | ✓ |
| 1. Agotado | before, again: served from the cache (same decision_id) | 1b9e10fd-6397-4033-ac54-542daeb47acd | 1b9e10fd-6397-4033-ac54-542daeb47acd | [1](exchanges.md#exchange-7), [2](exchanges.md#exchange-8) | ✓ |
| 1. Agotado | before: the item's quantity = stock-service = `inventory` | 10 = 10 = 10 | 10 = 10 = 10 | [1](exchanges.md#exchange-7), [2](exchanges.md#exchange-9), [3](rows.md#rows-1) | ✓ |
| 1. Agotado | the COUNT to zero is recorded | 201 | 201 | [1](exchanges.md#exchange-10) | ✓ |
| 1. Agotado | measured delay, movement 201 to the first read that no longer offers it, within the bound's maximum | ≤ 5000 ms | 38 ms | [1](exchanges.md#exchange-11) | ✓ |
| 1. Agotado | 5005 ms after the movement (bound 5000 ms), product 2 is not offered at store 3 | not offered | not offered | [1](exchanges.md#exchange-12) | ✓ |
| 1. Agotado | the next read agrees (it may be served from the cache) | not offered | not offered | [1](exchanges.md#exchange-13) | ✓ |
| 1. Agotado | the decision counts one more `out_of_stock` exclusion | 22 | 22 | [1](exchanges.md#exchange-12) | ✓ |
| 1. Agotado | the stored decision is the answer's: same items and exclusions | same | same | [1](exchanges.md#exchange-12), [2](rows.md#rows-2) | ✓ |
| 1. Agotado | stock-service and `inventory` hold the movement's result | 0 = 0 | 0 = 0 | [1](exchanges.md#exchange-14), [2](rows.md#rows-3) | ✓ |
| 2. Reabastecido | the RECEIPT of 10 is recorded | 201 | 201 | [1](exchanges.md#exchange-15) | ✓ |
| 2. Reabastecido | measured delay, movement 201 to the first read that offers it, within the bound's maximum | ≤ 5000 ms | 46 ms | [1](exchanges.md#exchange-16) | ✓ |
| 2. Reabastecido | 5001 ms after the movement (bound 5000 ms), product 2 is offered at store 3 | offered | offered | [1](exchanges.md#exchange-17) | ✓ |
| 2. Reabastecido | the next read agrees (it may be served from the cache) | offered | offered | [1](exchanges.md#exchange-18) | ✓ |
| 2. Reabastecido | it is a primary item again, with the restocked quantity | primary, 10 | primary, 10 | [1](exchanges.md#exchange-17) | ✓ |
| 2. Reabastecido | the stored decision is the answer's: same items and exclusions | same | same | [1](exchanges.md#exchange-17), [2](rows.md#rows-4) | ✓ |
| 2. Reabastecido | stock-service and `inventory` hold the movement's result | 10 = 10 | 10 = 10 | [1](exchanges.md#exchange-19), [2](rows.md#rows-5) | ✓ |

## Versions

| Piece | Version |
|---|---|
| checkout commit | `3a8b72b8a32de074c12fffe3d2de3c32d281a34c` |
| checkout describe | `kit-py/v0.1.8-721-g3a8b72b8-dirty` |
| auth contract | `1.1.0` |
| stock contract | `1.3.0` |
| products contract | `1.4.0` |
| recommendations contract | `2.0.3` |
