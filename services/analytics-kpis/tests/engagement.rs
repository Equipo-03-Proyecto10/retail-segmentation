//! Conversion after exposure and recommendation performance through the whole
//! router, on the seeded database as `svc_kpis` (F26-04, #849).
//!
//! March 2020 holds nothing the seed or another test writes. Every figure below is
//! computed by hand from these events (store, product and instant), where a click
//! or an impression is a row of `api_recommendations.interaction_v1` and a purchase
//! a sale line:
//!
//! | customer | exposure                                   | click                      | purchase                           |
//! |----------|--------------------------------------------|----------------------------|------------------------------------|
//! | c1       | impression, store 1, product 1, 03-02 10:00 | store 1, product 1, 03-02  | product 1, store 9, 03-05          |
//! | c2       | impression, store 1, 03-03 10:00            |                            | product 1, store 1, 03-12 (9 days) |
//! | c3       | impression, store 2, 03-04 10:00            | store 2, product 2, 03-04  | product 1, store 2, 03-04 12:00    |
//! | c4       | impressions, store 1, 03-06 and 03-07       |                            |                                    |
//! | c5       | campaign exposure, 03-10                    |                            | product 1, store 1, 03-11          |
//! | c8       |                                             | store 2, product 1, 03-08  | product 1, store 2, 03-09          |
//! | anon     | impression, store 1, 03-02, no customer     |                            |                                    |
//!
//! So conversion in March is 5 exposed and 3 converted (c1, c3, c5); by store it is
//! store 1: 3 exposed and 1 converted (a campaign exposure has no store, so c5 is
//! left out), store 2: 1 and 1. Recommendation performance is 6 impressions, 3 clicks
//! and 2 purchases (c1's, bought in store 9, and c8's; c3 clicked product 2 and bought
//! product 1, so the click earns nothing).

mod support;

use serde_json::Value;
use support::http::{assert_valid_xml, send, Stack, NET_SALES_ROLES, ROLES, TURN};
use support::{connect, customer, purge, sale, tag, Line, Spec, MANAGER};
use tokio_postgres::Client;
use uuid::Uuid;

const CONVERSION: &str = "/v1/kpi-reports/conversion";
const RECOMMENDATION: &str = "/v1/kpi-reports/recommendation-performance";
const MARCH: &str = "from=2020-03-01&to=2020-03-31";
/// Ids far above anything the seed or another test writes.
const CAMPAIGN: i32 = 849_200;
const EXPERIMENT: i32 = 849_200;

async fn json(stack: &Stack, path: &str, role: &str) -> (u16, Value) {
    let (status, body) = send(
        &stack.app,
        "GET",
        path,
        Some(&stack.token(role)),
        "application/json",
    )
    .await;
    (
        status.as_u16(),
        serde_json::from_str(&body).unwrap_or(Value::Null),
    )
}

async fn forget(owner: &Client) {
    owner
        .batch_execute(&format!(
            "DELETE FROM recommendations.recommendation_interaction
               WHERE occurred_at >= '2020-03-01' AND occurred_at < '2020-05-01';
             BEGIN; SET LOCAL session_replication_role = replica;
             DELETE FROM public.experiment_exposure WHERE assignment_id IN
                 (SELECT assignment_id FROM public.experiment_assignment
                   WHERE experiment_id = {EXPERIMENT});
             DELETE FROM public.experiment_assignment WHERE experiment_id = {EXPERIMENT};
             DELETE FROM public.experiment_group WHERE experiment_id = {EXPERIMENT};
             DELETE FROM public.experiment WHERE experiment_id = {EXPERIMENT};
             DELETE FROM public.campaign WHERE campaign_id = {CAMPAIGN};
             COMMIT;"
        ))
        .await
        .unwrap();
}

async fn interaction(
    owner: &Client,
    kind: &str,
    who: Option<Uuid>,
    store: i16,
    product: i32,
    at: &str,
) {
    owner
        .execute(
            "INSERT INTO recommendations.recommendation_interaction \
                 (event_id, kind, occurred_at, store_id, product_id, customer_id, label_code, \
                  rank) \
             VALUES ($1, $2, $3::text::timestamptz, $4, $5, $6, 'LOYAL', 1)",
            &[&Uuid::new_v4(), &kind, &at, &store, &product, &who],
        )
        .await
        .unwrap();
}

async fn buy(
    owner: &Client,
    tag: &str,
    who: Uuid,
    store: i16,
    product: i32,
    at: &str,
    origin: &str,
) {
    sale(
        owner,
        tag,
        Spec::new(who, store, 1, at, origin),
        &[Line {
            product,
            quantity: 1,
            cents: 1000,
        }],
    )
    .await;
}

/// The campaign exposure of `who` at `at`: a treated customer of a campaign.
async fn campaign_exposure(owner: &Client, who: Uuid, at: &str) {
    owner
        .execute(
            "INSERT INTO public.campaign (campaign_id, name, label_code, starts_on, ends_on, \
                 status) VALUES ($1, 'kpi-test-849200', 'LOYAL', '2020-03-01', '2020-03-31', \
                 'FINISHED')",
            &[&CAMPAIGN],
        )
        .await
        .unwrap();
    owner
        .execute(
            "INSERT INTO public.experiment (experiment_id, name, campaign_id, target_metric, \
                 starts_on, ends_on, conversion_window_days, data_origin) \
             VALUES ($1, 'kpi-test-849200', $2, 'CONVERSION', '2020-03-01', '2020-03-31', 7, \
                 'OBSERVED')",
            &[&EXPERIMENT, &CAMPAIGN],
        )
        .await
        .unwrap();
    let group = EXPERIMENT * 10 + 2;
    owner
        .execute(
            "INSERT INTO public.experiment_group (group_id, experiment_id, kind, name, \
                 treatment_description) VALUES ($1, $2, 'TREATMENT', 'T', 'kpi test arm')",
            &[&group, &EXPERIMENT],
        )
        .await
        .unwrap();
    let assignment: i64 = owner
        .query_one(
            "INSERT INTO public.experiment_assignment (experiment_id, group_id, customer_id, \
                 assigned_at) VALUES ($1, $2, $3, '2020-03-01T00:00:00Z') \
             RETURNING assignment_id",
            &[&EXPERIMENT, &group, &who],
        )
        .await
        .unwrap()
        .get(0);
    owner
        .execute(
            "INSERT INTO public.experiment_exposure (assignment_id, exposed_at) \
             VALUES ($1, $2::text::timestamptz)",
            &[&assignment, &at],
        )
        .await
        .unwrap();
}

async fn fixture(owner: &Client) {
    forget(owner).await;
    purge(owner).await;
    let tag = tag();
    let mut c = Vec::new();
    for _ in 0..9 {
        c.push(customer(owner).await);
    }
    // c1: an impression and a click on product 1 in store 1, then buys it in store 9.
    interaction(
        owner,
        "RECO_IMPRESSION",
        Some(c[1]),
        1,
        1,
        "2020-03-02T10:00:00Z",
    )
    .await;
    interaction(
        owner,
        "RECO_CLICK",
        Some(c[1]),
        1,
        1,
        "2020-03-02T10:05:00Z",
    )
    .await;
    buy(owner, &tag, c[1], 9, 1, "2020-03-05T10:00:00Z", "OBSERVED").await;
    // c2: an impression, and a purchase 9 days later.
    interaction(
        owner,
        "RECO_IMPRESSION",
        Some(c[2]),
        1,
        1,
        "2020-03-03T10:00:00Z",
    )
    .await;
    buy(owner, &tag, c[2], 1, 1, "2020-03-12T10:00:00Z", "OBSERVED").await;
    // c3: an impression and a click on product 2, then buys product 1.
    interaction(
        owner,
        "RECO_IMPRESSION",
        Some(c[3]),
        2,
        1,
        "2020-03-04T10:00:00Z",
    )
    .await;
    interaction(
        owner,
        "RECO_CLICK",
        Some(c[3]),
        2,
        2,
        "2020-03-04T10:05:00Z",
    )
    .await;
    buy(owner, &tag, c[3], 2, 1, "2020-03-04T12:00:00Z", "OBSERVED").await;
    // c4: two impressions, one customer.
    interaction(
        owner,
        "RECO_IMPRESSION",
        Some(c[4]),
        1,
        1,
        "2020-03-06T10:00:00Z",
    )
    .await;
    interaction(
        owner,
        "RECO_IMPRESSION",
        Some(c[4]),
        1,
        1,
        "2020-03-07T10:00:00Z",
    )
    .await;
    // c5: a campaign exposure, then a purchase.
    campaign_exposure(owner, c[5], "2020-03-10T09:00:00Z").await;
    buy(owner, &tag, c[5], 1, 1, "2020-03-11T10:00:00Z", "OBSERVED").await;
    // c8: a click and the purchase of that product, with no impression.
    interaction(
        owner,
        "RECO_CLICK",
        Some(c[8]),
        2,
        1,
        "2020-03-08T10:00:00Z",
    )
    .await;
    buy(owner, &tag, c[8], 2, 1, "2020-03-09T10:00:00Z", "OBSERVED").await;
    // An impression with no customer.
    interaction(owner, "RECO_IMPRESSION", None, 1, 1, "2020-03-02T11:00:00Z").await;
    // April: a seeded purchase after an exposure makes the row synthetic.
    interaction(
        owner,
        "RECO_IMPRESSION",
        Some(c[6]),
        1,
        1,
        "2020-04-02T10:00:00Z",
    )
    .await;
    buy(owner, &tag, c[6], 1, 1, "2020-04-03T10:00:00Z", "SEEDED").await;
}

/// `(code, value)` of a row's measures, in order.
fn measures(row: &Value) -> Vec<(String, String)> {
    row["measures"]
        .as_array()
        .unwrap()
        .iter()
        .map(|m| {
            (
                m["code"].as_str().unwrap().to_owned(),
                m["value"].as_str().unwrap_or("").to_owned(),
            )
        })
        .collect()
}

fn pairs(items: &[(&str, &str)]) -> Vec<(String, String)> {
    items
        .iter()
        .map(|(code, value)| ((*code).to_owned(), (*value).to_owned()))
        .collect()
}

fn views(body: &Value) -> Vec<&str> {
    body["data_origin"]["views"]
        .as_array()
        .unwrap()
        .iter()
        .map(|view| view.as_str().unwrap())
        .collect()
}

#[tokio::test]
async fn conversion_counts_exposed_customers_who_bought_within_seven_days() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    fixture(&owner).await;

    // The whole window: c1..c5 exposed, c1, c3 and c5 bought within 7 days.
    let (status, body) = json(&stack, &format!("{CONVERSION}?{MARCH}"), "ADMIN").await;
    assert_eq!(status, 200, "{body}");
    assert_eq!(body["kpi"], "conversion");
    assert_eq!(body["rows"].as_array().unwrap().len(), 1, "{body}");
    assert_eq!(
        measures(&body["rows"][0]),
        pairs(&[
            ("exposed_customers", "5"),
            ("converted_customers", "3"),
            ("conversion_rate", "0.6000"),
        ])
    );
    assert_eq!(
        views(&body),
        [
            "api_experiments.exposure_v1",
            "api_recommendations.interaction_v1",
            "api_sales.net_purchase_line_v1"
        ]
    );
    assert_eq!(body["rows"][0]["synthetic"], false);

    // By store: a campaign exposure has no store, so only impressions are read.
    let (_, body) = json(
        &stack,
        &format!("{CONVERSION}?{MARCH}&group_by=store"),
        "ANALYST",
    )
    .await;
    let rows = body["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 2, "{body}");
    assert_eq!(rows[0]["store_id"], 1);
    assert_eq!(
        measures(&rows[0]),
        pairs(&[
            ("exposed_customers", "3"),
            ("converted_customers", "1"),
            ("conversion_rate", "0.3333"),
        ])
    );
    assert_eq!(rows[1]["store_id"], 2);
    assert_eq!(
        measures(&rows[1]),
        pairs(&[
            ("exposed_customers", "1"),
            ("converted_customers", "1"),
            ("conversion_rate", "1.0000"),
        ])
    );
    assert_eq!(
        views(&body),
        [
            "api_recommendations.interaction_v1",
            "api_sales.net_purchase_line_v1"
        ]
    );

    // By week: a customer counts once in a bucket, at the first exposure in it.
    let (_, body) = json(
        &stack,
        &format!("{CONVERSION}?{MARCH}&group_by=period&granularity=week"),
        "MARKETING",
    )
    .await;
    let rows = body["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 2, "{body}");
    assert_eq!(rows[0]["period_start"], "2020-03-02");
    assert_eq!(rows[0]["period_end"], "2020-03-08");
    assert_eq!(
        measures(&rows[0]),
        pairs(&[
            ("exposed_customers", "4"),
            ("converted_customers", "2"),
            ("conversion_rate", "0.5000"),
        ])
    );
    assert_eq!(rows[1]["period_start"], "2020-03-09");
    assert_eq!(
        measures(&rows[1]),
        pairs(&[
            ("exposed_customers", "1"),
            ("converted_customers", "1"),
            ("conversion_rate", "1.0000"),
        ])
    );

    // A customer with no label is a segment state, never dropped.
    let (_, body) = json(
        &stack,
        &format!("{CONVERSION}?{MARCH}&group_by=segment"),
        "AUDITOR",
    )
    .await;
    assert_eq!(body["rows"][0]["segment_state"], "no_open_assignment");
    assert_eq!(measures(&body["rows"][0])[0].1, "5");

    // A seeded purchase makes its bucket synthetic.
    let (_, body) = json(
        &stack,
        &format!("{CONVERSION}?from=2020-04-01&to=2020-04-30"),
        "ADMIN",
    )
    .await;
    assert_eq!(body["rows"][0]["synthetic"], true, "{body}");
    assert_eq!(body["data_origin"]["synthetic"], true);

    forget(&owner).await;
    purge(&owner).await;
    stack.finish().await;
}

#[tokio::test]
async fn a_bucket_still_inside_its_seven_days_reports_no_conversion() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    let today = chrono::Utc::now().date_naive();
    let from = today - chrono::Days::new(3);
    let (status, body) = json(
        &stack,
        &format!("{CONVERSION}?from={from}&to={today}"),
        "ADMIN",
    )
    .await;
    assert_eq!(status, 200, "{body}");
    // The seed's impressions are two days old: the bucket's last exposure is under
    // 7 days before generated_at, so it counts the exposed and nothing more.
    let row = &body["rows"][0];
    let codes: Vec<String> = measures(row).into_iter().map(|(code, _)| code).collect();
    assert_eq!(codes, ["exposed_customers"], "{body}");
    drop(owner);
    stack.finish().await;
}

#[tokio::test]
async fn a_store_scoped_role_reads_counts_for_its_stores_and_no_customer() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    fixture(&owner).await;
    let token = stack.token_for(MANAGER, "STORE_MANAGER");
    let get = |path: String| {
        let token = token.clone();
        let app = stack.app.clone();
        async move { send(&app, "GET", &path, Some(&token), "application/json").await }
    };

    // The manager's stores are 1, 9, 17 and 25: store 1's impressions only (c1, c2,
    // c4), and the campaign exposure of c5, which has no store, is left out.
    let (status, body) = get(format!("{CONVERSION}?{MARCH}")).await;
    assert_eq!(status, 200, "{body}");
    let report: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(report["store_scope"]["restricted"], true);
    assert_eq!(
        measures(&report["rows"][0]),
        pairs(&[
            ("exposed_customers", "3"),
            ("converted_customers", "1"),
            ("conversion_rate", "0.3333"),
        ])
    );
    assert!(!views(&report).contains(&"api_experiments.exposure_v1"));
    // Counts and no customer: no member of the report names one.
    assert!(!body.contains("customer_id"), "{body}");

    // Another store is refused.
    let (status, body) = get(format!("{CONVERSION}?{MARCH}&store_id=2")).await;
    assert_eq!(status, 403, "{body}");
    assert!(body.contains("forbidden_store"), "{body}");

    // Recommendation performance for the same stores: 5 impressions and 1 click in
    // store 1, and the purchase of c1 made in store 9.
    let (status, body) = get(format!("{RECOMMENDATION}?{MARCH}")).await;
    assert_eq!(status, 200, "{body}");
    let report: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(
        measures(&report["rows"][0]),
        pairs(&[("impressions", "5"), ("clicks", "1"), ("purchases", "1")])
    );
    assert!(!body.contains("customer_id"), "{body}");

    forget(&owner).await;
    purge(&owner).await;
    stack.finish().await;
}

#[tokio::test]
async fn recommendation_performance_counts_each_measure_from_its_own_origin() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    fixture(&owner).await;

    let (status, body) = json(&stack, &format!("{RECOMMENDATION}?{MARCH}"), "ADMIN").await;
    assert_eq!(status, 200, "{body}");
    assert_eq!(body["kpi"], "recommendation-performance");
    assert_eq!(
        measures(&body["rows"][0]),
        pairs(&[("impressions", "6"), ("clicks", "3"), ("purchases", "2")])
    );
    assert_eq!(
        views(&body),
        [
            "api_recommendations.interaction_v1",
            "api_recommendations.recommended_purchase_v1"
        ]
    );

    // By store: store 9 has a purchase and no click or impression, store 2 has a
    // click that earned no purchase (c3) and a purchase after a click (c8).
    let (_, body) = json(
        &stack,
        &format!("{RECOMMENDATION}?{MARCH}&group_by=store"),
        "ANALYST",
    )
    .await;
    let rows = body["rows"].as_array().unwrap();
    let by_store: Vec<(i64, Vec<(String, String)>)> = rows
        .iter()
        .map(|row| (row["store_id"].as_i64().unwrap(), measures(row)))
        .collect();
    assert_eq!(
        by_store,
        [
            (
                1,
                pairs(&[("impressions", "5"), ("clicks", "1"), ("purchases", "0")])
            ),
            (
                2,
                pairs(&[("impressions", "1"), ("clicks", "2"), ("purchases", "1")])
            ),
            (
                9,
                pairs(&[("impressions", "0"), ("clicks", "0"), ("purchases", "1")])
            ),
        ]
    );

    // By week: the click of c8 on Sunday 03-08 is in the first week, and the
    // purchase of Monday 03-09 in the second.
    let (_, body) = json(
        &stack,
        &format!("{RECOMMENDATION}?{MARCH}&group_by=period&granularity=week"),
        "AUDITOR",
    )
    .await;
    let rows = body["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 2, "{body}");
    assert_eq!(
        measures(&rows[0]),
        pairs(&[("impressions", "6"), ("clicks", "3"), ("purchases", "1")])
    );
    assert_eq!(
        measures(&rows[1]),
        pairs(&[("impressions", "0"), ("clicks", "0"), ("purchases", "1")])
    );

    // An interaction with no customer, or a customer with no label, has no segment.
    let (_, body) = json(
        &stack,
        &format!("{RECOMMENDATION}?{MARCH}&group_by=segment"),
        "MARKETING",
    )
    .await;
    assert_eq!(body["rows"][0]["segment_state"], "no_open_assignment");
    assert_eq!(
        measures(&body["rows"][0]),
        pairs(&[("impressions", "6"), ("clicks", "3"), ("purchases", "2")])
    );

    forget(&owner).await;
    purge(&owner).await;
    stack.finish().await;
}

#[tokio::test]
async fn only_the_net_sales_roles_read_them_channel_is_refused_and_xml_is_valid() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    for path in [CONVERSION, RECOMMENDATION] {
        for role in ROLES {
            let (status, body) = json(&stack, &format!("{path}?{MARCH}"), role).await;
            let expected = if NET_SALES_ROLES.contains(&role) {
                200
            } else {
                403
            };
            assert_eq!(status, expected, "{path} {role}: {body}");
        }
        for (query, field) in [
            ("group_by=channel", "group_by"),
            ("group_by=store,channel", "group_by"),
            ("channel_id=1", "channel_id"),
        ] {
            let (status, body) = json(&stack, &format!("{path}?{MARCH}&{query}"), "ANALYST").await;
            assert_eq!(status, 422, "{path} {query}: {body}");
            assert_eq!(body["code"], "invalid_query", "{body}");
            assert_eq!(body["details"][0]["field"], field, "{body}");
            assert_eq!(
                body["details"][0]["code"], "dimension_not_supported",
                "{body}"
            );
        }
        let (status, body) = json(&stack, &format!("{path}?to=2020-03-31"), "ANALYST").await;
        assert_eq!(status, 422, "{body}");
        assert_eq!(body["details"][0]["code"], "required", "{body}");
        let (status, xml) = send(
            &stack.app,
            "GET",
            &format!("{path}?{MARCH}&group_by=store"),
            Some(&stack.token("ANALYST")),
            "application/xml",
        )
        .await;
        assert_eq!(status, 200, "{xml}");
        assert_valid_xml(&xml);
    }
    stack.finish().await;
}
