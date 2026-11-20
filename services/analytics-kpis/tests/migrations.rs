//! Segment migrations through the whole router, on the seeded database as
//! `svc_kpis` (F26-03).
//!
//! The seed holds 31 runs, one a day, of 30 customers each. Runs 1 to 29 and 31
//! are model version 1 and run 30 is version 2, so across the seed:
//!
//! | run | changes of label against the run before | versions (before, after) |
//! |-----|------------------------------------------|--------------------------|
//! | 8   | 3                                        | 1, 1                     |
//! | 18  | 1                                        | 1, 1                     |
//! | 30  | 19                                       | 1, 2                     |
//! | 31  | 19                                       | 2, 1                     |
//!
//! Every other run repeats the labels of the run before it. A window of one run's
//! day therefore holds exactly that run's changes, which is what the migration
//! matrix of segments-service holds between the two instants around it.

mod support;

use std::collections::BTreeMap;

use serde_json::{json, Value};
use support::http::{assert_valid_xml, send, Stack, ROLES, TURN};
use support::{connect, customer, measure_value, purge, remove_customer};
use tokio_postgres::Client;

const MIGRATIONS: &str = "/v1/kpi-reports/migrations";
const READERS: [&str; 4] = ["ADMIN", "ANALYST", "MARKETING", "AUDITOR"];

async fn json_as(stack: &Stack, path: &str, token: &str) -> (u16, Value) {
    let (status, body) = send(&stack.app, "GET", path, Some(token), "application/json").await;
    (
        status.as_u16(),
        serde_json::from_str(&body).unwrap_or(Value::Null),
    )
}

async fn json(stack: &Stack, path: &str) -> (u16, Value) {
    json_as(stack, path, &stack.token("ANALYST")).await
}

/// The UTC date a seeded run's assignments opened.
async fn day_of(owner: &Client, run_id: i64) -> String {
    owner
        .query_one(
            "SELECT (min(valid_from) AT TIME ZONE 'UTC')::date::text \
             FROM public.customer_segment_history WHERE run_id = $1",
            &[&run_id],
        )
        .await
        .unwrap()
        .get(0)
}

fn count(row: &Value, code: &str) -> i64 {
    measure_value(row, code).unwrap().parse().unwrap()
}

fn total(report: &Value, code: &str) -> i64 {
    report["rows"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| count(row, code))
        .sum()
}

/// The cells of the migration matrix of segments-service between two instants,
/// computed by its own rule: a customer's state at an instant is the assignment
/// row open then (`valid_from <= t AND (valid_to IS NULL OR valid_to > t)`), a
/// label, `unassigned`, or `new` when there is none. Only cells where a
/// customer moved from one label or `unassigned` to another count here, as a
/// migration does: `new` is an entry, not a change of label.
async fn matrix_moves(
    owner: &Client,
    from: &str,
    to_exclusive: &str,
) -> BTreeMap<(Option<String>, Option<String>), i64> {
    let rows = owner
        .query(
            "WITH ids AS (SELECT customer_id FROM api_customers.customer_ref_v1) \
             SELECT b.label_code, a.label_code, count(*) \
             FROM ids \
             JOIN public.customer_segment_history AS b \
               ON b.customer_id = ids.customer_id AND b.valid_from <= $1::text::timestamptz \
              AND (b.valid_to IS NULL OR b.valid_to > $1::text::timestamptz) \
             JOIN public.customer_segment_history AS a \
               ON a.customer_id = ids.customer_id AND a.valid_from <= $2::text::timestamptz \
              AND (a.valid_to IS NULL OR a.valid_to > $2::text::timestamptz) \
             WHERE b.label_code IS DISTINCT FROM a.label_code \
             GROUP BY 1, 2",
            &[&from, &to_exclusive],
        )
        .await
        .unwrap();
    rows.iter()
        .map(|row| ((row.get(0), row.get(1)), row.get::<_, i64>(2)))
        .collect()
}

/// The report's rows as `(label left, label entered) -> customers_migrated`.
fn report_moves(report: &Value) -> BTreeMap<(Option<String>, Option<String>), i64> {
    report["rows"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| {
            (
                (
                    row["from_segment_code"].as_str().map(str::to_owned),
                    row["segment_code"].as_str().map(str::to_owned),
                ),
                count(row, "customers_migrated"),
            )
        })
        .collect()
}

#[tokio::test]
async fn within_one_version_the_figures_match_the_migration_matrix() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;

    for (run, expected_changes) in [(8_i64, 3_i64), (18, 1)] {
        let day = day_of(&owner, run).await;
        let next: String = owner
            .query_one("SELECT ($1::text::date + 1)::text", &[&day])
            .await
            .unwrap()
            .get(0);
        let (status, report) = json(
            &stack,
            &format!("{MIGRATIONS}?from={day}&to={day}&group_by=segment"),
        )
        .await;
        assert_eq!(status, 200, "run {run}: {report}");
        // The matrix between midnight and midnight, by its own rule.
        let matrix = matrix_moves(
            &owner,
            &format!("{day}T00:00:00Z"),
            &format!("{next}T00:00:00Z"),
        )
        .await;
        assert_eq!(matrix.values().sum::<i64>(), expected_changes, "run {run}");
        assert_eq!(report_moves(&report), matrix, "run {run}: {report}");
        // Within one version: every change is behaviour, none methodological.
        assert_eq!(total(&report, "changes"), expected_changes);
        assert_eq!(total(&report, "customers_migrated"), expected_changes);
        assert_eq!(total(&report, "methodological_changes"), 0);
        assert_eq!(report["data_origin"]["spans_model_versions"], false);
        assert_eq!(report["data_origin"]["model_version_ids"], json!([1]));
        assert_eq!(
            report["data_origin"]["views"],
            json!(["api_segments.assignment_change_v1"])
        );
        for row in report["rows"].as_array().unwrap() {
            assert_eq!(row["model_version_id"], 1);
            assert_eq!(row["segment_state"], "assigned");
            assert_eq!(row["synthetic"], true, "the seeded runs are SEEDED");
            for member in ["store_id", "channel_id", "campaign_id", "period_start"] {
                assert!(row[member].is_null(), "{member}: {row}");
            }
        }
    }

    // The two periods side by side: each day's changes in its own row, and the
    // window's figures are their sum.
    let (first, second) = (day_of(&owner, 8).await, day_of(&owner, 18).await);
    let (status, report) = json(
        &stack,
        &format!("{MIGRATIONS}?from={first}&to={second}&group_by=period&granularity=day"),
    )
    .await;
    assert_eq!(status, 200, "{report}");
    assert_eq!(report["granularity"], "day");
    let rows = report["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 2, "{report}");
    assert_eq!(rows[0]["period_start"], first.as_str());
    assert_eq!(rows[0]["period_end"], first.as_str());
    assert_eq!(count(&rows[0], "changes"), 3);
    assert_eq!(rows[1]["period_start"], second.as_str());
    assert_eq!(count(&rows[1], "changes"), 1);
    assert!(rows[0]["segment_code"].is_null() && rows[0]["from_segment_code"].is_null());

    // With no dimension, one row per version: a row never mixes versions.
    let (_, report) = json(&stack, &format!("{MIGRATIONS}?from={first}&to={second}")).await;
    let rows = report["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 1, "{report}");
    assert_eq!(count(&rows[0], "changes"), 4);

    // A filter on the label entered.
    let day = day_of(&owner, 8).await;
    let (_, all) = json(
        &stack,
        &format!("{MIGRATIONS}?from={day}&to={day}&group_by=segment"),
    )
    .await;
    let entered = all["rows"][0]["segment_code"].as_str().unwrap().to_owned();
    let (_, kept) = json(
        &stack,
        &format!("{MIGRATIONS}?from={day}&to={day}&group_by=segment&segment_code={entered}"),
    )
    .await;
    assert!(kept["rows"]
        .as_array()
        .unwrap()
        .iter()
        .all(|row| row["segment_code"] == entered.as_str()));
    // A version nothing was entered under keeps nothing.
    let (_, none) = json(
        &stack,
        &format!("{MIGRATIONS}?from={day}&to={day}&model_version_id=2"),
    )
    .await;
    assert_eq!(none["rows"], json!([]));
    assert_eq!(none["data_origin"]["spans_model_versions"], false);

    stack.finish().await;
}

#[tokio::test]
async fn a_change_across_two_versions_is_methodological_and_the_report_marks_it() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    // Run 30 is version 2 and run 31 returns to version 1: 19 customers change
    // at each, and none of those 38 is behaviour (ADR-0064).
    let (from, to) = (day_of(&owner, 30).await, day_of(&owner, 31).await);
    let (status, report) = json(
        &stack,
        &format!("{MIGRATIONS}?from={from}&to={to}&group_by=segment"),
    )
    .await;
    assert_eq!(status, 200, "{report}");
    assert_eq!(report["data_origin"]["spans_model_versions"], true);
    assert_eq!(report["data_origin"]["model_version_ids"], json!([1, 2]));
    assert_eq!(total(&report, "methodological_changes"), 38);
    assert_eq!(total(&report, "changes"), 0);
    assert_eq!(total(&report, "customers_migrated"), 0);
    // A row never mixes versions: each carries the version entered, and the two
    // versions are in different rows.
    let versions: Vec<i64> = report["rows"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| row["model_version_id"].as_i64().unwrap())
        .collect();
    assert!(
        versions.contains(&1) && versions.contains(&2),
        "{versions:?}"
    );
    let entered_v2: i64 = report["rows"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|row| row["model_version_id"] == 2)
        .map(|row| count(row, "methodological_changes"))
        .sum();
    assert_eq!(entered_v2, 19);

    // Keeping one version keeps its rows, and the window still spans two.
    let (_, kept) = json(
        &stack,
        &format!("{MIGRATIONS}?from={from}&to={to}&model_version_id=2"),
    )
    .await;
    assert_eq!(total(&kept, "methodological_changes"), 19);
    assert_eq!(kept["data_origin"]["spans_model_versions"], true);

    // Run 31's day alone holds its 19, and a window before any run holds none.
    let (_, quiet) = json(&stack, &format!("{MIGRATIONS}?from={to}&to={to}")).await;
    assert_eq!(total(&quiet, "methodological_changes"), 19);
    let (_, after) = json(
        &stack,
        &format!("{MIGRATIONS}?from=2019-01-01&to=2019-01-31"),
    )
    .await;
    assert_eq!(after["rows"], json!([]));
    assert_eq!(after["page_info"]["total"], 0);
    assert_eq!(after["data_origin"]["spans_model_versions"], false);
    assert_eq!(after["data_origin"]["model_version_ids"], json!([]));

    stack.finish().await;
}

#[tokio::test]
async fn a_run_nobody_linked_to_a_version_is_methodological_and_a_label_lost_is_reported_as_it_is()
{
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    purge(&owner).await;
    // Two observed runs model-registry has not linked, and one customer who held
    // LOYAL under the first and no label under the second (RN-21).
    let mut runs = Vec::new();
    for at in ["2001-01-01T06:00:00Z", "2001-01-02T06:00:00Z"] {
        let run: i64 = owner
            .query_one(
                "INSERT INTO public.segmentation_run \
                     (method, window_days, parameters, customer_count, run_at, data_origin) \
                 VALUES ('RFM_RULES', 1, '{}'::jsonb, 1, $1::text::timestamptz, 'SEEDED') \
                 RETURNING run_id",
                &[&at],
            )
            .await
            .unwrap()
            .get(0);
        // The insert guard looks at the seeded sales of the days before today, which
        // the seed holds; the owner may state the origin of a run that is not.
        owner
            .execute(
                "UPDATE public.segmentation_run SET data_origin = 'OBSERVED' WHERE run_id = $1",
                &[&run],
            )
            .await
            .unwrap();
        runs.push(run);
    }
    let who = customer(&owner).await;
    owner
        .execute(
            "INSERT INTO public.customer_segment_history \
                 (customer_id, run_id, label_code, valid_from, valid_to) \
             VALUES ($1, $2, 'LOYAL', '2001-01-01T06:00:00Z', '2001-01-02T06:00:00Z')",
            &[&who, &runs[0]],
        )
        .await
        .unwrap();
    owner
        .execute(
            "INSERT INTO public.customer_segment_history \
                 (customer_id, run_id, label_code, valid_from) \
             VALUES ($1, $2, NULL, '2001-01-02T06:00:00Z')",
            &[&who, &runs[1]],
        )
        .await
        .unwrap();

    let (status, report) = json(
        &stack,
        &format!("{MIGRATIONS}?from=2001-01-02&to=2001-01-02&group_by=segment"),
    )
    .await;
    assert_eq!(status, 200, "{report}");
    let rows = report["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 1, "{report}");
    let row = &rows[0];
    assert_eq!(row["from_segment_code"], "LOYAL");
    assert!(row["segment_code"].is_null());
    assert_eq!(row["segment_state"], "unassigned_result");
    // An unknown version is not a version: nothing says the model did not move.
    assert!(row["model_version_id"].is_null());
    assert_eq!(count(row, "methodological_changes"), 1);
    assert_eq!(count(row, "changes"), 0);
    assert_eq!(count(row, "customers_migrated"), 0);
    assert_eq!(row["synthetic"], false, "both runs are OBSERVED");
    assert_eq!(report["data_origin"]["synthetic"], false);
    assert_eq!(report["data_origin"]["spans_model_versions"], true);
    assert_eq!(report["data_origin"]["model_version_ids"], json!([]));

    // The XML omits what is null and validates.
    let token = stack.token("ANALYST");
    let (_, xml) = send(
        &stack.app,
        "GET",
        &format!("{MIGRATIONS}?from=2001-01-02&to=2001-01-02&group_by=segment"),
        Some(&token),
        "application/xml",
    )
    .await;
    assert_valid_xml(&xml);
    assert!(
        xml.contains("<segment_state>unassigned_result</segment_state>"),
        "{xml}"
    );
    assert!(!xml.contains("<model_version_id"), "{xml}");

    remove_customer(&owner, who).await;
    for run in runs {
        owner
            .execute(
                "DELETE FROM public.segmentation_run WHERE run_id = $1",
                &[&run],
            )
            .await
            .unwrap();
    }
    stack.finish().await;
}

#[tokio::test]
async fn the_query_is_validated_the_shape_is_the_contracts_and_only_four_roles_read_it() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    let admin = stack.token("ADMIN");

    for (query, field, code) in [
        ("to=2020-03-31", "from", "required"),
        ("from=2020-03-31&to=2020-03-01", "to", "to_before_from"),
        (
            "from=2020-03-01&to=2020-03-31&group_by=store",
            "group_by",
            "dimension_not_supported",
        ),
        (
            "from=2020-03-01&to=2020-03-31&group_by=segment,channel",
            "group_by",
            "dimension_not_supported",
        ),
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
        (
            "from=2020-03-01&to=2020-03-31&model_version_id=0",
            "model_version_id",
            "invalid_format",
        ),
        (
            "from=2020-03-01&to=2020-03-31&model_version_id=one",
            "model_version_id",
            "invalid_format",
        ),
    ] {
        let (status, body) = json_as(&stack, &format!("{MIGRATIONS}?{query}"), &admin).await;
        assert_eq!(status, 422, "{query}: {body}");
        assert_eq!(body["code"], "invalid_query", "{body}");
        assert_eq!(body["details"][0]["field"], field, "{body}");
        assert_eq!(body["details"][0]["code"], code, "{body}");
    }

    // The shape of a row and of the data origin is the example's.
    let day = day_of(&owner, 8).await;
    let (_, report) = json(
        &stack,
        &format!("{MIGRATIONS}?from={day}&to={day}&group_by=segment"),
    )
    .await;
    let example: Value = serde_json::from_str(
        &std::fs::read_to_string("contracts/analytics-kpis/examples/migrations.json").unwrap(),
    )
    .unwrap();
    let keys =
        |value: &Value| -> Vec<String> { value.as_object().unwrap().keys().cloned().collect() };
    assert_eq!(keys(&report), keys(&example));
    assert_eq!(keys(&report["rows"][0]), keys(&example["rows"][0]));
    assert_eq!(keys(&report["data_origin"]), keys(&example["data_origin"]));
    let codes = |row: &Value| -> Vec<String> {
        row["measures"]
            .as_array()
            .unwrap()
            .iter()
            .map(|m| m["code"].as_str().unwrap().to_owned())
            .collect()
    };
    assert_eq!(codes(&report["rows"][0]), codes(&example["rows"][0]));
    assert_eq!(
        report["store_scope"],
        json!({"restricted": false, "store_ids": []})
    );

    let (_, xml) = send(
        &stack.app,
        "GET",
        &format!("{MIGRATIONS}?from={day}&to={day}&group_by=segment,period"),
        Some(&admin),
        "application/xml",
    )
    .await;
    assert_valid_xml(&xml);
    assert!(xml.contains("<from_segment_code>"), "{xml}");
    assert!(xml.contains("<common:PageInfo>"), "{xml}");
    // An empty report validates too.
    let (_, xml) = send(
        &stack.app,
        "GET",
        &format!("{MIGRATIONS}?from=2019-01-01&to=2019-01-02"),
        Some(&admin),
        "application/xml",
    )
    .await;
    assert_valid_xml(&xml);

    // A change has no store: the store-scoped roles and every other role are
    // refused, and only the four that read analytics read it.
    for role in ROLES {
        let (status, body) = json_as(
            &stack,
            &format!("{MIGRATIONS}?from={day}&to={day}"),
            &stack.token(role),
        )
        .await;
        let expected = if READERS.contains(&role) { 200 } else { 403 };
        assert_eq!(status, expected, "{role}: {body}");
    }

    stack.finish().await;
}
