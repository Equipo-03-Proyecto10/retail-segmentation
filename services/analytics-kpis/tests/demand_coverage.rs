//! Demand by segment beside stock, through the whole router, on the seeded
//! database as `svc_kpis` (F26-05).
//!
//! The seed stocks products 1 to 5 in every store, one category each, with a
//! known quantity; product 31 shares category 1 and has no stock record, and
//! products 6 to 30 are never stocked. The sales the tests record sit in March
//! 2020 (a window of 28 days, 1 to 28 March) and are computed by hand:
//!
//! | sale | store | buyer | at (UTC)            | origin   | lines            | reversed |
//! |------|-------|-------|---------------------|----------|------------------|----------|
//! | S1   | 1     | A     | 2020-03-05          | OBSERVED | p1 x 6           |          |
//! | S2   | 1     | L     | 2020-03-10          | SEEDED   | p1 x 4, p31 x 2  |          |
//! | S3   | 1     | A     | 2020-03-12          | OBSERVED | p1 x 3           | 1 unit   |
//! | S4   | 1     | A     | 2020-03-15          | OBSERVED | p3 x 5           | all      |
//! | S5   | 1     | A     | 2020-03-20          | OBSERVED | p4 x 1           |          |
//! | S6   | 1     | A     | 2020-03-29 00:00:00 | OBSERVED | p5 x 7           | outside  |
//! | S7   | 1     | A     | 2020-02-29 23:59:59 | OBSERVED | p5 x 8           | outside  |
//! | S8   | 1     | A     | 2020-03-28 23:59:59 | OBSERVED | p5 x 9           |          |
//! | S9   | 1     | A     | 2020-03-01 00:00:00 | OBSERVED | p6 x 42          |          |
//! | S10  | 2     | A     | 2020-03-05          | OBSERVED | p1 x 100         | other store |
//!
//! A is a new customer (no open assignment); L holds a label in the seed.

mod support;

use std::collections::BTreeSet;

use serde_json::Value;
use support::http::{assert_valid_error_xml, assert_valid_xml, send, Stack, ROLES, TURN};
use support::{
    connect, customer, purge, remove_customer, remove_sales, reverse, sale, tag, Line, Spec,
    MANAGER, NO_STORES,
};
use tokio_postgres::Client;
use uuid::Uuid;

/// (query, token, status, error code, the detail's field and code when it has one)
type Case<'a> = (
    String,
    Option<&'a str>,
    u16,
    &'a str,
    Option<(&'a str, &'a str)>,
);

const WINDOW: &str = "from=2020-03-01&to=2020-03-28";
const PATH: &str = "/v1/kpi-reports/demand-coverage";
const READERS: [&str; 5] = [
    "ADMIN",
    "ANALYST",
    "INVENTORY_PLANNER",
    "AUDITOR",
    "STORE_MANAGER",
];

struct Fixture {
    owner: Client,
    tag: String,
    a: Uuid,
    l: Uuid,
    label: String,
}

fn line(product: i32, quantity: i32) -> Line {
    Line {
        product,
        quantity,
        cents: 1000,
    }
}

impl Fixture {
    async fn record(database: &str) -> Self {
        let owner = connect(database).await;
        purge(&owner).await;
        let tag = tag();
        let a = customer(&owner).await;
        let labelled = owner
            .query_one(
                "SELECT customer_id, label_code FROM api_segments.current_label_v1 \
                 WHERE state = 'assigned' ORDER BY customer_id LIMIT 1",
                &[],
            )
            .await
            .unwrap();
        let l: Uuid = labelled.get(0);
        let label: String = labelled.get(1);
        let at = |day: &'static str, origin: &'static str, who: Uuid, store: i16| {
            Spec::new(who, store, 1, day, origin)
        };
        sale(
            &owner,
            &tag,
            at("2020-03-05T12:00:00Z", "OBSERVED", a, 1),
            &[line(1, 6)],
        )
        .await;
        sale(
            &owner,
            &tag,
            at("2020-03-10T12:00:00Z", "SEEDED", l, 1),
            &[line(1, 4), line(31, 2)],
        )
        .await;
        let s3 = sale(
            &owner,
            &tag,
            at("2020-03-12T12:00:00Z", "OBSERVED", a, 1),
            &[line(1, 3)],
        )
        .await;
        reverse(&owner, s3, 1, 1).await;
        let s4 = sale(
            &owner,
            &tag,
            at("2020-03-15T12:00:00Z", "OBSERVED", a, 1),
            &[line(3, 5)],
        )
        .await;
        reverse(&owner, s4, 3, 5).await;
        sale(
            &owner,
            &tag,
            at("2020-03-20T12:00:00Z", "OBSERVED", a, 1),
            &[line(4, 1)],
        )
        .await;
        sale(
            &owner,
            &tag,
            at("2020-03-29T00:00:00Z", "OBSERVED", a, 1),
            &[line(5, 7)],
        )
        .await;
        sale(
            &owner,
            &tag,
            at("2020-02-29T23:59:59Z", "OBSERVED", a, 1),
            &[line(5, 8)],
        )
        .await;
        sale(
            &owner,
            &tag,
            at("2020-03-28T23:59:59Z", "OBSERVED", a, 1),
            &[line(5, 9)],
        )
        .await;
        sale(
            &owner,
            &tag,
            at("2020-03-01T00:00:00Z", "OBSERVED", a, 1),
            &[line(6, 42)],
        )
        .await;
        sale(
            &owner,
            &tag,
            at("2020-03-05T12:00:00Z", "OBSERVED", a, 2),
            &[line(1, 100)],
        )
        .await;
        Self {
            owner,
            tag,
            a,
            l,
            label,
        }
    }

    /// Retire or restore product 5, as the products boundary's `is_active`
    /// reads. The triggers that mirror the change are off for this one fixture
    /// transaction: the view reads the column.
    async fn set_active(&self, active: bool) {
        self.owner.batch_execute("BEGIN").await.unwrap();
        self.owner
            .batch_execute("SET LOCAL session_replication_role = replica")
            .await
            .unwrap();
        self.owner
            .execute(
                "UPDATE public.product SET is_active = $1 WHERE product_id = 5",
                &[&active],
            )
            .await
            .unwrap();
        self.owner.batch_execute("COMMIT").await.unwrap();
    }

    async fn remove(self) {
        self.set_active(true).await;
        remove_sales(&self.owner, &self.tag).await;
        remove_customer(&self.owner, self.a).await;
        let _ = self.l;
    }
}

async fn read(stack: &Stack, query: &str, role: &str) -> (u16, Value) {
    let token = stack.token(role);
    let (status, body) = send(
        &stack.app,
        "GET",
        &format!("{PATH}?{query}"),
        Some(&token),
        "application/json",
    )
    .await;
    (
        status.as_u16(),
        serde_json::from_str(&body).unwrap_or(Value::Null),
    )
}

fn rows(report: &Value) -> Vec<&Value> {
    report["rows"].as_array().unwrap().iter().collect()
}

fn numbers(row: &Value) -> (i64, &str, i64, Option<&str>, i64) {
    (
        row["net_units"].as_i64().unwrap(),
        row["daily_demand"].as_str().unwrap(),
        row["on_hand"].as_i64().unwrap(),
        row["days_of_cover"].as_str(),
        row["replenishment_units"].as_i64().unwrap(),
    )
}

#[tokio::test]
async fn demand_stock_cover_and_need_are_computed_as_stated() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let fixture = Fixture::record(&stack.database).await;

    let (status, report) = read(&stack, &format!("{WINDOW}&store_id=1"), "INVENTORY_PLANNER").await;
    assert_eq!(status, 200, "{report}");
    assert_eq!(report["from"], "2020-03-01");
    assert_eq!(report["to"], "2020-03-28");
    assert_eq!(report["window_days"], 28);
    assert_eq!(report["target_cover_days"], 14);
    assert!(report["calculation"].as_str().unwrap().contains(
        "replenishment_units = max(0, ceil(net_units * target_cover_days / window_days) - on_hand)"
    ));
    assert_eq!(
        report["store_scope"],
        serde_json::json!({"restricted": false, "store_ids": []})
    );
    let rows = rows(&report);
    let categories: Vec<i64> = rows
        .iter()
        .map(|row| row["category_id"].as_i64().unwrap())
        .collect();
    assert_eq!(
        categories,
        [1, 2, 3, 4, 5, 6],
        "ordered by category: {report}"
    );
    assert!(rows.iter().all(|row| row["store_id"] == 1));

    // Category 1: 6 + 4 + 2 (product 31) + (3 - 1) = 14 net units over 28 days.
    // On hand is product 1's 12; product 31 has no stock record and adds none.
    assert_eq!(numbers(rows[0]), (14, "0.5000", 12, Some("24.00"), 0));
    assert_eq!(rows[0]["synthetic"], true, "S2 is SEEDED");
    // The buyer holds no label: reported as it is, with no code, and first
    // because its 8 units outnumber L's 6. Shares are of the 14.
    assert_eq!(
        rows[0]["segments"],
        serde_json::json!([
            {"segment_code": null, "segment_state": "no_open_assignment", "net_units": 8, "share": "0.5714"},
            {"segment_code": fixture.label, "segment_state": "assigned", "net_units": 6, "share": "0.4286"},
        ])
    );
    // No demand: stock only, no days of cover, no segments.
    assert_eq!(numbers(rows[1]), (0, "0.0000", 13, None, 0));
    assert_eq!(rows[1]["segments"], serde_json::json!([]));
    assert_eq!(rows[1]["synthetic"], false);
    // A sale reversed in full is not demand.
    assert_eq!(numbers(rows[2]), (0, "0.0000", 14, None, 0));
    // One unit: 1 / 28 = 0.0357 a day; days of cover follow from that figure,
    // 15 / 0.0357 = 420.17 (not the 420.00 of the unrounded demand).
    assert_eq!(numbers(rows[3]), (1, "0.0357", 15, Some("420.17"), 0));
    // The sale of 9 in the last second counts, those a second outside do not.
    assert_eq!(numbers(rows[4]), (9, "0.3214", 16, Some("49.78"), 0));
    // Demand and no stock record at all: zero cover, and the need is the
    // target: ceil(42 * 14 / 28) = 21.
    assert_eq!(numbers(rows[5]), (42, "1.5000", 0, Some("0.00"), 21));
    assert_eq!(rows[5]["segments"][0]["share"], "1.0000");
    assert_eq!(report["data_origin"]["synthetic"], true);
    assert_eq!(
        report["data_origin"]["views"],
        serde_json::json!([
            "api_products.product_ref_v1",
            "api_sales.net_purchase_line_v1",
            "api_segments.current_label_v1",
            "api_stock.stock_level_v1"
        ])
    );
    assert_eq!(
        report["page_info"],
        serde_json::json!({"page": 1, "size": 20, "total": 6})
    );

    // Segment units add up to the row's units, in every row.
    for row in &rows {
        let sum: i64 = row["segments"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| s["net_units"].as_i64().unwrap())
            .sum();
        assert_eq!(sum, row["net_units"].as_i64().unwrap());
    }

    // The need follows the target and is computed from the unrounded demand:
    // ceil(14 * 60 / 28) = 30 less 12 on hand; ceil(1 * 60 / 28) = 3 is covered
    // by 15; ceil(9 * 60 / 28) = 20 less 16; ceil(42 * 60 / 28) = 90 less none.
    let (_, report) = read(
        &stack,
        &format!("{WINDOW}&store_id=1&target_cover_days=60"),
        "ADMIN",
    )
    .await;
    let rows = rows_of(&report);
    assert_eq!(report["target_cover_days"], 60);
    let needs: Vec<i64> = rows
        .iter()
        .map(|row| row["replenishment_units"].as_i64().unwrap())
        .collect();
    assert_eq!(needs, [18, 0, 0, 0, 4, 90]);

    fixture.remove().await;
    stack.finish().await;
}

fn rows_of(report: &Value) -> Vec<&Value> {
    rows(report)
}

#[tokio::test]
async fn only_active_products_count_and_filters_keep_what_they_name() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let fixture = Fixture::record(&stack.database).await;

    // A retired product is not replenished: its demand and its stock are both
    // left out, and category 5 has nothing else.
    fixture.set_active(false).await;
    let (_, report) = read(&stack, &format!("{WINDOW}&store_id=1"), "ANALYST").await;
    let categories: Vec<i64> = rows_of(&report)
        .iter()
        .map(|row| row["category_id"].as_i64().unwrap())
        .collect();
    assert_eq!(categories, [1, 2, 3, 4, 6], "{report}");
    fixture.set_active(true).await;

    // One category, and only that one (its subcategories are not included).
    let (_, report) = read(
        &stack,
        &format!("{WINDOW}&store_id=1&category_id=1"),
        "AUDITOR",
    )
    .await;
    assert_eq!(rows_of(&report).len(), 1);
    assert_eq!(
        numbers(rows_of(&report)[0]),
        (14, "0.5000", 12, Some("24.00"), 0)
    );
    assert_eq!(report["page_info"]["total"], 1);

    // Another store is another figure: store 2 holds only S10's 100 units.
    let (_, report) = read(
        &stack,
        &format!("{WINDOW}&store_id=2&category_id=1"),
        "ADMIN",
    )
    .await;
    assert_eq!(
        numbers(rows_of(&report)[0]),
        (100, "3.5714", 13, Some("3.64"), 37)
    );

    // A window with no sale still lists the stock: nothing is demanded.
    let (_, report) = read(
        &stack,
        "from=2020-05-01&to=2020-05-31&store_id=1&category_id=2",
        "ADMIN",
    )
    .await;
    assert_eq!(numbers(rows_of(&report)[0]), (0, "0.0000", 13, None, 0));

    // A category with neither demand nor stock has no row.
    let (_, report) = read(
        &stack,
        &format!("{WINDOW}&store_id=1&category_id=20"),
        "ADMIN",
    )
    .await;
    assert_eq!(report["rows"], serde_json::json!([]));
    assert_eq!(report["page_info"]["total"], 0);

    // Pages are of rows ordered by store, then category.
    let (_, first) = read(&stack, &format!("{WINDOW}&store_id=1&size=4"), "ADMIN").await;
    assert_eq!(
        first["page_info"],
        serde_json::json!({"page": 1, "size": 4, "total": 6})
    );
    assert_eq!(first["rows"].as_array().unwrap().len(), 4);
    let (_, second) = read(
        &stack,
        &format!("{WINDOW}&store_id=1&size=4&page=2"),
        "ADMIN",
    )
    .await;
    let second: Vec<i64> = rows_of(&second)
        .iter()
        .map(|row| row["category_id"].as_i64().unwrap())
        .collect();
    assert_eq!(second, [5, 6]);
    let (_, past) = read(
        &stack,
        &format!("{WINDOW}&store_id=1&size=4&page=9"),
        "ADMIN",
    )
    .await;
    assert_eq!(past["rows"], serde_json::json!([]));
    assert_eq!(past["page_info"]["total"], 6);
    assert_eq!(
        past["data_origin"]["synthetic"], true,
        "that of the report, not of the page"
    );

    fixture.remove().await;
    stack.finish().await;
}

#[tokio::test]
async fn a_planner_reads_every_store_and_a_store_manager_its_own() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let fixture = Fixture::record(&stack.database).await;

    // The planner is not confined: with no store named, every stock row of the
    // seed (30 stores x 5 categories) and the demand of two stores, in pages of
    // at most 100 rows ordered by store, then category.
    let (status, first) = read(&stack, &format!("{WINDOW}&size=100"), "INVENTORY_PLANNER").await;
    assert_eq!(status, 200, "{first}");
    assert_eq!(
        first["store_scope"],
        serde_json::json!({"restricted": false, "store_ids": []})
    );
    assert_eq!(
        first["page_info"]["total"], 151,
        "30 stores x 5 stocked categories, and category 6 in store 1"
    );
    let (_, second) = read(
        &stack,
        &format!("{WINDOW}&size=100&page=2"),
        "INVENTORY_PLANNER",
    )
    .await;
    let ordered: Vec<(i64, i64)> = rows_of(&first)
        .into_iter()
        .chain(rows_of(&second))
        .map(|row| {
            (
                row["store_id"].as_i64().unwrap(),
                row["category_id"].as_i64().unwrap(),
            )
        })
        .collect();
    assert_eq!(ordered.len(), 151);
    let mut sorted = ordered.clone();
    sorted.sort_unstable();
    assert_eq!(ordered, sorted);
    assert!(
        ordered.contains(&(1, 6)) && ordered.last() == Some(&(30, 5)),
        "{ordered:?}"
    );

    // The store manager of stores 1, 9, 17 and 25 reads those, never another.
    let token = stack.token_for(MANAGER, "STORE_MANAGER");
    let get = |query: String| {
        let token = token.clone();
        let app = stack.app.clone();
        async move {
            let (status, body) = send(
                &app,
                "GET",
                &format!("{PATH}?{query}"),
                Some(&token),
                "application/json",
            )
            .await;
            (
                status.as_u16(),
                serde_json::from_str::<Value>(&body).unwrap_or(Value::Null),
            )
        }
    };
    let (status, report) = get(format!("{WINDOW}&size=100")).await;
    assert_eq!(status, 200, "{report}");
    assert_eq!(
        report["store_scope"],
        serde_json::json!({"restricted": true, "store_ids": [1, 9, 17, 25]})
    );
    let stores: BTreeSet<i64> = rows_of(&report)
        .iter()
        .map(|row| row["store_id"].as_i64().unwrap())
        .collect();
    assert_eq!(stores, BTreeSet::from([1, 9, 17, 25]));
    assert_eq!(
        report["data_origin"]["views"]
            .as_array()
            .unwrap()
            .last()
            .unwrap(),
        "api_users.store_assignment_v1"
    );
    assert_eq!(
        numbers(rows_of(&report)[0]),
        (14, "0.5000", 12, Some("24.00"), 0)
    );
    let (status, body) = get(format!("{WINDOW}&store_id=2")).await;
    assert_eq!(
        (status, body["code"].as_str()),
        (403, Some("forbidden_store"))
    );
    assert_eq!(
        body["details"],
        serde_json::json!([{"field": "store_id", "code": "not_assigned"}])
    );
    let (status, report) = get(format!("{WINDOW}&store_id=9")).await;
    assert_eq!(status, 200);
    assert_eq!(report["store_scope"]["store_ids"], serde_json::json!([9]));
    assert!(rows_of(&report).iter().all(|row| row["store_id"] == 9));

    // A store manager with no assigned store reads nothing.
    let token = stack.token_for(NO_STORES, "STORE_MANAGER");
    let (status, body) = send(
        &stack.app,
        "GET",
        &format!("{PATH}?{WINDOW}"),
        Some(&token),
        "application/json",
    )
    .await;
    assert_eq!(status, 200, "{body}");
    let report: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(report["rows"], serde_json::json!([]));
    assert_eq!(
        report["store_scope"],
        serde_json::json!({"restricted": true, "store_ids": []})
    );

    fixture.remove().await;
    stack.finish().await;
}

#[tokio::test]
async fn no_customer_reaches_a_planner_in_json_or_xml_and_the_xml_validates() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let fixture = Fixture::record(&stack.database).await;
    let token = stack.token("INVENTORY_PLANNER");
    let path = format!("{PATH}?{WINDOW}&store_id=1");

    let (status, json) = send(&stack.app, "GET", &path, Some(&token), "application/json").await;
    assert_eq!(status, 200, "{json}");
    let (status, xml) = send(&stack.app, "GET", &path, Some(&token), "application/xml").await;
    assert_eq!(status, 200, "{xml}");
    assert_valid_xml(&xml);
    assert!(
        xml.contains("<common:PageInfo>") && !xml.contains("<page_info>"),
        "{xml}"
    );
    for body in [&json, &xml] {
        for customer in [fixture.a.to_string(), fixture.l.to_string()] {
            assert!(
                !body.contains(&customer),
                "a customer id is in the response"
            );
        }
        assert!(!body.to_lowercase().contains("customer"), "{body}");
        for personal in ["email", "phone", "name\""] {
            assert!(!body.contains(personal), "{personal}");
        }
    }
    // The shape is the contract's example, member for member.
    let example: Value = serde_json::from_str(
        &std::fs::read_to_string("contracts/analytics-kpis/examples/demand-coverage.json").unwrap(),
    )
    .unwrap();
    let report: Value = serde_json::from_str(&json).unwrap();
    let keys = |value: &Value| -> BTreeSet<String> {
        value.as_object().unwrap().keys().cloned().collect()
    };
    assert_eq!(keys(&report), keys(&example));
    assert_eq!(keys(&report["rows"][0]), keys(&example["rows"][0]));
    assert_eq!(
        keys(&report["rows"][0]["segments"][0]),
        keys(&example["rows"][0]["segments"][0])
    );
    assert_eq!(
        report["calculation"], example["calculation"],
        "the calculation is stated as the contract states it"
    );
    // A null is omitted from the XML.
    assert!(
        xml.contains("<segment_state>no_open_assignment</segment_state>"),
        "{xml}"
    );
    assert!(!xml.contains("<segment_code>null"), "{xml}");
    // Stock-only rows (no demand) have no days_of_cover element.
    assert!(
        xml.matches("<days_of_cover>").count() < xml.matches("<rows>").count(),
        "{xml}"
    );

    // An empty page validates too.
    let (_, xml) = send(
        &stack.app,
        "GET",
        &format!("{PATH}?{WINDOW}&store_id=1&category_id=20"),
        Some(&token),
        "application/xml",
    )
    .await;
    assert_valid_xml(&xml);

    fixture.remove().await;
    stack.finish().await;
}

#[tokio::test]
async fn every_refusal_of_the_operation_in_json_and_xml() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let manager = stack.token_for(MANAGER, "STORE_MANAGER");
    let planner = stack.token("INVENTORY_PLANNER");
    let today: String = {
        let owner = connect(&stack.database).await;
        owner
            .query_one("SELECT (now() AT TIME ZONE 'UTC')::date::text", &[])
            .await
            .unwrap()
            .get(0)
    };
    let yesterday: String = {
        let owner = connect(&stack.database).await;
        owner
            .query_one("SELECT ((now() AT TIME ZONE 'UTC')::date - 1)::text", &[])
            .await
            .unwrap()
            .get(0)
    };

    // The roles: five read it; the others, MARKETING and COMMERCIAL_SUPERVISOR
    // among them, read no demand-coverage.
    for role in ROLES {
        let token = stack.token(role);
        let (status, body) = send(
            &stack.app,
            "GET",
            &format!("{PATH}?{WINDOW}&category_id=1&size=1"),
            Some(&token),
            "application/json",
        )
        .await;
        if READERS.contains(&role) {
            assert_eq!(status, 200, "{role}: {body}");
        } else {
            assert_eq!(status, 403, "{role}");
            assert!(body.contains("\"forbidden_role\""), "{role}: {body}");
        }
    }

    // (query, token, status, code, field and detail code)
    let cases: Vec<Case> = vec![
        (WINDOW.into(), None, 401, "invalid_token", None),
        (
            format!("{WINDOW}&store_id=2"),
            Some(&manager),
            403,
            "forbidden_store",
            Some(("store_id", "not_assigned")),
        ),
        (
            "to=2020-03-28".into(),
            Some(&planner),
            422,
            "invalid_query",
            Some(("from", "required")),
        ),
        (
            "from=2020-03-01".into(),
            Some(&planner),
            422,
            "invalid_query",
            Some(("to", "required")),
        ),
        (
            "from=2020-3-1&to=2020-03-28".into(),
            Some(&planner),
            422,
            "invalid_query",
            Some(("from", "invalid_format")),
        ),
        (
            "from=2020-03-28&to=2020-03-01".into(),
            Some(&planner),
            422,
            "invalid_query",
            Some(("to", "to_before_from")),
        ),
        (
            "from=2020-01-01&to=2022-01-01".into(),
            Some(&planner),
            422,
            "invalid_query",
            Some(("to", "window_too_long")),
        ),
        (
            format!("from={yesterday}&to={today}"),
            Some(&planner),
            422,
            "invalid_query",
            Some(("to", "window_not_ended")),
        ),
        (
            "from=2099-01-01&to=2099-01-31".into(),
            Some(&planner),
            422,
            "invalid_query",
            Some(("to", "window_not_ended")),
        ),
        (
            format!("{WINDOW}&target_cover_days=0"),
            Some(&planner),
            422,
            "invalid_query",
            Some(("target_cover_days", "out_of_range")),
        ),
        (
            format!("{WINDOW}&target_cover_days=181"),
            Some(&planner),
            422,
            "invalid_query",
            Some(("target_cover_days", "out_of_range")),
        ),
        (
            format!("{WINDOW}&target_cover_days=abc"),
            Some(&planner),
            422,
            "invalid_query",
            Some(("target_cover_days", "invalid_format")),
        ),
        (
            format!("{WINDOW}&store_id=0"),
            Some(&planner),
            422,
            "invalid_query",
            Some(("store_id", "invalid_format")),
        ),
        (
            format!("{WINDOW}&category_id=x"),
            Some(&planner),
            422,
            "invalid_query",
            Some(("category_id", "invalid_format")),
        ),
        (
            format!("{WINDOW}&page=0"),
            Some(&planner),
            422,
            "invalid_pagination",
            None,
        ),
        (
            format!("{WINDOW}&size=101"),
            Some(&planner),
            422,
            "invalid_pagination",
            None,
        ),
    ];
    for (query, token, status, code, detail) in &cases {
        for accept in ["application/json", "application/xml"] {
            let (got, body) = support::http::send_with(
                &stack.app,
                "GET",
                &format!("{PATH}?{query}"),
                *token,
                accept,
                Some("corr-demand"),
            )
            .await;
            assert_eq!(got, *status, "{query} {accept}: {body}");
            assert!(body.contains(code), "{query} {accept}: {body}");
            if let Some((field, detail_code)) = detail {
                assert!(
                    body.contains(field) && body.contains(detail_code),
                    "{query} {accept}: {body}"
                );
            }
            if accept == "application/xml" {
                assert_valid_error_xml(&body);
            }
        }
    }

    // The window must have ended: yesterday does, today does not.
    let (status, report) = read(
        &stack,
        &format!("from={yesterday}&to={yesterday}&store_id=1&category_id=1"),
        "ADMIN",
    )
    .await;
    assert_eq!(status, 200, "{report}");
    assert_eq!(report["window_days"], 1);
    // 731 days exactly is allowed.
    let (status, _) = read(
        &stack,
        "from=2020-01-01&to=2021-12-31&store_id=1&category_id=1",
        "ADMIN",
    )
    .await;
    assert_eq!(status, 200);
    stack.finish().await;
}
