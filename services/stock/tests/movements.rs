//! Stock movements against a real PostgreSQL, as `svc_stock` (F21-06): the
//! idempotent replay, the inventory and movement rows changing together, stock
//! never negative, an all-or-nothing transfer, and concurrent movements on one
//! row adding up. Every concurrent test uses real connections.
//!
//! Needs TEST_DATABASE_URL (the seeded database, as its owner). Without it the
//! tests skip, or fail under MOSAIQ_REQUIRE_POSTGRES.

use std::time::Duration;

use stock::movements::{parse_movement, record_movement, MovementRequest, MovementType};
use tokio::sync::{Mutex, MutexGuard};
use tokio_postgres::{Client, NoTls};
use uuid::Uuid;

const ADMIN: &str = "11111111-1111-1111-1111-000000000001";

async fn test_lock() -> MutexGuard<'static, ()> {
    static LOCK: Mutex<()> = Mutex::const_new(());
    LOCK.lock().await
}

fn database_url() -> Option<String> {
    match std::env::var("TEST_DATABASE_URL") {
        Ok(url) if !url.is_empty() => Some(url),
        _ => {
            if std::env::var("MOSAIQ_REQUIRE_POSTGRES").as_deref() == Ok("1") {
                panic!(
                    "TEST_DATABASE_URL is missing, and MOSAIQ_REQUIRE_POSTGRES forbids skipping"
                );
            }
            eprintln!("skipped: TEST_DATABASE_URL is not set");
            None
        }
    }
}

fn service_url(owner: &str) -> String {
    let (scheme, rest) = owner.split_once("://").expect("a database URL");
    let after_user = rest.split_once('@').map_or(rest, |(_, host)| host);
    format!("{scheme}://svc_stock:svc_stock@{after_user}")
}

async fn connect(url: &str) -> Client {
    let (client, connection) = tokio_postgres::connect(url, NoTls).await.unwrap();
    tokio::spawn(connection);
    client
}

fn admin() -> Uuid {
    Uuid::parse_str(ADMIN).unwrap()
}

/// A throwaway pair nothing else touches: product 6 and up have no stock
/// record in the seed, so a receipt creates the record and `cleanup` removes
/// it, together with the movements it made.
struct Pair {
    store_id: i16,
    product_id: i32,
}

impl Pair {
    async fn on_hand(&self, owner: &Client) -> Option<i32> {
        owner
            .query_opt(
                "SELECT quantity_on_hand FROM public.inventory \
                 WHERE store_id = $1 AND product_id = $2",
                &[&self.store_id, &self.product_id],
            )
            .await
            .unwrap()
            .map(|row| row.get(0))
    }

    async fn set(&self, owner: &Client, quantity: i32) {
        owner
            .execute(
                "INSERT INTO public.inventory (store_id, product_id, quantity_on_hand) \
                 VALUES ($1, $2, $3) \
                 ON CONFLICT (store_id, product_id) \
                 DO UPDATE SET quantity_on_hand = EXCLUDED.quantity_on_hand",
                &[&self.store_id, &self.product_id, &quantity],
            )
            .await
            .unwrap();
    }
}

async fn cleanup(owner: &Client, product_id: i32) {
    let keys: Vec<Uuid> = owner
        .query(
            "SELECT DISTINCT idempotency_key FROM stock.stock_movement \
             WHERE product_id = $1 AND idempotency_key IS NOT NULL",
            &[&product_id],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get(0))
        .collect();
    owner
        .execute(
            "DELETE FROM stock.stock_movement WHERE product_id = $1",
            &[&product_id],
        )
        .await
        .unwrap();
    owner
        .execute(
            "DELETE FROM stock.movement_request WHERE idempotency_key = ANY($1)",
            &[&keys],
        )
        .await
        .unwrap();
    owner
        .execute(
            "DELETE FROM public.inventory WHERE product_id = $1",
            &[&product_id],
        )
        .await
        .unwrap();
}

fn receipt(store_id: i16, product_id: i32, quantity: i32) -> MovementRequest {
    MovementRequest {
        movement_type: MovementType::Receipt,
        store_id,
        destination_store_id: None,
        product_id,
        quantity,
        reference: Some("DN-TEST".to_owned()),
        reason: None,
        counted_by: None,
        note: None,
    }
}

fn adjustment(store_id: i16, product_id: i32, delta: i32) -> MovementRequest {
    MovementRequest {
        movement_type: MovementType::Adjustment,
        reason: Some("DAMAGE".to_owned()),
        reference: None,
        quantity: delta,
        ..receipt(store_id, product_id, delta)
    }
}

fn transfer(origin: i16, destination: i16, product_id: i32, quantity: i32) -> MovementRequest {
    MovementRequest {
        movement_type: MovementType::Transfer,
        destination_store_id: Some(destination),
        reference: None,
        ..receipt(origin, product_id, quantity)
    }
}

fn count(store_id: i16, product_id: i32, counted: i32) -> MovementRequest {
    MovementRequest {
        movement_type: MovementType::Count,
        counted_by: Some("Test counter".to_owned()),
        reference: None,
        ..receipt(store_id, product_id, counted)
    }
}

async fn apply(
    client: &Client,
    key: Uuid,
    request: &MovementRequest,
) -> Result<(Vec<stock::movements::Movement>, bool), mosaiq_kit::ApiError> {
    record_movement(client, key, admin(), request).await
}

#[tokio::test]
async fn a_replay_changes_nothing_and_a_changed_body_is_refused() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else { return };
    let owner = connect(&url).await;
    let stock = connect(&service_url(&url)).await;
    let pair = Pair {
        store_id: 21,
        product_id: 6,
    };
    cleanup(&owner, pair.product_id).await;
    pair.set(&owner, 10).await;

    let key = Uuid::new_v4();
    let request = receipt(pair.store_id, pair.product_id, 7);
    let (first, replayed) = apply(&stock, key, &request).await.unwrap();
    assert!(!replayed);
    assert_eq!(first.len(), 1);
    assert_eq!(
        (
            first[0].quantity_before,
            first[0].quantity_after,
            first[0].quantity_delta
        ),
        (10, 17, 7)
    );
    assert_eq!(first[0].reference.as_deref(), Some("DN-TEST"));
    assert_eq!(first[0].recorded_by, Some(admin()));
    assert_eq!(pair.on_hand(&owner).await, Some(17));

    // A retry returns the first result and changes nothing.
    let (again, replayed) = apply(&stock, key, &request).await.unwrap();
    assert!(replayed);
    assert_eq!(again, first);
    assert_eq!(pair.on_hand(&owner).await, Some(17));
    let rows: i64 = owner
        .query_one(
            "SELECT count(*) FROM stock.stock_movement WHERE idempotency_key = $1",
            &[&key],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(rows, 1);

    // The same key with another body is a conflict, and still changes nothing.
    let other = receipt(pair.store_id, pair.product_id, 8);
    let error = apply(&stock, key, &other).await.unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (409, "idempotency_key_reused")
    );
    assert_eq!(pair.on_hand(&owner).await, Some(17));

    // Another caller with the same fields hashes differently: also a conflict.
    let error = record_movement(&stock, key, Uuid::new_v4(), &request)
        .await
        .unwrap_err();
    assert_eq!(error.code, "idempotency_key_reused");
    cleanup(&owner, pair.product_id).await;
}

#[tokio::test]
async fn the_movement_and_the_inventory_commit_in_one_transaction() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else { return };
    let owner = connect(&url).await;
    let mut stock = connect(&service_url(&url)).await;
    let pair = Pair {
        store_id: 22,
        product_id: 6,
    };
    cleanup(&owner, pair.product_id).await;
    pair.set(&owner, 10).await;

    // Rolled back by the caller: neither the quantity nor a row survives.
    let transaction = stock.transaction().await.unwrap();
    let key = Uuid::new_v4();
    apply_in(
        &transaction,
        key,
        &receipt(pair.store_id, pair.product_id, 5),
    )
    .await;
    transaction.rollback().await.unwrap();
    assert_eq!(pair.on_hand(&owner).await, Some(10));
    let remembered: i64 = owner
        .query_one(
            "SELECT count(*) FROM stock.movement_request WHERE idempotency_key = $1",
            &[&key],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(
        remembered, 0,
        "a rolled-back request must not be remembered"
    );

    // Committed: both are there.
    let transaction = stock.transaction().await.unwrap();
    apply_in(
        &transaction,
        key,
        &receipt(pair.store_id, pair.product_id, 5),
    )
    .await;
    transaction.commit().await.unwrap();
    assert_eq!(pair.on_hand(&owner).await, Some(15));
    cleanup(&owner, pair.product_id).await;
}

async fn apply_in(client: &tokio_postgres::Transaction<'_>, key: Uuid, request: &MovementRequest) {
    record_movement(client, key, admin(), request)
        .await
        .unwrap();
}

#[tokio::test]
async fn stock_is_never_negative() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else { return };
    let owner = connect(&url).await;
    let stock = connect(&service_url(&url)).await;
    let pair = Pair {
        store_id: 23,
        product_id: 6,
    };
    cleanup(&owner, pair.product_id).await;

    // No record: an adjustment cannot create one.
    let error = apply(
        &stock,
        Uuid::new_v4(),
        &adjustment(pair.store_id, pair.product_id, -1),
    )
    .await
    .unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (422, "no_stock_record")
    );
    assert_eq!(pair.on_hand(&owner).await, None);

    pair.set(&owner, 3).await;
    let key = Uuid::new_v4();
    let too_much = adjustment(pair.store_id, pair.product_id, -4);
    let error = apply(&stock, key, &too_much).await.unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (409, "stock_unavailable")
    );
    assert_eq!(pair.on_hand(&owner).await, Some(3));

    // Only accepted movements are remembered: the same key retries with a
    // movement that fits.
    let fits = adjustment(pair.store_id, pair.product_id, -3);
    let (done, replayed) = apply(&stock, key, &fits).await.unwrap();
    assert!(!replayed);
    assert_eq!((done[0].quantity_before, done[0].quantity_after), (3, 0));
    assert_eq!(pair.on_hand(&owner).await, Some(0));

    // The schema says it too: a direct write below zero is refused.
    let direct = owner
        .execute(
            "UPDATE public.inventory SET quantity_on_hand = $3 \
             WHERE store_id = $1 AND product_id = $2",
            &[&pair.store_id, &pair.product_id, &-1i32],
        )
        .await;
    assert!(direct.is_err());
    cleanup(&owner, pair.product_id).await;
}

#[tokio::test]
async fn a_count_sets_the_quantity_and_keeps_before_and_after() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else { return };
    let owner = connect(&url).await;
    let stock = connect(&service_url(&url)).await;
    let pair = Pair {
        store_id: 24,
        product_id: 6,
    };
    cleanup(&owner, pair.product_id).await;

    // A count creates an absent record from zero, then corrects it down.
    let (first, _) = apply(
        &stock,
        Uuid::new_v4(),
        &count(pair.store_id, pair.product_id, 9),
    )
    .await
    .unwrap();
    assert_eq!(
        (
            first[0].quantity_before,
            first[0].quantity_after,
            first[0].quantity_delta
        ),
        (0, 9, 9)
    );
    let (second, _) = apply(
        &stock,
        Uuid::new_v4(),
        &count(pair.store_id, pair.product_id, 4),
    )
    .await
    .unwrap();
    assert_eq!(
        (
            second[0].quantity_before,
            second[0].quantity_after,
            second[0].quantity_delta
        ),
        (9, 4, -5)
    );
    assert_eq!(second[0].counted_by.as_deref(), Some("Test counter"));
    assert_eq!(pair.on_hand(&owner).await, Some(4));
    cleanup(&owner, pair.product_id).await;
}

#[tokio::test]
async fn a_transfer_is_all_or_nothing() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else { return };
    let owner = connect(&url).await;
    let stock = connect(&service_url(&url)).await;
    let origin = Pair {
        store_id: 25,
        product_id: 6,
    };
    let destination = Pair {
        store_id: 26,
        product_id: 6,
    };
    cleanup(&owner, 6).await;
    origin.set(&owner, 5).await;

    // The origin cannot cover it: neither side changes, and no destination
    // record is created.
    let error = apply(&stock, Uuid::new_v4(), &transfer(25, 26, 6, 6))
        .await
        .unwrap_err();
    assert_eq!(error.code, "stock_unavailable");
    assert_eq!(origin.on_hand(&owner).await, Some(5));
    assert_eq!(destination.on_hand(&owner).await, None);

    // The destination cannot take it (its quantity would overflow), and the
    // origin, which is processed first, is given back.
    destination.set(&owner, i32::MAX).await;
    let error = apply(&stock, Uuid::new_v4(), &transfer(25, 26, 6, 1))
        .await
        .unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (422, "invalid_quantity")
    );
    assert_eq!(origin.on_hand(&owner).await, Some(5));
    assert_eq!(destination.on_hand(&owner).await, Some(i32::MAX));
    let rows: i64 = owner
        .query_one(
            "SELECT count(*) FROM stock.stock_movement WHERE product_id = $1",
            &[&6i32],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(rows, 0);

    // The same, the other way round: the destination store is the lower one,
    // so it is the origin whose subtraction fails after the destination added.
    destination.set(&owner, 1).await;
    let error = apply(&stock, Uuid::new_v4(), &transfer(26, 25, 6, 2))
        .await
        .unwrap_err();
    assert_eq!(error.code, "stock_unavailable");
    assert_eq!(origin.on_hand(&owner).await, Some(5));
    assert_eq!(destination.on_hand(&owner).await, Some(1));

    // A good transfer: two rows sharing one transfer_id, origin first.
    let key = Uuid::new_v4();
    let (rows, replayed) = apply(&stock, key, &transfer(25, 26, 6, 2)).await.unwrap();
    assert!(!replayed);
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0].store_id, 25);
    assert_eq!(rows[1].store_id, 26);
    assert_eq!((rows[0].quantity_delta, rows[1].quantity_delta), (-2, 2));
    assert_eq!((rows[0].quantity_before, rows[0].quantity_after), (5, 3));
    assert_eq!((rows[1].quantity_before, rows[1].quantity_after), (1, 3));
    assert!(rows[0].transfer_id.is_some());
    assert_eq!(rows[0].transfer_id, rows[1].transfer_id);
    assert!(rows[0].movement_id < rows[1].movement_id);
    assert_eq!(origin.on_hand(&owner).await, Some(3));
    assert_eq!(destination.on_hand(&owner).await, Some(3));

    // And its replay returns the same two rows and moves nothing.
    let (again, replayed) = apply(&stock, key, &transfer(25, 26, 6, 2)).await.unwrap();
    assert!(replayed);
    assert_eq!(again, rows);
    assert_eq!(origin.on_hand(&owner).await, Some(3));
    cleanup(&owner, 6).await;
}

/// Run `count` movements at once, each on its own connection, released
/// together so they overlap on the row.
async fn run_together(
    url: &str,
    requests: Vec<MovementRequest>,
) -> Vec<Result<(Vec<stock::movements::Movement>, bool), mosaiq_kit::ApiError>> {
    let barrier = std::sync::Arc::new(tokio::sync::Barrier::new(requests.len()));
    let mut tasks = Vec::new();
    for request in requests {
        let barrier = barrier.clone();
        let url = service_url(url);
        tasks.push(tokio::spawn(async move {
            let client = connect(&url).await;
            barrier.wait().await;
            apply(&client, Uuid::new_v4(), &request).await
        }));
    }
    let mut results = Vec::new();
    for task in tasks {
        results.push(
            tokio::time::timeout(Duration::from_secs(30), task)
                .await
                .unwrap()
                .unwrap(),
        );
    }
    results
}

#[tokio::test]
async fn concurrent_movements_on_one_row_sum() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else { return };
    let owner = connect(&url).await;
    let pair = Pair {
        store_id: 27,
        product_id: 6,
    };
    cleanup(&owner, pair.product_id).await;
    pair.set(&owner, 100).await;

    // Twenty receipts of 3 and twenty adjustments of -2, all on one row.
    let mut requests = Vec::new();
    for _ in 0..20 {
        requests.push(receipt(pair.store_id, pair.product_id, 3));
        requests.push(adjustment(pair.store_id, pair.product_id, -2));
    }
    let results = run_together(&url, requests).await;
    assert!(results.iter().all(Result::is_ok), "{results:?}");
    assert_eq!(pair.on_hand(&owner).await, Some(100 + 20 * 3 - 20 * 2));

    // The rows tell one consistent story: each one's before is the previous
    // one's after, in the order they took the row.
    let rows = owner
        .query(
            "SELECT quantity_before, quantity_after, quantity_delta FROM stock.stock_movement \
             WHERE product_id = $1 AND store_id = $2 ORDER BY movement_id",
            &[&pair.product_id, &pair.store_id],
        )
        .await
        .unwrap();
    assert_eq!(rows.len(), 40);
    // The row is held until commit, so ids follow the order the movements
    // took it, and each one starts where the one before it ended.
    let mut previous = 100;
    for row in &rows {
        let (before, after, delta): (i32, i32, i32) = (row.get(0), row.get(1), row.get(2));
        assert_eq!(before, previous);
        assert_eq!(after, before + delta);
        previous = after;
    }
    assert_eq!(previous, 100 + 20);
    cleanup(&owner, pair.product_id).await;
}

#[tokio::test]
async fn concurrent_subtractions_never_overdraw() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else { return };
    let owner = connect(&url).await;
    let pair = Pair {
        store_id: 28,
        product_id: 6,
    };
    cleanup(&owner, pair.product_id).await;
    pair.set(&owner, 10).await;

    let requests = (0..25)
        .map(|_| adjustment(pair.store_id, pair.product_id, -1))
        .collect();
    let results = run_together(&url, requests).await;
    let accepted = results.iter().filter(|result| result.is_ok()).count();
    let refused = results
        .iter()
        .filter(|result| matches!(result, Err(error) if error.code == "stock_unavailable"))
        .count();
    assert_eq!((accepted, refused), (10, 15), "{results:?}");
    assert_eq!(pair.on_hand(&owner).await, Some(0));
    cleanup(&owner, pair.product_id).await;
}

#[tokio::test]
async fn opposite_transfers_finish_without_a_deadlock_and_conserve_stock() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else { return };
    let owner = connect(&url).await;
    let a = Pair {
        store_id: 29,
        product_id: 6,
    };
    let b = Pair {
        store_id: 30,
        product_id: 6,
    };
    cleanup(&owner, 6).await;
    a.set(&owner, 50).await;
    b.set(&owner, 50).await;

    let mut requests = Vec::new();
    for _ in 0..15 {
        requests.push(transfer(29, 30, 6, 2));
        requests.push(transfer(30, 29, 6, 1));
    }
    let results = run_together(&url, requests).await;
    assert!(results.iter().all(Result::is_ok), "{results:?}");
    // 15 * 2 moved from 29 to 30, and 15 * 1 the other way.
    assert_eq!(a.on_hand(&owner).await, Some(50 - 30 + 15));
    assert_eq!(b.on_hand(&owner).await, Some(50 + 30 - 15));
    cleanup(&owner, 6).await;
}

#[tokio::test]
async fn the_same_key_sent_at_once_is_applied_once() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else { return };
    let owner = connect(&url).await;
    let pair = Pair {
        store_id: 21,
        product_id: 7,
    };
    cleanup(&owner, pair.product_id).await;
    pair.set(&owner, 10).await;

    let key = Uuid::new_v4();
    let request = receipt(pair.store_id, pair.product_id, 4);
    let barrier = std::sync::Arc::new(tokio::sync::Barrier::new(10));
    let mut tasks = Vec::new();
    for _ in 0..10 {
        let barrier = barrier.clone();
        let url = service_url(&url);
        let request = request.clone();
        tasks.push(tokio::spawn(async move {
            let client = connect(&url).await;
            barrier.wait().await;
            apply(&client, key, &request).await.unwrap()
        }));
    }
    let mut created = 0;
    for task in tasks {
        let (rows, replayed) = task.await.unwrap();
        assert_eq!(rows.len(), 1);
        if !replayed {
            created += 1;
        }
    }
    assert_eq!(created, 1);
    assert_eq!(pair.on_hand(&owner).await, Some(14));
    cleanup(&owner, pair.product_id).await;
}

#[tokio::test]
async fn a_movement_is_audited_with_its_reason_and_cannot_be_changed() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else { return };
    let owner = connect(&url).await;
    let stock = connect(&service_url(&url)).await;
    let pair = Pair {
        store_id: 22,
        product_id: 7,
    };
    cleanup(&owner, pair.product_id).await;
    pair.set(&owner, 10).await;
    owner
        .execute(
            "DELETE FROM stock_events.outbox WHERE entity_id = $1",
            &[&"22:7"],
        )
        .await
        .unwrap();

    let body = serde_json::json!({
        "movement_type": "ADJUSTMENT", "product_id": 7, "store_id": 22,
        "quantity_delta": -3, "reason": "EXPIRY", "note": "  Past its date.  "
    });
    let request = parse_movement(&body).unwrap();
    let (rows, _) = apply(&stock, Uuid::new_v4(), &request).await.unwrap();
    assert_eq!(rows[0].reason.as_deref(), Some("EXPIRY"));
    assert_eq!(rows[0].note.as_deref(), Some("Past its date."));
    assert_eq!(rows[0].recorded_by, Some(admin()));

    // The row is in the table with its reason and author, and the quantity
    // change was captured for the stream in the same transaction.
    let stored = owner
        .query_one(
            "SELECT reason, recorded_by, quantity_delta FROM stock.stock_movement \
             WHERE movement_id = $1",
            &[&rows[0].movement_id],
        )
        .await
        .unwrap();
    assert_eq!(
        stored.get::<_, Option<String>>(0).as_deref(),
        Some("EXPIRY")
    );
    assert_eq!(stored.get::<_, Option<Uuid>>(1), Some(admin()));
    let events: i64 = owner
        .query_one(
            "SELECT count(*) FROM stock_events.outbox \
             WHERE entity_id = $1 AND stream = $2 AND relayed_at IS NULL",
            &[&"22:7", &stock::STREAM],
        )
        .await
        .unwrap()
        .get(0);
    assert!(events >= 1);

    // History is append-only for the service.
    let error = stock
        .execute("UPDATE stock.stock_movement SET note = $1", &[&"rewritten"])
        .await
        .unwrap_err();
    assert_eq!(error.code().unwrap().code(), "42501");
    let error = stock
        .execute("DELETE FROM stock.stock_movement", &[])
        .await
        .unwrap_err();
    assert_eq!(error.code().unwrap().code(), "42501");
    let error = stock
        .execute(
            "INSERT INTO stock.stock_movement (movement_type, store_id, product_id, \
             quantity_delta, quantity_before, quantity_after, reference, recorded_by) \
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            &[
                &"RECEIPT",
                &22i16,
                &7i32,
                &1i32,
                &0i32,
                &1i32,
                &"x",
                &admin(),
            ],
        )
        .await
        .unwrap_err();
    assert_eq!(error.code().unwrap().code(), "42501");
    let error = stock
        .execute(
            "UPDATE public.inventory SET quantity_on_hand = $1",
            &[&999i32],
        )
        .await
        .unwrap_err();
    assert_eq!(error.code().unwrap().code(), "42501");
    cleanup(&owner, pair.product_id).await;
}

#[tokio::test]
async fn the_schema_refuses_a_movement_that_states_nothing() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else { return };
    let owner = connect(&url).await;
    // The owner bypasses the function: the table's own CHECKs still hold.
    for (kind, reference, reason, counted_by, delta) in [
        ("RECEIPT", None, None, None, 1),
        ("ADJUSTMENT", None, Some("OTHER"), None, -1),
        ("ADJUSTMENT", None, None, None, -1),
        ("COUNT", None, None, Some("  "), 0),
        ("RECEIPT", Some("DN"), None, None, 0),
    ] {
        let result = owner
            .execute(
                "INSERT INTO stock.stock_movement (movement_type, store_id, product_id, \
                 quantity_delta, quantity_before, quantity_after, reference, reason, counted_by, \
                 recorded_by) \
                 VALUES ($1, $2, $3, $4, $5, $5 + $4, $6, $7, $8, $9)",
                &[
                    &kind,
                    &1i16,
                    &1i32,
                    &delta,
                    &5i32,
                    &reference,
                    &reason,
                    &counted_by,
                    &admin(),
                ],
            )
            .await;
        assert!(
            result.is_err(),
            "{kind} {reference:?} {reason:?} {counted_by:?} {delta}"
        );
    }
}

#[tokio::test]
async fn movement_history_restricts_master_deletion() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else { return };
    let owner = connect(&url).await;
    let rows = owner
        .query(
            "SELECT conname, confdeltype::text \
             FROM pg_constraint \
             WHERE conrelid = $1::text::regclass \
               AND conname = ANY($2::text[]) \
             ORDER BY conname",
            &[
                &"stock.stock_movement",
                &vec![
                    "stock_movement_product_id_fkey",
                    "stock_movement_store_id_fkey",
                ],
            ],
        )
        .await
        .unwrap();
    assert_eq!(rows.len(), 2);
    for row in rows {
        assert_eq!(row.get::<_, String>(1), "r", "{}", row.get::<_, String>(0));
    }
}

#[test]
fn a_request_is_validated_in_the_contracts_words() {
    use serde_json::json;
    let refused = |body: serde_json::Value| {
        let error = parse_movement(&body).unwrap_err();
        (
            error.status,
            error.code,
            error.details.first().map(|detail| detail.field.clone()),
        )
    };
    assert_eq!(
        refused(
            json!({"movement_type": "RECEIPT", "product_id": 1, "store_id": 1, "quantity": 0, "reference": "x"})
        ),
        (
            422,
            "invalid_quantity".to_owned(),
            Some("quantity".to_owned())
        )
    );
    assert_eq!(
        refused(json!({"movement_type": "RECEIPT", "product_id": 1, "store_id": 1, "quantity": 1})),
        (
            422,
            "reference_required".to_owned(),
            Some("reference".to_owned())
        )
    );
    assert_eq!(
        refused(json!({"movement_type": "RECEIPT", "product_id": 1, "store_id": 1, "quantity": 1, "reference": "  "})).1,
        "reference_required"
    );
    assert_eq!(
        refused(json!({"movement_type": "COUNT", "product_id": 1, "store_id": 1, "quantity": 3})).1,
        "counted_by_required"
    );
    assert_eq!(
        refused(json!({"movement_type": "COUNT", "product_id": 1, "store_id": 1, "quantity": -1, "counted_by": "A"})).1,
        "invalid_quantity"
    );
    assert_eq!(
        refused(json!({"movement_type": "ADJUSTMENT", "product_id": 1, "store_id": 1, "quantity_delta": -1, "reason": "THEFT"})).1,
        "invalid_reason"
    );
    assert_eq!(
        refused(json!({"movement_type": "ADJUSTMENT", "product_id": 1, "store_id": 1, "quantity_delta": 0, "reason": "LOSS"})).1,
        "invalid_quantity"
    );
    assert_eq!(
        refused(json!({"movement_type": "TRANSFER", "product_id": 1, "origin_store_id": 2, "destination_store_id": 2, "quantity": 1})).1,
        "same_store_transfer"
    );
    assert_eq!(
        refused(json!({"movement_type": "RESTOCK", "product_id": 1, "store_id": 1, "quantity": 1}))
            .1,
        "invalid_body"
    );
    assert_eq!(
        refused(
            json!({"movement_type": "RECEIPT", "product_id": 1, "quantity": 1, "reference": "x"})
        )
        .1,
        "invalid_body"
    );
    assert_eq!(
        refused(json!({"movement_type": "RECEIPT", "product_id": 1, "store_id": 70000, "quantity": 1, "reference": "x"})).1,
        "unknown_store"
    );
    assert_eq!(
        refused(json!({"movement_type": "RECEIPT", "product_id": 0, "store_id": 1, "quantity": 1, "reference": "x"})).1,
        "unknown_product"
    );
}

#[test]
fn a_json_and_an_xml_retry_hash_the_same() {
    use serde_json::json;
    // The kit hands both formats to the service as the same JSON value, so
    // one canonical form covers both: member order and spacing do not matter.
    let one = parse_movement(&json!({
        "movement_type": "RECEIPT", "product_id": 3, "store_id": 4, "quantity": 5,
        "reference": "DN-1", "note": "a"
    }))
    .unwrap();
    let other = parse_movement(&json!({
        "note": "a", "reference": " DN-1 ", "quantity": 5, "store_id": 4,
        "product_id": 3, "movement_type": "RECEIPT"
    }))
    .unwrap();
    let sub = admin();
    assert_eq!(one.canonical(&sub), other.canonical(&sub));
    let different = MovementRequest {
        quantity: 6,
        ..one.clone()
    };
    assert_ne!(one.canonical(&sub), different.canonical(&sub));
    let stranger = Uuid::new_v4();
    assert_ne!(one.canonical(&sub), one.canonical(&stranger));
}
