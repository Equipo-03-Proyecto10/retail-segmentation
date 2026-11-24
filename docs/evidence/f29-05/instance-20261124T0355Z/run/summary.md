# F29-05 Exposición y conversión

Started 2026-11-24T03:54:48Z, finished 2026-11-24T03:54:50Z.
11 checks, 11 held, 0 did not.

- **experiment:** `16`
- **reader:** `s1-f2905-analyst@example.test`
- **gateway:** `https://127.0.0.1`
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
| checkout commit | `(unavailable)` |
| checkout describe | `(unavailable)` |
| auth contract | `1.1.0` |
| experimentation contract | `1.2.1` |
| engagement contract | `2.5.0` |
| auth release | `/opt/mosaiq/services/auth/releases/0.1.4` |
| experimentation release | `/opt/mosaiq/services/experimentation/releases/0.8.1` |
| engagement release | `/opt/mosaiq/services/engagement/releases/0.4.0` |
| web commit | `50bf7d5c48336a39f89f34f9bb8d44a78057fb06` |
