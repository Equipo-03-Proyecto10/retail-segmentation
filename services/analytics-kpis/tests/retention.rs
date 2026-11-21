//! Cohort retention through the whole router, on the seeded database as
//! `svc_kpis` (F26-03). Every figure is computed by hand from what the test wrote.
//!
//! A cohort is the customers whose first net purchase *ever* falls in a month;
//! retained is those with a net purchase in the next month, anywhere. D1 and D2
//! are LOYAL (seeded run 31, version 1), D3 CHAMPION (seeded run 30, version 2),
//! the others hold no label. Every sale is one unit of product 1 at 10.00.
//!
//! | customer | purchases (UTC)                                                    | origin   |
//! |----------|--------------------------------------------------------------------|----------|
//! | D1       | 2020-03-05 12:00 store 1 ch 1; 2020-04-10 12:00 store 2 ch 1       | OBSERVED |
//! | D2       | 2020-03-10 12:00 store 1 ch 2; 2020-04-30 23:59 store 1 ch 1       | OBSERVED |
//! | D3       | 2020-03-31 23:30 store 2 ch 1; 2020-03-31 23:45; 2020-05-01 00:00  | OBSERVED |
//! | D4       | 2020-02-15 store 1 ch 1; 2020-03-20; 2020-04-02                    | OBSERVED |
//! | D5       | 2020-03-12 store 1 ch 1, reversed in full                          | OBSERVED |
//! | D6       | 2020-03-02 store 2 ch 1; 2020-04-03 store 1 ch 1                   | SEEDED   |
//!
//! March's cohort is D1, D2, D3 and D6: 4 customers. D1, D2 (April 30, the last
//! minute) and D6 bought in April: 3 retained, 0.7500. D3 bought again only in
//! March itself and on May 1, neither the next month. D4's first purchase is in
//! February, so it is February's cohort and not March's. D5 has no net purchase.

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
const RETENTION: &str = "/v1/kpi-reports/retention";

struct Fixture {
    owner: Client,
    tag: String,
    customers: Vec<Uuid>,
}

impl Fixture {
    async fn record(database: &str) -> Self {
        let owner = connect(database).await;
        purge(&owner).await;
        let tag = tag();
        let mut customers = Vec::new();
        for _ in 0..6 {
            customers.push(customer(&owner).await);
        }
        let [d1, d2, d3, d4, d5, d6] = customers[..] else {
            unreachable!()
        };
        assign(&owner, d1, 31, "LOYAL").await;
        assign(&owner, d2, 31, "LOYAL").await;
        assign(&owner, d3, 30, "CHAMPION").await;
        let line = [Line {
            product: 1,
            quantity: 1,
            cents: 1000,
        }];
        for (who, store, channel, at, origin) in [
            (d1, 1, 1, "2020-03-05T12:00:00Z", "OBSERVED"),
            (d1, 2, 1, "2020-04-10T12:00:00Z", "OBSERVED"),
            (d2, 1, 2, "2020-03-10T12:00:00Z", "OBSERVED"),
            (d2, 1, 1, "2020-04-30T23:59:00Z", "OBSERVED"),
            (d3, 2, 1, "2020-03-31T23:30:00Z", "OBSERVED"),
            (d3, 2, 1, "2020-03-31T23:45:00Z", "OBSERVED"),
            (d3, 1, 1, "2020-05-01T00:00:00Z", "OBSERVED"),
            (d4, 1, 1, "2020-02-15T12:00:00Z", "OBSERVED"),
            (d4, 1, 1, "2020-03-20T12:00:00Z", "OBSERVED"),
            (d4, 1, 1, "2020-04-02T12:00:00Z", "OBSERVED"),
            (d6, 2, 1, "2020-03-02T12:00:00Z", "SEEDED"),
            (d6, 1, 1, "2020-04-03T12:00:00Z", "SEEDED"),
        ] {
            sale(
                &owner,
                &tag,
                Spec::new(who, store, channel, at, origin),
                &line,
            )
            .await;
        }
        let d5_sale = sale(
            &owner,
            &tag,
            Spec::new(d5, 1, 1, "2020-03-12T12:00:00Z", "OBSERVED"),
            &line,
        )
        .await;
        reverse(&owner, d5_sale, 1, 1).await;
        Self {
            owner,
            tag,
            customers,
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

/// `(cohort_customers, retained_customers, retention_rate)` of a row.
fn figures(row: &Value) -> (String, Option<String>, Option<String>) {
    (
        measure_value(row, "cohort_customers").unwrap(),
        measure_value(row, "retained_customers"),
        measure_value(row, "retention_rate"),
    )
}

fn expect(row: &Value, cohort: &str, retained: &str, rate: &str) {
    assert_eq!(
        figures(row),
        (
            cohort.to_owned(),
            Some(retained.to_owned()),
            Some(rate.to_owned())
        ),
        "{row}"
    );
}

#[tokio::test]
async fn a_cohort_is_the_customers_whose_first_net_purchase_falls_in_the_bucket() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let fixture = Fixture::record(&stack.database).await;

    // March: D1, D2, D3, D6 first bought in March, D1, D2 and D6 again in April.
    let (status, report) = json(&stack, &format!("{RETENTION}?{WINDOW}")).await;
    assert_eq!(status, 200, "{report}");
    assert_eq!(report["kpi"], "retention");
    assert_eq!(report["definition_version"], 1);
    // Rows are always cohorts: period is a dimension though nobody asked.
    assert_eq!(report["group_by"], json!(["period"]));
    assert_eq!(report["granularity"], "month");
    let rows = report["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 1, "{report}");
    assert_eq!(rows[0]["period_start"], "2020-03-01");
    assert_eq!(rows[0]["period_end"], "2020-03-31");
    expect(&rows[0], "4", "3", "0.7500");
    let measures: Vec<(&str, &str, &str)> = rows[0]["measures"]
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
            ("cohort_customers", "OBSERVED", "count"),
            ("retained_customers", "OBSERVED", "count"),
            ("retention_rate", "OBSERVED", "ratio"),
        ]
    );
    assert_eq!(rows[0]["synthetic"], true, "D6's sales are SEEDED");
    assert_eq!(
        report["data_origin"]["views"],
        json!(["api_sales.net_purchase_line_v1"])
    );
    assert_eq!(report["data_origin"]["spans_model_versions"], false);

    // The first purchase is read over everything: D4's is in February, so a
    // window of both months has two cohorts and D4 is February's alone.
    let (_, report) = json(
        &stack,
        &format!("{RETENTION}?from=2020-02-01&to=2020-03-31"),
    )
    .await;
    let rows = report["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 2, "{report}");
    assert_eq!(rows[0]["period_start"], "2020-02-01");
    expect(&rows[0], "1", "1", "1.0000");
    expect(&rows[1], "4", "3", "0.7500");

    // Store and channel are those of the first purchase; the retained purchase
    // may be anywhere (D1 came back to store 2).
    let (_, report) = json(&stack, &format!("{RETENTION}?{WINDOW}&group_by=store")).await;
    assert_eq!(report["group_by"], json!(["period", "store"]));
    let rows = report["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 2, "{report}");
    assert_eq!(rows[0]["store_id"], 1);
    expect(&rows[0], "2", "2", "1.0000");
    assert_eq!(rows[1]["store_id"], 2);
    expect(&rows[1], "2", "1", "0.5000");
    let (_, report) = json(&stack, &format!("{RETENTION}?{WINDOW}&group_by=channel")).await;
    let rows = report["rows"].as_array().unwrap();
    assert_eq!(rows[0]["channel_id"], 1);
    expect(&rows[0], "3", "2", "0.6667");
    assert_eq!(rows[1]["channel_id"], 2);
    expect(&rows[1], "1", "1", "1.0000");
    let (_, report) = json(
        &stack,
        &format!("{RETENTION}?{WINDOW}&store_id=2&channel_id=1"),
    )
    .await;
    expect(&report["rows"][0], "2", "1", "0.5000");

    // By segment, with the labels' two model versions named (ADR-0064).
    let (_, report) = json(&stack, &format!("{RETENTION}?{WINDOW}&group_by=segment")).await;
    let rows = report["rows"].as_array().unwrap();
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
    expect(&rows[0], "1", "0", "0.0000");
    expect(&rows[1], "2", "2", "1.0000");
    expect(&rows[2], "1", "1", "1.0000");
    assert_eq!(report["data_origin"]["spans_model_versions"], true);
    assert_eq!(report["data_origin"]["model_version_ids"], json!([1, 2]));
    assert_eq!(
        report["data_origin"]["views"],
        json!([
            "api_sales.net_purchase_line_v1",
            "api_segments.current_label_v1",
            "api_models.run_version_v1"
        ])
    );

    // Weekly cohorts, from Monday: D6 and D1 first bought in the week of
    // March 2, D2 in March 9 and D3 in March 30; nobody bought in the week after.
    let (_, report) = json(&stack, &format!("{RETENTION}?{WINDOW}&granularity=week")).await;
    let rows = report["rows"].as_array().unwrap();
    let weeks: Vec<(&str, &str)> = rows
        .iter()
        .map(|r| {
            (
                r["period_start"].as_str().unwrap(),
                r["period_end"].as_str().unwrap(),
            )
        })
        .collect();
    assert_eq!(
        weeks,
        [
            ("2020-03-02", "2020-03-08"),
            ("2020-03-09", "2020-03-15"),
            ("2020-03-30", "2020-04-05"),
        ]
    );
    expect(&rows[0], "2", "0", "0.0000");
    expect(&rows[1], "1", "0", "0.0000");
    expect(&rows[2], "1", "0", "0.0000");

    fixture.remove().await;
    stack.finish().await;
}

#[tokio::test]
async fn a_cohort_whose_next_bucket_has_not_ended_reports_no_retained_count() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    purge(&owner).await;
    let tag = tag();
    let buyer = customer(&owner).await;
    let (now,): (String,) = {
        let row = owner
            .query_one("SELECT (now() - interval '1 hour')::text", &[])
            .await
            .unwrap();
        (row.get(0),)
    };
    sale(
        &owner,
        &tag,
        Spec::new(buyer, 1, 1, &now.replace(' ', "T"), "OBSERVED"),
        &[Line {
            product: 1,
            quantity: 1,
            cents: 1000,
        }],
    )
    .await;
    let today: String = owner
        .query_one("SELECT (now() AT TIME ZONE 'UTC')::date::text", &[])
        .await
        .unwrap()
        .get(0);
    let yesterday: String = owner
        .query_one("SELECT ((now() AT TIME ZONE 'UTC')::date - 1)::text", &[])
        .await
        .unwrap()
        .get(0);

    // The buyer's bucket is this month (or last, in the first hour of a month):
    // its next bucket is still running, so no retained count and no rate, and the
    // cohort is not read as lost.
    let (status, report) = json(
        &stack,
        &format!("{RETENTION}?from={yesterday}&to={today}&granularity=month"),
    )
    .await;
    assert_eq!(status, 200, "{report}");
    let rows = report["rows"].as_array().unwrap();
    assert!(!rows.is_empty(), "{report}");
    for row in rows {
        assert!(measure_value(row, "cohort_customers").is_some(), "{row}");
        assert_eq!(measure_value(row, "retained_customers"), None, "{row}");
        assert_eq!(measure_value(row, "retention_rate"), None, "{row}");
    }
    let (_, xml) = {
        let token = stack.token("ADMIN");
        send(
            &stack.app,
            "GET",
            &format!("{RETENTION}?from={yesterday}&to={today}"),
            Some(&token),
            "application/xml",
        )
        .await
    };
    assert_valid_xml(&xml);
    assert!(!xml.contains("retention_rate"), "{xml}");

    remove_sales(&owner, &tag).await;
    remove_customer(&owner, buyer).await;
    stack.finish().await;
}

#[tokio::test]
async fn retention_is_store_scoped_validated_and_closes_the_role_matrix() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let fixture = Fixture::record(&stack.database).await;
    let manager = stack.token_for(MANAGER, "STORE_MANAGER");

    // The manager has store 1: the cohorts whose first purchase was there, D1
    // and D2, both retained. D3 and D6 first bought in store 2.
    let (status, report) = json_as(&stack, &format!("{RETENTION}?{WINDOW}"), &manager).await;
    assert_eq!(status, 200, "{report}");
    expect(&report["rows"][0], "2", "2", "1.0000");
    assert_eq!(report["store_scope"]["restricted"], true);
    let (status, report) = json_as(
        &stack,
        &format!("{RETENTION}?{WINDOW}&store_id=2"),
        &manager,
    )
    .await;
    assert_eq!(
        (status, report["code"].as_str()),
        (403, Some("forbidden_store"))
    );

    let admin = stack.token("ADMIN");
    for (query, field, code) in [
        ("from=2020-03-01", "to", "required"),
        (
            "from=2020-03-01&to=2020-03-31&granularity=year",
            "granularity",
            "enum",
        ),
        (
            "from=2020-03-01&to=2020-03-31&group_by=period,period",
            "group_by",
            "invalid_format",
        ),
    ] {
        let (status, body) = json_as(&stack, &format!("{RETENTION}?{query}"), &admin).await;
        assert_eq!(status, 422, "{query}: {body}");
        assert_eq!(body["details"][0]["field"], field, "{body}");
        assert_eq!(body["details"][0]["code"], code, "{body}");
    }

    let (_, xml) = send(
        &stack.app,
        "GET",
        &format!("{RETENTION}?{WINDOW}&group_by=segment,store"),
        Some(&admin),
        "application/xml",
    )
    .await;
    assert_valid_xml(&xml);

    for role in ROLES {
        let token = if role == "STORE_MANAGER" {
            manager.clone()
        } else {
            stack.token(role)
        };
        let (status, _) = json_as(&stack, &format!("{RETENTION}?{WINDOW}"), &token).await;
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
