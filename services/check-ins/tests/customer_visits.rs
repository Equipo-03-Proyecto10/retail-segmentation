//! RN-72 customer visits through the service's real PostgreSQL paths.

mod support;

use check_ins::{
    list_customer_visits, parse_customer_visit, read_customer_visit, record_customer_visit,
    CustomerVisitRequest, Outcome, VisitFilters, VISIT_DUPLICATE_SECONDS,
};
use chrono::{Duration, Utc};
use serde_json::json;
use support::{
    connect, earlier, owner_url, principal, service_database, TestCustomer, TestStore, ADMIN,
    SALES_ASSOCIATE, STORE_MANAGER,
};
use uuid::Uuid;

static TURN: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

fn request(customer: &TestCustomer, store: &TestStore) -> CustomerVisitRequest {
    CustomerVisitRequest {
        loyalty_code: customer.code.clone(),
        store_id: i32::from(store.id),
        occurred_at: earlier(),
        campaign_id: Some(1),
    }
}

async fn count(owner: &tokio_postgres::Client, store: &TestStore) -> i64 {
    owner
        .query_one(
            "SELECT count(*) FROM checkins.customer_visit WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap()
        .get(0)
}

#[test]
fn the_request_refuses_location_and_keeps_only_the_opaque_code_in_memory() {
    let body = json!({
        "loyalty_code": "7M3QX9T2KD5VHA8R0WBN4ZPCJE",
        "store_id": 3,
        "occurred_at": "2026-11-09T10:00:00Z",
        "latitude": 25.0
    });
    let error = parse_customer_visit(&body).unwrap_err();
    assert_eq!((error.status, error.code.as_str()), (422, "invalid_body"));
    assert_eq!(error.details[0].code, "location_not_stored");
}

#[tokio::test]
async fn campaign_consent_actor_and_privacy_are_persisted_exactly() {
    let _turn = TURN.lock().await;
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let store = TestStore::create(&owner, true).await;
    let customer = TestCustomer::create(&owner, Some("GRANTED")).await;
    let actor = principal(ADMIN, "ADMIN");

    let (visit, outcome) =
        record_customer_visit(&db, &actor, &request(&customer, &store), Utc::now())
            .await
            .unwrap();
    assert_eq!(outcome, Outcome::Created);
    assert_eq!(visit.customer_id, Some(customer.id));
    assert_eq!(visit.campaign_id, Some(1));
    assert_eq!(visit.recorded_by.to_string(), ADMIN);

    let row: serde_json::Value = serde_json::from_str(
        owner
            .query_one(
                "SELECT to_jsonb(v)::text FROM checkins.customer_visit AS v \
                 WHERE visit_id = $1",
                &[&visit.visit_id],
            )
            .await
            .unwrap()
            .get::<_, &str>(0),
    )
    .unwrap();
    assert_eq!(row["campaign_id"], 1);
    assert_eq!(row["recorded_by"], ADMIN);
    assert!(!row.to_string().contains(&customer.code));
    let forbidden_columns: i64 = owner
        .query_one(
            "SELECT count(*) FROM information_schema.columns \
             WHERE table_schema = 'checkins' AND table_name = 'customer_visit' \
               AND column_name IN ('loyalty_code', 'card_id', 'latitude', 'longitude', \
                                   'accuracy_m', 'address', 'device_id', 'network_id')",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(forbidden_columns, 0);
    store.cleanup(&owner).await;
}

#[tokio::test]
async fn refusals_follow_the_contract_order_and_store_nothing() {
    let _turn = TURN.lock().await;
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let mine = TestStore::create(&owner, true).await;
    let other = TestStore::create(&owner, true).await;
    other.unassign(&owner, STORE_MANAGER).await;
    let granted = TestCustomer::create(&owner, Some("GRANTED")).await;
    let withdrawn = TestCustomer::create(&owner, Some("WITHDRAWN")).await;
    let unrecorded = TestCustomer::create(&owner, None).await;
    let manager = principal(STORE_MANAGER, "STORE_MANAGER");
    let admin = principal(ADMIN, "ADMIN");

    let mut future = request(&granted, &other);
    future.occurred_at = Utc::now() + Duration::minutes(6);
    let error = record_customer_visit(&db, &manager, &future, Utc::now())
        .await
        .unwrap_err();
    assert_eq!(error.code, "occurred_in_future");

    let mut forbidden = request(&granted, &other);
    forbidden.campaign_id = Some(999_999);
    let error = record_customer_visit(&db, &manager, &forbidden, Utc::now())
        .await
        .unwrap_err();
    assert_eq!(error.code, "forbidden_store");

    let mut unknown_store = request(&granted, &mine);
    unknown_store.store_id = 32_767;
    unknown_store.campaign_id = Some(999_999);
    let error = record_customer_visit(&db, &admin, &unknown_store, Utc::now())
        .await
        .unwrap_err();
    assert_eq!(error.code, "unknown_store");

    let mut unknown_campaign = request(&granted, &mine);
    unknown_campaign.campaign_id = Some(999_999);
    unknown_campaign.loyalty_code = "AAAAAAAAAAAAAAAAAAAAAAAAAA".to_owned();
    let error = record_customer_visit(&db, &admin, &unknown_campaign, Utc::now())
        .await
        .unwrap_err();
    assert_eq!(error.code, "unknown_campaign");

    let mut unusable = request(&unrecorded, &mine);
    unusable.loyalty_code = "AAAAAAAAAAAAAAAAAAAAAAAAAA".to_owned();
    let error = record_customer_visit(&db, &admin, &unusable, Utc::now())
        .await
        .unwrap_err();
    assert_eq!(error.code, "loyalty_code_not_usable");

    for customer in [&withdrawn, &unrecorded] {
        let error = record_customer_visit(&db, &admin, &request(customer, &mine), Utc::now())
            .await
            .unwrap_err();
        assert_eq!(
            (error.status, error.code.as_str()),
            (409, "consent_required")
        );
    }
    assert_eq!(count(&owner, &mine).await, 0);
    assert_eq!(count(&owner, &other).await, 0);
    mine.cleanup(&owner).await;
    other.cleanup(&owner).await;
}

#[tokio::test]
async fn revoked_and_unknown_codes_have_one_indistinguishable_refusal() {
    let _turn = TURN.lock().await;
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let store = TestStore::create(&owner, true).await;
    let customer = TestCustomer::create(&owner, Some("GRANTED")).await;
    customer.revoke_card(&owner).await;
    let admin = principal(ADMIN, "ADMIN");

    let revoked = record_customer_visit(&db, &admin, &request(&customer, &store), Utc::now())
        .await
        .unwrap_err();
    let mut unknown = request(&customer, &store);
    unknown.loyalty_code = "ZZZZZZZZZZZZZZZZZZZZZZZZZZ".to_owned();
    let unknown = record_customer_visit(&db, &admin, &unknown, Utc::now())
        .await
        .unwrap_err();
    assert_eq!(
        (
            revoked.status,
            revoked.code,
            revoked.message,
            revoked.details
        ),
        (
            unknown.status,
            unknown.code,
            unknown.message,
            unknown.details
        )
    );
    assert_eq!(count(&owner, &store).await, 0);
    store.cleanup(&owner).await;
}

#[tokio::test]
async fn unreadable_consent_fails_closed_with_503_and_no_row() {
    let _turn = TURN.lock().await;
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let store = TestStore::create(&owner, true).await;
    let customer = TestCustomer::create(&owner, Some("GRANTED")).await;
    owner
        .execute(
            "REVOKE SELECT ON api_preferences.consent_state_v1 FROM svc_checkins",
            &[],
        )
        .await
        .unwrap();
    let result = record_customer_visit(
        &db,
        &principal(ADMIN, "ADMIN"),
        &request(&customer, &store),
        Utc::now(),
    )
    .await;
    owner
        .execute(
            "GRANT SELECT ON api_preferences.consent_state_v1 TO svc_checkins",
            &[],
        )
        .await
        .unwrap();
    let error = result.unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (503, "consent_unavailable")
    );
    assert_eq!(count(&owner, &store).await, 0);
    store.cleanup(&owner).await;
}

#[tokio::test]
async fn concurrent_posts_and_the_strict_window_leave_the_contracts_rows() {
    let _turn = TURN.lock().await;
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let store = TestStore::create(&owner, true).await;
    let customer = TestCustomer::create(&owner, Some("GRANTED")).await;
    let mut first = request(&customer, &store);
    first.occurred_at = Utc::now() - Duration::hours(3);

    let mut tasks = Vec::new();
    for seconds in 0..8 {
        let url = url.clone();
        let mut posted = first.clone();
        posted.occurred_at += Duration::seconds(seconds);
        tasks.push(tokio::spawn(async move {
            record_customer_visit(
                &service_database(&url),
                &principal(ADMIN, "ADMIN"),
                &posted,
                Utc::now(),
            )
            .await
        }));
    }
    let mut visits = Vec::new();
    let mut created = 0;
    for task in tasks {
        let (visit, outcome) = task.await.unwrap().unwrap();
        created += usize::from(outcome == Outcome::Created);
        visits.push(visit.visit_id);
    }
    assert_eq!(created, 1);
    assert!(visits.iter().all(|id| *id == visits[0]));
    assert_eq!(count(&owner, &store).await, 1);

    let mut boundary = first.clone();
    let anchor: chrono::DateTime<Utc> = owner
        .query_one(
            "SELECT occurred_at FROM checkins.customer_visit WHERE visit_id = $1",
            &[&visits[0]],
        )
        .await
        .unwrap()
        .get(0);
    boundary.occurred_at = anchor + Duration::seconds(VISIT_DUPLICATE_SECONDS);
    boundary.campaign_id = None;
    let (second, outcome) = record_customer_visit(
        &service_database(&url),
        &principal(ADMIN, "ADMIN"),
        &boundary,
        Utc::now(),
    )
    .await
    .unwrap();
    assert_eq!(outcome, Outcome::Created);
    assert_ne!(second.visit_id, visits[0]);
    assert_eq!(count(&owner, &store).await, 2);
    store.cleanup(&owner).await;
}

#[tokio::test]
async fn reads_apply_store_scope_filters_and_retention() {
    let _turn = TURN.lock().await;
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let mine = TestStore::create(&owner, true).await;
    let other = TestStore::create(&owner, true).await;
    mine.assign(&owner, SALES_ASSOCIATE).await;
    let customer = TestCustomer::create(&owner, Some("GRANTED")).await;
    let admin = principal(ADMIN, "ADMIN");
    let (own, _) = record_customer_visit(&db, &admin, &request(&customer, &mine), Utc::now())
        .await
        .unwrap();
    let (theirs, _) = record_customer_visit(&db, &admin, &request(&customer, &other), Utc::now())
        .await
        .unwrap();
    owner
        .execute(
            "INSERT INTO checkins.customer_visit \
             (visit_id, customer_id, store_id, occurred_at, recorded_by) \
             VALUES ($1, $2, $3, now() - interval '37 months', $4::text::uuid)",
            &[&Uuid::new_v4(), &customer.id, &mine.id, &ADMIN],
        )
        .await
        .unwrap();

    let seller = principal(SALES_ASSOCIATE, "SALES_ASSOCIATE");
    assert!(read_customer_visit(&db, &seller, own.visit_id)
        .await
        .is_ok());
    let error = read_customer_visit(&db, &seller, theirs.visit_id)
        .await
        .unwrap_err();
    assert_eq!(error.code, "forbidden_store");
    let mine_filter = VisitFilters {
        store_id: Some(i32::from(mine.id)),
        ..VisitFilters::default()
    };
    let (items, total) = list_customer_visits(&db, &seller, &mine_filter, 1, 100)
        .await
        .unwrap();
    assert_eq!(total, 2);
    assert!(items.iter().all(|visit| visit.store_id == mine.id));
    assert!(items.iter().any(|visit| visit.customer_id.is_none()));

    let filters = VisitFilters {
        store_id: Some(i32::from(other.id)),
        ..VisitFilters::default()
    };
    let error = list_customer_visits(&db, &seller, &filters, 1, 20)
        .await
        .unwrap_err();
    assert_eq!(error.code, "forbidden_store");
    let (items, total) = list_customer_visits(&db, &admin, &filters, 1, 20)
        .await
        .unwrap();
    assert_eq!(total, 1);
    assert_eq!(items[0].visit_id, theirs.visit_id);
    mine.cleanup(&owner).await;
    other.cleanup(&owner).await;
}
