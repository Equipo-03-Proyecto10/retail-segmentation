# F5-04 — Screenshots of the key functionality

Deliverable 10 in [`../scope.md`](../scope.md) §5: a screenshot for every item on
the demonstration list, so the delivery can be evaluated without running it.
Each image is captioned with the requirement it demonstrates
([`../requirements.md`](../requirements.md) §4).

## How these were produced

The application from `develop` against the seeded Compose stack — the same
three SQL scripts CI runs, no manual step:

```bash
docker compose up -d --build            # db seeds from sql/00,01,02; web on :8000
```

Rendered through headless Chromium at 1440 px wide. Items 1, 2, 3, 5 and 8 also
have a 375 px capture — the sign-in page, the two role screens, the product
query and the audit log, where the narrow layout is what a role most likely
sees and the reflow has to hold (RNF-11). Items 4, 6 and 7 (the catalog edit
form, the stock filter and the segment run) are shown at 1440 px only. These
are **functionality** evidence captured locally; proof that the same build runs
on the assigned host is F6-07
([`f1-04-f1-05-postgresql-access.md`](f1-04-f1-05-postgresql-access.md) and the
deployment record). No real credential appears in any image — the
sign-in form is shown empty, and the seed password
([`../../README.md`](../../README.md) "Signing in") is demonstration-only.

The interface carries no design system yet (F3-08, #68); these show the
structure and behaviour, not the final styling.

## The demonstration list

| # | Demonstration item | Requirement | Screenshot |
|---|---|---|---|
| 1 | Inicio de sesión | RF-01, RF-02 | `f5-04-signin-1440.png`, `f5-04-signin-375.png` — the sign-in page |
| 2 | Acceso diferenciado por perfil | RF-04 | `f5-04-access-analyst-1440.png`, `f5-04-access-analyst-375.png` — an analyst's shell: the menu carries only **Home** and **Catalogs**, and the figures omit *Active users*; compare the administrator's menu in `f5-04-catalog-operation-1440.png` |
| 3 | Acceso denegado por perfil | RF-04 | `f5-04-access-denied-1440.png`, `f5-04-access-denied-375.png` — the same analyst requesting `/admin/products/1/edit`: a 403 page that explains the refusal was deliberate, with a reference code. **This is the negative case F5-04 requires.** |
| 4 | Operación de catálogos | RF-06 | `f5-04-catalog-operation-1440.png` — the administrator editing product 1 (SKU, name, category, price, active, image) |
| 5 | Consulta de información — productos | RF-10 | `f5-04-query-result-1440.png`, `f5-04-query-result-375.png` — the consultation module, a product search (`q=Demo Product 1`) narrowed to 11 results, each row opening a detail page |
| 6 | Consulta de información — existencias | RF-11 | `f5-04-query-stock-1440.png` — stock filtered to one store, rows below 20 units tinted and tagged `LOW` (no reliance on colour alone) |
| 7 | Ejecución de un proceso principal | RF-12 | `f5-04-segment-run-1440.png` — the segment recalculation over a 180-day window: 30 processed, 13 assigned, 17 matched no rule, and the note that the audit log now holds one entry per changed customer |
| 8 | Registro de auditoría | RF-14 | `f5-04-audit-log-1440.png`, `f5-04-audit-log-375.png` — the audit log immediately after that run: 310 entries, newest first, the 30 `customer UPDATE` rows the run wrote, with the entity / date filter |

Items 2 and 3 together are *acceso diferenciado por perfil*: the menu adapts to
the role, and a route outside the role is refused rather than hidden-then-500.
The other two requirements under that heading — RF-03 (an unauthenticated
request is sent to sign-in) and RF-05 (exactly one administrator) — are
evidenced in [`f4-01-authorization.md`](f4-01-authorization.md) and
[`f4-02-single-administrator.md`](f4-02-single-administrator.md).

## Related evidence

The audit view and the segment run each have their own deeper walkthroughs —
[`f3-11-audit-log-view.md`](f3-11-audit-log-view.md),
[`f3-10-segment-run.md`](f3-10-segment-run.md) — and the authorization matrix is
exercised in [`f4-01-authorization.md`](f4-01-authorization.md) and
[`f5-02-negative-tests.md`](f5-02-negative-tests.md). This file is the single
index the Product Owner reads for the demonstration list.
