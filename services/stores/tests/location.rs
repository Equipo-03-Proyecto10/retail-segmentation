//! The location rule against PostgreSQL, through the service's own write and
//! read path, connected as `svc_stores`.

mod support;

use rust_decimal::Decimal;
use stores::{clear_location, read_directory, set_location};
use support::{connect, owner_url, principal, service_database, users, TestStore};

fn dec(text: &str) -> Decimal {
    text.parse().unwrap()
}

async fn rows_for(owner: &tokio_postgres::Client, id: i16) -> i64 {
    owner
        .query_one(
            "SELECT count(*) FROM stores.store_location WHERE store_id = $1",
            &[&id],
        )
        .await
        .unwrap()
        .get(0)
}

#[tokio::test]
async fn put_creates_then_replaces_and_a_store_never_has_two() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let (first, second) = users(&owner).await;
    let store = TestStore::create(&owner).await;

    let created = set_location(
        &db,
        &principal(first),
        store.id,
        dec("25.6866"),
        dec("-100.3161"),
        150,
    )
    .await
    .unwrap();
    assert_eq!(created.geofence_radius_m, Some(150));
    assert_eq!(rows_for(&owner, store.id).await, 1);

    let replaced = set_location(
        &db,
        &principal(second),
        store.id,
        dec("19.4326"),
        dec("-99.1332"),
        2000,
    )
    .await
    .unwrap();
    assert_eq!(replaced.latitude, Some(dec("19.4326")));
    assert_eq!(replaced.geofence_radius_m, Some(2000));
    assert_eq!(rows_for(&owner, store.id).await, 1);

    // set_by is the caller's sub, and follows the latest writer.
    let set_by: uuid::Uuid = owner
        .query_one(
            "SELECT set_by FROM stores.store_location WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(set_by, second);
    store.cleanup(&owner).await;
}

#[tokio::test]
async fn out_of_range_values_are_422_invalid_body_naming_the_field() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let (caller, _) = users(&owner).await;
    let store = TestStore::create(&owner).await;
    let cases = [
        ("latitude", "91", "0", 100),
        ("longitude", "0", "-181", 100),
        ("geofence_radius_m", "0", "0", 24),
        ("geofence_radius_m", "0", "0", 2001),
    ];
    for (field, latitude, longitude, radius) in cases {
        let error = set_location(
            &db,
            &principal(caller),
            store.id,
            dec(latitude),
            dec(longitude),
            radius,
        )
        .await
        .expect_err(field);
        assert_eq!(
            (error.status, error.code.as_str()),
            (422, "invalid_body"),
            "{field}"
        );
        assert_eq!(error.details[0].field, field);
    }
    assert_eq!(rows_for(&owner, store.id).await, 0);
    // The edges are accepted.
    for (latitude, longitude, radius) in [("90", "180", 25), ("-90", "-180", 2000)] {
        set_location(
            &db,
            &principal(caller),
            store.id,
            dec(latitude),
            dec(longitude),
            radius,
        )
        .await
        .unwrap();
    }
    store.cleanup(&owner).await;
}

#[tokio::test]
async fn an_unknown_store_is_404_store_not_found() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let (caller, _) = users(&owner).await;
    let actor = principal(caller);
    let put = set_location(&db, &actor, 29_999, dec("1"), dec("1"), 100)
        .await
        .unwrap_err();
    assert_eq!((put.status, put.code.as_str()), (404, "store_not_found"));
    let delete = clear_location(&db, Some(&actor), 29_999).await.unwrap_err();
    assert_eq!(
        (delete.status, delete.code.as_str()),
        (404, "store_not_found")
    );
}

#[tokio::test]
async fn delete_removes_the_row_and_the_directory_shows_no_location() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let (caller, _) = users(&owner).await;
    let actor = principal(caller);
    let store = TestStore::create(&owner).await;

    let listed = |stores: &[stores::StoreRecord]| {
        stores
            .iter()
            .find(|s| s.store_id == store.id)
            .cloned()
            .unwrap()
    };
    assert_eq!(listed(&read_directory(&db).await.unwrap()).latitude, None);
    set_location(&db, &actor, store.id, dec("20.5"), dec("-100.4"), 80)
        .await
        .unwrap();
    assert_eq!(
        listed(&read_directory(&db).await.unwrap()).latitude,
        Some(dec("20.5"))
    );
    clear_location(&db, Some(&actor), store.id).await.unwrap();
    assert_eq!(rows_for(&owner, store.id).await, 0);
    assert_eq!(listed(&read_directory(&db).await.unwrap()).latitude, None);
    store.cleanup(&owner).await;
}

#[tokio::test]
async fn each_write_leaves_one_audit_row_naming_the_caller() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let (caller, _) = users(&owner).await;
    let actor = principal(caller);
    let store = TestStore::create(&owner).await;
    let pk = store.id.to_string();

    set_location(&db, &actor, store.id, dec("20"), dec("-100"), 90)
        .await
        .unwrap();
    set_location(&db, &actor, store.id, dec("21"), dec("-101"), 95)
        .await
        .unwrap();
    clear_location(&db, Some(&actor), store.id).await.unwrap();

    let rows = owner
        .query(
            "SELECT action, user_id FROM audit_log \
             WHERE entity = 'store_location' AND entity_pk = $1 ORDER BY audit_id",
            &[&pk],
        )
        .await
        .unwrap();
    let seen: Vec<(String, Option<uuid::Uuid>)> =
        rows.iter().map(|row| (row.get(0), row.get(1))).collect();
    assert_eq!(
        seen,
        vec![
            ("INSERT".to_owned(), Some(caller)),
            ("UPDATE".to_owned(), Some(caller)),
            ("DELETE".to_owned(), Some(caller)),
        ]
    );
    store.cleanup(&owner).await;
}

#[tokio::test]
async fn the_role_cannot_write_the_store_directory() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let service = connect(&support::service_url(&url)).await;
    let store = TestStore::create(&owner).await;
    let denied = service
        .execute(
            "UPDATE public.store SET name = 'x' WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap_err();
    assert_eq!(denied.code().map(|c| c.code()), Some("42501"));
    store.cleanup(&owner).await;
}

#[tokio::test]
async fn a_session_whose_sub_is_not_a_user_is_401_not_500() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let (known, _) = users(&owner).await;
    let store = TestStore::create(&owner).await;
    let stranger = uuid::Uuid::new_v4();

    let error = set_location(
        &db,
        &principal(stranger),
        store.id,
        dec("25.6866"),
        dec("-100.3161"),
        150,
    )
    .await
    .expect_err("unknown actor");
    assert_eq!((error.status, error.code.as_str()), (401, "invalid_token"));
    assert_eq!(rows_for(&owner, store.id).await, 0);

    set_location(
        &db,
        &principal(known),
        store.id,
        dec("25.6866"),
        dec("-100.3161"),
        150,
    )
    .await
    .unwrap();
    let error = clear_location(&db, Some(&principal(stranger)), store.id)
        .await
        .expect_err("unknown actor");
    assert_eq!((error.status, error.code.as_str()), (401, "invalid_token"));
    assert_eq!(rows_for(&owner, store.id).await, 1);
    store.cleanup(&owner).await;
}
