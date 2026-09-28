# F8-04 — The consumption profile page

Evidence that a customer's consumption profile is usable by the business and not
only by a query: `/catalog/customers/<id>/profile` shows every measure with its
unit and the window it covers, says so plainly when there is no purchase history,
and is refused by the default-deny gate to any profile without `segment.read`.

The profile is computed by F8-03 (`build_profile`, evidenced in
[`f8-03-consumption-profile.md`](f8-03-consumption-profile.md)); this story adds
the route, the template and the link from the customer page. No schema change, no
new permission and no new dependency.

## How this run was produced

Against the database the three ordered scripts build, with the application run as
the restricted role `retail_app`, and the page opened in headless Chromium at both
widths, signed in as seeded demonstration accounts:

```bash
psql -v ON_ERROR_STOP=1 -f sql/00_create_database.sql
psql -v ON_ERROR_STOP=1 -d retail -f sql/01_schema.sql
psql -v ON_ERROR_STOP=1 -d retail -f sql/02_seed_30_per_table.sql
DATABASE_URL=postgresql://retail_app:retail_app@127.0.0.1:5432/retail \
  FLASK_ENV=development python -m flask --app web.app run
```

Accounts: `user14@mosaiq-demo.com` (ANALYST) and `user15@mosaiq-demo.com`
(STORE_MANAGER). Both are published demonstration accounts and no credential is
legible in any capture.

**One real segment run came first.** The seeded history holds R/F/M values that the
seed itself calls illustrative, so a page opened on the raw seed shows 11 purchases
and 402.50 MXN in the R/F/M block next to the 10 purchases and 1,420.00 MXN the
sales give. After one `RFM_RULES` run over 180 days the two agree, and the captures
were taken after it. That run also scored the extra customer created for the
no-history capture, who therefore appears as *Unassigned* (RN-21).

The spend here is 1,420.00 MXN, not the 2,750.00 in the F8-03 evidence, because
`develop`'s seed has since been reconciled so that header totals equal the sum of
their lines.

## Every measure, with its unit and its window

Customer *Demo Customer 1*, default 180-day window.

| | |
|---|---|
| 1440 px | [`f8-04-profile-1440.png`](f8-04-profile-1440.png) |
| 375 px | [`f8-04-profile-375.png`](f8-04-profile-375.png) |

| Measure | Shown as | Unit |
|---|---|---|
| Total spend | `1,420.00` | MXN |
| Purchase frequency | `10` | purchases, "in 180 days" |
| Average ticket | `142.00` | MXN, per purchase |
| Last purchase | `2026-09-27` | date, with days before the end of the window |
| Average discount | `64.94` | %, "paid versus today's list price; negative means above list price" |
| Dominant channel, dominant store | name, "10 of 10 purchases · 1,420.00 MXN" | purchases and MXN |
| Favourite categories | table: purchases, units, spend | MXN |
| Frequent products | table: purchases, units | |
| Recency, Frequency, Monetary | value and score 1–5 | date, purchases, MXN |

The window appears three times: in the page header (*Accepted sales from 2026-04-01
to 2026-09-28 · 180-day window*), in a form that changes it, and in each measure's
footer. The R/F/M block states its own window separately (*Measured by run #31 on
2026-09-28 over its own 180-day window, which may differ from the window above*),
because it is the run's window and not the profile's.

The discount's footer says what it is measured against, which is RN-35: it can be
negative, and the page does not hide that.

## No purchase history

A customer created for the capture, with no sale at all:

| | |
|---|---|
| 1440 px | [`f8-04-no-history-1440.png`](f8-04-no-history-1440.png) |
| 375 px | [`f8-04-no-history-375.png`](f8-04-no-history-375.png) |

The page says **No purchase history**, names the window, and renders no measure:
none of *Total spend*, *Average ticket* or *Average discount* appears, and no `0.00`
does either (`tests/test_customer_profile_route.py` asserts both).

R/F/M and the segments are still shown, because they come from assignment history
and not from the window (decided on #211). Here the latest run left the customer
unassigned, and the page says exactly that: *Unassigned — run #31 found no sales to
label*, with *No previous assignment* beneath it. Those are two different
absences and the page keeps them apart: an unassigned result is a run's finding,
while no previous assignment means there was no earlier run to consult.

A seeded customer asked for a 1-day window that holds none of their sales shows the
other half of the rule, no sales measure but the full history:
[`f8-04-window-without-sales-1440.png`](f8-04-window-without-sales-1440.png).

## A profile without the required permission is refused

The route declares `segment.read`, the permission F4-07's
[map](../analytics-permission-map.md) assigns the consumption profile. The gate
refuses before the view runs.

| Signed in as | Result |
|---|---|
| ADMIN, ANALYST, MARKETING, AUDITOR | 200 |
| STORE_MANAGER, INVENTORY_PLANNER, CUSTOMER | **403** |
| nobody | 302 to sign-in |

Captured for a store manager:
[`f8-04-refused-store-manager-1440.png`](f8-04-refused-store-manager-1440.png). The
refusal is also asserted for all three roles, and the build fails if the route is
ever registered without a declaration
(`test_refusal_matrix_covers_every_protected_route`, which this story extends).

### Open point: the role the story names is refused

The story is written *as a store manager*, and `STORE_MANAGER` holds no
`segment.read`, so the capture above is that role being turned away. The
requirement was followed as written — *gated through the permission F4-07
assigns* — and the map assigns `segment.read`, which the store manager does not
hold. Making the page reachable to that role means either granting
`segment.read` to `STORE_MANAGER`, which would also open every other `segment.read`
surface to them, or assigning this page a different permission. That is a change to
the permission matrix, which is not decided here.

## 375 px and 1440 px

Horizontal overflow, measured in the browser as
`scrollWidth - clientWidth`:

| Page | 1440 px | 375 px |
|---|---|---|
| Profile | 0 px | 0 px |
| No purchase history | 0 px | 0 px |

At 375 px the measures stack into one column and the tables fit their panels, with
no horizontal scrolling.

## What else the page does

* **A window the segment run would refuse** (`abc`, `0`, `3651`, empty, `1.5`)
  answers **400** with the parser's own message and computes nothing:
  [`f8-04-invalid-window-1440.png`](f8-04-invalid-window-1440.png). The window is
  read with the same `parse_window` the segment run uses, so the two refuse the
  same inputs.
* **An unknown customer** answers 404, whether the route finds no row or the
  service raises `UnknownCustomer`.
* **It only reads.** A `POST` answers 405.
* **It never names how a run was made.** No page text contains `rfm_rules`,
  `kmeans` or `cluster` (ADR-0018).
* **A customer name is escaped**, so a name holding markup is shown as text.
* **Counts of one are singular**: *1 purchase*, *1 day*.

## Quality gates

```
$ pytest -q
1020 passed         # 983 on develop, plus 35 in tests/test_customer_profile_route.py
                    # and 2 rows the refusal matrix now runs for the new route
$ black --check .   # 91 files would be left unchanged
$ ruff check .      # All checks passed!
```

The three SQL scripts are untouched and ran clean in order from empty.

## Not evidenced here

Shift detection (F8-05) and any use of the profile by recommendations (F10-01,
F10-02). The 375 px capture of the window-without-sales and refusal cases was not
taken: both reuse layouts captured above at both widths.
