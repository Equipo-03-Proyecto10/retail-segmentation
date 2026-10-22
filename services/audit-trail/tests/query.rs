use audit_trail::{parse_filters, ACTIONS, ENTITIES};

fn rejected(query: &str) -> Vec<(String, String)> {
    let error = parse_filters(query).unwrap_err();
    assert_eq!((error.status, error.code.as_str()), (422, "invalid_query"));
    error
        .details
        .iter()
        .map(|detail| (detail.field.clone(), detail.code.clone()))
        .collect()
}

fn pair(field: &str, code: &str) -> Vec<(String, String)> {
    vec![(field.to_owned(), code.to_owned())]
}

#[test]
fn no_filter_is_a_valid_query() {
    assert_eq!(parse_filters("").unwrap(), Default::default());
    assert_eq!(parse_filters("page=2&size=5").unwrap(), Default::default());
}

#[test]
fn every_filter_parses() {
    let filters = parse_filters(
        "entity=campaign&entity_pk=12&user_id=11111111-1111-1111-1111-000000000001\
         &action=UPDATE&from=2026-10-01T00:00:00Z&to=2026-10-02T00%3A00%3A00%2B00%3A00",
    )
    .unwrap();
    assert_eq!(filters.entity.as_deref(), Some("campaign"));
    assert_eq!(filters.entity_pk.as_deref(), Some("12"));
    assert_eq!(filters.action.as_deref(), Some("UPDATE"));
    assert!(filters.user_id.is_some() && filters.from.is_some() && filters.to.is_some());
}

#[test]
fn the_thirteen_entities_and_three_actions_are_accepted() {
    for entity in ENTITIES {
        assert!(
            parse_filters(&format!("entity={entity}")).is_ok(),
            "{entity}"
        );
    }
    for action in ACTIONS {
        assert!(
            parse_filters(&format!("action={action}")).is_ok(),
            "{action}"
        );
    }
    assert_eq!(ENTITIES.len(), 13);
}

#[test]
fn an_unknown_entity_or_action_is_invalid_query() {
    assert_eq!(rejected("entity=audit_log"), pair("entity", "enum"));
    assert_eq!(rejected("entity=Campaign"), pair("entity", "enum"));
    assert_eq!(rejected("action=update"), pair("action", "enum"));
    assert_eq!(rejected("action=TRUNCATE"), pair("action", "enum"));
}

#[test]
fn entity_pk_needs_an_entity_and_a_bounded_length() {
    assert_eq!(
        rejected("entity_pk=12"),
        pair("entity_pk", "requires_entity")
    );
    assert_eq!(
        rejected("entity=campaign&entity_pk="),
        pair("entity_pk", "too_short")
    );
    let long = "x".repeat(81);
    assert_eq!(
        rejected(&format!("entity=campaign&entity_pk={long}")),
        pair("entity_pk", "too_long")
    );
    assert!(parse_filters(&format!("entity=campaign&entity_pk={}", "x".repeat(80))).is_ok());
}

#[test]
fn malformed_values_are_invalid_format() {
    assert_eq!(rejected("user_id=nope"), pair("user_id", "invalid_format"));
    assert_eq!(rejected("from=2026-10-01"), pair("from", "invalid_format"));
    // A literal `+` decodes to a space, so the offset is lost.
    assert_eq!(
        rejected("to=2026-10-01T00:00:00+00:00"),
        pair("to", "invalid_format")
    );
}

#[test]
fn to_before_from_names_to_and_the_same_instant_is_allowed() {
    assert_eq!(
        rejected("from=2026-10-02T00:00:00Z&to=2026-10-01T00:00:00Z"),
        pair("to", "before_from")
    );
    assert!(parse_filters("from=2026-10-02T00:00:00Z&to=2026-10-02T00:00:00Z").is_ok());
}

#[test]
fn every_rejected_parameter_is_named() {
    let fields: Vec<String> = rejected("entity=x&action=y&user_id=z&from=a&to=b")
        .into_iter()
        .map(|(field, _)| field)
        .collect();
    assert_eq!(fields, ["entity", "user_id", "action", "from", "to"]);
}
