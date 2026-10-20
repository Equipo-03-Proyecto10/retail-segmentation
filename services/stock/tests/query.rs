use std::collections::HashMap;

use stock::{parse_filters, StockFilters};

#[test]
fn parses_the_three_optional_positive_filters() {
    let query = HashMap::from([
        ("store_id".to_owned(), "1".to_owned()),
        ("product_id".to_owned(), "27".to_owned()),
        ("below".to_owned(), "5".to_owned()),
    ]);
    assert_eq!(
        parse_filters(&query).unwrap(),
        StockFilters {
            store_id: Some(1),
            product_id: Some(27),
            below: Some(5),
        }
    );
}

#[test]
fn invalid_filters_name_every_bad_parameter() {
    let query = HashMap::from([
        ("store_id".to_owned(), "0".to_owned()),
        ("product_id".to_owned(), "-1".to_owned()),
        ("below".to_owned(), "abc".to_owned()),
    ]);
    let error = parse_filters(&query).unwrap_err();
    assert_eq!((error.status, error.code.as_str()), (422, "invalid_query"));
    assert_eq!(
        error
            .details
            .iter()
            .map(|detail| detail.field.as_str())
            .collect::<Vec<_>>(),
        ["store_id", "product_id", "below"]
    );
}
