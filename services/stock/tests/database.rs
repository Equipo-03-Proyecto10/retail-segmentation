use tokio::sync::{Mutex, MutexGuard};
use tokio_postgres::{Client, NoTls};

async fn test_lock() -> MutexGuard<'static, ()> {
    static LOCK: Mutex<()> = Mutex::const_new(());
    LOCK.lock().await
}

fn database_url() -> Option<String> {
    std::env::var("TEST_DATABASE_URL").ok()
}

async fn connect(url: &str) -> Client {
    let (client, connection) = tokio_postgres::connect(url, NoTls).await.unwrap();
    tokio::spawn(async move {
        connection.await.unwrap();
    });
    client
}

async fn seeded_pair(client: &Client) -> (i16, i32, i32) {
    let row = client
        .query_one(
            "SELECT store_id, product_id, quantity_on_hand \
             FROM public.inventory ORDER BY store_id, product_id LIMIT $1",
            &[&1i64],
        )
        .await
        .unwrap();
    (row.get(0), row.get(1), row.get(2))
}

async fn refused_quantity(url: &str, store_id: i16, product_id: i32, quantity: Option<i32>) {
    let client = connect(url).await;
    client
        .batch_execute("BEGIN; SET LOCAL ROLE svc_sales")
        .await
        .unwrap();
    let error = client
        .query_one(
            "SELECT api_stock.consume_v1($1::smallint, $2, $3)",
            &[&store_id, &product_id, &quantity],
        )
        .await
        .unwrap_err();
    assert_eq!(error.code().unwrap().code(), "22023");
    client.batch_execute("ROLLBACK").await.unwrap();
}

#[tokio::test]
async fn consume_contract_refuses_invalid_and_unavailable_stock() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else {
        return;
    };
    let owner = connect(&url).await;
    let (store_id, product_id, original) = seeded_pair(&owner).await;

    for quantity in [Some(0), Some(-1), None] {
        refused_quantity(&url, store_id, product_id, quantity).await;
        let current: i32 = owner
            .query_one(
                "SELECT quantity_on_hand FROM public.inventory \
                 WHERE store_id = $1 AND product_id = $2",
                &[&store_id, &product_id],
            )
            .await
            .unwrap()
            .get(0);
        assert_eq!(current, original);
    }

    let missing = connect(&url).await;
    missing
        .batch_execute("BEGIN; SET LOCAL ROLE svc_sales")
        .await
        .unwrap();
    let error = missing
        .query_one(
            "SELECT api_stock.consume_v1($1::smallint, $2, $3)",
            &[&32767i16, &i32::MAX, &1i32],
        )
        .await
        .unwrap_err();
    let db_error = error.as_db_error().unwrap();
    assert_eq!(db_error.code().code(), "23514");
    assert_eq!(db_error.constraint(), Some("no_stock_record"));
    missing.batch_execute("ROLLBACK").await.unwrap();

    let unavailable = connect(&url).await;
    unavailable
        .batch_execute("BEGIN; SET LOCAL ROLE svc_sales")
        .await
        .unwrap();
    let requested = original + 1;
    let error = unavailable
        .query_one(
            "SELECT api_stock.consume_v1($1::smallint, $2, $3)",
            &[&store_id, &product_id, &requested],
        )
        .await
        .unwrap_err();
    let db_error = error.as_db_error().unwrap();
    assert_eq!(db_error.code().code(), "23514");
    assert_eq!(db_error.constraint(), Some("stock_unavailable"));
    unavailable.batch_execute("ROLLBACK").await.unwrap();

    let uncast = connect(&url).await;
    uncast
        .batch_execute("BEGIN; SET LOCAL ROLE svc_sales")
        .await
        .unwrap();
    let error = uncast
        .query_one("SELECT api_stock.consume_v1(1, 1, 1)", &[])
        .await
        .unwrap_err();
    assert_eq!(error.code().unwrap().code(), "42883");
    uncast.batch_execute("ROLLBACK").await.unwrap();
}

#[tokio::test]
async fn valid_consume_updates_stock_and_captures_one_event() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else {
        return;
    };
    let owner = connect(&url).await;
    let (store_id, product_id, original) = seeded_pair(&owner).await;
    let entity_id = format!("{store_id}:{product_id}");
    let before: i64 = owner
        .query_one(
            "SELECT count(*) FROM stock_events.outbox \
             WHERE entity_id = $1 AND event_type = $2",
            &[&entity_id, &"UPDATE"],
        )
        .await
        .unwrap()
        .get(0);

    let sales = connect(&url).await;
    sales
        .batch_execute("BEGIN; SET LOCAL ROLE svc_sales")
        .await
        .unwrap();
    let remaining: i32 = sales
        .query_one(
            "SELECT api_stock.consume_v1($1::smallint, $2, $3)",
            &[&store_id, &product_id, &1i32],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(remaining, original - 1);
    sales.batch_execute("COMMIT").await.unwrap();

    let row = owner
        .query_one(
            "SELECT quantity_on_hand, updated_at <= now() \
             FROM public.inventory WHERE store_id = $1 AND product_id = $2",
            &[&store_id, &product_id],
        )
        .await
        .unwrap();
    assert_eq!(row.get::<_, i32>(0), original - 1);
    assert!(row.get::<_, bool>(1));
    let captured: i64 = owner
        .query_one(
            "SELECT count(*) FROM stock_events.outbox \
             WHERE entity_id = $1 AND event_type = $2",
            &[&entity_id, &"UPDATE"],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(captured, before + 1);

    owner
        .execute(
            "UPDATE public.inventory SET quantity_on_hand = $1, updated_at = now() \
             WHERE store_id = $2 AND product_id = $3",
            &[&original, &store_id, &product_id],
        )
        .await
        .unwrap();
}

async fn concurrent_consume(url: &str, store_id: i16, product_id: i32) -> bool {
    let client = connect(url).await;
    client
        .batch_execute("BEGIN; SET LOCAL ROLE svc_sales")
        .await
        .unwrap();
    let result = client
        .query_one(
            "SELECT api_stock.consume_v1($1::smallint, $2, $3)",
            &[&store_id, &product_id, &4i32],
        )
        .await;
    match result {
        Ok(_) => {
            client.batch_execute("COMMIT").await.unwrap();
            true
        }
        Err(error) => {
            let db_error = error.as_db_error().unwrap();
            assert_eq!(db_error.constraint(), Some("stock_unavailable"));
            client.batch_execute("ROLLBACK").await.unwrap();
            false
        }
    }
}

#[tokio::test]
async fn concurrent_consumers_cannot_make_stock_negative() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else {
        return;
    };
    let owner = connect(&url).await;
    let (store_id, product_id, original) = seeded_pair(&owner).await;
    owner
        .execute(
            "UPDATE public.inventory SET quantity_on_hand = $1, updated_at = now() \
             WHERE store_id = $2 AND product_id = $3",
            &[&5i32, &store_id, &product_id],
        )
        .await
        .unwrap();

    let (first, second) = tokio::join!(
        concurrent_consume(&url, store_id, product_id),
        concurrent_consume(&url, store_id, product_id)
    );
    assert_eq!(u8::from(first) + u8::from(second), 1);
    let remaining: i32 = owner
        .query_one(
            "SELECT quantity_on_hand FROM public.inventory \
             WHERE store_id = $1 AND product_id = $2",
            &[&store_id, &product_id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(remaining, 1);

    owner
        .execute(
            "UPDATE public.inventory SET quantity_on_hand = $1, updated_at = now() \
             WHERE store_id = $2 AND product_id = $3",
            &[&original, &store_id, &product_id],
        )
        .await
        .unwrap();
}
