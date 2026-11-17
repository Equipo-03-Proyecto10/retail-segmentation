//! Check-ins are narrowed to the caller's assigned stores (F19-07, ADR-0060).
//!
//! Against the seeded PostgreSQL of TEST_DATABASE_URL, as `svc_checkins`.

mod support;

use check_ins::{list_check_ins, read_check_in, record_check_in, CheckInRequest, Filters, Fix};
use rust_decimal::prelude::FromPrimitive;
use rust_decimal::Decimal;
use support::{
    connect, dec, earlier, owner_url, principal, service_database, TestStore, ADMIN, STORE_MANAGER,
};
use uuid::Uuid;

fn request(store: &TestStore) -> CheckInRequest {
    CheckInRequest {
        client_check_in_id: Uuid::new_v4(),
        store_id: i32::from(store.id),
        fix: Some(Fix {
            latitude: dec("25.6866"),
            longitude: dec("-100.3161"),
            accuracy_m: Decimal::from_f64(5.0).unwrap(),
        }),
        occurred_at: earlier(),
    }
}

#[tokio::test]
async fn a_manager_records_reads_and_lists_only_in_an_assigned_store() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let mine = TestStore::create(&owner, true).await;
    let theirs = TestStore::create(&owner, true).await;
    theirs.unassign(&owner, STORE_MANAGER).await;
    let manager = principal(STORE_MANAGER, "STORE_MANAGER");
    let admin = principal(ADMIN, "ADMIN");

    // Recording: the other store is refused and nothing is stored.
    let refused = request(&theirs);
    let error = record_check_in(&db, &manager, &refused, chrono::Utc::now())
        .await
        .unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (403, "forbidden_store")
    );
    let stored = owner
        .query_one(
            "SELECT count(*) FROM checkins.store_check_in WHERE client_check_in_id = $1",
            &[&refused.client_check_in_id],
        )
        .await
        .unwrap()
        .get::<_, i64>(0);
    assert_eq!(stored, 0);
    let (own, _) = record_check_in(&db, &manager, &request(&mine), chrono::Utc::now())
        .await
        .unwrap();
    let (other, _) = record_check_in(&db, &admin, &request(&theirs), chrono::Utc::now())
        .await
        .unwrap();

    // Reading one: the other store's check-in is 403, with no data.
    let error = read_check_in(&db, &manager, other.check_in_id)
        .await
        .unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (403, "forbidden_store")
    );
    assert!(!format!("{error:?}").contains(&other.check_in_id.to_string()));
    assert!(read_check_in(&db, &manager, own.check_in_id).await.is_ok());

    // Listing: without a store filter, only the caller's stores; naming
    // another store is refused.
    let (items, _) = list_check_ins(&db, &manager, &Filters::default(), 1, 100)
        .await
        .unwrap();
    assert!(items.iter().any(|item| item.check_in_id == own.check_in_id));
    assert!(items
        .iter()
        .all(|item| item.check_in_id != other.check_in_id));
    let named = Filters {
        store_id: Some(i32::from(theirs.id)),
        ..Filters::default()
    };
    let error = list_check_ins(&db, &manager, &named, 1, 20)
        .await
        .unwrap_err();
    assert_eq!(error.code, "forbidden_store");

    // ADMIN and AUDITOR are not confined.
    for role in [("ADMIN", ADMIN), ("AUDITOR", ADMIN)] {
        let caller = principal(role.1, role.0);
        assert!(read_check_in(&db, &caller, other.check_in_id).await.is_ok());
        let (_, total) = list_check_ins(&db, &caller, &named, 1, 20).await.unwrap();
        assert_eq!(total, 1);
    }

    mine.cleanup(&owner).await;
    theirs.cleanup(&owner).await;
}

#[tokio::test]
async fn a_removed_assignment_is_refused_on_the_next_call() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let store = TestStore::create(&owner, true).await;
    let manager = principal(STORE_MANAGER, "STORE_MANAGER");
    record_check_in(&db, &manager, &request(&store), chrono::Utc::now())
        .await
        .unwrap();

    store.unassign(&owner, STORE_MANAGER).await;
    let error = record_check_in(&db, &manager, &request(&store), chrono::Utc::now())
        .await
        .unwrap_err();
    assert_eq!(error.code, "forbidden_store");
    let named = Filters {
        store_id: Some(i32::from(store.id)),
        ..Filters::default()
    };
    let error = list_check_ins(&db, &manager, &named, 1, 20)
        .await
        .unwrap_err();
    assert_eq!(error.code, "forbidden_store");
    store.cleanup(&owner).await;
}
