//! Campaign performance through the whole router, on the seeded database as
//! `svc_kpis` (F26-04, #849).
//!
//! Five experiments of four campaigns are written, with their stored results, as
//! experimentation leaves them. Every figure below is computed by hand:
//!
//! | experiment | campaign | window              | arms                      | attributed sales (net)         | stored effect (95%?)          |
//! |------------|----------|---------------------|---------------------------|--------------------------------|-------------------------------|
//! | E1         | C1       | 2020-03-01 – 03-31  | control 2, treatment 3    | 40.00, and 35.00 with 10.00 returned | 14.30 [3.10, 25.50], alpha 0.05 |
//! | E2         | C2       | 2020-03-10 – 03-20  | treatment A 2, B 2        | 30.00                          | none: no control              |
//! | E3         | C3       | 2020-03-05 – 03-25  | control 1, treatment 1    | none                           | 9.99 [1.00, 18.98], alpha 0.025 |
//! | E4         | C1       | 2020-06-01 – 06-30  | control 1, treatment 1    | none                           | outside the range             |
//! | E5         | C5       | 2020-03-08 – 03-28  | control 1, treatments 1+1 | none                           | two comparisons, no single effect |
//!
//! So in March 2020: C1 reads 65.00 attributed (40.00 + 35.00 - 10.00 returned), 3
//! treated, 2 control, effect 14.30 in [3.10, 25.50]; C2 reads 30.00, 4 treated, 0
//! control and no effect; C3 reads 0.00 and no effect, because its interval is a
//! 97.5% one and the contract's measure is a 95% interval.

mod support;

use serde_json::Value;
use support::http::{assert_valid_xml, send, Stack, ROLES, TURN};
use support::{connect, customer, purge, reverse, sale, tag, Line, Spec};
use tokio_postgres::Client;
use uuid::Uuid;

const REPORT: &str = "/v1/kpi-reports/campaign-performance";
const MARCH: &str = "from=2020-03-01&to=2020-03-31";
const READERS: [&str; 4] = ["ADMIN", "ANALYST", "MARKETING", "AUDITOR"];
/// Ids far above anything the seed or another test writes.
const BASE: i32 = 849_000;

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

/// Take away every row this file wrote. Assignments and results refuse a delete
/// by trigger, so the fixture switches the triggers off for its own rows only.
async fn forget(owner: &Client) {
    owner
        .batch_execute(&format!(
            "BEGIN; SET LOCAL session_replication_role = replica;
             DELETE FROM experiments.sale_attribution WHERE assignment_id IN
                 (SELECT assignment_id FROM public.experiment_assignment
                   WHERE experiment_id BETWEEN {BASE} AND {BASE} + 99);
             DELETE FROM experiments.experiment_result_comparison
                 WHERE experiment_id BETWEEN {BASE} AND {BASE} + 99;
             DELETE FROM experiments.experiment_result_arm
                 WHERE experiment_id BETWEEN {BASE} AND {BASE} + 99;
             DELETE FROM experiments.experiment_result
                 WHERE experiment_id BETWEEN {BASE} AND {BASE} + 99;
             DELETE FROM experiments.experiment_plan
                 WHERE experiment_id BETWEEN {BASE} AND {BASE} + 99;
             DELETE FROM public.experiment_exposure WHERE assignment_id IN
                 (SELECT assignment_id FROM public.experiment_assignment
                   WHERE experiment_id BETWEEN {BASE} AND {BASE} + 99);
             DELETE FROM public.experiment_assignment
                 WHERE experiment_id BETWEEN {BASE} AND {BASE} + 99;
             DELETE FROM public.experiment_group
                 WHERE experiment_id BETWEEN {BASE} AND {BASE} + 99;
             DELETE FROM public.experiment WHERE experiment_id BETWEEN {BASE} AND {BASE} + 99;
             DELETE FROM public.campaign WHERE campaign_id BETWEEN {BASE} AND {BASE} + 99;
             COMMIT;"
        ))
        .await
        .unwrap();
}

struct Experiment {
    id: i32,
    campaign: i32,
    starts: &'static str,
    ends: &'static str,
    /// `(group id offset, kind, customers)`.
    arms: Vec<(i32, &'static str, Vec<Uuid>)>,
}

async fn record(owner: &Client, e: &Experiment) -> Vec<(i32, i64, Uuid)> {
    owner
        .execute(
            "INSERT INTO public.campaign (campaign_id, name, label_code, starts_on, ends_on, \
                 status) VALUES ($1, $2, 'LOYAL', $3::text::date, $4::text::date, 'FINISHED') \
             ON CONFLICT (campaign_id) DO NOTHING",
            &[
                &e.campaign,
                &format!("kpi-test-{}", e.campaign),
                &e.starts,
                &e.ends,
            ],
        )
        .await
        .unwrap();
    owner
        .execute(
            "INSERT INTO public.experiment (experiment_id, name, campaign_id, target_metric, \
                 starts_on, ends_on, conversion_window_days, data_origin) \
             VALUES ($1, $2, $3, 'REVENUE_PER_CUSTOMER', $4::text::date, $5::text::date, 30, \
                 'OBSERVED')",
            &[
                &e.id,
                &format!("kpi-test-{}", e.id),
                &e.campaign,
                &e.starts,
                &e.ends,
            ],
        )
        .await
        .unwrap();
    let mut assigned = Vec::new();
    for (offset, kind, customers) in &e.arms {
        let group = e.id * 10 + offset;
        owner
            .execute(
                "INSERT INTO public.experiment_group (group_id, experiment_id, kind, name, \
                     treatment_description) VALUES ($1, $2, $3, $3, 'kpi test arm')",
                &[&group, &e.id, kind],
            )
            .await
            .unwrap();
        for who in customers {
            let assignment: i64 = owner
                .query_one(
                    "INSERT INTO public.experiment_assignment (experiment_id, group_id, \
                         customer_id, assigned_at) VALUES ($1, $2, $3, $4::text::timestamptz) \
                     RETURNING assignment_id",
                    &[&e.id, &group, who, &format!("{}T00:00:00Z", e.starts)],
                )
                .await
                .unwrap()
                .get(0);
            assigned.push((group, assignment, *who));
        }
    }
    owner
        .execute(
            "INSERT INTO experiments.experiment_result (experiment_id, computed_at, \
                 sample_ratio_p_value, synthetic) VALUES ($1, $2::text::timestamptz, 0.9, false)",
            &[&e.id, &format!("{}T12:00:00Z", e.ends)],
        )
        .await
        .unwrap();
    for (offset, _, customers) in &e.arms {
        let analysed = i32::try_from(customers.len()).unwrap();
        owner
            .execute(
                "INSERT INTO experiments.experiment_result_arm (experiment_id, group_id, \
                     analysed, exposed, converted, pending, not_converted, net_revenue, \
                     revenue_std_dev) VALUES ($1, $2, $3, 0, 0, 0, $3, 0, 0)",
                &[&e.id, &(e.id * 10 + offset), &analysed],
            )
            .await
            .unwrap();
    }
    assigned
}

async fn plan(owner: &Client, experiment: i32, comparison_alpha: &str) {
    owner
        .execute(
            "INSERT INTO experiments.experiment_plan (experiment_id, alpha, comparison_alpha, \
                 power, minimum_detectable_effect, baseline, required_per_arm, settle_days, \
                 frame_taken_at, frame_digest, label_code, salt, planned_at, planned_by) \
             VALUES ($1, $2::text::numeric, $2::text::numeric, 0.8, 1, 0, 1, 0, \
                 '2020-01-01T00:00:00Z', repeat('a', 64), 'LOYAL', repeat('b', 64), \
                 '2020-01-01T00:00:00Z', '11111111-1111-1111-1111-000000000001')",
            &[&experiment, &comparison_alpha],
        )
        .await
        .unwrap();
}

async fn effect(owner: &Client, experiment: i32, difference: f64, lower: f64, upper: f64) {
    effect_for(owner, experiment, 2, difference, lower, upper).await;
}

async fn effect_for(
    owner: &Client,
    experiment: i32,
    treatment_offset: i32,
    difference: f64,
    lower: f64,
    upper: f64,
) {
    owner
        .execute(
            "INSERT INTO experiments.experiment_result_comparison (experiment_id, group_id, \
                 against_group_id, metric, difference, lower, upper, p_value, \
                 bootstrap_lower, bootstrap_upper) \
             VALUES ($1, $1 * 10 + $2, $1 * 10 + 1, 'REVENUE_PER_CUSTOMER', $3, $4, $5, 0.01, \
                 $4, $5)",
            &[&experiment, &treatment_offset, &difference, &lower, &upper],
        )
        .await
        .unwrap();
}

/// Expose the treated `customer`, record their sale after it and attribute it.
async fn attribute(
    owner: &Client,
    tag: &str,
    assignment: i64,
    customer: Uuid,
    quantity: i32,
    cents: i64,
) -> i64 {
    let exposure: i64 = owner
        .query_one(
            "INSERT INTO public.experiment_exposure (assignment_id, exposed_at) \
             VALUES ($1, '2020-03-11T09:00:00Z') RETURNING exposure_id",
            &[&assignment],
        )
        .await
        .unwrap()
        .get(0);
    let id = sale(
        owner,
        tag,
        Spec::new(customer, 1, 1, "2020-03-12T10:00:00Z", "OBSERVED"),
        &[Line {
            product: 1,
            quantity,
            cents,
        }],
    )
    .await;
    owner
        .execute(
            "INSERT INTO experiments.sale_attribution (sale_id, assignment_id, exposure_id) \
             VALUES ($1, $2, $3)",
            &[&id, &assignment, &exposure],
        )
        .await
        .unwrap();
    id
}

async fn fixture(owner: &Client) {
    forget(owner).await;
    purge(owner).await;
    let tag = tag();
    let mut people = Vec::new();
    for _ in 0..14 {
        people.push(customer(owner).await);
    }
    // E1: C1, control 2 and treatment 3, one sale half returned.
    let e1 = record(
        owner,
        &Experiment {
            id: BASE + 1,
            campaign: BASE + 1,
            starts: "2020-03-01",
            ends: "2020-03-31",
            arms: vec![
                (1, "CONTROL", people[0..2].to_vec()),
                (2, "TREATMENT", people[2..5].to_vec()),
            ],
        },
    )
    .await;
    plan(owner, BASE + 1, "0.05").await;
    effect(owner, BASE + 1, 14.3, 3.1, 25.5).await;
    let treated: Vec<_> = e1
        .iter()
        .filter(|(g, _, _)| *g == (BASE + 1) * 10 + 2)
        .collect();
    attribute(owner, &tag, treated[0].1, treated[0].2, 1, 4_000).await;
    // 7 x 5.00, of which 2 come back: 25.00 stays attributed.
    let returned = attribute(owner, &tag, treated[1].1, treated[1].2, 7, 500).await;
    reverse(owner, returned, 1, 2).await;
    // E2: C2, two offers and no control.
    let e2 = record(
        owner,
        &Experiment {
            id: BASE + 2,
            campaign: BASE + 2,
            starts: "2020-03-10",
            ends: "2020-03-20",
            arms: vec![
                (1, "TREATMENT", people[5..7].to_vec()),
                (2, "TREATMENT", people[7..9].to_vec()),
            ],
        },
    )
    .await;
    plan(owner, BASE + 2, "0.05").await;
    attribute(owner, &tag, e2[0].1, e2[0].2, 1, 3_000).await;
    // E3: C3, a 97.5% interval.
    record(
        owner,
        &Experiment {
            id: BASE + 3,
            campaign: BASE + 3,
            starts: "2020-03-05",
            ends: "2020-03-25",
            arms: vec![
                (1, "CONTROL", vec![people[9]]),
                (2, "TREATMENT", vec![people[10]]),
            ],
        },
    )
    .await;
    plan(owner, BASE + 3, "0.025").await;
    effect(owner, BASE + 3, 9.99, 1.0, 18.98).await;
    // E5: C5, one control and two treatments. Experimentation stores one
    // treatment-vs-control comparison per treatment, but this report still has
    // exactly one experiment row and no single effect.
    record(
        owner,
        &Experiment {
            id: BASE + 5,
            campaign: BASE + 5,
            starts: "2020-03-08",
            ends: "2020-03-28",
            arms: vec![
                (1, "CONTROL", vec![people[11]]),
                (2, "TREATMENT", vec![people[12]]),
                (3, "TREATMENT", vec![people[13]]),
            ],
        },
    )
    .await;
    plan(owner, BASE + 5, "0.025").await;
    effect(owner, BASE + 5, 5.0, 1.0, 9.0).await;
    effect_for(owner, BASE + 5, 3, 7.0, 2.0, 12.0).await;
    // E4: C1 again, in June.
    record(
        owner,
        &Experiment {
            id: BASE + 4,
            campaign: BASE + 1,
            starts: "2020-06-01",
            ends: "2020-06-30",
            arms: vec![
                (1, "CONTROL", vec![people[0]]),
                (2, "TREATMENT", vec![people[2]]),
            ],
        },
    )
    .await;
}

/// A measure's value as text, or `None` when the report carries it as null.
fn measure_value(row: &Value, code: &str) -> Option<String> {
    row["measures"]
        .as_array()
        .unwrap()
        .iter()
        .find(|m| m["code"] == code)
        .and_then(|m| m["value"].as_str().map(str::to_owned))
}

fn measure_interval(row: &Value, code: &str) -> (Value, Value, Value) {
    let found = row["measures"]
        .as_array()
        .unwrap()
        .iter()
        .find(|m| m["code"] == code)
        .unwrap();
    (
        found["kind"].clone(),
        found["lower"].clone(),
        found["upper"].clone(),
    )
}

#[tokio::test]
async fn attribution_and_effect_are_labelled_apart_and_the_effect_shows_its_interval() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    fixture(&owner).await;

    let (status, report) = json(&stack, &format!("{REPORT}?{MARCH}"), "MARKETING").await;
    assert_eq!(status, 200, "{report}");
    assert_eq!(report["kpi"], "campaign-performance");
    assert_eq!(report["group_by"], serde_json::json!([]));
    assert_eq!(
        report["data_origin"]["views"],
        serde_json::json!(["api_experiments.campaign_effect_v1"])
    );
    let rows: Vec<&Value> = report["rows"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|row| row["campaign_id"].as_i64().unwrap() >= i64::from(BASE))
        .collect();
    let campaigns: Vec<i64> = rows
        .iter()
        .map(|r| r["campaign_id"].as_i64().unwrap())
        .collect();
    assert_eq!(
        campaigns,
        [BASE + 1, BASE + 3, BASE + 5, BASE + 2]
            .map(i64::from)
            .to_vec(),
        "ordered by window start; E4 in June is outside"
    );

    let c1 = rows[0];
    assert_eq!(c1["period_start"], "2020-03-01");
    assert_eq!(c1["period_end"], "2020-03-31");
    assert_eq!(c1["store_id"], Value::Null, "a campaign has no store");
    let codes: Vec<&str> = c1["measures"]
        .as_array()
        .unwrap()
        .iter()
        .map(|m| m["code"].as_str().unwrap())
        .collect();
    assert_eq!(
        codes,
        [
            "attributed_sales_amount",
            "treated_customers",
            "control_customers",
            "incremental_effect"
        ]
    );
    assert_eq!(
        measure_value(c1, "attributed_sales_amount").as_deref(),
        Some("65.00")
    );
    assert_eq!(
        measure_interval(c1, "attributed_sales_amount"),
        ("ATTRIBUTED".into(), Value::Null, Value::Null)
    );
    assert_eq!(measure_value(c1, "treated_customers").as_deref(), Some("3"));
    assert_eq!(measure_value(c1, "control_customers").as_deref(), Some("2"));
    assert_eq!(
        measure_value(c1, "incremental_effect").as_deref(),
        Some("14.30")
    );
    assert_eq!(
        measure_interval(c1, "incremental_effect"),
        ("INCREMENTAL".into(), "3.10".into(), "25.50".into())
    );

    // A 97.5% interval is not the contract's 95% one: no effect is shown.
    let c3 = rows[1];
    assert_eq!(
        measure_value(c3, "attributed_sales_amount").as_deref(),
        Some("0.00")
    );
    assert_eq!(measure_value(c3, "incremental_effect"), None);

    // One control and two treatments: one row, and no arbitrary effect.
    let c5 = rows[2];
    assert_eq!(measure_value(c5, "incremental_effect"), None);
    let (_, multiple_treatments) = json(
        &stack,
        &format!("{REPORT}?{MARCH}&campaign_id={}", BASE + 5),
        "MARKETING",
    )
    .await;
    assert_eq!(multiple_treatments["page_info"]["total"], 1);
    assert_eq!(multiple_treatments["rows"].as_array().unwrap().len(), 1);

    // Two offers and no control: attributed sales, and no effect.
    let c2 = rows[3];
    assert_eq!(
        measure_value(c2, "attributed_sales_amount").as_deref(),
        Some("30.00")
    );
    assert_eq!(measure_value(c2, "treated_customers").as_deref(), Some("4"));
    assert_eq!(measure_value(c2, "control_customers").as_deref(), Some("0"));
    assert_eq!(measure_value(c2, "incremental_effect"), None);
    assert_eq!(
        measure_interval(c2, "incremental_effect"),
        ("INCREMENTAL".into(), Value::Null, Value::Null)
    );

    // One campaign, and its June experiment when the range reaches it.
    let (_, one) = json(
        &stack,
        &format!(
            "{REPORT}?from=2020-01-01&to=2020-12-31&campaign_id={}",
            BASE + 1
        ),
        "ANALYST",
    )
    .await;
    let periods: Vec<&str> = one["rows"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["period_start"].as_str().unwrap())
        .collect();
    assert_eq!(periods, ["2020-03-01", "2020-06-01"]);
    assert_eq!(one["page_info"]["total"], 2);

    forget(&owner).await;
    purge(&owner).await;
}

#[tokio::test]
async fn a_returned_attributed_sale_counts_net() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    fixture(&owner).await;
    // The second attributed sale of C1 is 7 x 5.00 with 2 returned: 25.00 net,
    // beside the first sale's 40.00.
    let (_, report) = json(
        &stack,
        &format!("{REPORT}?{MARCH}&campaign_id={}", BASE + 1),
        "AUDITOR",
    )
    .await;
    assert_eq!(
        measure_value(&report["rows"][0], "attributed_sales_amount").as_deref(),
        Some("65.00")
    );
    forget(&owner).await;
    purge(&owner).await;
}

#[tokio::test]
async fn only_four_roles_read_it_no_dimension_groups_it_and_xml_is_valid() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    fixture(&owner).await;
    for role in ROLES {
        let (status, body) = json(&stack, &format!("{REPORT}?{MARCH}"), role).await;
        let expected = if READERS.contains(&role) { 200 } else { 403 };
        assert_eq!(status, expected, "{role}: {body}");
    }
    for (query, field, code) in [
        ("to=2020-03-31", "from", "required"),
        ("from=2020-03-31&to=2020-03-01", "to", "to_before_from"),
        (
            "from=2020-03-01&to=2020-03-31&group_by=store",
            "group_by",
            "dimension_not_supported",
        ),
        (
            "from=2020-03-01&to=2020-03-31&campaign_id=0",
            "campaign_id",
            "invalid_format",
        ),
    ] {
        let (status, body) = json(&stack, &format!("{REPORT}?{query}"), "ANALYST").await;
        assert_eq!(status, 422, "{query}: {body}");
        assert_eq!(body["code"], "invalid_query", "{body}");
        assert_eq!(body["details"][0]["field"], field, "{body}");
        assert_eq!(body["details"][0]["code"], code, "{body}");
    }
    let (status, xml) = send(
        &stack.app,
        "GET",
        &format!("{REPORT}?{MARCH}"),
        Some(&stack.token("ANALYST")),
        "application/xml",
    )
    .await;
    assert_eq!(status.as_u16(), 200, "{xml}");
    assert_valid_xml(&xml);
    assert!(xml.contains("<kind>INCREMENTAL</kind>"), "{xml}");
    assert!(xml.contains("<kind>ATTRIBUTED</kind>"), "{xml}");

    // The shape is the example's.
    let (_, report) = json(&stack, &format!("{REPORT}?{MARCH}"), "ANALYST").await;
    let example: Value = serde_json::from_str(
        &std::fs::read_to_string("contracts/analytics-kpis/examples/campaign-performance.json")
            .unwrap(),
    )
    .unwrap();
    let keys =
        |value: &Value| -> Vec<String> { value.as_object().unwrap().keys().cloned().collect() };
    assert_eq!(keys(&report), keys(&example));
    assert_eq!(keys(&report["rows"][0]), keys(&example["rows"][0]));
    forget(&owner).await;
    purge(&owner).await;
}
