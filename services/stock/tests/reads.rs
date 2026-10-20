use stock::{
    read_stock_level, read_stock_levels, stock_level_or_not_found, StockFilters, StockLevel,
};
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

async fn connect_as_stock(url: &str) -> Client {
    let client = connect(url).await;
    client.batch_execute("SET ROLE svc_stock").await.unwrap();
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

#[tokio::test]
async fn next_read_observes_the_web_csv_decrement() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else {
        return;
    };
    let owner = connect(&url).await;
    let stock = connect_as_stock(&url).await;
    let (store_id, product_id, original) = seeded_pair(&owner).await;

    owner
        .batch_execute("BEGIN; SET LOCAL ROLE retail_app")
        .await
        .unwrap();
    let remaining: i32 = owner
        .query_one(
            "UPDATE inventory \
             SET quantity_on_hand = quantity_on_hand - $1, updated_at = now() \
             WHERE store_id = $2 AND product_id = $3 AND quantity_on_hand >= $1 \
             RETURNING quantity_on_hand",
            &[&1i32, &store_id, &product_id],
        )
        .await
        .unwrap()
        .get(0);
    owner.batch_execute("COMMIT").await.unwrap();

    let observed = read_stock_level(&stock, i32::from(store_id), product_id).await;
    owner
        .execute(
            "UPDATE public.inventory SET quantity_on_hand = $1, updated_at = now() \
             WHERE store_id = $2 AND product_id = $3",
            &[&original, &store_id, &product_id],
        )
        .await
        .unwrap();

    let observed = observed.unwrap().unwrap();
    assert_eq!(remaining, original - 1);
    assert_eq!(observed.quantity_on_hand, remaining);
}

#[tokio::test]
async fn missing_pair_maps_to_stock_level_not_found() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else {
        return;
    };
    let stock = connect_as_stock(&url).await;
    let level = read_stock_level(&stock, 32767, i32::MAX).await.unwrap();
    let error = stock_level_or_not_found(level).unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (404, "stock_level_not_found")
    );
}

#[tokio::test]
async fn filtered_list_is_ordered_and_has_the_right_page_info() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else {
        return;
    };
    let owner = connect(&url).await;
    let stock = connect_as_stock(&url).await;
    let original = owner
        .query(
            "SELECT store_id, product_id, quantity_on_hand FROM public.inventory \
             WHERE (store_id = $1 AND product_id IN ($2, $3)) \
                OR (store_id = $4 AND product_id = $5) \
             ORDER BY store_id, product_id",
            &[&1i16, &1i32, &2i32, &2i16, &1i32],
        )
        .await
        .unwrap()
        .into_iter()
        .map(|row| {
            (
                row.get::<_, i16>(0),
                row.get::<_, i32>(1),
                row.get::<_, i32>(2),
            )
        })
        .collect::<Vec<_>>();
    owner
        .execute(
            "UPDATE public.inventory SET quantity_on_hand = CASE product_id \
                 WHEN $1::integer THEN $2::integer ELSE $3::integer END, updated_at = now() \
             WHERE store_id = $4 AND product_id IN ($1::integer, $5::integer)",
            &[&1i32, &0i32, &4i32, &1i16, &2i32],
        )
        .await
        .unwrap();
    owner
        .execute(
            "UPDATE public.inventory SET quantity_on_hand = $1, updated_at = now() \
             WHERE store_id = $2 AND product_id = $3",
            &[&0i32, &2i16, &1i32],
        )
        .await
        .unwrap();
    let expected = owner
        .query(
            "SELECT store_id, product_id, quantity_on_hand FROM public.inventory \
             WHERE store_id = $1 AND quantity_on_hand < $2 \
             ORDER BY store_id, product_id",
            &[&1i16, &5i32],
        )
        .await
        .unwrap()
        .into_iter()
        .map(|row| {
            (
                row.get::<_, i16>(0),
                row.get::<_, i32>(1),
                row.get::<_, i32>(2),
            )
        })
        .collect::<Vec<_>>();

    let result = read_stock_levels(
        &stock,
        &StockFilters {
            store_id: Some(1),
            product_id: None,
            below: Some(5),
        },
        1,
        100,
    )
    .await;
    for (store_id, product_id, quantity) in original {
        owner
            .execute(
                "UPDATE public.inventory SET quantity_on_hand = $1, updated_at = now() \
                 WHERE store_id = $2 AND product_id = $3",
                &[&quantity, &store_id, &product_id],
            )
            .await
            .unwrap();
    }

    let result = result.unwrap();
    let actual = result
        .levels
        .iter()
        .map(
            |StockLevel {
                 store_id,
                 product_id,
                 quantity_on_hand,
                 ..
             }| (*store_id, *product_id, *quantity_on_hand),
        )
        .collect::<Vec<_>>();
    assert!(!actual.is_empty());
    assert_eq!(actual, expected);
    assert!(actual
        .iter()
        .all(|(store, _, quantity)| *store == 1 && *quantity < 5));
    assert!(actual.windows(2).all(|pair| pair[0] < pair[1]));
    assert_eq!(
        (result.page, result.size, result.total),
        (1, 100, actual.len() as u64)
    );
}

#[tokio::test]
async fn only_sales_may_consume_and_the_service_writes_nothing() {
    let _guard = test_lock().await;
    let Some(url) = database_url() else {
        return;
    };
    let owner = connect(&url).await;
    let row = owner
        .query_one(
            "SELECT has_function_privilege($1::text, $3::text::regprocedure, 'EXECUTE'), \
                    has_function_privilege($2::text, $3::text::regprocedure, 'EXECUTE'), \
                    has_function_privilege($4::text, $3::text::regprocedure, 'EXECUTE'), \
                    has_table_privilege($1::text, 'public.inventory', 'INSERT, UPDATE, DELETE')",
            &[
                &"svc_stock",
                &"retail_app",
                &"api_stock.consume_v1(smallint,integer,integer)",
                &"svc_sales",
            ],
        )
        .await
        .unwrap();
    assert_eq!(
        (
            row.get::<_, bool>(0),
            row.get::<_, bool>(1),
            row.get::<_, bool>(2),
            row.get::<_, bool>(3)
        ),
        (false, false, true, false)
    );
}
