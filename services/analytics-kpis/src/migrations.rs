//! Segment migrations (`migrations`, definition version 1): changes of a
//! customer's segment label within one model version (F26-03).
//!
//! It reads `api_segments.assignment_change_v1`, one row per change of label
//! between two consecutive assignments. A change whose two sides belong to two
//! model versions, or to a version nobody has linked, is methodological: the
//! model moved, not necessarily the customer, so it is counted in
//! `methodological_changes` and never in `changes` or `customers_migrated`
//! (ADR-0064). A row never mixes versions: it is keyed by the version entered.
//! `data_origin.spans_model_versions` is true, and `model_version_ids` names the
//! versions on either side, when the window holds a methodological change.
//!
//! The migration matrix of segments-service compares each customer's label at
//! two instants. For a customer with one change between them, the matrix cell
//! (label before, label after) and the row (`from_segment_code`, `segment_code`)
//! hold the same customer; a customer with two changes in the window is one cell
//! of the matrix and two changes here, which is why `changes` and
//! `customers_migrated` are apart.

use chrono::NaiveDate;
use indexmap::IndexMap;
use mosaiq_kit::{ApiError, Database, Value};

use crate::net_sales::measure;
use crate::query::{Dimension, MigrationQuery};

pub const KPI: &str = "migrations";
pub const DEFINITION_VERSION: i64 = 1;
pub const CHANGE_VIEW: &str = "api_segments.assignment_change_v1";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub period_start: Option<NaiveDate>,
    pub period_end: Option<NaiveDate>,
    /// `assigned` or `unassigned_result`: what the change entered.
    pub segment_state: Option<String>,
    /// The label entered.
    pub segment_code: Option<String>,
    /// The label left; null when the earlier run found no label.
    pub from_segment_code: Option<String>,
    /// The version of the later assignment; null while it is unlinked.
    pub model_version_id: Option<i64>,
    pub synthetic: bool,
    pub customers_migrated: String,
    pub changes: String,
    pub methodological_changes: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub rows: Vec<Row>,
    /// Rows over every page.
    pub total: u64,
    /// Whether any row of any page is synthetic.
    pub synthetic: bool,
    /// Whether the window holds a methodological change.
    pub spans_model_versions: bool,
    /// The model versions on either side of a change in the window.
    pub model_version_ids: Vec<i64>,
}

/// One page of the report. The statement is fixed; every value is a parameter.
pub async fn read(
    db: &Database,
    query: &MigrationQuery,
    page: u32,
    size: u32,
) -> Result<Report, ApiError> {
    let connection = db.connection().await?;
    let limit = i64::from(size);
    let offset = i64::from(page.saturating_sub(1)) * limit;
    let granularity = query.granularity.map_or("month", |g| g.code());
    let rows = connection
        .query(
            "WITH changes AS ( \
                 SELECT c.customer_id, c.previous_label_code, c.new_label_code, \
                        c.previous_version_id, c.new_version_id, c.changed_at, c.data_origin, \
                        (c.previous_version_id IS NULL OR c.new_version_id IS NULL \
                         OR c.previous_version_id <> c.new_version_id) AS methodological \
                 FROM api_segments.assignment_change_v1 AS c \
                 WHERE c.changed_at >= $1 AND c.changed_at < $2 \
                   AND ($3::text IS NULL OR c.new_label_code = $3::text) \
                   AND ($4::bigint IS NULL OR c.new_version_id = $4::bigint)), \
             aggregated AS ( \
                 SELECT CASE WHEN $5::boolean \
                             THEN date_trunc($6::text, changed_at AT TIME ZONE 'UTC')::date \
                        END AS period_start, \
                        CASE WHEN $7::boolean THEN CASE WHEN new_label_code IS NULL \
                             THEN 'unassigned_result' ELSE 'assigned' END END AS segment_state, \
                        CASE WHEN $7::boolean THEN new_label_code END AS segment_code, \
                        CASE WHEN $7::boolean THEN previous_label_code END AS from_segment_code, \
                        new_version_id AS model_version_id, \
                        bool_or(data_origin = 'SEEDED') AS synthetic, \
                        count(DISTINCT customer_id) FILTER (WHERE NOT methodological) \
                            AS customers_migrated, \
                        count(*) FILTER (WHERE NOT methodological) AS changes, \
                        count(*) FILTER (WHERE methodological) AS methodological_changes \
                 FROM changes \
                 GROUP BY 1, 2, 3, 4, 5) \
             SELECT t.total, t.synthetic, t.spans, t.versions, \
                    p.period_start, \
                    p.period_start + CASE $6::text WHEN 'day' THEN 1 WHEN 'week' THEN 7 \
                        ELSE (p.period_start + interval '1 month')::date \
                             - p.period_start END - 1 AS period_end, \
                    p.segment_state, p.segment_code, p.from_segment_code, p.model_version_id, \
                    p.synthetic, p.customers_migrated::text, p.changes::text, \
                    p.methodological_changes::text \
             FROM (SELECT (SELECT count(*) FROM aggregated) AS total, \
                          (SELECT coalesce(bool_or(synthetic), false) FROM aggregated) \
                              AS synthetic, \
                          (SELECT coalesce(bool_or(methodological), false) FROM changes) \
                              AS spans, \
                          (SELECT coalesce(array_agg(DISTINCT v ORDER BY v), '{}'::bigint[]) \
                             FROM changes, \
                                  unnest(ARRAY[previous_version_id, new_version_id]) AS v \
                            WHERE v IS NOT NULL) AS versions) AS t \
             LEFT JOIN LATERAL (SELECT * FROM aggregated \
                                 ORDER BY period_start, segment_code NULLS LAST, \
                                          from_segment_code NULLS LAST, model_version_id \
                                 LIMIT $8 OFFSET $9) AS p ON TRUE",
            &[
                &query.window_start(),
                &query.window_end(),
                &query.segment_code,
                &query.model_version_id,
                &query.groups(Dimension::Period),
                &granularity,
                &query.groups(Dimension::Segment),
                &limit,
                &offset,
            ],
        )
        .await?;
    let first = rows.first();
    let mut report = Report {
        rows: Vec::new(),
        total: first
            .map(|row| row.try_get::<_, i64>(0))
            .transpose()?
            .map_or(0, |total| u64::try_from(total).unwrap_or(0)),
        synthetic: first
            .map(|row| row.try_get::<_, bool>(1))
            .transpose()?
            .unwrap_or(false),
        spans_model_versions: first
            .map(|row| row.try_get::<_, bool>(2))
            .transpose()?
            .unwrap_or(false),
        model_version_ids: first
            .map(|row| row.try_get::<_, Vec<i64>>(3))
            .transpose()?
            .unwrap_or_default(),
    };
    for row in &rows {
        // The lateral join yields one all-null row for a page past the end.
        let Some(customers_migrated) = row.try_get::<_, Option<String>>(11)? else {
            continue;
        };
        report.rows.push(Row {
            period_start: row.try_get(4)?,
            period_end: row.try_get(5)?,
            segment_state: row.try_get(6)?,
            segment_code: row.try_get(7)?,
            from_segment_code: row.try_get(8)?,
            model_version_id: row.try_get(9)?,
            synthetic: row.try_get(10)?,
            customers_migrated,
            changes: row.try_get(12)?,
            methodological_changes: row.try_get(13)?,
        });
    }
    Ok(report)
}

impl Row {
    /// The row as the contract's `Row`; a member that is null is omitted from
    /// the XML by the kit.
    pub fn wire(&self) -> Value {
        let mut members: IndexMap<String, Value> = IndexMap::new();
        members.insert("period_start".into(), Value::from(self.period_start));
        members.insert("period_end".into(), Value::from(self.period_end));
        members.insert(
            "segment_code".into(),
            Value::from(self.segment_code.clone()),
        );
        members.insert(
            "segment_state".into(),
            Value::from(self.segment_state.clone()),
        );
        members.insert(
            "from_segment_code".into(),
            Value::from(self.from_segment_code.clone()),
        );
        members.insert("store_id".into(), Value::Null);
        members.insert("channel_id".into(), Value::Null);
        members.insert("campaign_id".into(), Value::Null);
        members.insert(
            "model_version_id".into(),
            Value::from(self.model_version_id),
        );
        members.insert("synthetic".into(), Value::from(self.synthetic));
        members.insert(
            "measures".into(),
            Value::List(vec![
                measure("customers_migrated", "count", &self.customers_migrated),
                measure("changes", "count", &self.changes),
                measure(
                    "methodological_changes",
                    "count",
                    &self.methodological_changes,
                ),
            ]),
        );
        Value::Object(members)
    }
}
