//! The two reads against the seed's 40 products, as `svc_products_bench`.

mod support;

use mosaiq_kit::Value;
use products_service_rs::{get_product, list_products, parse_filters};
use support::{bench_database, owner_url};

fn ids(items: &[indexmap::IndexMap<String, Value>]) -> Vec<i64> {
    items
        .iter()
        .map(|item| match item["product_id"] {
            Value::Int(id) => id,
            ref other => panic!("product_id is {other:?}"),
        })
        .collect()
}

#[tokio::test]
async fn a_page_is_ordered_by_id_with_the_filtered_total() {
    let Some(url) = owner_url() else { return };
    let db = bench_database(&url);
    let (items, total) = list_products(&db, &parse_filters("").unwrap(), 2, 5)
        .await
        .unwrap();
    assert!(total >= 40);
    assert_eq!(ids(&items), [6, 7, 8, 9, 10]);
    let first = &items[0];
    assert_eq!(
        first.keys().map(String::as_str).collect::<Vec<_>>(),
        [
            "product_id",
            "sku",
            "name",
            "category_id",
            "list_price",
            "is_active",
            "image_path"
        ]
    );
    assert_eq!(first["sku"], Value::from("SKU-00006"));
    assert_eq!(first["list_price"], Value::Money("37.50".to_owned()));
    assert_eq!(first["image_path"], Value::Null);
}

#[tokio::test]
async fn filters_combine_as_products_service_combines_them() {
    let Some(url) = owner_url() else { return };
    let db = bench_database(&url);
    let search = |query: &'static str| {
        let db = &db;
        async move {
            list_products(db, &parse_filters(query).unwrap(), 1, 100)
                .await
                .unwrap()
        }
    };
    // q matches name or sku, case-insensitively and literally.
    let (items, _) = search("q=demo%20product%201&ids=1,10,12,40").await;
    assert_eq!(ids(&items), [1, 10, 12]);
    let (items, total) = search("sku=SKU-00031").await;
    assert_eq!((ids(&items), total), (vec![31], 1));
    let (items, _) = search("category_id=1&ids=1,2,31,32").await;
    assert_eq!(ids(&items), [1, 31]);
    let (_, total) = search("q=%25").await;
    assert_eq!(total, 0, "% is a character, not a wildcard");
    let (_, total) = search("active=false&ids=1,2,3").await;
    assert_eq!(total, 0);
}

#[tokio::test]
async fn past_the_last_page_the_total_still_comes_back() {
    let Some(url) = owner_url() else { return };
    let db = bench_database(&url);
    let (items, total) = list_products(&db, &parse_filters("ids=1,2,3").unwrap(), 9, 2)
        .await
        .unwrap();
    assert!(items.is_empty());
    assert_eq!(total, 3);
}

#[tokio::test]
async fn one_product_or_product_not_found() {
    let Some(url) = owner_url() else { return };
    let db = bench_database(&url);
    let product = get_product(&db, 3).await.unwrap();
    assert_eq!(product["name"], Value::from("Demo Product 3"));
    for missing in [999_999, i64::from(i32::MAX) + 1, -1] {
        let error = get_product(&db, missing).await.unwrap_err();
        assert_eq!(
            (error.status, error.code.as_str()),
            (404, "product_not_found")
        );
    }
}
