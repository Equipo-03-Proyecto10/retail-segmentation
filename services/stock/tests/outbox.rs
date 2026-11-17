//! A committed movement is announced on `stream:stock.v1.changed` from the
//! owner's outbox (ADR-0044), and a rolled-back one is not. PostgreSQL and
//! Redis are real test servers.

use std::time::Duration;

use mosaiq_kit::OutboxRelay;
use stock::movements::{record_movement, MovementRequest, MovementType};
use stock::{STREAM, SUFFIX};
use tokio_postgres::{Client, NoTls};
use uuid::Uuid;

const ADMIN: &str = "11111111-1111-1111-1111-000000000001";

fn required_url(name: &str, requirement: &str) -> Option<String> {
    match std::env::var(name) {
        Ok(url) if !url.is_empty() => Some(url),
        _ => {
            if std::env::var(requirement).as_deref() == Ok("1") {
                panic!("{name} is missing, and {requirement} forbids skipping");
            }
            eprintln!("skipped: {name} is not set");
            None
        }
    }
}

fn service_url(owner: &str) -> String {
    let (scheme, rest) = owner.split_once("://").expect("a database URL");
    let after_user = rest.split_once('@').map_or(rest, |(_, host)| host);
    format!("{scheme}://svc_stock:svc_stock@{after_user}")
}

async fn postgres(url: &str) -> Client {
    let (client, connection) = tokio_postgres::connect(url, NoTls).await.unwrap();
    tokio::spawn(connection);
    client
}

async fn unrelayed(owner: &Client, entity: &str) -> Vec<String> {
    owner
        .query(
            "SELECT event_id::text FROM stock_events.outbox \
             WHERE entity_id = $1 AND stream = $2 AND relayed_at IS NULL ORDER BY occurred_at",
            &[&entity, &STREAM],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get(0))
        .collect()
}

#[tokio::test]
async fn a_committed_movement_is_published_and_a_rolled_back_one_is_not() {
    let (Some(database_url), Some(redis_url)) = (
        required_url("TEST_DATABASE_URL", "MOSAIQ_REQUIRE_POSTGRES"),
        required_url("TEST_REDIS_URL", "MOSAIQ_REQUIRE_REDIS"),
    ) else {
        return;
    };
    let owner = postgres(&database_url).await;
    let mut service = postgres(&service_url(&database_url)).await;
    let mut admin = redis::Client::open(redis_url.as_str())
        .unwrap()
        .get_multiplexed_async_connection()
        .await
        .unwrap();
    let relay_connection = redis::Client::open(redis_url.as_str())
        .unwrap()
        .get_multiplexed_async_connection()
        .await
        .unwrap();
    let mut relay = OutboxRelay::new(
        &service_url(&database_url),
        SUFFIX,
        [STREAM],
        relay_connection,
    )
    .unwrap();
    // Drain whatever earlier tests left, so only this test's events remain.
    while relay.run_once(Duration::ZERO).await.unwrap() > 0 {}

    let store_id = 23i16;
    let product_id = 9i32;
    let entity = format!("{store_id}:{product_id}");
    owner
        .execute(
            "DELETE FROM public.inventory WHERE store_id = $1 AND product_id = $2",
            &[&store_id, &product_id],
        )
        .await
        .unwrap();
    let request = MovementRequest {
        movement_type: MovementType::Receipt,
        store_id,
        destination_store_id: None,
        product_id,
        quantity: 4,
        reference: Some("DN-OUTBOX".to_owned()),
        reason: None,
        counted_by: None,
        note: None,
    };
    let sub = Uuid::parse_str(ADMIN).unwrap();

    // Rolled back: nothing is captured, so nothing is published.
    let transaction = service.transaction().await.unwrap();
    record_movement(&transaction, Uuid::new_v4(), sub, &request)
        .await
        .unwrap();
    transaction.rollback().await.unwrap();
    assert!(unrelayed(&owner, &entity).await.is_empty());

    // Committed: the inventory change was captured in the same transaction.
    let key = Uuid::new_v4();
    record_movement(&service, key, sub, &request).await.unwrap();
    let events = unrelayed(&owner, &entity).await;
    assert_eq!(events.len(), 1, "one insert of the new stock record");
    while relay.run_once(Duration::ZERO).await.unwrap() > 0 {}

    let entries: Vec<(String, Vec<(String, String)>)> = redis::cmd("XREVRANGE")
        .arg(STREAM)
        .arg("+")
        .arg("-")
        .arg("COUNT")
        .arg(200)
        .query_async(&mut admin)
        .await
        .unwrap();
    let published = entries.iter().any(|(_, fields)| {
        fields.contains(&("event_id".to_owned(), events[0].clone()))
            && fields.contains(&("entity_id".to_owned(), entity.clone()))
    });
    assert!(published, "the event was not on the stream: {entries:?}");
    assert!(unrelayed(&owner, &entity).await.is_empty());

    owner
        .execute(
            "DELETE FROM stock.stock_movement WHERE idempotency_key = $1",
            &[&key],
        )
        .await
        .unwrap();
    owner
        .execute(
            "DELETE FROM stock.movement_request WHERE idempotency_key = $1",
            &[&key],
        )
        .await
        .unwrap();
    owner
        .execute(
            "DELETE FROM public.inventory WHERE store_id = $1 AND product_id = $2",
            &[&store_id, &product_id],
        )
        .await
        .unwrap();
    let _: i64 = redis::cmd("DEL")
        .arg(STREAM)
        .query_async(&mut admin)
        .await
        .unwrap();
}
