//! What a client receives, in JSON and in XML: the report and the catalog
//! against the contract's own examples and XSD, and every refusal of the
//! contract in both formats. The harness is `support::http`.

mod support;

use serde_json::Value;
use std::collections::BTreeSet;
use support::http::{
    assert_valid_error_xml, assert_valid_xml, send, send_with, Stack, NET_SALES_ROLES, ROLES, TURN,
};
use support::{
    connect, customer, purge, remove_customer, remove_sales, sale, tag, Line, Spec, MANAGER,
};

/// (path, token, status, error code, the detail's field and code when it has one)
type Case<'a> = (
    String,
    Option<&'a str>,
    u16,
    &'a str,
    Option<(&'a str, &'a str)>,
);

const JSON: &str = "application/json";
const XML: &str = "application/xml";

fn keys(value: &Value) -> BTreeSet<String> {
    value.as_object().unwrap().keys().cloned().collect()
}

fn example(name: &str) -> Value {
    let path = format!("contracts/analytics-kpis/examples/{name}");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

#[tokio::test]
async fn a_report_has_the_shape_of_the_contracts_examples_in_json_and_xml() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    purge(&owner).await;
    let tag = tag();
    let buyer = customer(&owner).await;
    let line = Line {
        product: 1,
        quantity: 2,
        cents: 1050,
    };
    sale(
        &owner,
        &tag,
        Spec::new(buyer, 1, 1, "2020-03-05T12:00:00Z", "SEEDED"),
        &[line],
    )
    .await;
    let token = stack.token("ANALYST");
    let path = "/v1/kpi-reports/net-sales?from=2020-03-01&to=2020-03-31&group_by=store,period";

    let (status, body) = send(&stack.app, "GET", path, Some(&token), JSON).await;
    assert_eq!(status, 200, "{body}");
    let report: Value = serde_json::from_str(&body).unwrap();
    let expected = example("net-sales.json");
    assert_eq!(keys(&report), keys(&expected));
    assert_eq!(keys(&report["rows"][0]), keys(&expected["rows"][0]));
    assert_eq!(
        keys(&report["rows"][0]["measures"][0]),
        keys(&expected["rows"][0]["measures"][0])
    );
    assert_eq!(keys(&report["data_origin"]), keys(&expected["data_origin"]));
    assert_eq!(keys(&report["store_scope"]), keys(&expected["store_scope"]));
    // The measures, in the catalog's order, each with its kind and unit.
    let measures: Vec<(&str, &str, &str)> = report["rows"][0]["measures"]
        .as_array()
        .unwrap()
        .iter()
        .map(|m| {
            (
                m["code"].as_str().unwrap(),
                m["kind"].as_str().unwrap(),
                m["unit"].as_str().unwrap(),
            )
        })
        .collect();
    assert_eq!(
        measures,
        [
            ("net_amount", "OBSERVED", "currency"),
            ("net_units", "OBSERVED", "count"),
            ("net_orders", "OBSERVED", "count"),
        ]
    );
    // A sale marked SEEDED is marked synthetic (ADR-0052).
    assert_eq!(report["rows"][0]["synthetic"], true);
    assert_eq!(report["data_origin"]["synthetic"], true);
    // A decimal is a string, in JSON as in the example.
    assert_eq!(report["rows"][0]["measures"][0]["value"], "21.00");

    let (status, xml) = send(&stack.app, "GET", path, Some(&token), XML).await;
    assert_eq!(status, 200, "{xml}");
    assert_valid_xml(&xml);
    assert!(xml.contains("<common:PageInfo>"), "{xml}");
    assert!(xml.contains("<synthetic>true</synthetic>"), "{xml}");
    assert!(!xml.contains("<page_info>"), "{xml}");
    // A null is omitted, never written empty.
    assert!(!xml.contains("<segment_code"), "{xml}");
    assert!(!xml.contains("<campaign_id"), "{xml}");
    assert!(xml.contains("<value>21.00</value>"), "{xml}");
    assert!(xml.contains("<store_id>1</store_id>"), "{xml}");

    // Both of a store-scoped caller's shapes validate as well.
    let manager = stack.token_for(MANAGER, "STORE_MANAGER");
    for query in [
        "group_by=store",
        "",
        "group_by=segment,channel,period&granularity=week",
    ] {
        let path = format!("/v1/kpi-reports/net-sales?from=2020-03-01&to=2020-03-31&{query}");
        let (status, xml) = send(&stack.app, "GET", &path, Some(&manager), XML).await;
        assert_eq!(status, 200, "{xml}");
        assert_valid_xml(&xml);
        assert!(xml.contains("<restricted>true</restricted>"), "{xml}");
        assert!(xml.contains("<store_ids>1</store_ids>"), "{xml}");
    }
    // An empty report validates too.
    let path = "/v1/kpi-reports/net-sales?from=2020-05-01&to=2020-05-02&group_by=store";
    let (_, xml) = send(&stack.app, "GET", path, Some(&token), XML).await;
    assert_valid_xml(&xml);
    assert!(!xml.contains("<rows>"), "{xml}");

    remove_sales(&owner, &tag).await;
    remove_customer(&owner, buyer).await;
    stack.finish().await;
}

#[tokio::test]
async fn the_catalog_is_the_contracts_and_every_role_that_reads_reports_reads_it() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let expected = example("kpi-definitions.json");
    for role in ROLES {
        let token = stack.token(role);
        let (status, body) = send(
            &stack.app,
            "GET",
            "/v1/kpi-reports/definitions",
            Some(&token),
            JSON,
        )
        .await;
        if NET_SALES_ROLES.contains(&role) {
            assert_eq!(status, 200, "{role}: {body}");
            let catalog: Value = serde_json::from_str(&body).unwrap();
            assert_eq!(catalog, expected, "{role}");
            let (status, xml) = send(
                &stack.app,
                "GET",
                "/v1/kpi-reports/definitions",
                Some(&token),
                XML,
            )
            .await;
            assert_eq!(status, 200, "{xml}");
            assert_valid_xml(&xml);
        } else {
            assert_eq!(status, 403, "{role}");
            assert!(body.contains("\"forbidden_role\""), "{body}");
        }
    }
    let token = stack.token("ADMIN");
    let (_, body) = send(
        &stack.app,
        "GET",
        "/v1/kpi-reports/definitions?size=3&page=3",
        Some(&token),
        JSON,
    )
    .await;
    let page: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(page["kpi_definitions"].as_array().unwrap().len(), 1);
    assert_eq!(
        page["kpi_definitions"][0]["kpi_code"],
        "campaign-performance"
    );
    assert_eq!(
        page["page_info"],
        serde_json::json!({"page": 3, "size": 3, "total": 7})
    );
    let (status, body) = send(
        &stack.app,
        "GET",
        "/v1/kpi-reports/definitions?size=101",
        Some(&token),
        JSON,
    )
    .await;
    assert_eq!(status, 422);
    assert!(body.contains("\"invalid_pagination\""), "{body}");
    stack.finish().await;
}

#[tokio::test]
async fn every_refusal_of_the_contract_in_json_and_xml() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let admin = stack.token("ADMIN");
    let manager = stack.token_for(MANAGER, "STORE_MANAGER");
    let planner = stack.token("INVENTORY_PLANNER");
    let net = "/v1/kpi-reports/net-sales";
    let window = "from=2020-03-01&to=2020-03-31";

    // (path, token, status, error code, the detail's field and code when it has one)
    let cases: Vec<Case> = vec![
        (format!("{net}?{window}"), None, 401, "invalid_token", None),
        (
            format!("{net}?{window}"),
            Some("not-a-token"),
            401,
            "invalid_token",
            None,
        ),
        (
            format!("{net}?{window}"),
            Some(&planner),
            403,
            "forbidden_role",
            None,
        ),
        (
            format!("{net}?{window}&store_id=2"),
            Some(&manager),
            403,
            "forbidden_store",
            Some(("store_id", "not_assigned")),
        ),
        (
            format!("{net}?to=2020-03-31"),
            Some(&admin),
            422,
            "invalid_query",
            Some(("from", "required")),
        ),
        (
            format!("{net}?from=2020-03-01"),
            Some(&admin),
            422,
            "invalid_query",
            Some(("to", "required")),
        ),
        (
            format!("{net}?from=03/01/2020&to=2020-03-31"),
            Some(&admin),
            422,
            "invalid_query",
            Some(("from", "invalid_format")),
        ),
        (
            format!("{net}?from=2020-02-30&to=2020-03-31"),
            Some(&admin),
            422,
            "invalid_query",
            Some(("from", "invalid_format")),
        ),
        (
            format!("{net}?from=2020-03-31&to=2020-03-01"),
            Some(&admin),
            422,
            "invalid_query",
            Some(("to", "to_before_from")),
        ),
        (
            format!("{net}?from=2020-01-01&to=2022-01-01"),
            Some(&admin),
            422,
            "invalid_query",
            Some(("to", "window_too_long")),
        ),
        (
            format!("{net}?{window}&group_by=region"),
            Some(&admin),
            422,
            "invalid_query",
            Some(("group_by", "enum")),
        ),
        (
            format!("{net}?{window}&group_by=store,store"),
            Some(&admin),
            422,
            "invalid_query",
            Some(("group_by", "invalid_format")),
        ),
        (
            format!("{net}?{window}&granularity=month"),
            Some(&admin),
            422,
            "invalid_query",
            Some(("granularity", "requires_period")),
        ),
        (
            format!("{net}?{window}&group_by=period&granularity=year"),
            Some(&admin),
            422,
            "invalid_query",
            Some(("granularity", "enum")),
        ),
        (
            format!("{net}?{window}&store_id=0"),
            Some(&admin),
            422,
            "invalid_query",
            Some(("store_id", "invalid_format")),
        ),
        (
            format!("{net}?{window}&channel_id=x"),
            Some(&admin),
            422,
            "invalid_query",
            Some(("channel_id", "invalid_format")),
        ),
        (
            format!("{net}?{window}&segment_code="),
            Some(&admin),
            422,
            "invalid_query",
            Some(("segment_code", "invalid_format")),
        ),
        (
            format!("{net}?{window}&page=0"),
            Some(&admin),
            422,
            "invalid_pagination",
            None,
        ),
        (
            format!("{net}?{window}&size=101"),
            Some(&admin),
            422,
            "invalid_pagination",
            None,
        ),
    ];
    for (path, token, status, code, detail) in &cases {
        for accept in [JSON, XML] {
            let (got, body) =
                send_with(&stack.app, "GET", path, *token, accept, Some("corr-test")).await;
            assert_eq!(got, *status, "{path} {accept}: {body}");
            assert!(body.contains(code), "{path} {accept}: {body}");
            assert!(
                body.contains("corr-test") || *status == 401,
                "{path}: {body}"
            );
            if let Some((field, detail_code)) = detail {
                assert!(
                    body.contains(field) && body.contains(detail_code),
                    "{path} {accept}: {body}"
                );
            }
            if accept == XML {
                assert_valid_error_xml(&body);
            } else {
                let parsed: Value = serde_json::from_str(&body).unwrap();
                assert_eq!(parsed["code"], *code);
                assert!(parsed["details"].is_array());
            }
        }
    }

    // Every rejected parameter is named, not just the first.
    let path = format!("{net}?from=x&to=y&group_by=bad");
    let (_, body) = send(&stack.app, "GET", &path, Some(&admin), JSON).await;
    let parsed: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(parsed["details"].as_array().unwrap().len(), 3, "{body}");

    // 731 days exactly is allowed (2020 has 366 days).
    let (status, body) = send(
        &stack.app,
        "GET",
        &format!("{net}?from=2020-01-01&to=2021-12-31"),
        Some(&admin),
        JSON,
    )
    .await;
    assert_eq!(status, 200, "{body}");

    // A format the service does not speak, and a method it does not have.
    let (status, body) = send(
        &stack.app,
        "GET",
        &format!("{net}?{window}"),
        Some(&admin),
        "text/html",
    )
    .await;
    assert_eq!(status, 406, "{body}");
    assert!(body.contains("not_acceptable"), "{body}");
    for method in ["POST", "PUT", "PATCH", "DELETE"] {
        let (status, _) = send(
            &stack.app,
            method,
            &format!("{net}?{window}"),
            Some(&admin),
            JSON,
        )
        .await;
        assert_eq!(status, 405, "{method}");
    }
    stack.finish().await;
}

#[tokio::test]
async fn an_operation_that_is_planned_is_not_served() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let admin = stack.token("ADMIN");
    for path in [
        "conversion",
        "recommendation-performance",
        "campaign-performance",
    ] {
        let uri = format!("/v1/kpi-reports/{path}?from=2020-03-01&to=2020-03-31");
        let (status, body) = send(&stack.app, "GET", &uri, Some(&admin), JSON).await;
        assert_eq!(status, 404, "{path}: {body}");
    }
    stack.finish().await;
}

#[tokio::test]
async fn the_meta_routes_answer_without_a_token() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let (status, _) = send(&stack.app, "GET", "/health/live", None, JSON).await;
    assert_eq!(status, 204);
    let (status, _) = send(&stack.app, "GET", "/health/ready", None, JSON).await;
    assert_eq!(
        status, 204,
        "every view the service reads is granted and present"
    );
    let (status, body) = send(&stack.app, "GET", "/openapi.yaml", None, "application/yaml").await;
    assert_eq!(status, 200);
    assert_eq!(
        body,
        std::fs::read_to_string("contracts/analytics-kpis/openapi.yaml").unwrap()
    );
    let (status, body) = send(&stack.app, "GET", "/schema.xsd", None, XML).await;
    assert_eq!(status, 200);
    assert_eq!(
        body,
        std::fs::read_to_string("contracts/analytics-kpis/analytics-kpis.xsd").unwrap()
    );
    stack.finish().await;
}

/// With nothing listening where the session store should be, authentication
/// fails closed (ADR-0036, ADR-0037): the token is never accepted unchecked.
#[tokio::test]
async fn an_unreachable_session_store_is_503_never_an_open_door() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start_with(Some("redis://127.0.0.1:1/0")).await else {
        return;
    };
    let token = stack.token("ADMIN");
    for accept in [JSON, XML] {
        let (status, body) = send(
            &stack.app,
            "GET",
            "/v1/kpi-reports/net-sales?from=2020-03-01&to=2020-03-31",
            Some(&token),
            accept,
        )
        .await;
        assert_eq!(status, 503, "{body}");
        assert!(body.contains("auth_unavailable"), "{body}");
        if accept == XML {
            assert_valid_error_xml(&body);
        }
    }
    stack.finish().await;
}
