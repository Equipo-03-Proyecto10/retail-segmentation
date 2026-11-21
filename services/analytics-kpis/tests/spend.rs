//! Spend through the whole router, on the seeded database as `svc_kpis` (F26-03).
//!
//! The sales sit in March 2020, a window the seed does not reach, so every
//! figure is computed by hand from what the test wrote. A is labelled LOYAL
//! under seeded run 31 (model version 1), B CHAMPION under seeded run 30 (model
//! version 2), C holds no label at all.
//!
//! | sale | store | channel | customer | at (UTC)         | origin   | lines                 | reversed | net   |
//! |------|-------|---------|----------|------------------|----------|-----------------------|----------|-------|
//! | S1   | 1     | 1       | A        | 2020-03-05 12:00 | OBSERVED | p1 2 x 10.00, p2 1 x 5.50 | none | 25.50 |
//! | S2   | 1     | 2       | A        | 2020-03-31 23:30 | OBSERVED | p1 3 x 10.00          | none     | 30.00 |
//! | S3   | 2     | 1       | B        | 2020-03-10 08:00 | SEEDED   | p1 4 x 2.50           | 1 unit   | 7.50  |
//! | S4   | 1     | 1       | B        | 2020-03-20 10:00 | OBSERVED | p1 1 x 40.00          | none     | 40.00 |
//! | S5   | 2     | 1       | C        | 2020-03-12 08:00 | OBSERVED | p1 1 x 100.00         | all      | gone  |
//! | S6   | 2     | 3       | C        | 2020-03-15 09:00 | OBSERVED | p1 2 x 7.00           | none     | 14.00 |
//! | S7   | 1     | 1       | A        | 2020-04-01 00:00 | OBSERVED | p1 9 x 10.00          | none     | outside |
//!
//! Whole window: 117.00 net, 3 customers, 39.00 each. Store 1: 95.50 over A and
//! B (47.75); store 2: 21.50 over B and C (10.75). B is active once in each
//! store and once in the total, so the stores' customers add to 4 and the
//! window's to 3.

mod support;

use serde_json::{json, Value};
use support::http::{assert_valid_xml, send, Stack, NET_SALES_ROLES, ROLES, TURN};
use support::{
    assign, connect, customer, measure_value, purge, remove_customer, remove_sales, reverse, sale,
    tag, Line, Spec, MANAGER,
};
use tokio_postgres::Client;
use uuid::Uuid;

const WINDOW: &str = "from=2020-03-01&to=2020-03-31";
const SPEND: &str = "/v1/kpi-reports/spend";

struct Fixture {
    owner: Client,
    tag: String,
    customers: [Uuid; 3],
}

impl Fixture {
    async fn record(database: &str) -> Self {
        let owner = connect(database).await;
        purge(&owner).await;
        let tag = tag();
        let a = customer(&owner).await;
        let b = customer(&owner).await;
        let c = customer(&owner).await;
        assign(&owner, a, 31, "LOYAL").await;
        assign(&owner, b, 30, "CHAMPION").await;
        let line = |product, quantity, cents| Line {
            product,
            quantity,
            cents,
        };
        let on =
            |customer, store, channel, at, origin| Spec::new(customer, store, channel, at, origin);
        sale(
            &owner,
            &tag,
            on(a, 1, 1, "2020-03-05T12:00:00Z", "OBSERVED"),
            &[line(1, 2, 1000), line(2, 1, 550)],
        )
        .await;
        sale(
            &owner,
            &tag,
            on(a, 1, 2, "2020-03-31T23:30:00Z", "OBSERVED"),
            &[line(1, 3, 1000)],
        )
        .await;
        let s3 = sale(
            &owner,
            &tag,
            on(b, 2, 1, "2020-03-10T08:00:00Z", "SEEDED"),
            &[line(1, 4, 250)],
        )
        .await;
        reverse(&owner, s3, 1, 1).await;
        sale(
            &owner,
            &tag,
            on(b, 1, 1, "2020-03-20T10:00:00Z", "OBSERVED"),
            &[line(1, 1, 4000)],
        )
        .await;
        let s5 = sale(
            &owner,
            &tag,
            on(c, 2, 1, "2020-03-12T08:00:00Z", "OBSERVED"),
            &[line(1, 1, 10000)],
        )
        .await;
        reverse(&owner, s5, 1, 1).await;
        sale(
            &owner,
            &tag,
            on(c, 2, 3, "2020-03-15T09:00:00Z", "OBSERVED"),
            &[line(1, 2, 700)],
        )
        .await;
        sale(
            &owner,
            &tag,
            on(a, 1, 1, "2020-04-01T00:00:00Z", "OBSERVED"),
            &[line(1, 9, 1000)],
        )
        .await;
        Self {
            owner,
            tag,
            customers: [a, b, c],
        }
    }

    async fn remove(self) {
        remove_sales(&self.owner, &self.tag).await;
        for id in self.customers {
            remove_customer(&self.owner, id).await;
        }
    }
}

async fn json_as(stack: &Stack, path: &str, token: &str) -> (u16, Value) {
    let (status, body) = send(&stack.app, "GET", path, Some(token), "application/json").await;
    (
        status.as_u16(),
        serde_json::from_str(&body).unwrap_or(Value::Null),
    )
}

async fn json(stack: &Stack, path: &str) -> (u16, Value) {
    json_as(stack, path, &stack.token("ADMIN")).await
}

/// `(net_amount, active_customers, average_spend)` of a row.
fn figures(row: &Value) -> (String, String, Option<String>) {
    (
        measure_value(row, "net_amount").unwrap(),
        measure_value(row, "active_customers").unwrap(),
        measure_value(row, "average_spend"),
    )
}

fn expect(row: &Value, amount: &str, active: &str, average: Option<&str>) {
    assert_eq!(
        figures(row),
        (
            amount.to_owned(),
            active.to_owned(),
            average.map(str::to_owned)
        ),
        "{row}"
    );
}

#[tokio::test]
async fn spend_counts_each_customer_once_and_is_net_of_reversals() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let fixture = Fixture::record(&stack.database).await;

    // The whole window: 117.00 net, A, B and C active, 39.00 each; S5 is
    // reversed in full and S7 is outside.
    let (status, report) = json(&stack, &format!("{SPEND}?{WINDOW}")).await;
    assert_eq!(status, 200, "{report}");
    assert_eq!(report["kpi"], "spend");
    assert_eq!(report["definition_version"], 1);
    assert_eq!(report["group_by"], json!([]));
    assert_eq!(report["rows"].as_array().unwrap().len(), 1);
    let row = &report["rows"][0];
    expect(row, "117.00", "3", Some("39.00"));
    let measures: Vec<(&str, &str, &str)> = row["measures"]
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
            ("active_customers", "OBSERVED", "count"),
            ("average_spend", "OBSERVED", "currency"),
        ]
    );
    assert_eq!(row["synthetic"], true, "S3 is SEEDED");
    assert_eq!(
        report["data_origin"]["views"],
        json!(["api_sales.net_purchase_line_v1"])
    );
    // No segment, so no label was read and no model version named.
    assert_eq!(report["data_origin"]["spans_model_versions"], false);
    assert_eq!(report["data_origin"]["model_version_ids"], json!([]));

    // By store: B is active in both stores and once in the total, so the
    // stores' customers add to 4 where the window has 3.
    let (_, report) = json(&stack, &format!("{SPEND}?{WINDOW}&group_by=store")).await;
    let rows = report["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 2, "{report}");
    assert_eq!(rows[0]["store_id"], 1);
    expect(&rows[0], "95.50", "2", Some("47.75"));
    assert_eq!(rows[1]["store_id"], 2);
    expect(&rows[1], "21.50", "2", Some("10.75"));

    // By segment: the label each customer holds now (RN-21), the unlabelled
    // one reported as it is.
    let (_, report) = json(&stack, &format!("{SPEND}?{WINDOW}&group_by=segment")).await;
    let rows = report["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 3, "{report}");
    let by: Vec<(Option<&str>, &str)> = rows
        .iter()
        .map(|r| {
            (
                r["segment_code"].as_str(),
                r["segment_state"].as_str().unwrap(),
            )
        })
        .collect();
    assert_eq!(
        by,
        [
            (Some("CHAMPION"), "assigned"),
            (Some("LOYAL"), "assigned"),
            (None, "no_open_assignment"),
        ]
    );
    expect(&rows[0], "47.50", "1", Some("47.50"));
    expect(&rows[1], "55.50", "1", Some("55.50"));
    expect(&rows[2], "14.00", "1", Some("14.00"));
    assert_eq!(rows[0]["synthetic"], true, "B's S3 is SEEDED");
    assert_eq!(rows[1]["synthetic"], false);
    // The labels come from two model versions, and the report says so (ADR-0064).
    assert_eq!(
        report["data_origin"]["views"],
        json!([
            "api_sales.net_purchase_line_v1",
            "api_segments.current_label_v1",
            "api_models.run_version_v1"
        ])
    );
    assert_eq!(report["data_origin"]["spans_model_versions"], true);
    assert_eq!(report["data_origin"]["model_version_ids"], json!([1, 2]));

    // One segment's report names its one version and does not span.
    let (_, report) = json(&stack, &format!("{SPEND}?{WINDOW}&segment_code=LOYAL")).await;
    expect(&report["rows"][0], "55.50", "1", Some("55.50"));
    assert_eq!(report["data_origin"]["spans_model_versions"], false);
    assert_eq!(report["data_origin"]["model_version_ids"], json!([1]));

    // Filters: channel 1 holds S1 25.50, S3 7.50 and S4 40.00 over A and B.
    let (_, report) = json(&stack, &format!("{SPEND}?{WINDOW}&channel_id=1")).await;
    expect(&report["rows"][0], "73.00", "2", Some("36.50"));

    // By period: March is one month, S7 is April's.
    let (_, report) = json(&stack, &format!("{SPEND}?{WINDOW}&group_by=period")).await;
    assert_eq!(report["granularity"], "month");
    let row = &report["rows"][0];
    assert_eq!(row["period_start"], "2020-03-01");
    assert_eq!(row["period_end"], "2020-03-31");
    expect(row, "117.00", "3", Some("39.00"));

    fixture.remove().await;
    stack.finish().await;
}

#[tokio::test]
async fn a_window_with_no_sale_reads_zeros_and_no_average() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let (status, report) = json(&stack, &format!("{SPEND}?from=2019-01-01&to=2019-01-31")).await;
    assert_eq!(status, 200, "{report}");
    assert_eq!(report["rows"].as_array().unwrap().len(), 1);
    expect(&report["rows"][0], "0.00", "0", None);
    assert_eq!(report["rows"][0]["synthetic"], false);

    let (_, report) = json(
        &stack,
        &format!("{SPEND}?from=2019-01-01&to=2019-01-31&group_by=segment"),
    )
    .await;
    assert_eq!(report["rows"], json!([]));
    assert_eq!(report["page_info"]["total"], 0);
    stack.finish().await;
}

#[tokio::test]
async fn a_store_scoped_role_reads_its_assigned_stores_only() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let fixture = Fixture::record(&stack.database).await;
    let manager = stack.token_for(MANAGER, "STORE_MANAGER");

    // The manager has store 1 among its stores: S1, S2 and S4 are 95.50 over A
    // and B; store 2's sales are not theirs to read.
    let (status, report) = json_as(&stack, &format!("{SPEND}?{WINDOW}"), &manager).await;
    assert_eq!(status, 200, "{report}");
    expect(&report["rows"][0], "95.50", "2", Some("47.75"));
    assert_eq!(report["store_scope"]["restricted"], true);
    assert_eq!(
        report["data_origin"]["views"],
        json!([
            "api_sales.net_purchase_line_v1",
            "api_users.store_assignment_v1"
        ])
    );

    let (status, report) = json_as(&stack, &format!("{SPEND}?{WINDOW}&store_id=2"), &manager).await;
    assert_eq!(status, 403, "{report}");
    assert_eq!(report["code"], "forbidden_store");

    fixture.remove().await;
    stack.finish().await;
}

#[tokio::test]
async fn spend_validates_its_query_serves_both_formats_and_closes_the_role_matrix() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let fixture = Fixture::record(&stack.database).await;
    let token = stack.token("ANALYST");

    for (query, field, code) in [
        ("to=2020-03-31", "from", "required"),
        ("from=2020-03-31&to=2020-03-01", "to", "to_before_from"),
        (
            "from=2020-03-01&to=2020-03-31&group_by=region",
            "group_by",
            "enum",
        ),
        (
            "from=2020-03-01&to=2020-03-31&granularity=week",
            "granularity",
            "requires_period",
        ),
    ] {
        let (status, body) = json_as(&stack, &format!("{SPEND}?{query}"), &token).await;
        assert_eq!(status, 422, "{query}: {body}");
        assert_eq!(body["code"], "invalid_query");
        assert_eq!(body["details"][0]["field"], field, "{body}");
        assert_eq!(body["details"][0]["code"], code, "{body}");
    }

    let path = format!("{SPEND}?{WINDOW}&group_by=segment,store");
    let (status, xml) = send(&stack.app, "GET", &path, Some(&token), "application/xml").await;
    assert_eq!(status, 200, "{xml}");
    assert_valid_xml(&xml);
    assert!(xml.contains("<code>average_spend</code>"), "{xml}");
    assert!(
        xml.contains("<segment_state>no_open_assignment</segment_state>"),
        "{xml}"
    );

    // An empty window's row has no average, and validates.
    let path = format!("{SPEND}?from=2019-01-01&to=2019-01-31");
    let (_, xml) = send(&stack.app, "GET", &path, Some(&token), "application/xml").await;
    assert_valid_xml(&xml);
    assert!(!xml.contains("average_spend"), "{xml}");

    for role in ROLES {
        let token = if role == "STORE_MANAGER" {
            stack.token_for(MANAGER, role)
        } else {
            stack.token(role)
        };
        let (status, _) = json_as(&stack, &format!("{SPEND}?{WINDOW}"), &token).await;
        let expected = if NET_SALES_ROLES.contains(&role) {
            200
        } else {
            403
        };
        assert_eq!(status, expected, "{role}");
    }

    fixture.remove().await;
    stack.finish().await;
}
