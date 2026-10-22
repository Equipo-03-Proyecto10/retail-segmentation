//! RN-56 against PostgreSQL, through the service's own write and read paths,
//! connected as `svc_checkins`.

mod support;

use check_ins::{
    append_review, list_check_ins, read_check_in, record_check_in, CheckInRequest, Filters, Fix,
    Outcome,
};
use chrono::Duration;
use rust_decimal::prelude::FromPrimitive;
use rust_decimal::Decimal;
use support::{
    connect, dec, earlier, owner_url, principal, service_database, TestStore, ADMIN, STORE_MANAGER,
};
use uuid::Uuid;

/// A fix `metres` due north of the test store, with the given accuracy.
fn north(metres: f64, accuracy: &str) -> Option<Fix> {
    let latitude = 25.6866 + metres / 111_195.08;
    Some(Fix {
        latitude: Decimal::from_f64(latitude).unwrap().round_dp(9),
        longitude: dec("-100.3161"),
        accuracy_m: dec(accuracy),
    })
}

fn request(store: &TestStore, fix: Option<Fix>) -> CheckInRequest {
    CheckInRequest {
        client_check_in_id: Uuid::new_v4(),
        store_id: i32::from(store.id),
        occurred_at: earlier(),
        fix,
    }
}

#[tokio::test]
async fn each_reason_is_recorded_with_its_distance_and_verdict() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let located = TestStore::create(&owner, true).await;
    let unlocated = TestStore::create(&owner, false).await;
    let manager = principal(STORE_MANAGER, "STORE_MANAGER");

    let cases = [
        (
            &located,
            north(150.0, "5"),
            "within_geofence",
            Some("150.0"),
            true,
        ),
        (
            &located,
            north(400.0, "5"),
            "outside_geofence",
            Some("400.0"),
            false,
        ),
        (
            &located,
            north(10.0, "151"),
            "low_accuracy",
            Some("10.0"),
            false,
        ),
        (&located, None, "no_fix", None, false),
        (
            &unlocated,
            north(10.0, "5"),
            "store_location_unknown",
            None,
            false,
        ),
    ];
    for (store, fix, reason, distance, verified) in cases {
        let (check_in, outcome) =
            record_check_in(&db, &manager, &request(store, fix), chrono::Utc::now())
                .await
                .unwrap();
        assert_eq!(outcome, Outcome::Created, "{reason}");
        assert_eq!(check_in.reason, reason);
        assert_eq!(check_in.distance_m, distance.map(dec), "{reason}");
        assert_eq!(check_in.verified, verified, "{reason}");
        assert_eq!(check_in.auto_verified(), verified, "{reason}");
        // user_id is the token's sub, never something the phone sent.
        assert_eq!(check_in.user_id.to_string(), STORE_MANAGER);
    }

    located.cleanup(&owner).await;
    unlocated.cleanup(&owner).await;
}

#[tokio::test]
async fn a_replay_answers_the_original_and_another_value_conflicts() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let store = TestStore::create(&owner, true).await;
    let manager = principal(STORE_MANAGER, "STORE_MANAGER");
    let first = request(&store, north(20.0, "5"));

    let (original, created) = record_check_in(&db, &manager, &first, chrono::Utc::now())
        .await
        .unwrap();
    assert_eq!(created, Outcome::Created);
    let (again, replayed) = record_check_in(&db, &manager, &first, chrono::Utc::now())
        .await
        .unwrap();
    assert_eq!((again, replayed), (original, Outcome::Replayed));

    let mut moved = first.clone();
    moved.fix = north(25.0, "5");
    let error = record_check_in(&db, &manager, &moved, chrono::Utc::now())
        .await
        .unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (409, "client_check_in_conflict")
    );
    // Another user replaying the phone's id is a conflict too.
    let error = record_check_in(&db, &principal(ADMIN, "ADMIN"), &first, chrono::Utc::now())
        .await
        .unwrap_err();
    assert_eq!(error.code, "client_check_in_conflict");

    let rows: i64 = owner
        .query_one(
            "SELECT count(*) FROM checkins.store_check_in WHERE client_check_in_id = $1",
            &[&first.client_check_in_id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(rows, 1);
    store.cleanup(&owner).await;
}

#[tokio::test]
async fn two_concurrent_identical_posts_leave_one_row() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let store = TestStore::create(&owner, true).await;
    let shared = request(&store, north(30.0, "5"));

    let mut tasks = Vec::new();
    for _ in 0..8 {
        let url = url.clone();
        let shared = shared.clone();
        tasks.push(tokio::spawn(async move {
            let db = service_database(&url);
            record_check_in(
                &db,
                &principal(STORE_MANAGER, "STORE_MANAGER"),
                &shared,
                chrono::Utc::now(),
            )
            .await
        }));
    }
    let mut outcomes = Vec::new();
    for task in tasks {
        outcomes.push(
            task.await
                .unwrap()
                .expect("every identical post succeeds")
                .1,
        );
    }
    assert_eq!(
        outcomes.iter().filter(|o| **o == Outcome::Created).count(),
        1
    );

    let rows: i64 = owner
        .query_one(
            "SELECT count(*) FROM checkins.store_check_in WHERE client_check_in_id = $1",
            &[&shared.client_check_in_id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(rows, 1);
    store.cleanup(&owner).await;
}

#[tokio::test]
async fn unknown_or_inactive_stores_and_future_moments_are_refused() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let store = TestStore::create(&owner, true).await;
    let manager = principal(STORE_MANAGER, "STORE_MANAGER");

    let mut ahead = request(&store, north(10.0, "5"));
    ahead.occurred_at = chrono::Utc::now() + Duration::minutes(6);
    let error = record_check_in(&db, &manager, &ahead, chrono::Utc::now())
        .await
        .unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (422, "occurred_in_future")
    );
    // Four minutes ahead is a phone clock, not the future.
    ahead.occurred_at = chrono::Utc::now() + Duration::minutes(4);
    record_check_in(&db, &manager, &ahead, chrono::Utc::now())
        .await
        .unwrap();

    let mut missing = request(&store, None);
    missing.store_id = 32_767;
    let error = record_check_in(&db, &manager, &missing, chrono::Utc::now())
        .await
        .unwrap_err();
    assert_eq!((error.status, error.code.as_str()), (422, "unknown_store"));

    store.deactivate(&owner).await;
    let error = record_check_in(&db, &manager, &request(&store, None), chrono::Utc::now())
        .await
        .unwrap_err();
    assert_eq!(error.code, "unknown_store");
    store.cleanup(&owner).await;
}

#[tokio::test]
async fn reviews_append_and_the_latest_decides() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let store = TestStore::create(&owner, true).await;
    let admin = principal(ADMIN, "ADMIN");
    let (check_in, _) = record_check_in(
        &db,
        &principal(STORE_MANAGER, "STORE_MANAGER"),
        &request(&store, north(900.0, "5")),
        chrono::Utc::now(),
    )
    .await
    .unwrap();
    assert!(!check_in.verified);

    let first = append_review(&db, &admin, check_in.check_in_id, true, "Seen on camera")
        .await
        .unwrap();
    assert_eq!(first.reviewed_by.to_string(), ADMIN);
    let (read, reviews) = read_check_in(&db, check_in.check_in_id).await.unwrap();
    assert!(read.verified && !read.auto_verified());
    assert_eq!(reviews.len(), 1);

    append_review(
        &db,
        &admin,
        check_in.check_in_id,
        false,
        "Camera was another day",
    )
    .await
    .unwrap();
    let (read, reviews) = read_check_in(&db, check_in.check_in_id).await.unwrap();
    assert!(!read.verified);
    assert_eq!(
        reviews
            .iter()
            .map(|review| review.verified)
            .collect::<Vec<_>>(),
        [true, false]
    );
    assert!(reviews[0].review_id < reviews[1].review_id);

    let error = append_review(&db, &admin, check_in.check_in_id, true, "   ")
        .await
        .unwrap_err();
    assert_eq!((error.status, error.code.as_str()), (422, "invalid_body"));
    let error = append_review(&db, &admin, Uuid::new_v4(), true, "No such check-in")
        .await
        .unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (404, "check_in_not_found")
    );
    let error = read_check_in(&db, Uuid::new_v4()).await.unwrap_err();
    assert_eq!(error.code, "check_in_not_found");
    store.cleanup(&owner).await;
}

#[tokio::test]
async fn the_list_filters_on_store_user_effective_verdict_and_time() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let store = TestStore::create(&owner, true).await;
    let manager = principal(STORE_MANAGER, "STORE_MANAGER");
    // One occurred_at for both, so the `from` filter below excludes both. Each
    // request() reads the clock, truncated to the second: two calls that
    // straddle a second boundary put the second check-in inside the filter.
    let now = chrono::Utc::now();
    let at = earlier();
    let mut near = request(&store, north(10.0, "5"));
    near.occurred_at = at;
    let mut far = request(&store, north(800.0, "5"));
    far.occurred_at = at;
    let (inside, _) = record_check_in(&db, &manager, &near, now).await.unwrap();
    let (outside, _) = record_check_in(&db, &manager, &far, now).await.unwrap();
    // A review overturns the outside one: the filter is on the effective verdict.
    append_review(
        &db,
        &principal(ADMIN, "ADMIN"),
        outside.check_in_id,
        true,
        "Confirmed",
    )
    .await
    .unwrap();

    let mut filters = Filters {
        store_id: Some(i32::from(store.id)),
        ..Filters::default()
    };
    let (items, total) = list_check_ins(&db, &filters, 1, 20).await.unwrap();
    assert_eq!(total, 2);
    assert_eq!(items.len(), 2);

    filters.verified = Some(true);
    assert_eq!(list_check_ins(&db, &filters, 1, 20).await.unwrap().1, 2);
    filters.verified = Some(false);
    assert_eq!(list_check_ins(&db, &filters, 1, 20).await.unwrap().1, 0);

    filters.verified = None;
    filters.user_id = Some(Uuid::parse_str(ADMIN).unwrap());
    assert_eq!(list_check_ins(&db, &filters, 1, 20).await.unwrap().1, 0);
    filters.user_id = Some(inside.user_id);
    filters.from = Some(inside.occurred_at + Duration::seconds(1));
    assert_eq!(list_check_ins(&db, &filters, 1, 20).await.unwrap().1, 0);
    filters.from = None;

    // Past the last page the total still comes back, with no items.
    let (items, total) = list_check_ins(&db, &filters, 9, 1).await.unwrap();
    assert_eq!((items.len(), total), (0, 2));
    store.cleanup(&owner).await;
}

#[tokio::test]
async fn the_web_cannot_delete_a_store_that_has_check_ins() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let store = TestStore::create(&owner, true).await;
    record_check_in(
        &db,
        &principal(STORE_MANAGER, "STORE_MANAGER"),
        &request(&store, None),
        chrono::Utc::now(),
    )
    .await
    .unwrap();
    owner.batch_execute("SET ROLE retail_app").await.unwrap();
    let error = owner
        .execute("DELETE FROM store WHERE store_id = $1", &[&store.id])
        .await
        .unwrap_err();
    owner.batch_execute("RESET ROLE").await.unwrap();
    // PostgreSQL 16 reports RESTRICT as 23503 and 18 as 23001; the key that
    // refused the delete is what is asserted.
    assert_eq!(
        error.as_db_error().and_then(|error| error.constraint()),
        Some("store_check_in_store_id_fkey")
    );
    store.cleanup(&owner).await;
}
