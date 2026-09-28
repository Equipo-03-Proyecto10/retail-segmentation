# Business rules

The invariants MOSAIQ holds true regardless of who is using it or which screen
they are on.

Each rule names **where it is enforced**, because that is the part that decides
whether it actually holds. A rule enforced only in the application is bypassed
by a direct `INSERT`; a rule enforced only in the database surfaces as an
unexplained error in the interface. Where both are needed, both are listed, and
where only one exists today, the gap is stated rather than glossed.

Rules marked **verified** have a negative case that was actually run against a
database — see
[`evidence/f2-07-integrity-verification.md`](evidence/f2-07-integrity-verification.md).

---

## Access and identity

### RN-01 — There is exactly one administrator
Creating a second administrator, or promoting a second user to the role, is
refused. So is demoting or deactivating the only one: the system is never left
without an administrator.

**Enforced:** `web/services/users.py` *and* the partial unique index
`ux_app_user_single_administrator` on `app_user(role_id) WHERE role_id = 1`.
**Verified** — cases N17, N18 and P5, and each half run with the other removed:
[`evidence/f4-02-single-administrator.md`](evidence/f4-02-single-administrator.md).

The two directions are not enforced the same way, and the difference matters.
*Never two* is refused twice, which is what `AGENTS.md` requires. *Never zero*
is refused only by the application: no unique index can require a row to exist,
and a trigger that refused every write leaving the table without an
administrator would also refuse the rotation F4-06 (#107) performs on the
instance. `transfer_administrator` is the single operation that moves the role
between two users, because promoting the successor and demoting the incumbent
are each refused on their own. · `RF-05`

A role's **code** never changes, because the two halves above key on
different things: the permission matrix (`web/middleware/authz.py`) on the
code, the index on `role_id = 1`. Renaming codes would let them drift apart —
moving `ADMIN` to another `role_id` hands its permissions to every holder of
that role, and no index guards it (#252). **Enforced:** twice, like *never
two*. The role form shows the code read-only and refuses a changed one, and
`trg_role_code_immutable` refuses any `UPDATE` of `role.code` as
`role_code_immutable`. **Verified** — case N29 and `tests/test_admin_crud.py`.

### RN-02 — A user's email is unique and is an email address
No two accounts share an address, and an address without `@` is refused.

**Enforced:** `app_user_email_key` and `app_user_email_check`. **Verified** —
cases N3 and N7. · `RF-01`

### RN-03 — A password is never stored, logged or transmitted in the clear
Only an argon2id hash is stored. The database never hashes and never receives a
plaintext password.

**Enforced:** application (`argon2-cffi`, pinned in `web/requirements.txt`). The
schema cannot enforce this; a review reads the sign-in and user-management code
for it. · `RNF-04`

### RN-04 — A user who has history is deactivated, never deleted
Removing access must not remove the record of what that person did.

**Enforced:** application refuses deletion and offers deactivation.
`audit_log.user_id` is `ON DELETE SET NULL`, so even a direct deletion degrades
to an unattributed entry rather than destroying the entry. · `RF-09`

### RN-05 — Only the administrator runs the segment recalculation
It rewrites a column on every customer, so it is not an analyst's button.

**Enforced:** the authorization middleware, F4-01 (#69) — `SEGMENT_RUN_EXECUTE`
is held by `ADMIN` alone, and `web/routes/segment_run.py` declares it on both
the form and the execution route. · `RF-12`

## Referential integrity

### RN-06 — A category with products cannot be deleted
**Enforced:** `product_category_id_fkey` `ON DELETE RESTRICT`. **Verified** —
case N13. The application turns the refusal into a message naming how many
products reference it. · `RF-07`

### RN-07 — A parent category with children cannot be deleted
The hierarchy is protected by the same rule as the product reference.

**Enforced:** `category_parent_category_id_fkey` `ON DELETE RESTRICT`.
**Verified** — case N16. · `RF-07`

### RN-33 — The category hierarchy is a tree
A category cannot be its own parent, and cannot be moved under one of its own
subcategories: a cycle would make every walk of the hierarchy endless.

**Enforced:** twice. The application refuses the move with a message on the
parent field (`web/services/catalog.py:update_category`), and the database
refuses it independently: `category_not_own_parent` (CHECK) for the one-row
cycle, `trg_category_no_cycle` (raising `category_no_cycle`) for longer ones.
**Verified** — cases N27 and N28, and `tests/test_category_hierarchy.py`. ·
`RF-07`

### RN-08 — A customer with recorded sales cannot be deleted
Sales history is the basis of every segment; deleting the customer would orphan
it.

**Enforced:** `transaction_customer_id_fkey` `ON DELETE RESTRICT`. **Verified** —
case N14. · `RF-07`

### RN-09 — Deleting a sale removes its lines
A sale line has no meaning without its sale.

**Enforced:** `ON DELETE CASCADE` on `transaction_line`. **Verified** — case P4.

### RN-10 — A product SKU is unique
**Enforced:** `product_sku_key`. **Verified** — case N2. · `RF-06`

## Money and quantities

### RN-11 — Prices and totals are never negative
Product list price, sale total and sale line unit price are all zero or greater.

**Enforced:** `product_list_price_check`, `transaction_total_check`,
`transaction_line_unit_price_check`. **Verified** — case N6. · `RF-06`

### RN-12 — A sale line's quantity is greater than zero
A line with zero units is not a line.

**Enforced:** `transaction_line_quantity_check`. **Verified** — case N10.

### RN-13 — A sale line records the price actually charged
`transaction_line.unit_price` is what the customer paid, not
`product.list_price`. A later price change must not rewrite history.

**Enforced:** the model — the price is stored on the line rather than read
through the product. The application must never populate it by copying the
current list price at read time. Reviewed, not constrained. · `RF-10`

### RN-14 — Stock is never negative
**Enforced:** `inventory_quantity_on_hand_check`. · `RF-11`

## Campaigns, segments and experiments

### RN-15 — A campaign cannot end before it starts
**Enforced:** `campaign_check`. **Verified** — case N11.

### RN-16 — A campaign's status is one of four
`DRAFT`, `ACTIVE`, `FINISHED`, `CANCELLED`.

**Enforced:** `campaign_status_check`. **Verified** — case N8.

### RN-17 — A segment's validity cannot end before it starts
**Enforced:** `segment_check`.

### RN-18 — RFM bands run 1 to 5, and no minimum exceeds its maximum
**Enforced:** the six `segment_rule_*_check` column constraints and the
table-level `segment_rule_check`. **Verified** — case N9.

### RN-19 — An experiment group is control or treatment
**Enforced:** `experiment_group_kind_check`.

### RN-20 — A customer sits in at most one segment at a time
The current segment is the open `customer_segment_history` row, whose
`valid_to` is null. Closed rows preserve earlier assignments.

**Enforced:** partial unique index `ux_customer_segment_history_open`, which
allows at most one open row per customer. **Verified** —
[`evidence/f9-01-rfm-rules-adapter.md`](evidence/f9-01-rfm-rules-adapter.md) and
`tests/test_segmentation_pipeline.py`. See
[ADR-0017](adr/0017-segment-assignment-history-replaces-the-mutable-current-segment.md).

### RN-21 — A customer with no sales in the window is unassigned
The recalculation clears the segment rather than leaving a stale one. An empty
segment is information; a wrong one is not.

**Enforced:** the segmentation pipeline writes a new open history row with a
null label for every customer with no sales in the window, in the same
transaction that records the run and all other assignments. **Verified** —
[`evidence/f9-01-rfm-rules-adapter.md`](evidence/f9-01-rfm-rules-adapter.md) and
`tests/test_segmentation_pipeline.py`. · `RF-12`

### RN-37 — A K-means run is reproducible from what it records, and each of its numerical hazards has a defined behaviour
The fit is fixed by the seed, k, the iteration limit, the tolerance and the feature
window, and all five are stored on the run with its quality measures, so a run can
be reproduced from the database alone. Customers are sorted by id before anything
is done, so the order rows arrive in cannot reach the result. Only customers with
sales in the window are clustered; the rest are the unassigned result (RN-21).

| Situation | What happens | Where it is recorded |
|---|---|---|
| A feature is the same for every customer | It maps to 0, not to a division by zero, and reversing recency does not turn it into the best score | `normalisation` |
| A cluster is empty after an assignment step | It is refilled with the customer farthest from their own centroid, taken only from a cluster holding at least two, and the lowest customer id on a tie. A run never ends with fewer than k clusters | `empty_cluster_policy`, `quality.empty_cluster_events` |
| The iteration limit is reached before the tolerance | The run is written and recorded as not converged. It is never presented as settled | `quality.converged`, `quality.stopped_on`, and a logged warning |
| A customer is equidistant from two centroids | They go to the lower-numbered cluster | `tie_break` |
| Fewer customers have sales than k | The run is refused and nothing is written | — |

Quality is recorded as inertia, mean silhouette (absent above 2,000 customers,
where it would be quadratic in pure Python, and for a single cluster, where it is
undefined) and the cluster sizes, largest first. Sizes are a list and not a mapping
by cluster number: a raw cluster number means something only inside one fit and is
never something a report can key on (ADR-0018). A clustered customer carries their
raw recency, frequency and monetary values and no quintile scores, which are
RFM_RULES' and would make two measures look like one.

**Enforced:** application — `web/services/kmeans.py` for the fit and
`kmeans_adapter` in `web/services/segmentation.py` for what is written. No
scientific-computing dependency is taken (ADR-0021). **Verified** — by
`tests/test_kmeans.py` and `tests/test_kmeans_adapter.py`, including that faults
seeded into the normalisation, the refill, the convergence flag, the tie rule and
the seed each fail a test, and against real rows and an
independent exact-arithmetic computation by
[`evidence/f9-02-kmeans-fit.md`](evidence/f9-02-kmeans-fit.md). The mapping from
clusters to labels is RN-38's. · `F9-02`

### RN-38 — A K-means cluster becomes a stable label by what it contains, never by its number
Cluster numbers are arbitrary names one fit gave its clusters: with the same
customers and the same partition, one run may call a cluster 3 and the next may call
it 1, and a report comparing them would claim a migration when nobody moved. Every
cluster therefore takes its label from its contents, by one rule:

1. Clusters are ordered by the descending sum of their centroid's R, F and M, each
   normalised so that higher is better (RN-37).
2. A tie is broken by the higher R, then the higher F, then the higher M.
3. A tie between identical centroids is broken by the lexicographically smallest
   customer id among the cluster's members.
4. That order is paired with the label vocabulary in the order `segment_label`
   declares it, best to worst. **The vocabulary's size must equal k.**

The customer-id tie-break is deterministic and has no commercial meaning: ids are
compared as text, so `10` sorts before `9`. It exists so that two clusters identical
in every measure cannot be ordered by anything arbitrary. The sum is exact and does
not depend on the order of its terms, because a plain floating-point sum ranks
`0.1 + 0.2 + 0.3` and `0.3 + 0.2 + 0.1` by rounding instead of tying them and
moving on to R. What a label means commercially is reduced to that declared order:
two centroids with similar totals can exchange labels when their R, F and M cross,
even if few customers moved.

A K-means run whose k is not the vocabulary's size is **refused when it is started**,
before any sale is read and before anything is written. Customers with no sales in
the window are unassigned (RN-21) and take no cluster. No raw cluster number is ever
stored: `customer_segment_history` has no column that could hold one, and a run's
parameters record cluster sizes as a list, not a mapping by number.

**Enforced:** application — `web/services/cluster_labels.py` for the rule and
`run_kmeans` in `web/services/segmentation.py` for the refusal. **Verified** — by
`tests/test_cluster_labels.py`, `tests/test_kmeans_run.py` and the two cases
ADR-0018 names in `tests/test_segmentation_pipeline.py`, including that faults seeded
into each step of the rule fail a test, and against a real K-means run by
[`evidence/f9-03-cluster-labels.md`](evidence/f9-03-cluster-labels.md). · `F9-03`

### RN-39 — Two runs are compared by label code, and the method is only a description of a run
A model comparison sets one rule-based run beside one K-means run over the same
customers and reports, on label codes alone (ADR-0018):

* **The population under each label, for each run**, in the vocabulary's declared
  best-to-worst order and then *Unassigned*. A label nobody holds is listed with zero.
* **Agreement per customer.** A customer both runs scored *agrees* when they were
  given the same label and *disagrees* otherwise. Two runs that both left a customer
  unassigned agree, because each found nothing to label and that is a result and not
  an absence; one that left them unassigned and one that labelled them disagree.
* **A customer only one run scored** is named as that, and is neither an agreement nor
  a disagreement, because there is nothing to compare them with. The counts reconcile:
  everyone either run scored is agreed, disagreed or in only one.
* **A cross-tabulation** of the customers both runs scored, whose cells sum to them
  and whose diagonal sums to the agreements.

A comparison has no direction. Migration is a change over time and reads "improved"
and "declined"; two runs compared side by side say only where they differ, and never
that one is better.

**The method is run metadata.** The page uses it to offer one run of each kind, to
refuse a run of the wrong kind in a slot, and to name each run on screen. The
comparison is built from two runs' rows and the label vocabulary and is never given
the method, so an assignment cannot be interpreted through it: the same labelled rows
give the same comparison whatever produced them.

**Enforced:** application — `web/services/model_comparison.py` for the rules,
`web/db/model_comparison.py` for the reads (`SELECT`-only, parameterized, and the
assignment reader does not select the method), and the page gated on `segment.read`
(`docs/analytics-permission-map.md`, Phase 9). **Verified** — by
`tests/test_model_comparison.py` and `tests/test_model_comparison_route.py`, including
that faults seeded into each rule above fail a test, and against the seeded PostgreSQL
by [`evidence/f9-04-model-comparison.md`](evidence/f9-04-model-comparison.md). · `F9-04`

### RN-40 — A recommendation is in stock at the customer's usual store, matches a stated signal, and says why
A product is recommended to a customer only when all of these hold:

* **It is eligible.** It is active, has a *positive* quantity on hand in the customer's
  **usual store**, and the customer has not already bought it in the window. The usual
  store is the consumption profile's dominant store (RN-35), so it is the store the
  profile names. A product with no stock there never appears, whatever it scores, and
  neither does one that only another store holds.
* **It is relevant.** At least one of three signals says why *this* customer: other
  customers whose **open** assignment carries the same label bought it in the window
  (*segment*); it is in a category the customer registered an interest in (*preferred
  category*); or it is in one of the categories they buy from (*purchase history*, their
  top three over the window, RN-35). In stock is not a reason.
* **A category covers the categories below it.** A preferred or bought category matches a
  product in that category or in any category below it, at any depth, and never one above
  or beside it: an interest in *Dairy* matches a product in *Milk*. The hierarchy is a
  tree (RN-33). When more than one category above a product matches, the nearest is the
  one named in the reason and, for purchase history, the one whose share is counted.
  Decided in the review of #276 (#277).
* **It carries its reasons.** Every recommendation lists each signal that matched, in a
  fixed order, in words and with the figures behind it, and the quantity the store holds.

Recommendations are ordered by the number of signals that match, then by how many
segment customers bought the product, then by how much of the customer's buying its
category is, then by product id. There are no weights: nothing is tuned, and every
position can be explained by the reasons it carries. At most ten are returned by
default, from one to fifty.

**When it cannot recommend, it says so and reads nothing else.** A customer with no open
assignment, or whose latest assignment is the unassigned result (RN-21), has no segment,
and the result says which of the two it is instead of falling back to another source. A
customer with no accepted sale in the window has no usual store, so stock cannot be
checked, and the result says that. The segment is checked first. Nothing is guessed.

Only the stable label is read (ADR-0018): the computation is not told, and does not
read, how a run was produced or any raw cluster number, so the same labels give the same
recommendations whatever produced them.

**Enforced:** application — `web/services/recommendations.py` for the rules and
`web/db/recommendations.py` for the two reads it adds (`SELECT`-only, parameterized).
The open assignment, the usual store and the categories bought come from the consumption
profile, and the hierarchy from `list_all_categories`. **Verified** — by
`tests/test_recommendations.py` and `tests/test_recommendations_db.py`, including that
faults seeded into each rule above fail a test, and against the seeded PostgreSQL and an
independently written query by
[`evidence/f10-01-recommendations.md`](evidence/f10-01-recommendations.md) and, for the
category hierarchy,
[`evidence/recommendations-category-subtree.md`](evidence/recommendations-category-subtree.md).
· `F10-01`

### RN-22 — A campaign targets a real, stable segment label
**Enforced:** `campaign_label_code_fkey` → `segment_label(label_code)`.
**Verified** — case N23.

### RN-23 — A customer holds at most one assignment per experiment
Two arms of the same experiment is not two independent facts; it is a
measurement error.

**Enforced:** `experiment_assignment_experiment_id_customer_id_key`.
**Verified** — case N21.

### RN-24 — An experiment has at most one control group
**Enforced:** `ux_experiment_one_control`. **Verified** — case N22.

The other half — at least one treatment group before activation — is not
expressible as a static constraint, the same shape as RN-01's *never zero*
half. It waits on F11-03's service-level check.

### RN-25 — An experiment's conversion window is a positive number of days
**Enforced:** `experiment_conversion_window_days_check`. **Verified** — case
N24.

Fixed before the run starts and immutable after the first assignment
(ADR-0019) is not yet enforced anywhere; it waits on F11-04's service, which
must lock the value before assignment begins.

### RN-26 — Every experiment's data carries its origin
`OBSERVED`, `SEEDED` or `INJECTED`.

**Enforced:** `experiment_data_origin_check`. **Verified** — case N25.

Rendering the `Synthetic` label on every screen and export for `SEEDED` and
`INJECTED` data (ADR-0019) is not yet built; it waits on F11-07.

### RN-27 — Exposure and conversion are recorded as events separate from assignment
Neither is a column on `experiment_assignment`: an assigned customer may
remain unexposed, and a conversion is a link to a qualifying `transaction`,
not a second total invented on the experiment side.

**Enforced:** the model — `experiment_exposure` and `experiment_conversion`
are their own relations, foreign-keyed to `experiment_assignment`.

Refusing exposure for the control group (ADR-0019) is not yet enforced
anywhere; it waits on F11-05.

### RN-31 — A campaign moves only along fixed transitions, and two states are final
`DRAFT` → `ACTIVE` or `CANCELLED`; `ACTIVE` → `FINISHED` or `CANCELLED`.
`FINISHED` and `CANCELLED` permit nothing further. An illegal move is refused
with a message naming the campaign, its status and what it may become; it is
never silently ignored. The update is conditional on the status the service
read, so two people moving the same campaign at once cannot both succeed. Each
transition is one `UPDATE`, so RN-28's trigger records it, with the acting user,
without a second history table. State is explicit; dates never imply it.

A draft may also be cancelled — the issue lists only draft → active and
active → finished/cancelled, and discarding a draft is the natural reading of
"cancellation" (decided at F11-02, #221).

**Enforced:** application — `TRANSITIONS` in `web/services/campaigns.py`. Not
expressible in the schema: `campaign_status_check` says which statuses exist,
not which moves between them are legal. **Verified** — the rules by
`tests/test_campaigns.py`; the audit entry per transition by case P6 in
`sql/verify_integrity.sql`, which the mocked-cursor tests cannot show.

### RN-32 — Only a draft campaign is edited, and its target is a label code
A campaign's name, target label and dates change only while it is a `DRAFT`. The
target is a `label_code` from the vocabulary (RN-22), never the id of a segment
belonging to one run, so it survives every recalculation. `starts_on`, `ends_on`
and `label_code` are `NOT NULL`, so an activated campaign always has all three.

`campaign_id` has no identity; the service allocates `max(campaign_id) + 1`
under a transaction-scoped advisory lock rather than asking the user to type
one.

**Enforced:** application for draft-only editing; `campaign_label_code_fkey` for
the label. **Verified** — draft-only editing by `tests/test_campaigns.py`; the
label by case N23; the allocation statement by case P7. Concurrent creates
getting distinct ids is the advisory lock's job and is not a single-session
case: it was reviewed against PostgreSQL 16 and 18 in PR #240, not scripted.

## Consumption profile

### RN-34 — A consumption profile is computed from accepted sales, over a stated window, and its sales measures are absent when there are none
A profile summarises one customer's buying from the sales the ingestion
accepted (ADR-0020), over the last `window_days` days ending at a stated moment.
Rows the ingestion rejected are never persisted, so reading `transaction` and
`transaction_line` is reading accepted sales. The window is part of the profile,
so a page can say what span every figure covers.

A customer with no accepted sale in the window still gets a profile, not an
error and not a row of zeros. Every sales-derived measure is absent, and the
remaining sales reads are skipped; R/F/M and the current and previous segment
still come from assignment history because their run window is independent of
the profile window. Zero purchases and zero spend are measurements; this case
makes neither claim.

The customer's R, F and M values and scores, and their current and previous
segment, are read from assignment history (ADR-0017) — the open row, and the
most recently closed row — and never from a mutable column. A customer who has
held one assignment only has **no** previous segment; the profile reports that
as absent instead of repeating the current one. A previous result of
*unassigned* (RN-21) is kept distinct from an absent one, because a run did
score that customer. No part of a profile names the method that produced a run
(ADR-0018).

The open row and the most recently closed row are read by one statement, so a
segment run committed between two `READ COMMITTED` reads cannot make the same
history row appear as both current and previous. An id that is not a UUID, or
that names no customer, raises `UnknownCustomer`; the page that shows the
profile (F8-04) maps it to HTTP 404.

**Enforced:** application — `web/services/consumption_profile.py` for the rules,
`web/db/consumption.py` for the reads, which are `SELECT`-only and parameterized.
**Verified** — the rules by `tests/test_consumption_profile.py`, the statements
by `tests/test_consumption_db.py`, and the reads against the seeded PostgreSQL by
[`evidence/f8-03-consumption-profile.md`](evidence/f8-03-consumption-profile.md),
which the mocked-cursor tests cannot show. · `F8-03`

### RN-35 — Every ranking in a profile has a stated tie-break, and every derived measure is defined
The order in which the database returns rows is not defined, so a ranking that
did not name its tie-breaks would give a customer a different "dominant" store
on different days.

| Measure | Rule |
|---|---|
| Dominant channel, dominant store | most purchases; then highest spend; then lowest id |
| Favourite categories (top 3) | most purchases containing the category; then units; then spend; then lowest id |
| Frequent products (top 5) | most purchases including the product; then units; then lowest id |
| Total spend, average ticket | from the purchase headers (`transaction.total`) — the quantity the segment run scores as Monetary — so a profile's spend and its M value are one measurement. Average ticket is spend per purchase, rounded half up to the cent |
| Purchase frequency | the number of accepted purchases in the window — the F the segment run ranks |
| Average discount | `(1 - paid / listed) * 100`, weighted by value, where `listed` prices the same units at `product.list_price` |

**The discount is a comparison with the current list price, not a record of
promotions.** RN-13 stores the price actually charged but not the list price a
sale was made against, so a later price change moves this figure without any
discount having been granted, and a customer who paid more than today's list
price shows a negative number. It is reported as measured, not clamped to zero.
A product listed at zero is left out of both sums rather than divided by.

**Enforced:** application — `rank_dominant`, `rank_categories`, `rank_products`,
`average_ticket` and `average_discount_pct` in
`web/services/consumption_profile.py`, each of them pure. **Verified** — by
`tests/test_consumption_profile.py`, including that every ranking is unchanged
under any permutation of its input, and on real rows by
[`evidence/f8-03-consumption-profile.md`](evidence/f8-03-consumption-profile.md).
Deciding that "discount" means this, and not a stored discount, is recorded here
because the schema has no such column. · `F8-03`

### RN-36 — A consumption shift compares two stated periods, and absence is not a shift
A shift is a change in what a customer mostly buys through, where and of: their
dominant channel, their dominant store or their leading category. Detection
compares two periods and reports, per customer, which of the three changed, with
the value before and the value after.

The periods are chosen by the caller and stated on the result, never implied.
Each is half-open, `[start, end)`, so two adjacent periods never both count the
same instant. Overlapping periods are refused, because a sale in both would make
the two sides less independent than the comparison assumes; their lengths may
differ, and each is reported with its own. Passed the wrong way round they are put
in order by their start.

"Dominant" and "leading" are what the consumption profile says they are (RN-35),
ranked by the same functions with the same tie-breaks, so a customer's dominant
store in a shift report is their dominant store on their profile. A tie that
resolves differently in the two periods because the spend moved is a change under
that rule, and is reported as one.

A customer with accepted sales in only one period is **absent** from the other,
with the empty side named, and is not compared: "no sales" is not a channel, a
store or a category. A customer with none in either period is not in the report.
A customer with sales in both whose three leaders did not change has no entry,
but is counted, so the report reconciles: every customer with sales is compared or
absent, and every compared customer is shifted or unchanged. A category needs a
leader on both sides; a purchase with no product lines has none, and no category
shift is reported for it.

**Enforced:** application — `web/services/consumption_shift.py` for the rules,
`web/db/consumption_shift.py` for the single, `SELECT`-only parameterized read
of both periods. Channel and store totals use accepted transaction headers;
category totals additionally use their lines and products (ADR-0020). Reading
both periods in one statement gives the detector one database snapshot.
**Verified** — the rules by
`tests/test_consumption_shift.py`, the statements by
`tests/test_consumption_shift_db.py`, and both against the seeded PostgreSQL by
[`evidence/f8-05-consumption-shifts.md`](evidence/f8-05-consumption-shifts.md).
Reporting shifts as a page is F12-03, not this rule. · `F8-05`

## Audit

### RN-28 — Every change to a catalog or a business rule is recorded
Inserts, updates and deletes on `category`, `product`, `store`, `customer`,
`app_user`, `segment`, `segment_rule`, `campaign`, `experiment`, `channel`,
`role` and `customer_segment_history` all write an audit entry. The last one
replaces the signal that used to come from updating
`customer.current_segment_id` directly — ADR-0017 retires that column in
favour of durable history, so the history table itself is what a
segmentation run's assignments are now traced through.

Individual sales are deliberately **not** audited: their history already lives
in `transaction` and `transaction_line`, and auditing them would double the
write cost of the busiest table in the model.

**Enforced:** `AFTER INSERT OR UPDATE OR DELETE` triggers calling
`fn_audit()`. **Verified** — case P2. · `RF-14`

### RN-29 — The audit log never carries a credential
**Enforced:** `fn_audit()` strips `password_hash` from both payloads before
writing. **Verified** — case P3: zero hashes across 280 audit rows. · `RNF-17`

### RN-30 — The audit log is append-only
Nothing in the application updates or deletes an entry. Entries are kept
indefinitely as compliance evidence.

**Enforced:** application *and* schema — the audit view offers no write
action, and `retail_app` holds no `UPDATE` or `DELETE` on `audit_log`
(`REVOKE` in `sql/01_schema.sql`, checked by its self-test). The role that
owns the schema keeps its privileges, so archiving remains possible.
ADR-0024. · `RF-14`

---

## Where the gaps are

Every rule in this document is now enforced somewhere. The four that were open
were all application-side, and all four are closed: RN-01 by F4-02 (#70), RN-03
by F3-03 (#63), RN-05 by F4-01 (#69) and RN-21 by F3-10 (#102). A rule added
from here starts in this section until the story that enforces it lands.

F11-01 adds four more, each waiting on its own later story: RN-24 (at least
one treatment group before activation) waits on F11-03, RN-25 (locking the
conversion window before assignment) waits on F11-04, RN-26 (rendering the
`Synthetic` label) waits on F11-07, and RN-27 (refusing exposure for the
control group) waits on F11-05.

RN-01 was the one to watch — a hard rule in `AGENTS.md` and a success criterion
in [`scope.md`](scope.md) §7 that held only because the seed happened to contain
one administrator. Both halves landed with F4-02 (#70). RN-03 landed with F3-03
(#63) and RN-05 with F4-01 (#69), whose authorization middleware refuses the
recalculation to everyone but the administrator.
