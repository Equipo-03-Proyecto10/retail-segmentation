# F12-03 — Filtered consumption-shift and recommendation reports

Evidence that a store can act on what changed rather than on the whole customer
base: `/consumption-reports/` shows consumption shifts and product
recommendations side by side, both filterable by store, channel, category and
period, every recommendation row carries its stated reason and its store stock
read fresh on every request, both tables are server-paginated without dropping
customers or shifts, a filter combination with no rows says so per report
rather than an empty table, and the page is refused by the default-deny gate
to any profile without `segment.read`.

Covers the four acceptance criteria on F12-03 and business rule RN-44. Neither
report recomputes anything: the shift report reuses F8-05's `detect_shifts`
unchanged, and the recommendation report reuses F10-01's `recommend` unchanged,
once per customer. The one new read is bulk customer names; the one small,
backward-compatible extension is `RecommendationResult` gaining `channel_id`/
`channel_name`, needed so a report can filter recommendations by channel the way
it already could by store. No schema change, nothing here writes.

## A numbering collision this story's own review caught

Building this story's rule meant reading every `RN-` heading in
`docs/business-rules.md` first, which surfaced a defect already merged into
`develop`: **F12-02's own rule was written as `RN-42`, and `RN-42` was already
in use** — "An assignment is never rewritten" (ADR-0026, Phase 11), referenced
across `web/services/experiments.py`, `web/db/experiments.py`,
`tests/test_experiment_assignment.py` and its own evidence doc. F12-02's rule
had far fewer references (its own heading, its own evidence doc, and two code
comments), so it is the one renumbered here, to **`RN-43`**, with every
reference updated to match; this story's own rule is `RN-44`. Phase 11's `RN-42`
is untouched. This is included in this story's patch rather than filed
separately, since fixing it costs nothing once found and leaving a known,
duplicate rule number in place would only make the next story's search harder.

## What this delivers

| | Where |
|---|---|
| The one new read: customer names in bulk | `web/db/consumption_reports.py` |
| Filtering and orchestration for both reports | `web/services/consumption_reports.py` |
| `channel_id`/`channel_name` added to `RecommendationResult` | `web/services/recommendations.py` (F10-01) |
| The page: `/consumption-reports/`, gated on `segment.read`, under Analysis | `web/routes/consumption_reports.py`, `web/templates/consumption_reports/index.html` |

## How this run was produced

Against the database the three ordered scripts build from empty, with the
application run as the restricted role `retail_app`, one real `RFM_RULES` run
(so every customer has a segment to recommend against), and the page opened in
headless Chromium. The seed's own transactions span exactly 180 days, so with
the default window every customer's sales fall inside a single period; a
90-day window was used throughout so both of the two periods the shift report
compares hold real seeded sales.

**One real cross-store shift** was created deliberately, the same way F8-05's
own evidence did: six purchases were added for *Demo Customer 1* at Store 5 on
`channel_id=3` (physical_store), 135 days before now — outside their otherwise
consistent Store 8 / marketplace pattern:

```
Demo Customer 1's real shift: Store 5 -> Store 8 | channel: physical_store -> marketplace
```

## Criterion 1 — every applied filter is reflected

Unfiltered, both reports show their full figures:

| | |
|---|---|
| 1440 px | [`f12-03-report-1440.png`](f12-03-report-1440.png) |
| 375 px | [`f12-03-report-375.png`](f12-03-report-375.png) |

13 shifts (the 12 the seed's own sales already produce, plus the injected one),
and 107 recommendations across every customer. Both were checked directly
against the two underlying functions, called by hand:

```
unfiltered shift report rows == raw detect_shifts shifts: True (13, 13)
recommendation report total: 107 (page showed 107)
first page row count == page_size: True
```

The report consumes the complete customer directory in 500-customer batches;
the batch size is an implementation detail, not a result cap. Shift rows are
filtered before pagination and use an independent `shift_page` parameter,
while recommendation pages retain `page`. Moving either table keeps the other
table's page and all store/channel/category/period filters. Focused regression
tests exercise 501 customers, the second customer batch, a second shift page,
and links that preserve both page parameters.

Filtered to `store=8` (the injected shift's *later* store, also this customer's
usual store):

[`f12-03-filtered-store-1440.png`](f12-03-filtered-store-1440.png)

Exactly one shift (*Demo Customer 1*'s, since Store 8 is its *after* leader) and
exactly the three recommendations that customer's own usual store produced. No
other customer's shift or recommendation survives the filter.

## Criterion 2 — a recommendation row carries its reason and its stock

Visible on every capture above: each row states the product, the store, the
exact stock figure (*22 units*), and every signal that matched, named and in
words (*Segment — 2 other Lost customers bought it in the last 90 days*,
*Preferred category — In Beverages, a category the customer said they like*) —
the same reasons F10-01 already computes, carried through unchanged.

## Criterion 3 — a product whose stock reaches zero is gone on regenerate

This was done for real, against the running application, between two loads of
the filtered report:

```
Demo Product 4 stock at Store 8, before: 22
Demo Product 4 listed before: True
stock set to 0
Demo Product 4 listed after stock reached zero and the report was regenerated: False
stock restored to 22
```

Capture after the stock reached zero and the page was reloaded:
[`f12-03-stock-zero-regenerated-1440.png`](f12-03-stock-zero-regenerated-1440.png).
Nothing is cached here: the recommendation report calls `recommend` fresh for
every customer on every request, exactly as the customer-facing page (F10-02)
already does, so this is inherited behaviour confirmed for the new report
rather than assumed from it.

## Criterion 4 — a profile without the permission is refused

```
Signed in as INVENTORY_PLANNER -> 403
```

Captured: [`f12-03-refused-inventory-planner-1440.png`](f12-03-refused-inventory-planner-1440.png),
with no *Consumption reports* entry in the menu. `CUSTOMER` is refused the same
way; `ADMIN`, `ANALYST`, `MARKETING`, `AUDITOR` and `STORE_MANAGER` — the role
this story is written for, per ADR-0023 — all reach 200. The route is in the
negative-flow matrix, so the anonymous refusal and a `405` on `POST` are
covered too.

## A filter combination with no rows: each report says so independently

A store/channel combination that matches nobody's shift still had three
recommendations for an unrelated customer who happens to hold that same store
and channel — the two reports are filtered independently, so one being empty
does not imply the other is. A combination chosen to match neither (a category
no product anywhere is stocked under, `Chargers`) shows both:

[`f12-03-no-rows-either-1440.png`](f12-03-no-rows-either-1440.png)

*No shifts match those filters* and *No recommendations match those filters*,
each in its own section, with no empty table rendered for either.

## 375 px

[`f12-03-report-375.png`](f12-03-report-375.png). The page body itself never
scrolls sideways; each wide table scrolls inside its own `mq-table-wrap`
container, the same pattern every other table page in this delivery already
uses.

## The tests catch a broken report

Faults were seeded into the service and the route, one at a time, and each was
required to make a test fail. All did, including one that survived the first
pass:

| Fault seeded | Caught by |
|---|---|
| A filter that is unset still excludes rows | 6 tests |
| A shift with no dimension matches a filter anyway | 3 tests |
| The three filters OR instead of AND | 6 tests |
| A store filter ignored on recommendation rows | 2 tests |
| A category filter applied to the whole customer, not the product | 1 test |
| The pagination offset computed one page ahead | 1 test |
| Names looked up for every shift instead of the filtered ones | 1 test |
| The store filter dropped before reaching the shift builder (route) | 1 test |
| A malformed value passed as a numeric filter accepted instead of refused | 3 tests |

One fault, **skipping the "only RECOMMENDED customers" guard, survived the
first pass**: with real `RecommendationResult` data, a non-`RECOMMENDED` status
always carries an empty `recommendations` tuple already, so the guard had
nothing to catch in the tests as first written. A second test was added that
constructs a deliberately inconsistent result (a `NO_SEGMENT` status carrying a
non-empty recommendation) to exercise the guard itself rather than trust the
invariant it defends to hold everywhere a `RecommendationResult` might ever be
built.

## Tests

`tests/test_consumption_reports.py` (24), `tests/test_consumption_reports_db.py`
(6) and `tests/test_consumption_reports_route.py` (25) are new — 55 tests, plus
2 new tests in `tests/test_recommendations.py` for `channel_id`/`channel_name`.
Two existing tests changed only because they enumerate what exists: the menu
lists in `tests/test_authz.py` now include *Consumption reports*, and the
negative-flow matrix now includes `/consumption-reports/`.

```
$ .venv/bin/pytest -q      # 2948 passed
$ .venv/bin/black --check .  # 191 files unchanged
$ .venv/bin/ruff check .   # All checks passed!
```

## Things to know

* **The recommendation report calls `recommend` for every customer** on every
  request. Customers are read in bounded batches, but coverage is complete;
  recommendations themselves are paginated at 25 rows for the page.
* **The period is one control for both reports**, `as_of` and `window_days`,
  rather than two independently chosen ranges: for the shift report it becomes
  the two consecutive periods `consecutive_periods` already builds; for the
  recommendation report it is exactly `recommend`'s own window. "The last 90
  days" means the same 90 days in both sections.
* **A store or channel filter on the shift report matches either side of the
  change** — a customer who started using it or stopped, not only one
  direction — stated plainly in RN-44 and pinned by a test.

## Not evidenced here

Campaign and experiment reports (F12-04).
