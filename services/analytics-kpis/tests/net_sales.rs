//! Net sales through the whole router, on the seeded database as `svc_kpis`.
//!
//! The sales the tests record sit in March 2020, a window the seed does not
//! reach, so every figure below is computed by hand from what the test wrote:
//!
//! | sale | store | channel | customer | at (UTC)            | origin   | lines                   | reversed |
//! |------|-------|---------|----------|---------------------|----------|-------------------------|----------|
//! | S1   | 1     | 1       | A        | 2020-03-05 12:00    | OBSERVED | p1 2 x 10.00, p2 1 x 5.50 | none   |
//! | S2   | 1     | 2       | A        | 2020-03-31 23:30    | OBSERVED | p1 3 x 10.00            | none     |
//! | S3   | 2     | 1       | B        | 2020-04-01 00:00    | OBSERVED | p1 9 x 10.00            | none (outside) |
//! | S4   | 2     | 1       | B        | 2020-03-10 08:00    | SEEDED   | p1 4 x 2.50             | 1 unit   |
//! | S5   | 2     | 3       | B        | 2020-03-12 08:00    | OBSERVED | p1 1 x 100.00           | all      |
//! | S6   | 1     | 1       | A        | 2020-02-29 23:59:59 | OBSERVED | p1 5 x 10.00            | none (outside) |

mod support;

use serde_json::Value;
use support::http::{send, Stack, NET_SALES_ROLES, ROLES, TURN};
use support::{
    connect, customer, purge, remove_customer, remove_sales, reverse, sale, tag, Line, Spec,
    MANAGER, MANAGER_STORES, NO_STORES,
};
use tokio_postgres::Client;
use uuid::Uuid;

const WINDOW: &str = "from=2020-03-01&to=2020-03-31";

struct Fixture {
    owner: Client,
    tag: String,
    a: Uuid,
    b: Uuid,
}

impl Fixture {
    async fn record(database: &str) -> Self {
        let owner = connect(database).await;
        // What an earlier run that failed half way left behind.
        purge(&owner).await;
        let tag = tag();
        let a = customer(&owner).await;
        let b = customer(&owner).await;
        let line = |product, quantity, cents| Line {
            product,
            quantity,
            cents,
        };
        sale(
            &owner,
            &tag,
            Spec::new(a, 1, 1, "2020-03-05T12:00:00Z", "OBSERVED"),
            &[line(1, 2, 1000), line(2, 1, 550)],
        )
        .await;
        sale(
            &owner,
            &tag,
            Spec::new(a, 1, 2, "2020-03-31T23:30:00Z", "OBSERVED"),
            &[line(1, 3, 1000)],
        )
        .await;
        sale(
            &owner,
            &tag,
            Spec::new(b, 2, 1, "2020-04-01T00:00:00Z", "OBSERVED"),
            &[line(1, 9, 1000)],
        )
        .await;
        let s4 = sale(
            &owner,
            &tag,
            Spec::new(b, 2, 1, "2020-03-10T08:00:00Z", "SEEDED"),
            &[line(1, 4, 250)],
        )
        .await;
        reverse(&owner, s4, 1, 1).await;
        let s5 = sale(
            &owner,
            &tag,
            Spec::new(b, 2, 3, "2020-03-12T08:00:00Z", "OBSERVED"),
            &[line(1, 1, 10000)],
        )
        .await;
        reverse(&owner, s5, 1, 1).await;
        sale(
            &owner,
            &tag,
            Spec::new(a, 1, 1, "2020-02-29T23:59:59Z", "OBSERVED"),
            &[line(1, 5, 1000)],
        )
        .await;
        Self { owner, tag, a, b }
    }

    async fn remove(self) {
        remove_sales(&self.owner, &self.tag).await;
        remove_customer(&self.owner, self.a).await;
        remove_customer(&self.owner, self.b).await;
    }
}

async fn json(stack: &Stack, path: &str, role: &str) -> (u16, Value) {
    let token = stack.token(role);
    let (status, body) = send(&stack.app, "GET", path, Some(&token), "application/json").await;
    (
        status.as_u16(),
        serde_json::from_str(&body).unwrap_or(Value::Null),
    )
}

async fn json_as(stack: &Stack, path: &str, sub: &str, role: &str) -> (u16, Value) {
    let token = stack.token_for(sub, role);
    let (status, body) = send(&stack.app, "GET", path, Some(&token), "application/json").await;
    (
        status.as_u16(),
        serde_json::from_str(&body).unwrap_or(Value::Null),
    )
}

/// `(net_amount, net_units, net_orders)` of a row.
fn figures(row: &Value) -> (String, String, String) {
    let value = |code: &str| {
        row["measures"]
            .as_array()
            .unwrap()
            .iter()
            .find(|measure| measure["code"] == code)
            .unwrap()["value"]
            .as_str()
            .unwrap()
            .to_owned()
    };
    (value("net_amount"), value("net_units"), value("net_orders"))
}

fn expect(row: &Value, amount: &str, units: &str, orders: &str) {
    assert_eq!(
        figures(row),
        (amount.to_owned(), units.to_owned(), orders.to_owned()),
        "{row}"
    );
}

#[tokio::test]
async fn net_sales_are_net_of_reversals_and_cut_at_the_window_edges() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let fixture = Fixture::record(&stack.database).await;

    // No dimension: one row for the whole window. S1 25.50 + S2 30.00 + S4
    // 3 x 2.50 = 7.50; S3 and S6 are outside, S5 is reversed in full and is
    // neither units nor an order.
    let (status, report) = json(
        &stack,
        &format!("/v1/kpi-reports/net-sales?{WINDOW}"),
        "ADMIN",
    )
    .await;
    assert_eq!(status, 200, "{report}");
    assert_eq!(report["kpi"], "net-sales");
    assert_eq!(report["definition_version"], 1);
    assert_eq!(report["from"], "2020-03-01");
    assert_eq!(report["to"], "2020-03-31");
    assert!(report["granularity"].is_null());
    assert_eq!(report["group_by"], serde_json::json!([]));
    assert_eq!(
        report["store_scope"],
        serde_json::json!({"restricted": false, "store_ids": []})
    );
    assert_eq!(report["rows"].as_array().unwrap().len(), 1);
    let row = &report["rows"][0];
    expect(row, "63.00", "9", "3");
    for member in [
        "period_start",
        "period_end",
        "segment_code",
        "segment_state",
        "store_id",
        "channel_id",
        "campaign_id",
        "model_version_id",
        "from_segment_code",
    ] {
        assert!(row[member].is_null(), "{member}: {row}");
    }
    assert_eq!(row["synthetic"], true, "S4 is SEEDED");
    assert_eq!(report["data_origin"]["synthetic"], true);
    assert_eq!(
        report["data_origin"]["views"],
        serde_json::json!(["api_sales.net_purchase_line_v1"])
    );
    assert_eq!(report["data_origin"]["spans_model_versions"], false);
    assert_eq!(
        report["page_info"],
        serde_json::json!({"page": 1, "size": 20, "total": 1})
    );

    // By store: store 1 holds only observed sales, store 2 the seeded one.
    let (_, report) = json(
        &stack,
        &format!("/v1/kpi-reports/net-sales?{WINDOW}&group_by=store"),
        "ANALYST",
    )
    .await;
    let rows = report["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 2, "{report}");
    assert_eq!(rows[0]["store_id"], 1);
    expect(&rows[0], "55.50", "6", "2");
    assert_eq!(rows[0]["synthetic"], false);
    assert_eq!(rows[1]["store_id"], 2);
    expect(&rows[1], "7.50", "3", "1");
    assert_eq!(rows[1]["synthetic"], true);
    assert_eq!(
        report["data_origin"]["synthetic"], true,
        "any row is synthetic"
    );

    // A filter on the seeded store alone: the report is synthetic.
    let (_, report) = json(
        &stack,
        &format!("/v1/kpi-reports/net-sales?{WINDOW}&store_id=1"),
        "MARKETING",
    )
    .await;
    expect(&report["rows"][0], "55.50", "6", "2");
    assert_eq!(
        report["data_origin"]["synthetic"], false,
        "no seeded sale in store 1"
    );

    // By channel, and filtered by channel.
    let (_, report) = json(
        &stack,
        &format!("/v1/kpi-reports/net-sales?{WINDOW}&group_by=channel"),
        "AUDITOR",
    )
    .await;
    let rows = report["rows"].as_array().unwrap();
    assert_eq!(
        rows.len(),
        2,
        "channel 3 holds only the reversed sale: {report}"
    );
    assert_eq!(
        (
            rows[0]["channel_id"].as_i64(),
            rows[1]["channel_id"].as_i64()
        ),
        (Some(1), Some(2))
    );
    expect(&rows[0], "33.00", "6", "2"); // S1 25.50 + S4 7.50
    expect(&rows[1], "30.00", "3", "1");
    let (_, report) = json(
        &stack,
        &format!("/v1/kpi-reports/net-sales?{WINDOW}&channel_id=2"),
        "ADMIN",
    )
    .await;
    expect(&report["rows"][0], "30.00", "3", "1");

    // By month and by day: a sale counts at its own instant, in UTC.
    let (_, report) = json(
        &stack,
        &format!("/v1/kpi-reports/net-sales?{WINDOW}&group_by=period"),
        "ADMIN",
    )
    .await;
    assert_eq!(report["granularity"], "month");
    let rows = report["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(
        (
            rows[0]["period_start"].as_str(),
            rows[0]["period_end"].as_str()
        ),
        (Some("2020-03-01"), Some("2020-03-31"))
    );
    let (_, report) = json(
        &stack,
        &format!("/v1/kpi-reports/net-sales?{WINDOW}&group_by=period&granularity=day"),
        "ADMIN",
    )
    .await;
    let days: Vec<(&str, &str)> = report["rows"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| {
            (
                r["period_start"].as_str().unwrap(),
                r["period_end"].as_str().unwrap(),
            )
        })
        .collect();
    assert_eq!(
        days,
        [
            ("2020-03-05", "2020-03-05"),
            ("2020-03-10", "2020-03-10"),
            ("2020-03-31", "2020-03-31")
        ]
    );
    let (_, report) = json(
        &stack,
        &format!("/v1/kpi-reports/net-sales?{WINDOW}&group_by=period&granularity=week"),
        "ADMIN",
    )
    .await;
    let weeks: Vec<(&str, &str)> = report["rows"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| {
            (
                r["period_start"].as_str().unwrap(),
                r["period_end"].as_str().unwrap(),
            )
        })
        .collect();
    // ISO weeks start on Monday: 2 March, 9 March and 30 March 2020.
    assert_eq!(
        weeks,
        [
            ("2020-03-02", "2020-03-08"),
            ("2020-03-09", "2020-03-15"),
            ("2020-03-30", "2020-04-05")
        ]
    );
    expect(&report["rows"][2], "30.00", "3", "1");

    // The window is inclusive of `to` to the end of its day, and `from` starts
    // at 00:00 UTC: 31 March alone holds S2, 1 April alone holds S3.
    let (_, report) = json(
        &stack,
        "/v1/kpi-reports/net-sales?from=2020-03-31&to=2020-03-31",
        "ADMIN",
    )
    .await;
    expect(&report["rows"][0], "30.00", "3", "1");
    let (_, report) = json(
        &stack,
        "/v1/kpi-reports/net-sales?from=2020-04-01&to=2020-04-01",
        "ADMIN",
    )
    .await;
    expect(&report["rows"][0], "90.00", "9", "1");

    // A window with no sale is one row of zeros, never a missing figure.
    let (_, report) = json(
        &stack,
        "/v1/kpi-reports/net-sales?from=2020-05-01&to=2020-05-31",
        "ADMIN",
    )
    .await;
    expect(&report["rows"][0], "0.00", "0", "0");
    assert_eq!(report["rows"][0]["synthetic"], false);

    // Pages: the total counts every row, a page past the end is empty.
    let path = format!("/v1/kpi-reports/net-sales?{WINDOW}&group_by=period&granularity=day");
    let (_, first) = json(&stack, &format!("{path}&size=2"), "ADMIN").await;
    assert_eq!(
        first["page_info"],
        serde_json::json!({"page": 1, "size": 2, "total": 3})
    );
    assert_eq!(first["rows"].as_array().unwrap().len(), 2);
    let (_, second) = json(&stack, &format!("{path}&size=2&page=2"), "ADMIN").await;
    assert_eq!(second["rows"].as_array().unwrap().len(), 1);
    assert_eq!(second["rows"][0]["period_start"], "2020-03-31");
    let (_, past) = json(&stack, &format!("{path}&size=2&page=9"), "ADMIN").await;
    assert_eq!(past["rows"], serde_json::json!([]));
    assert_eq!(
        past["page_info"],
        serde_json::json!({"page": 9, "size": 2, "total": 3})
    );
    assert_eq!(
        past["data_origin"]["synthetic"], true,
        "the origin is that of the report, not of the page"
    );

    // Every role of the contract reads it; the others are refused.
    for role in ROLES {
        let (status, body) =
            json(&stack, &format!("/v1/kpi-reports/net-sales?{WINDOW}"), role).await;
        if NET_SALES_ROLES.contains(&role) {
            assert_eq!(status, 200, "{role}: {body}");
        } else {
            assert_eq!(status, 403, "{role}");
            assert_eq!(body["code"], "forbidden_role", "{role}: {body}");
        }
    }

    fixture.remove().await;
    stack.finish().await;
}

#[tokio::test]
async fn a_customer_without_a_label_is_reported_as_it_is() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let fixture = Fixture::record(&stack.database).await;
    // A customer the seed has already labelled, buying in the same window.
    let labelled: (Uuid, String) = {
        let row = fixture
            .owner
            .query_one(
                "SELECT customer_id, label_code FROM api_segments.current_label_v1 \
                 WHERE state = 'assigned' ORDER BY customer_id LIMIT 1",
                &[],
            )
            .await
            .unwrap();
        (row.get(0), row.get(1))
    };
    sale(
        &fixture.owner,
        &fixture.tag,
        Spec::new(labelled.0, 3, 1, "2020-03-20T10:00:00Z", "OBSERVED"),
        &[Line {
            product: 1,
            quantity: 1,
            cents: 1234,
        }],
    )
    .await;

    let (status, report) = json(
        &stack,
        &format!("/v1/kpi-reports/net-sales?{WINDOW}&group_by=segment"),
        "ADMIN",
    )
    .await;
    assert_eq!(status, 200, "{report}");
    let rows = report["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 2, "{report}");
    // Ordered by segment code, a row with no code last.
    assert_eq!(rows[0]["segment_state"], "assigned");
    assert_eq!(rows[0]["segment_code"], labelled.1.as_str());
    expect(&rows[0], "12.34", "1", "1");
    assert_eq!(rows[1]["segment_state"], "no_open_assignment");
    assert!(rows[1]["segment_code"].is_null());
    expect(&rows[1], "63.00", "9", "3");
    assert_eq!(
        report["data_origin"]["views"],
        serde_json::json!([
            "api_sales.net_purchase_line_v1",
            "api_segments.current_label_v1"
        ])
    );

    // The filter keeps one label; the origin names the view it was read from.
    let (_, report) = json(
        &stack,
        &format!(
            "/v1/kpi-reports/net-sales?{WINDOW}&segment_code={}",
            labelled.1
        ),
        "ADMIN",
    )
    .await;
    expect(&report["rows"][0], "12.34", "1", "1");
    assert!(
        report["rows"][0]["segment_state"].is_null(),
        "not grouped by segment"
    );
    let (_, report) = json(
        &stack,
        &format!("/v1/kpi-reports/net-sales?{WINDOW}&segment_code=NO_SUCH_LABEL"),
        "ADMIN",
    )
    .await;
    expect(&report["rows"][0], "0.00", "0", "0");

    fixture.remove().await;
    stack.finish().await;
}

#[tokio::test]
async fn a_store_scoped_role_reads_its_assigned_stores_only() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let fixture = Fixture::record(&stack.database).await;
    for role in ["STORE_MANAGER", "COMMERCIAL_SUPERVISOR"] {
        // Another store is refused, in every dimension combination.
        let (status, body) = json_as(
            &stack,
            &format!("/v1/kpi-reports/net-sales?{WINDOW}&store_id=2"),
            MANAGER,
            role,
        )
        .await;
        assert_eq!(status, 403, "{role}: {body}");
        assert_eq!(body["code"], "forbidden_store");
        assert_eq!(
            body["details"],
            serde_json::json!([{"field": "store_id", "code": "not_assigned"}])
        );

        // No store named: the assigned stores only. Store 2 holds S4, but it is
        // not this manager's, so it is not in the figure.
        let (status, report) = json_as(
            &stack,
            &format!("/v1/kpi-reports/net-sales?{WINDOW}"),
            MANAGER,
            role,
        )
        .await;
        assert_eq!(status, 200, "{report}");
        expect(&report["rows"][0], "55.50", "6", "2");
        assert_eq!(
            report["store_scope"],
            serde_json::json!({"restricted": true, "store_ids": MANAGER_STORES})
        );
        assert_eq!(
            report["data_origin"]["views"],
            serde_json::json!([
                "api_sales.net_purchase_line_v1",
                "api_users.store_assignment_v1"
            ])
        );
        assert_eq!(report["data_origin"]["synthetic"], false);

        // Grouped by store alone, every assigned store has a row; one with no
        // sale reads as zeros.
        let (_, report) = json_as(
            &stack,
            &format!("/v1/kpi-reports/net-sales?{WINDOW}&group_by=store"),
            MANAGER,
            role,
        )
        .await;
        let rows = report["rows"].as_array().unwrap();
        let stores: Vec<i64> = rows
            .iter()
            .map(|r| r["store_id"].as_i64().unwrap())
            .collect();
        assert_eq!(stores, [1, 9, 17, 25], "{report}");
        expect(&rows[0], "55.50", "6", "2");
        expect(&rows[1], "0.00", "0", "0");
        assert_eq!(report["page_info"]["total"], 4);

        // Naming an assigned store narrows the scope to it.
        let (_, report) = json_as(
            &stack,
            &format!("/v1/kpi-reports/net-sales?{WINDOW}&store_id=9"),
            MANAGER,
            role,
        )
        .await;
        assert_eq!(
            report["store_scope"],
            serde_json::json!({"restricted": true, "store_ids": [9]})
        );
        expect(&report["rows"][0], "0.00", "0", "0");
    }

    // A store-scoped role with no assignment reads nothing, and is not refused.
    let (status, report) = json_as(
        &stack,
        &format!("/v1/kpi-reports/net-sales?{WINDOW}&group_by=store"),
        NO_STORES,
        "STORE_MANAGER",
    )
    .await;
    assert_eq!(status, 200, "{report}");
    assert_eq!(report["rows"], serde_json::json!([]));
    assert_eq!(
        report["store_scope"],
        serde_json::json!({"restricted": true, "store_ids": []})
    );
    assert_eq!(report["page_info"]["total"], 0);
    let (status, body) = json_as(
        &stack,
        &format!("/v1/kpi-reports/net-sales?{WINDOW}&store_id=1"),
        NO_STORES,
        "STORE_MANAGER",
    )
    .await;
    assert_eq!(
        (status, body["code"].as_str()),
        (403, Some("forbidden_store"))
    );

    // The assignment is read on each request: removing a store takes effect with
    // the next request, with no cache and no token to expire.
    let owner = connect(&stack.database).await;
    let path = format!("/v1/kpi-reports/net-sales?{WINDOW}&store_id=25");
    let (status, _) = json_as(&stack, &path, MANAGER, "STORE_MANAGER").await;
    assert_eq!(status, 200);
    owner
        .execute(
            "DELETE FROM users.store_assignment WHERE user_id = $1 AND store_id = 25",
            &[&Uuid::parse_str(MANAGER).unwrap()],
        )
        .await
        .unwrap();
    let (status, body) = json_as(&stack, &path, MANAGER, "STORE_MANAGER").await;
    assert_eq!(
        (status, body["code"].as_str()),
        (403, Some("forbidden_store"))
    );
    let (_, report) = json_as(
        &stack,
        &format!("/v1/kpi-reports/net-sales?{WINDOW}"),
        MANAGER,
        "STORE_MANAGER",
    )
    .await;
    assert_eq!(
        report["store_scope"]["store_ids"],
        serde_json::json!([1, 9, 17])
    );
    owner
        .execute(
            "INSERT INTO users.store_assignment (user_id, store_id) VALUES ($1, 25)",
            &[&Uuid::parse_str(MANAGER).unwrap()],
        )
        .await
        .unwrap();
    let (status, _) = json_as(&stack, &path, MANAGER, "STORE_MANAGER").await;
    assert_eq!(status, 200);

    fixture.remove().await;
    stack.finish().await;
}
