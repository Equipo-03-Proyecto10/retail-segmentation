# F9-04 — Model comparison over rule-based and K-means runs

Evidence that a commercial analyst can put what the two methods produce side by side
and read the difference: per-label population for each run, where the two agree and
disagree customer by customer, all on label codes (ADR-0018), and refused by the
default-deny gate to any profile without `segment.read`.

Covers the four acceptance criteria on F9-04 and business rule RN-39. It builds on the
K-means run F9-03 made possible. No schema change, no new dependency, no new
permission, and nothing here writes.

## What this delivers

| | Where |
|---|---|
| The comparison: population per label, agreement per customer, cross-tabulation | `web/services/model_comparison.py` |
| The reads: a run's labelled rows, and the newest runs of one kind | `web/db/model_comparison.py` |
| The page: `/model-comparison/`, gated on `segment.read`, with a menu entry under *Analysis* | `web/routes/model_comparison.py`, `web/templates/model_comparison/index.html` |

The permission map (F4-07) assigns Phase 9's analytics surface to `segment.read`. Its
row was titled *Model parameters and quality measures (read)*; this page shows both
alongside the comparison, so the row now reads *Model comparison, parameters and
quality measures (read)*. The permission and the profiles are unchanged.

## How this run was produced

Against the database the three ordered scripts build from empty, with the application
run as the restricted role `retail_app` and the page opened in headless Chromium,
signed in as seeded demonstration accounts (`user14@…` ANALYST, `user15@…`
STORE_MANAGER). The runs were made by the application's own code:

```
rule-based run 31: RFM_RULES processed=30
k-means run 32:    KMEANS    processed=31 assigned=31   (k = 6, seed 2026, the real label mapping)
```

A customer, *Late Arrival*, was added with one sale between the two runs, so that one
customer exists that only the K-means run scored.

## Per-label population, checked against independent SQL

The page's numbers were read from the rendered DOM and compared with `GROUP BY` and
`FULL JOIN` statements written separately from the application:

```
SQL: agree=2 disagree=28 only-in-rules=0 only-in-kmeans=1
page metrics: {'Customers in both runs': '30', 'Agree': '2', 'Disagree': '28', 'Agreement rate': '6.7%'}
agree / disagree / compared match SQL: True
per-label populations match SQL for both runs: True
cross-tabulation cells match SQL: True
diagonal = 2 = agreements 2: True
agreement rate shown: 6.7% | SQL: 6.7%
customer only the K-means run scored is named: True
```

| | |
|---|---|
| 1440 px | [`f9-04-comparison-1440.png`](f9-04-comparison-1440.png) |
| 375 px | [`f9-04-comparison-375.png`](f9-04-comparison-375.png) |

| Label | Rule-based #31 | K-means #32 | Difference |
|---|---|---|---|
| CHAMPION | 7 | 4 | -3 |
| LOYAL | 0 | 5 | +5 |
| POTENTIAL | 6 | 6 | 0 |
| AT_RISK | 0 | 7 | +7 |
| HIBERNATING | 0 | 8 | +8 |
| LOST | 17 | 1 | -16 |
| Unassigned | 0 | 0 | 0 |
| **Total** | **30** | **31** | |

Labels are listed in the vocabulary's own best-to-worst order and then *Unassigned*; a
label nobody holds is listed with zero.

**What the comparison says about this data.** On the seeded sales the two methods agree
on **2 of 30** customers (6.7 %). The rule-based run puts 17 of 30 under `LOST` and uses
only three of the six labels; K-means spreads the same customers over all six. That is
a finding about this data and these rules, and it is exactly what the page is for: the
choice of method is now something one can look at.

## Where they agree and disagree, per customer

The lower part of the page lists every customer with the label each run gave them and a
result, and a cross-tabulation (rows: rule-based label, columns: K-means label) whose
diagonal is agreement. The list can be narrowed and is paged, 20 customers a page:

| | |
|---|---|
| Disagreements only | [`f9-04-disagreements-1440.png`](f9-04-disagreements-1440.png) |
| Only the customers one run scored | [`f9-04-only-one-run-1440.png`](f9-04-only-one-run-1440.png) |

A customer only one run scored is named as that (*Late Arrival: Only in K-means run*)
and is neither an agreement nor a disagreement, since there is nothing to compare them
with. Two runs that both leave a customer unassigned *agree*, and one that leaves them
unassigned while the other labels them *disagree*. The counts reconcile: everyone either
run scored is agreed, disagreed or in only one.

## The comparison does not branch on the method

Four things hold, each checked:

* `compare_runs` is handed two runs' rows and the label vocabulary and nothing else.
  Its signature names no method, and the module's text contains neither the word
  *method* nor either method's name (a test reads the file);
* the reader of a run's labelled rows does not select the method or anything about
  clusters;
* the route calls `compare_runs` with the same arguments however the runs were made;
* the method appears on the page only as a description of each run (*Method:
  RFM_RULES*), and the route uses it for three things and no others: offering one run of
  each kind, refusing a run of the wrong kind in a slot, and naming each run.

**On the real database.** A run was recorded with method `KMEANS` that carries *exactly*
the labels of rule-based run 31, written through the same pipeline. Compared with run
31, it must agree on everyone, because there is nothing but the labels to read:

```
run 33 is recorded with method KMEANS but carries exactly run 31's labels
page: {'Customers in both runs': '30', 'Agree': '30', 'Disagree': '0', 'Agreement rate': '100.0%'}
```

## A profile without the permission is refused

| Signed in as | Result |
|---|---|
| ADMIN, ANALYST, MARKETING, AUDITOR | 200 |
| STORE_MANAGER, INVENTORY_PLANNER, CUSTOMER | **403** |
| nobody | 302 to sign-in |

Captured for a store manager:
[`f9-04-refused-store-manager-1440.png`](f9-04-refused-store-manager-1440.png). The page
is registered in the negative-flow matrix, so the anonymous and per-role refusals run
for it too, and the build fails if a route is ever registered without a declaration.

## When there is nothing to compare, and when the request is wrong

A comparison needs one run of each kind. The seed holds no K-means run, and no screen
starts one in this release, so the page says so plainly instead of offering an empty
comparison:

| | |
|---|---|
| No K-means run yet, 1440 px | [`f9-04-empty-no-kmeans-1440.png`](f9-04-empty-no-kmeans-1440.png) |
| No K-means run yet, 375 px | [`f9-04-empty-no-kmeans-375.png`](f9-04-empty-no-kmeans-375.png) |

* A run of the wrong kind in a slot answers **400** and compares nothing:
  [`f9-04-wrong-kind-1440.png`](f9-04-wrong-kind-1440.png) (*Run #32 is not a rule-based
  run.*).
* A choice that is not a run number (`abc`, `1.5`, `9;DROP`) answers 400, and an unknown
  run id answers **404**.
* An unknown filter answers 400. A page past the end redirects to the last page.

## 375 px and 1440 px

Horizontal overflow of the page body, measured in the browser as
`scrollWidth - clientWidth`, was **0 px** for every capture above at both widths. At
375 px the panels stack into one column and the wide tables scroll inside their own
panel, as the migration matrix's do.

## The tests catch a broken comparison

Faults were seeded into the code, one at a time, and each was required to make a test
fail. All 11 did:

| Fault seeded | Tests that failed |
|---|---|
| Both-unassigned counted as a disagreement | 2 |
| A customer only one run scored counted as a disagreement | 3 |
| Labels listed alphabetically instead of by the vocabulary's order | 1 |
| A label outside the vocabulary silently accepted | 1 |
| Agreement rate taken over all customers, not those in both runs | 1 |
| Unassigned dropped from the cross-tabulation | 1 |
| Customers not sorted by name | 2 |
| A run of the wrong kind accepted in a slot | 1 |
| An unknown run id not answering 404 | 1 |
| The K-means slot reading the rule-based run's rows | 5 |
| The assignment reader selecting the method | 1 |

## Tests

`tests/test_model_comparison.py` (34) and `tests/test_model_comparison_route.py` (34) are
new. Existing tests changed only because they enumerate what exists: the menu lists in
`tests/test_authz.py` now include *Model comparison*, and the negative-flow matrix now
includes `/model-comparison/`.

```
$ pytest -q
1304 passed         # 1234 with F9-03, plus 70
$ black --check .   # All done
$ ruff check .      # All checks passed!
```

## Things to know

* **The run pickers offer only the newest 100 of each kind.** A run chosen by id in the
  URL that is older is added to its list so it stays selected, as the migration matrix
  does.
* **The page builds the comparison in memory.** Both runs' rows are read in full and
  paged afterwards, which is fine for the customer counts of this delivery and is the
  first thing to change if a run holds many thousands.
* **No screen starts a K-means run,** so on a real instance the page will say *No
  K-means run has been recorded yet* until one is started from code (`run_kmeans`).
  Whether that needs a screen is a question for the team, not this story.
* **After a K-means run, the customer page this table links to shows *Unassigned*.** A
  K-means assignment carries a label and no `segment_id`, and the catalog pages read
  `segment_id` (#272). On the verification database, *Demo Customer 13*, labelled
  `CHAMPION` by K-means run 32, shows *Current segment: Unassigned* on their page. The
  comparison reads labels and is unaffected; the inconsistency is the catalog's, and the
  team's note that #272 lands before or with this story stands.
* **Run 33 above exists only in the verification database** and is not part of the
  repository. It was written to prove that the comparison reads labels and nothing else.

## Not evidenced here

Charts of these figures (F12-01, which this story blocks), and the migration between two
runs over time, which is F7's and untouched.
