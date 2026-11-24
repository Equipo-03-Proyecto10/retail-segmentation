# F29-03 the sale and the parity after it

Started 2026-11-24T04:54:19Z, finished 2026-11-24T04:55:55Z.
15 checks, 15 held, 0 did not.

- **sale_id:** `308`
- **Idempotency-Key:** `99a05079-6950-48f5-b3d8-f592b12a63ce`
- **service reader:** `f29-03-store-manager@example.test`
- **portal reader:** `f29-03-store-manager@example.test`
- **portal:** `https://mosaiq.maxthecoder.online`
- **gateway:** `https://mosaiq.maxthecoder.online`
- **xmllint:** `contracts/sales/sales.xsd: - validates`

| Criterion | Case | Expected | Actual | Evidence | |
|---|---|---|---|---|---|
| Venta Android | the sale reads back in JSON | 200 | 200 | [1](exchanges.md#exchange-11) | ✓ |
| Consulta escritorio (protocol) | GET in XML validates against the sales XSD | 200 application/xml, valid | 200 application/xml, valid | [1](exchanges.md#exchange-12) | ✓ |
| Venta Android | PostgreSQL holds the sale under its Idempotency-Key | true | true | [1](rows.md#rows-1), [2](exchanges.md#exchange-11) | ✓ |
| Venta Android | the row's total is the response's total | 41.25 | 41.25 | [1](rows.md#rows-1), [2](exchanges.md#exchange-11) | ✓ |
| Venta Android | exactly one capture row | 1 | 1 | [1](rows.md#rows-1) | ✓ |
| Paridad web | after: product 1 (SKU-00001), portal = service = row | 39 = 39 = 39 | 39 = 39 = 39 | [1](exchanges.md#exchange-14), [2](exchanges.md#exchange-13), [3](rows.md#rows-2) | ✓ |
| Paridad web | after: product 2 (SKU-00002), portal = service = row | 40 = 40 = 40 | 40 = 40 = 40 | [1](exchanges.md#exchange-16), [2](exchanges.md#exchange-15), [3](rows.md#rows-3) | ✓ |
| Paridad web | product 1: web fell by the 1 sold | 39 | 39 | [1](../before/exchanges.md#exchange-12), [2](../before/exchanges.md#exchange-11), [3](../before/rows.md#rows-1), [4](exchanges.md#exchange-14), [5](exchanges.md#exchange-13), [6](rows.md#rows-2) | ✓ |
| Paridad web | product 1: service fell by the 1 sold | 39 | 39 | [1](../before/exchanges.md#exchange-12), [2](../before/exchanges.md#exchange-11), [3](../before/rows.md#rows-1), [4](exchanges.md#exchange-14), [5](exchanges.md#exchange-13), [6](rows.md#rows-2) | ✓ |
| Paridad web | product 1: database fell by the 1 sold | 39 | 39 | [1](../before/exchanges.md#exchange-12), [2](../before/exchanges.md#exchange-11), [3](../before/rows.md#rows-1), [4](exchanges.md#exchange-14), [5](exchanges.md#exchange-13), [6](rows.md#rows-2) | ✓ |
| Paridad web | product 2: web fell by the 1 sold | 40 | 40 | [1](../before/exchanges.md#exchange-14), [2](../before/exchanges.md#exchange-13), [3](../before/rows.md#rows-2), [4](exchanges.md#exchange-16), [5](exchanges.md#exchange-15), [6](rows.md#rows-3) | ✓ |
| Paridad web | product 2: service fell by the 1 sold | 40 | 40 | [1](../before/exchanges.md#exchange-14), [2](../before/exchanges.md#exchange-13), [3](../before/rows.md#rows-2), [4](exchanges.md#exchange-16), [5](exchanges.md#exchange-15), [6](rows.md#rows-3) | ✓ |
| Paridad web | product 2: database fell by the 1 sold | 40 | 40 | [1](../before/exchanges.md#exchange-14), [2](../before/exchanges.md#exchange-13), [3](../before/rows.md#rows-2), [4](exchanges.md#exchange-16), [5](exchanges.md#exchange-15), [6](rows.md#rows-3) | ✓ |
| Paridad web | the portal's profile counts one more purchase | 2 | 2 | [1](../before/exchanges.md#exchange-15), [2](../before/rows.md#rows-3), [3](exchanges.md#exchange-17), [4](rows.md#rows-4) | ✓ |
| Paridad web | the portal's spend grew by the sale's total | 82.50 | 82.50 | [1](exchanges.md#exchange-17), [2](rows.md#rows-4) | ✓ |

## Versions

| Piece | Version |
|---|---|
| checkout commit | `85e109cb6db3c3407927f25f2826988c2b4c574d` |
| checkout describe | `kit-py/v0.1.8-777-g85e109cb` |
| auth contract | `1.1.0` |
| users contract | `1.4.0` |
| stores contract | `1.4.0` |
| products contract | `1.4.0` |
| stock contract | `1.3.0` |
| customers contract | `1.8.1` |
| sales contract | `1.10.1` |
