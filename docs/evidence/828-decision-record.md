# F24-05 (#828): every recommendation's explanation is kept

What the service records when it answers, and what was checked. Every claim is a
test in `services/recommendations/tests/test_decisions.py`, run on the seeded
PostgreSQL and a real Redis (`TEST_DATABASE_URL`, `TEST_REDIS_URL`); a test that
needs neither is said so. Governing records: ADR-0067 (F24-02), ADR-0046.

## 1. What a served recommendation stores

The service now owns the schema `recommendations`, four tables in 4NF (the
justification is in `docs/data-model.md` §2.4):

| Table | One row per | Holds |
|---|---|---|
| `recommendation_decision` | answer | customer (null once anonymized), status, `computed_at`, segmentation run, model version, reference store and where it came from, reference channel |
| `recommendation_exclusion` | reason above zero | how many products were excluded for it |
| `recommendation_item` | product offered | position, product, its category then, kind, the quantity on hand it used, the other store of an `elsewhere` entry, `alternative_for`, `unavailable_reason` |
| `recommendation_reason` | signal of an item | `segment_buyers`, category, share |

Ids, figures and codes only. A test reads the catalogue and fails if any column
is not `uuid`, a number or a timestamp, if a text column is not one of the six
code columns, or if a column is named like a name, email, phone, SKU, price or
note.

Nothing derivable is stored. The contract's `factors` are the count and the
figures of an item's reason rows, an item's availability channel is the
decision's channel, and its store is the decision's unless it is an `elsewhere`
entry. Storing them would hold each fact twice.

Test: `test_a_served_answer_stores_its_decision` asks `GET /v1/recommendations`
for a recommended customer and finds one new decision with the customer, the
status, the run, the usual store as `usual_store`, no channel, a `computed_at`
within the call, and the products, their quantities and their order equal to the
response's.

## 2. A stored decision matches what the client received

- `test_a_stored_decision_matches_what_the_client_received`: the decision's
  items rendered as a `/v1` answer equal the response body's items, and its
  `computed_at` equals the response's. The decision also validates against the
  published `RecommendationDecision` schema.
- `test_it_still_matches_after_stock_and_rules_changed`: in a transaction that is
  rolled back, every product the client was shown is set to zero stock and
  withdrawn. The rule, asked again, now answers something else (`none_match`
  for the seeded customer used); the stored decision still equals what was
  sent. The record is read from its own rows and consults no live view.
- `test_a_decision_is_stored_with_everything_it_holds_and_read_back_equal`: a
  decision with a primary item, a two-reason item, an alternative and an
  `elsewhere` entry, two exclusion reasons, a given store and channel, round
  trips equal as an object and as the wire document.
- Each status without items (`none_match`, `no_usual_store`,
  `no_open_assignment`, `unassigned_result`, `PERSONALIZATION_WITHDRAWN`) is
  stored and read back with no items.
- All 30 seeded decisions validate against the contract and between them hold
  all three kinds.

## 3. A cached answer is attributable to a stored decision

The cache entry carries the `decision_id` recorded for it, and the decision is
recorded and committed before the answer is cached or returned.

- `test_a_cached_answer_is_attributed_to_the_stored_decision`: the second ask is
  served from the cache with the same body, writes no decision, keeps the same
  `decision_id` in the entry, logs `Served cached decision <id>`, and that id
  reads back the answer that was served.
- `test_each_computation_is_its_own_decision`: a different `limit` is a different
  key and a different decision.
- `test_a_cache_entry_from_before_decisions_is_recomputed_and_recorded`: an entry
  with no `decision_id` (written before this change) is a miss; the answer is
  recomputed and recorded, so nothing is served that no stored decision explains.
- `test_recording_the_same_decision_again_writes_nothing`: recording the same
  `decision_id` twice leaves every table's row count as it was.

Mutation check, as in earlier stories: with the call to `decision_store.record`
removed from the service, seven of these tests fail; with the cache accepting an
entry without a decision id, the pre-upgrade test fails.

## 4. Retention and anonymization

- `test_decisions_older_than_90_days_are_deleted_with_what_they_hold`: of three
  decisions (91 days, 89 days 23 hours, now), only the first goes, with its
  exclusions, items and reasons and no orphan rows; the 30 seeded decisions, all
  within 90 days, stay.
- `test_the_retention_job_runs_by_itself`: the service's own job
  (`app/retention.py`, once an hour, `RETENTION_ENABLED`) deletes a decision
  computed 120 days ago within its interval.
- `test_anonymization_clears_the_customer_and_keeps_the_figures`: after
  `clear_customer` the decision has no customer, and everything else, items
  included, is equal.
- `test_removing_a_customer_row_leaves_its_decisions_without_a_customer`: the
  foreign key does the same when a customer row goes.

## 5. The schema's own refusals

`test_the_schema_refuses_what_contradicts_its_kind_or_signal` (nine cases)
shows PostgreSQL refusing: a primary with `alternative_for`; an alternative
without it; an elsewhere entry with no other store; zero quantity; a
`purchase_history` reason carrying `segment_buyers`; a `segment` reason with no
figure; a store without its source; a status outside the list; an exclusion of
zero. A reason cannot name the same signal twice for an item.

`test_the_service_may_write_and_expire_decisions_and_nothing_else`: the service
holds exactly SELECT, INSERT and DELETE on the four tables and UPDATE of
`customer_id` alone; `retail_app`, `svc_engagement` and `svc_stock` have no
privilege on the schema. `contracts/db/check_grants.sh` and
`contracts/db/check_boundaries.sql` print nothing against the database built by
the three scripts, and the section re-applies cleanly a second time.

## 6. Limits

- **No `/v2` yet.** The `/v2` operations are F24-04 to F24-07. The record is
  written today by the `/v1` path, judged at the usual store, no channel, only
  `primary` items, and `/v1` carries no decision id on the wire. `/v2` will use
  `decisions.Decision` and `decision_store` as they are; the table already holds
  alternatives, `elsewhere` entries, channels and a given store, but the code
  that produces them is not written, so those rows are tested with decisions
  built by hand and with the seed.
- **No exclusion counts from `/v1`.** The `/v1` rule drops ineligible products
  without counting them, so its decisions have none. F24-04 supplies them.
- **No model version.** `api_segments.current_label_v1` does not publish one, so
  `model_version_id` is null. The column and its check exist for when it does.
- **Retention is the ADR's proposal.** 90 days, which F21-15 may change.
- **Anonymization is half wired.** The tables and `clear_customer` are in place;
  what calls it when a customer is anonymized is F21-19's.
- **A product, store, category or channel shown in the last 90 days cannot be
  deleted** (the references are `RESTRICT`), as for check-ins (E-11). Products
  are retired by deactivation (RN-10, RN-11), so no story deletes one.
- **PostgreSQL 16 locally.** The suite ran on 16; the instance's major is 18.
  `verify_integrity.sql` N13, N14, N16 and N26 expect the PostgreSQL 18 spelling
  of an `ON DELETE RESTRICT` refusal and report 23503 on 16, as they did before
  this change.
