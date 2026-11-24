# F29-06 Desconexión

Started 2026-11-24T05:11:52Z, finished 2026-11-24T05:11:52Z.
5 checks, 5 held, 0 did not.

- **account:** `f29-06-store-manager@example.test`
- **since:** `2026-11-24T05:00:00Z`
- **until:** `2026-11-24T05:13:45Z`

| Criterion | Case | Expected | Actual | Evidence | |
|---|---|---|---|---|---|
| Desconexión | sales recorded = sales the tester queued and confirmed | 2 | 2 | [1](rows.md#rows-1) | ✓ |
| Desconexión | no request body recorded as two sales | 0 | 0 | [1](rows.md#rows-2) | ✓ |
| Desconexión | no sale's content recorded twice | 0 | 0 | [1](rows.md#rows-3) | ✓ |
| Desconexión | key 0522a5de-2a7e-47ba-aa85-a8b303c04545 recorded exactly once | 1 | 1 | [1](rows.md#rows-4) | ✓ |
| Desconexión | key 77c8b2fa-add5-43c1-bb7a-05d84c08b1f6 recorded exactly once | 1 | 1 | [1](rows.md#rows-5) | ✓ |
