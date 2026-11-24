# F29-05 Exposición y conversión

Started 2026-11-24T03:53:15Z, finished 2026-11-24T03:53:16Z.
11 checks, 11 held, 0 did not.

- **experiment:** `16`
- **reader:** `user2@mosaiq-demo.com`
- **gateway:** `http://localhost:18129`
- **assigned:** `4`
- **exposed assignments:** `2`
- **exposure facts:** `2`
- **converted assignments:** `4`
- **conversion facts:** `14`
- **exposures from engagement events:** `0`

| Criterion | Case | Expected | Actual | Evidence | |
|---|---|---|---|---|---|
| Exposición y conversión | the run has assignments, exposures and sales, each in its own records | all three | 4 assignments, 2 exposures, 14 sales | [1](rows.md#rows-1), [2](rows.md#rows-2), [3](rows.md#rows-3) | ✓ |
| Exposición y conversión | the service lists the assignments the database holds | 4 | 4 | [1](exchanges.md#exchange-5), [2](rows.md#rows-1) | ✓ |
| Exposición y conversión | the exposures the assignments report are the ones the database holds | 2 | 2 | [1](exchanges.md#exchange-5), [2](rows.md#rows-2) | ✓ |
| Exposición y conversión | the service lists the sales the database holds as conversions | 14 | 14 | [1](exchanges.md#exchange-6), [2](rows.md#rows-3) | ✓ |
| Exposición y conversión | the result's control arm counts assigned and exposed apart | assigned 2, exposed 0 | assigned 2, exposed 0 | [1](exchanges.md#exchange-7), [2](rows.md#rows-1), [3](rows.md#rows-2) | ✓ |
| Exposición y conversión | the result's treatment arm counts assigned and exposed apart | assigned 2, exposed 2 | assigned 2, exposed 2 | [1](exchanges.md#exchange-7), [2](rows.md#rows-1), [3](rows.md#rows-2) | ✓ |
| Exposición y conversión | no one in the control arm was exposed (ADR-0027) | 0 | 0 | [1](rows.md#rows-2) | ✓ |
| Exposición y conversión | every exposure came after its assignment | none before | none before | [1](rows.md#rows-2) | ✓ |
| Exposición y conversión | every sale counted as a conversion lies in its window, after the assignment | none outside | none outside | [1](rows.md#rows-3) | ✓ |
| Exposición y conversión | an assignment is not an exposure: someone was assigned and never exposed | at least one | 2 | [1](rows.md#rows-1), [2](rows.md#rows-2) | ✓ |
| Exposición y conversión | an exposure is not a sale: someone was exposed and did not buy, or bought without an exposure | at least one of the two | 0 exposed without a sale, 2 sales without an exposure | [1](rows.md#rows-2), [2](rows.md#rows-3) | ✓ |

## Versions

| Piece | Version |
|---|---|
| checkout commit | `fdf0caedb301d5308ea804ff9cb9bb5b43ce73cb` |
| checkout describe | `kit-py/v0.1.8-776-gfdf0caed-dirty` |
| auth contract | `1.1.0` |
| experimentation contract | `1.2.1` |
| engagement contract | `404` |
