//! Version-aware migration reads against the real seeded PostgreSQL.

mod support;

use chrono::{DateTime, Duration, Utc};
use mosaiq_kit::rendering::{render_json, render_xml};
use segments::{
    explanation_reply, frequency_judgement, matrix_reply, migration_movement, migration_query,
    migration_window, migrations_reply, monetary_judgement, read_migration_explanation,
    read_migration_matrix, read_migrations, recency_judgement, MigrationFilter, MigrationQuery,
    MigrationState, MigrationWindow,
};
use std::collections::HashMap;
use std::io::Write;
use std::process::{Command, Stdio};
use support::{connect, owner_url, service_database};
use uuid::Uuid;

const NAMESPACE: &str = "https://mosaiq.maxthecoder.online/xml/segments/v1";

fn assert_valid_xml(xml: &str) {
    let mut child = Command::new("xmllint")
        .args([
            "--noout",
            "--schema",
            "contracts/segments/segments.xsd",
            "-",
        ])
        .stdin(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("xmllint is installed for contract validation");
    child
        .stdin
        .take()
        .unwrap()
        .write_all(xml.as_bytes())
        .unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(
        output.status.success(),
        "{}\n{xml}",
        String::from_utf8_lossy(&output.stderr)
    );
}

async fn run_at(owner: &tokio_postgres::Client, run_id: i64) -> DateTime<Utc> {
    owner
        .query_one(
            "SELECT run_at FROM public.segmentation_run WHERE run_id = $1",
            &[&run_id],
        )
        .await
        .unwrap()
        .get(0)
}

#[tokio::test]
async fn one_version_matrix_reconciles_every_state_and_cell() {
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    let from = run_at(&owner, 28).await + Duration::minutes(1);
    let to = run_at(&owner, 29).await + Duration::minutes(1);
    let matrix = read_migration_matrix(&db, MigrationWindow { from, to })
        .await
        .unwrap();

    assert_eq!(matrix.from_model_version_id, Some(1));
    assert_eq!(matrix.to_model_version_id, Some(1));
    assert!(!matrix.methodological);
    assert_eq!(matrix.customer_count, 30);
    assert_eq!(
        matrix
            .cells
            .iter()
            .map(|cell| cell.customer_count)
            .sum::<i64>(),
        matrix.customer_count
    );
    assert_eq!(
        matrix
            .totals
            .iter()
            .map(|total| total.before_count)
            .sum::<i64>(),
        matrix.customer_count
    );
    assert_eq!(
        matrix
            .totals
            .iter()
            .map(|total| total.after_count)
            .sum::<i64>(),
        matrix.customer_count
    );
    for total in &matrix.totals {
        assert_eq!(
            total.before_count + total.entries - total.exits,
            total.after_count,
            "{} {:?}",
            total.state.state,
            total.state.label_code
        );
    }

    let json = render_json(&matrix_reply(&matrix, false), 20).unwrap();
    assert_eq!(json["customer_count"], 30);
    assert_eq!(json["methodological"], false);
    assert!(json["totals"].is_array() && json["cells"].is_array());
    let xml = render_xml(&matrix_reply(&matrix, true), NAMESPACE, 20).unwrap();
    assert!(xml.contains("<SegmentMigrationMatrix"));
    assert!(xml.contains("<methodological>false</methodological>"));
    assert!(xml.contains("<MigrationTotal>"));
    assert!(xml.contains("<MigrationCell>"));
    assert_valid_xml(&xml);
}

#[tokio::test]
async fn a_move_names_both_labels_run_date_and_rn60_variables() {
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    let from = run_at(&owner, 29).await + Duration::minutes(1);
    let to = run_at(&owner, 30).await + Duration::minutes(1);
    let customer = Uuid::parse_str("00000000-0000-0000-0000-000000000006").unwrap();
    let explanation = read_migration_explanation(&db, customer, MigrationWindow { from, to })
        .await
        .unwrap();

    let move_ = &explanation.migration;
    assert_eq!(move_.previous.state.label_code.as_deref(), Some("LOYAL"));
    assert_eq!(move_.new.state.label_code.as_deref(), Some("CHAMPION"));
    assert_eq!(move_.previous.run_id, Some(29));
    assert_eq!(move_.new.run_id, Some(30));
    assert_eq!(move_.changed_at, move_.new.valid_from);
    assert!(move_.responsible_variables.is_empty());
    assert!(move_.rank_only);
    assert_eq!(
        explanation
            .measures
            .iter()
            .map(|measure| (measure.variable.as_str(), measure.judgement.as_str()))
            .collect::<Vec<_>>(),
        [
            ("recency", "stable"),
            ("frequency", "stable"),
            ("monetary", "stable")
        ]
    );
    assert!(explanation.measures.iter().all(|measure| {
        measure.previous_value.is_some()
            && measure.new_value.is_some()
            && !measure.statement.is_empty()
    }));

    let json = render_json(&explanation_reply(&explanation, false), 20).unwrap();
    assert_eq!(json["migration"]["previous_label_code"], "LOYAL");
    assert_eq!(json["migration"]["new_label_code"], "CHAMPION");
    assert_eq!(json["measures"].as_array().unwrap().len(), 3);
    let xml = render_xml(&explanation_reply(&explanation, true), NAMESPACE, 20).unwrap();
    assert!(xml.contains("<SegmentMigrationExplanation"));
    assert!(xml.contains("<previous_label_code>LOYAL</previous_label_code>"));
    assert_eq!(xml.matches("<MigrationMeasure>").count(), 3);
    assert_valid_xml(&xml);
}

#[tokio::test]
async fn crossing_versions_is_methodological_and_excluded_from_behaviour() {
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    let from = run_at(&owner, 29).await + Duration::minutes(1);
    let to = run_at(&owner, 30).await + Duration::minutes(1);
    let matrix = read_migration_matrix(&db, MigrationWindow { from, to })
        .await
        .unwrap();
    assert_eq!(
        (matrix.from_model_version_id, matrix.to_model_version_id),
        (Some(1), Some(2))
    );
    assert!(matrix.methodological);

    let all = read_migrations(
        &db,
        &MigrationQuery {
            window: MigrationWindow { from, to },
            filter: MigrationFilter::default(),
        },
    )
    .await
    .unwrap();
    assert_eq!(all.len(), 30);
    assert!(all.iter().all(|migration| migration.methodological));
    let json = render_json(
        &migrations_reply(
            &MigrationWindow { from, to },
            &all[..2],
            1,
            2,
            all.len() as u64,
            false,
        ),
        20,
    )
    .unwrap();
    assert_eq!(json["migrations"].as_array().unwrap().len(), 2);
    assert!(json["migrations"][0]["methodological"].as_bool().unwrap());
    let xml = render_xml(
        &migrations_reply(
            &MigrationWindow { from, to },
            &all[..2],
            1,
            2,
            all.len() as u64,
            true,
        ),
        NAMESPACE,
        20,
    )
    .unwrap();
    assert_eq!(xml.matches("<SegmentMigration>").count(), 2);
    assert_valid_xml(&xml);
    let behaviour = read_migrations(
        &db,
        &MigrationQuery {
            window: MigrationWindow { from, to },
            filter: MigrationFilter {
                methodological: Some(false),
                ..MigrationFilter::default()
            },
        },
    )
    .await
    .unwrap();
    assert!(
        behaviour.is_empty(),
        "method changes never count as behaviour"
    );
}

#[test]
fn bad_periods_and_filter_combinations_are_refused() {
    let past = Utc::now() - Duration::days(2);
    let later = Utc::now() - Duration::days(1);
    let future = Utc::now() + Duration::days(1);
    for query in [
        String::new(),
        format!("from={later}&to={past}"),
        format!("from={past}&to={future}"),
        "from=nope&to=also-nope".to_owned(),
    ] {
        let error = migration_window(&query).unwrap_err();
        assert_eq!((error.status, error.code.as_str()), (422, "invalid_query"));
    }
    for suffix in [
        "direction=entries",
        "direction=entries&label_code=LOYAL&previous_state=label&previous_label_code=LOYAL&new_state=label&new_label_code=CHAMPION",
        "previous_state=label&new_state=label&new_label_code=LOYAL",
        "previous_state=new&previous_label_code=LOYAL&new_state=label&new_label_code=CHAMPION",
    ] {
        let query = format!("from={past}&to={later}&{suffix}");
        let error = migration_query(&query).unwrap_err();
        assert_eq!((error.status, error.code.as_str()), (422, "invalid_query"));
    }
}

#[test]
fn rust_port_reproduces_the_web_golden_fixture() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!("golden/segments.json"))
        .expect("the captured web fixture is JSON");
    let cases = fixture["cases"].as_array().unwrap();
    let rn39 = cases
        .iter()
        .find(|case| case["id"] == "rn39-label-comparison")
        .unwrap();
    let ordinals: HashMap<String, i16> = rn39["input"]["ordinals"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(label, value)| (label.clone(), value.as_i64().unwrap() as i16))
        .collect();
    let expected = rn39["outcome"]["migrations"].as_array().unwrap();
    let mut cells: HashMap<String, i64> = HashMap::new();
    for row in expected {
        let before = row["before"].as_str().map(str::to_owned);
        let after = row["after"].as_str().map(str::to_owned);
        let previous = MigrationState {
            state: if row["category"] == "absent_from_earlier" {
                "new".to_owned()
            } else if before.is_some() {
                "label".to_owned()
            } else {
                "unassigned".to_owned()
            },
            label_code: before.clone(),
        };
        let new = MigrationState {
            state: if after.is_some() {
                "label".to_owned()
            } else {
                "unassigned".to_owned()
            },
            label_code: after.clone(),
        };
        let movement = migration_movement(&previous, &new, &ordinals);
        let expected_movement = row["direction"].as_str().unwrap_or("not_ranked");
        assert_eq!(movement, expected_movement, "{}", row["customer_id"]);
        let before_name = before.unwrap_or_else(|| {
            if previous.state == "new" {
                "Not in earlier run".to_owned()
            } else {
                "Unassigned".to_owned()
            }
        });
        let after_name = after.unwrap_or_else(|| "Unassigned".to_owned());
        *cells
            .entry(format!("{before_name}->{after_name}"))
            .or_default() += 1;
    }
    let rn59 = cases
        .iter()
        .find(|case| case["id"] == "rn59-traceable-matrix")
        .unwrap();
    let expected_cells: HashMap<String, i64> = rn59["outcome"]["nonzero_cells"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(cell, count)| (cell.clone(), count.as_i64().unwrap()))
        .collect();
    assert_eq!(cells, expected_cells);

    let rn60 = cases
        .iter()
        .find(|case| case["id"] == "rn60-raw-value-judgement")
        .unwrap();
    let observed = [
        recency_judgement(41, 9),
        frequency_judgement(8, 8),
        monetary_judgement("1200.00", "1560.00"),
    ];
    let expected: Vec<_> = rn60["outcome"]["judgements"]
        .as_array()
        .unwrap()
        .iter()
        .map(|value| value.as_str().unwrap())
        .collect();
    assert_eq!(observed.as_slice(), expected.as_slice());
}
