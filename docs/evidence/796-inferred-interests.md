# F21-18 (#796): interests inferred from net purchases

Evidence for the three acceptance criteria, from the tests in
`services/customer-preferences/tests/inference.rs` and `tests/http.rs`, run against the
seeded database as `svc_preferences`.

| Criterion | Test | What it shows |
|---|---|---|
| Each inferred interest states its source, window and strength | `strength_is_the_share_of_sales_that_include_the_category_rounded_half_up`, `a_sale_outside_the_window_does_not_count_and_the_window_is_recorded` | Eight sales: 8/8 gives 100, 3/8 gives 38 and 1/8 gives 13 (half up); a sale with both products of a category counts once; each row carries `NET_PURCHASES` and its window |
| Net purchases only | `a_sale_returned_in_full_is_not_a_purchase`, `an_interest_under_one_percent_is_not_reported` | A fully returned sale leaves the denominator; 1 in 201 is under 1% and is dropped |
| A customer who objects to profiling gets nothing inferred | `a_customer_who_does_not_grant_profiling_gets_nothing_inferred`, `withdrawing_profiling_deletes_what_was_inferred_and_a_regrant_infers_nothing_by_itself`, `a_whole_run_sweeps_the_rows_of_customers_who_no_longer_grant_profiling` | Withdrawn and never-recorded customers get 0 rows; a withdrawal deletes the rows in its own transaction |
| A declared interest stands and the two stay distinguishable | `a_declared_interest_stands_and_is_never_repeated_among_the_inferred`, `interests_show_the_declared_apart_from_the_inferred_in_json_and_xml_to_the_floor_roles` | Declared before or after a run, the category is never in `inferred_interests`; JSON and XML keep two members |
| Personalization | `withdrawing_personalization_hides_the_inferred_interests_without_deleting_them` | The reader returns none while it is withdrawn; the rows return when it is granted |

Results: customer-preferences `cargo test` 7 unit, 1 backfill, 7 consent, 5 contract, 3
history, 3 http, 12 inference, 10 replace and 3 source-guard tests pass; `cargo clippy
--all-targets -D warnings` and `cargo fmt --check` are clean. The repository suite gives
4,025 passed. `contracts/db/check_grants.sh` prints nothing.
