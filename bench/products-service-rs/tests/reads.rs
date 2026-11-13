//! The four reads against the seed's 40 products, as `svc_products_bench`.

mod support;

use mosaiq_kit::Value;
use products_service_rs::{
    get_product, get_product_details, list_product_relations, list_products, parse_filters,
    DEFAULT_PHOTO_BASE_URL,
};
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

fn text(value: &Value) -> &str {
    match value {
        Value::Text(text) => text,
        other => panic!("{other:?} is not text"),
    }
}

#[tokio::test]
async fn the_details_come_in_the_xsd_order_with_the_seeded_status_attributes_and_barcode() {
    let Some(url) = owner_url() else { return };
    let db = bench_database(&url);
    let details = get_product_details(&db, 3, DEFAULT_PHOTO_BASE_URL)
        .await
        .unwrap();
    assert_eq!(
        details.keys().map(String::as_str).collect::<Vec<_>>(),
        [
            "product_id",
            "sku",
            "name",
            "category_id",
            "list_price",
            "is_active",
            "commercial_status",
            "attributes",
            "photo_url",
            "barcode"
        ]
    );
    assert_eq!(details["name"], Value::from("Demo Product 3"));
    assert_eq!(details["list_price"], Value::Money("26.25".to_owned()));
    assert_eq!(details["is_active"], Value::Bool(true));
    assert_eq!(details["commercial_status"], Value::from("active"));
    assert_eq!(details["photo_url"], Value::Null);
    assert_eq!(details["barcode"], Value::from("7501000000036"));
    let Value::List(attributes) = &details["attributes"] else {
        panic!("attributes is not a list");
    };
    let pairs: Vec<(&str, &str)> = attributes
        .iter()
        .map(|attribute| match attribute {
            Value::Object(members) => (text(&members["name"]), text(&members["value"])),
            other => panic!("{other:?} is not an attribute"),
        })
        .collect();
    assert_eq!(
        pairs.iter().map(|(name, _)| *name).collect::<Vec<_>>(),
        ["brand", "color", "pack_size"]
    );
}

#[tokio::test]
async fn details_of_an_unknown_product_are_product_not_found() {
    let Some(url) = owner_url() else { return };
    let db = bench_database(&url);
    for missing in [999_999, i64::from(i32::MAX) + 1, -1] {
        let error = get_product_details(&db, missing, DEFAULT_PHOTO_BASE_URL)
            .await
            .unwrap_err();
        assert_eq!(
            (error.status, error.code.as_str()),
            (404, "product_not_found")
        );
    }
}

fn pairs(items: &[indexmap::IndexMap<String, Value>]) -> Vec<(i64, String)> {
    items
        .iter()
        .map(|item| match (&item["related_product_id"], &item["kind"]) {
            (Value::Int(id), Value::Text(kind)) => (*id, kind.clone()),
            other => panic!("{other:?} is not a relation"),
        })
        .collect()
}

#[tokio::test]
async fn relations_are_ordered_by_kind_then_id_and_filtered_by_kind() {
    let Some(url) = owner_url() else { return };
    let db = bench_database(&url);
    // The seed: 1 -> 31 substitute, 1 -> 2 complement.
    let (items, total) = list_product_relations(&db, 1, None, 1, 20).await.unwrap();
    assert_eq!(total, 2);
    assert_eq!(
        pairs(&items),
        [(2, "complement".to_owned()), (31, "substitute".to_owned())]
    );
    assert_eq!(
        items[0].keys().map(String::as_str).collect::<Vec<_>>(),
        ["related_product_id", "kind"]
    );
    let (items, total) = list_product_relations(&db, 1, Some("substitute"), 1, 20)
        .await
        .unwrap();
    assert_eq!(
        (pairs(&items), total),
        (vec![(31, "substitute".to_owned())], 1)
    );
    // A relation is directed: 11 -> 12 is stated as a complement and nothing
    // says 12 -> 11, and product 11 has no substitute at all.
    let (items, _) = list_product_relations(&db, 11, Some("substitute"), 1, 20)
        .await
        .unwrap();
    assert!(items.is_empty());
    let (items, _) = list_product_relations(&db, 12, Some("complement"), 1, 20)
        .await
        .unwrap();
    assert_eq!(pairs(&items), [(13, "complement".to_owned())]);
}

#[tokio::test]
async fn relations_page_and_past_the_last_page_the_total_still_comes_back() {
    let Some(url) = owner_url() else { return };
    let db = bench_database(&url);
    let (items, total) = list_product_relations(&db, 1, None, 2, 1).await.unwrap();
    assert_eq!(
        (pairs(&items), total),
        (vec![(31, "substitute".to_owned())], 2)
    );
    let (items, total) = list_product_relations(&db, 1, None, 9, 5).await.unwrap();
    assert!(items.is_empty());
    assert_eq!(total, 2);
    let (items, total) = list_product_relations(&db, 1, Some("complement"), 3, 1)
        .await
        .unwrap();
    assert!(items.is_empty());
    assert_eq!(total, 1);
}

#[tokio::test]
async fn relations_of_an_unknown_product_are_product_not_found() {
    let Some(url) = owner_url() else { return };
    let db = bench_database(&url);
    for missing in [999_999, i64::from(i32::MAX) + 1] {
        let error = list_product_relations(&db, missing, None, 1, 20)
            .await
            .unwrap_err();
        assert_eq!(
            (error.status, error.code.as_str()),
            (404, "product_not_found")
        );
    }
}

#[tokio::test]
async fn the_bench_role_reads_the_four_tables_and_writes_nothing() {
    let Some(url) = owner_url() else { return };
    let db = bench_database(&url);
    let connection = db.connection().await.unwrap();
    for table in [
        "products.product_status",
        "products.product_attribute",
        "products.product_barcode",
        "products.product_relation",
    ] {
        let row = connection
            .query_one(
                "SELECT has_table_privilege(current_user, $1, 'SELECT'), \
                        has_table_privilege(current_user, $1, 'INSERT, UPDATE, DELETE, TRUNCATE')",
                &[&table],
            )
            .await
            .unwrap();
        assert_eq!(
            (row.get::<_, bool>(0), row.get::<_, bool>(1)),
            (true, false),
            "{table}"
        );
    }
}
