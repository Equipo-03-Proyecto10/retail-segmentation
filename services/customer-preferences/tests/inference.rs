//! Interests inferred from net purchases (F21-18, ADR-0066, RN-34, RN-35)
//! against PostgreSQL, through the service's own functions, connected as
//! `svc_preferences`: what a run stores, what consent keeps it from storing,
//! and what a reader is shown next to the declared interests.

mod support;

use customer_preferences::{
    infer_all, infer_customer, read_interests, record_consent, replace, InterestProfile,
    PreferenceSet, INFERENCE_SOURCE,
};
use support::{
    admin, connect, owner_url, principal, products_of, service_database, stocked_categories,
    TestCustomer,
};
use tokio_postgres::Client;
use uuid::Uuid;

const WINDOW: i16 = 180;

fn entries(list: &[(&str, &str)]) -> Vec<(String, String)> {
    list.iter()
        .map(|(purpose, decision)| ((*purpose).to_owned(), (*decision).to_owned()))
        .collect()
}

/// A customer whose three purposes are explicitly granted.
async fn consenting(owner: &Client, db: &mosaiq_kit::Database) -> TestCustomer {
    let customer = TestCustomer::create(owner).await;
    let actor = principal(admin(), "ADMIN");
    record_consent(
        db,
        &actor,
        customer.id,
        &entries(&[
            ("PROFILING", "GRANTED"),
            ("PERSONALIZATION", "GRANTED"),
            ("MARKETING_CONTACT", "WITHDRAWN"),
        ]),
    )
    .await
    .unwrap();
    customer
}

async fn profile(db: &mosaiq_kit::Database, customer: Uuid) -> InterestProfile {
    let connection = db.connection().await.unwrap();
    read_interests(&***connection, customer)
        .await
        .unwrap()
        .expect("the customer exists")
}

/// Eight sales: category `a` in three of them (once with both of its products),
/// `b` in one, `c` in all eight.
async fn eight_sales(owner: &Client, customer: &TestCustomer, cats: [i16; 3]) {
    let [a, b, c] = cats;
    let (pa, pb, pc) = (
        products_of(owner, a).await,
        products_of(owner, b).await,
        products_of(owner, c).await,
    );
    customer.sale(owner, 10, &[pa[0], pa[1], pc[0]]).await;
    customer.sale(owner, 20, &[pa[0], pc[0]]).await;
    customer.sale(owner, 30, &[pa[1], pc[1]]).await;
    customer.sale(owner, 40, &[pb[0], pc[0]]).await;
    for days in [50, 60, 70, 80] {
        customer.sale(owner, days, &[pc[0]]).await;
    }
}

#[tokio::test]
async fn strength_is_the_share_of_sales_that_include_the_category_rounded_half_up() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let cats = stocked_categories(&owner).await;
    let customer = consenting(&owner, &db).await;
    eight_sales(&owner, &customer, cats).await;

    let kept = infer_customer(&db, customer.id, WINDOW).await.unwrap();
    assert_eq!(kept, 3);
    // 8/8 = 100; 3/8 = 37.5 rounds up to 38 and counts once for the sale that
    // holds both products; 1/8 = 12.5 rounds up to 13.
    assert_eq!(
        customer.stored(&owner).await,
        vec![
            (cats[2], 100, WINDOW),
            (cats[0], 38, WINDOW),
            (cats[1], 13, WINDOW)
        ]
    );

    // Each states its source, window and strength, strongest first and then by
    // category.
    let shown = profile(&db, customer.id).await;
    let seen: Vec<_> = shown
        .inferred_interests
        .iter()
        .map(|i| (i.category_id, i.strength, i.window_days, i.source.as_str()))
        .collect();
    assert_eq!(
        seen,
        vec![
            (cats[2], 100, WINDOW, INFERENCE_SOURCE),
            (cats[0], 38, WINDOW, INFERENCE_SOURCE),
            (cats[1], 13, WINDOW, INFERENCE_SOURCE),
        ]
    );
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn equal_strengths_are_ordered_by_category() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let [a, b, _] = stocked_categories(&owner).await;
    let customer = consenting(&owner, &db).await;
    let (pa, pb) = (products_of(&owner, a).await, products_of(&owner, b).await);
    customer.sale(&owner, 5, &[pa[0]]).await;
    customer.sale(&owner, 6, &[pb[0]]).await;
    infer_customer(&db, customer.id, WINDOW).await.unwrap();
    let shown = profile(&db, customer.id).await;
    let order: Vec<_> = shown
        .inferred_interests
        .iter()
        .map(|i| (i.category_id, i.strength))
        .collect();
    assert_eq!(order, vec![(a.min(b), 50), (a.max(b), 50)]);
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn a_sale_outside_the_window_does_not_count_and_the_window_is_recorded() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let [a, b, _] = stocked_categories(&owner).await;
    let customer = consenting(&owner, &db).await;
    let (pa, pb) = (products_of(&owner, a).await, products_of(&owner, b).await);
    customer.sale(&owner, 400, &[pa[0]]).await;
    customer.sale(&owner, 10, &[pb[0]]).await;

    infer_customer(&db, customer.id, 30).await.unwrap();
    assert_eq!(customer.stored(&owner).await, vec![(b, 100, 30)]);
    infer_customer(&db, customer.id, 3650).await.unwrap();
    assert_eq!(
        customer
            .stored(&owner)
            .await
            .iter()
            .map(|r| (r.1, r.2))
            .collect::<Vec<_>>(),
        vec![(50, 3650), (50, 3650)]
    );
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn a_sale_returned_in_full_is_not_a_purchase() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let [a, b, _] = stocked_categories(&owner).await;
    let customer = consenting(&owner, &db).await;
    let (pa, pb) = (products_of(&owner, a).await, products_of(&owner, b).await);
    let returned = customer.sale(&owner, 5, &[pa[0]]).await;
    customer.sale(&owner, 6, &[pb[0]]).await;
    customer.return_all(&owner, returned).await;

    infer_customer(&db, customer.id, WINDOW).await.unwrap();
    assert_eq!(customer.stored(&owner).await, vec![(b, 100, WINDOW)]);
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn an_interest_under_one_percent_is_not_reported() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let [a, b, _] = stocked_categories(&owner).await;
    let customer = consenting(&owner, &db).await;
    let (pa, pb) = (products_of(&owner, a).await, products_of(&owner, b).await);
    // One sale in 201 is 0.4975%, which rounds to 0; one in 200 is 0.5%,
    // which rounds up to 1.
    customer.sale(&owner, 3, &[pa[0]]).await;
    for _ in 0..200 {
        customer.sale(&owner, 4, &[pb[0]]).await;
    }
    infer_customer(&db, customer.id, WINDOW).await.unwrap();
    assert_eq!(customer.stored(&owner).await, vec![(b, 100, WINDOW)]);
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn a_customer_who_does_not_grant_profiling_gets_nothing_inferred() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let cats = stocked_categories(&owner).await;

    // Never recorded: the log fails closed.
    let silent = TestCustomer::create(&owner).await;
    eight_sales(&owner, &silent, cats).await;
    assert_eq!(infer_customer(&db, silent.id, WINDOW).await.unwrap(), 0);
    assert!(silent.stored(&owner).await.is_empty());

    // Withdrawn explicitly.
    let objector = TestCustomer::create(&owner).await;
    eight_sales(&owner, &objector, cats).await;
    record_consent(
        &db,
        &principal(admin(), "ADMIN"),
        objector.id,
        &entries(&[("PROFILING", "WITHDRAWN"), ("PERSONALIZATION", "GRANTED")]),
    )
    .await
    .unwrap();
    assert_eq!(infer_customer(&db, objector.id, WINDOW).await.unwrap(), 0);
    assert!(objector.stored(&owner).await.is_empty());
    assert!(profile(&db, objector.id)
        .await
        .inferred_interests
        .is_empty());

    silent.cleanup(&owner).await;
    objector.cleanup(&owner).await;
}

#[tokio::test]
async fn withdrawing_profiling_deletes_what_was_inferred_and_a_regrant_infers_nothing_by_itself() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let cats = stocked_categories(&owner).await;
    let customer = consenting(&owner, &db).await;
    eight_sales(&owner, &customer, cats).await;
    let actor = principal(admin(), "ADMIN");
    replace(
        &db,
        &actor,
        customer.id,
        PreferenceSet::InterestCategories,
        &[Some(cats[1])],
    )
    .await
    .unwrap();
    infer_customer(&db, customer.id, WINDOW).await.unwrap();
    assert!(!customer.stored(&owner).await.is_empty());

    record_consent(
        &db,
        &actor,
        customer.id,
        &entries(&[("PROFILING", "WITHDRAWN")]),
    )
    .await
    .unwrap();
    assert!(
        customer.stored(&owner).await.is_empty(),
        "deleted with the decision"
    );
    let shown = profile(&db, customer.id).await;
    assert!(shown.inferred_interests.is_empty());
    assert_eq!(
        shown.declared_category_ids,
        vec![cats[1]],
        "declared interests stay"
    );

    // Granting again does not bring them back until a run infers them anew.
    record_consent(
        &db,
        &actor,
        customer.id,
        &entries(&[("PROFILING", "GRANTED")]),
    )
    .await
    .unwrap();
    assert!(profile(&db, customer.id)
        .await
        .inferred_interests
        .is_empty());
    assert!(infer_customer(&db, customer.id, WINDOW).await.unwrap() > 0);
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn withdrawing_personalization_hides_the_inferred_interests_without_deleting_them() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let cats = stocked_categories(&owner).await;
    let customer = consenting(&owner, &db).await;
    eight_sales(&owner, &customer, cats).await;
    infer_customer(&db, customer.id, WINDOW).await.unwrap();
    let actor = principal(admin(), "ADMIN");
    record_consent(
        &db,
        &actor,
        customer.id,
        &entries(&[("PERSONALIZATION", "WITHDRAWN")]),
    )
    .await
    .unwrap();
    assert!(profile(&db, customer.id)
        .await
        .inferred_interests
        .is_empty());
    assert_eq!(customer.stored(&owner).await.len(), 3);
    record_consent(
        &db,
        &actor,
        customer.id,
        &entries(&[("PERSONALIZATION", "GRANTED")]),
    )
    .await
    .unwrap();
    assert_eq!(profile(&db, customer.id).await.inferred_interests.len(), 3);
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn a_declared_interest_stands_and_is_never_repeated_among_the_inferred() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let cats = stocked_categories(&owner).await;
    let customer = consenting(&owner, &db).await;
    eight_sales(&owner, &customer, cats).await;
    let actor = principal(admin(), "ADMIN");

    // Declared before the run: the category is never inferred.
    replace(
        &db,
        &actor,
        customer.id,
        PreferenceSet::InterestCategories,
        &[Some(cats[0])],
    )
    .await
    .unwrap();
    infer_customer(&db, customer.id, WINDOW).await.unwrap();
    assert_eq!(
        customer
            .stored(&owner)
            .await
            .iter()
            .map(|r| r.0)
            .collect::<Vec<_>>(),
        vec![cats[2], cats[1]]
    );
    assert_eq!(
        customer.categories(&owner).await,
        vec![cats[0]],
        "inference wrote no declared row"
    );

    // Declared after the run: the stored row stays but the reader leaves it
    // out, so the two sets never overlap in a response.
    replace(
        &db,
        &actor,
        customer.id,
        PreferenceSet::InterestCategories,
        &[Some(cats[0]), Some(cats[1])],
    )
    .await
    .unwrap();
    let shown = profile(&db, customer.id).await;
    assert_eq!(shown.declared_category_ids, {
        let mut declared = vec![cats[0], cats[1]];
        declared.sort_unstable();
        declared
    });
    assert_eq!(
        shown
            .inferred_interests
            .iter()
            .map(|i| i.category_id)
            .collect::<Vec<_>>(),
        vec![cats[2]]
    );

    // The next run drops the stored row too.
    infer_customer(&db, customer.id, WINDOW).await.unwrap();
    assert_eq!(customer.stored(&owner).await.len(), 1);
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn an_unknown_customer_is_not_found_and_a_customer_without_purchases_has_nothing() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let error = infer_customer(&db, Uuid::new_v4(), WINDOW)
        .await
        .unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (404, "customer_not_found")
    );
    let connection = db.connection().await.unwrap();
    assert!(read_interests(&***connection, Uuid::new_v4())
        .await
        .unwrap()
        .is_none());
    drop(connection);

    let customer = consenting(&owner, &db).await;
    assert_eq!(infer_customer(&db, customer.id, WINDOW).await.unwrap(), 0);
    assert!(profile(&db, customer.id)
        .await
        .inferred_interests
        .is_empty());
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn a_whole_run_sweeps_the_rows_of_customers_who_no_longer_grant_profiling() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let [a, ..] = stocked_categories(&owner).await;
    let customer = consenting(&owner, &db).await;
    customer
        .sale(&owner, 2, &[products_of(&owner, a).await[0]])
        .await;
    let done = infer_all(&db, WINDOW).await.unwrap();
    assert!(done >= 1);
    assert_eq!(customer.stored(&owner).await, vec![(a, 100, WINDOW)]);

    // A row left by a writer that went around the service: the next run
    // removes it, since the customer no longer grants PROFILING.
    owner
        .batch_execute("SET session_replication_role = replica")
        .await
        .unwrap();
    owner
        .execute(
            "INSERT INTO preferences.consent_decision \
             (customer_id, purpose, decision, recorded_by, source, basis) \
             VALUES ($1, 'PROFILING', 'WITHDRAWN', $2, 'STAFF_ON_BEHALF', 'EXPLICIT')",
            &[&customer.id, &admin()],
        )
        .await
        .unwrap();
    owner
        .batch_execute("SET session_replication_role = origin")
        .await
        .unwrap();
    assert_eq!(customer.stored(&owner).await.len(), 1);
    infer_all(&db, WINDOW).await.unwrap();
    assert!(customer.stored(&owner).await.is_empty());
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn the_schema_refuses_what_a_reader_could_not_make_sense_of() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let [a, ..] = stocked_categories(&owner).await;
    let customer = TestCustomer::create(&owner).await;
    for (source, window, strength, name) in [
        (
            "DECLARED",
            180_i16,
            50_i16,
            "inferred_interest_source_check",
        ),
        (
            "NET_PURCHASES",
            0,
            50,
            "inferred_interest_window_days_check",
        ),
        (
            "NET_PURCHASES",
            3651,
            50,
            "inferred_interest_window_days_check",
        ),
        ("NET_PURCHASES", 180, 0, "inferred_interest_strength_check"),
        (
            "NET_PURCHASES",
            180,
            101,
            "inferred_interest_strength_check",
        ),
    ] {
        let error = owner
            .execute(
                "INSERT INTO preferences.inferred_interest \
                 (customer_id, category_id, source, window_days, strength) \
                 VALUES ($1, $2, $3, $4, $5)",
                &[&customer.id, &a, &source, &window, &strength],
            )
            .await
            .unwrap_err();
        assert_eq!(
            error.as_db_error().and_then(|e| e.constraint()),
            Some(name),
            "{source} {window} {strength}"
        );
    }
    customer.cleanup(&owner).await;
}
