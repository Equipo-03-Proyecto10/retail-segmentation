//! The query string of a sales report, without a database.

use analytics_kpis::query::{parse_sales_query, Dimension, Granularity};

fn rejected(query: &str) -> Vec<(String, String)> {
    let error = parse_sales_query(query).unwrap_err();
    assert_eq!((error.status, error.code.as_str()), (422, "invalid_query"));
    error
        .details
        .iter()
        .map(|detail| (detail.field.clone(), detail.code.clone()))
        .collect()
}

#[test]
fn a_window_and_nothing_else_is_one_row_for_the_whole_window() {
    let query = parse_sales_query("from=2026-10-01&to=2026-10-31").unwrap();
    assert!(query.group_by.is_empty());
    assert_eq!(query.granularity, None);
    assert_eq!(
        query.window_start().to_rfc3339(),
        "2026-10-01T00:00:00+00:00"
    );
    // `to` is inclusive: the window ends where the next day begins.
    assert_eq!(query.window_end().to_rfc3339(), "2026-11-01T00:00:00+00:00");
}

#[test]
fn dimensions_keep_their_order_and_period_defaults_to_month() {
    let query = parse_sales_query("from=2026-10-01&to=2026-10-31&group_by=store,period").unwrap();
    assert_eq!(query.group_by, [Dimension::Store, Dimension::Period]);
    assert_eq!(query.granularity, Some(Granularity::Month));
    let query = parse_sales_query("from=2026-10-01&to=2026-10-31&group_by=period&granularity=week")
        .unwrap();
    assert_eq!(query.granularity, Some(Granularity::Week));
}

#[test]
fn filters_are_read() {
    let query = parse_sales_query(
        "from=2026-10-01&to=2026-10-31&store_id=3&channel_id=2&segment_code=LOYAL",
    )
    .unwrap();
    assert_eq!(
        (
            query.store_id,
            query.channel_id,
            query.segment_code.as_deref()
        ),
        (Some(3), Some(2), Some("LOYAL"))
    );
}

#[test]
fn each_rejected_parameter_is_named() {
    assert_eq!(
        rejected("from=2026-1-01&to=tomorrow"),
        [
            ("from".into(), "invalid_format".into()),
            ("to".into(), "invalid_format".into())
        ]
    );
    assert_eq!(
        rejected(""),
        [
            ("from".into(), "required".into()),
            ("to".into(), "required".into())
        ]
    );
    assert_eq!(
        rejected("from=2026-10-31&to=2026-10-01"),
        [("to".into(), "to_before_from".into())]
    );
    assert_eq!(
        rejected("from=2024-01-01&to=2026-01-01"),
        [("to".into(), "window_too_long".into())]
    );
    assert_eq!(
        rejected("from=2026-10-01&to=2026-10-31&granularity=day"),
        [("granularity".into(), "requires_period".into())]
    );
    // An empty element of the list is not a dimension; an empty list is none.
    assert_eq!(
        rejected("from=2026-10-01&to=2026-10-31&group_by=store,"),
        [("group_by".into(), "enum".into())]
    );
    let query = parse_sales_query("from=2026-10-01&to=2026-10-31&group_by=").unwrap();
    assert!(query.group_by.is_empty());
}
