//! Spend (`spend`, definition version 1): the net amount, the customers who
//! bought and the average spend per customer, grouped by any of segment, store,
//! channel and period (F26-03).
//!
//! It reads the same two views as net sales, so a figure that both reports
//! share, the net amount, is the one number (ADR-0062). A customer who bought
//! twice counts once as active, and one who bought in two stores counts once in
//! each store and once in a total. A segment is the label a customer holds now
//! (RN-21); `model_version_ids` names the model versions those labels come from,
//! and `spans_model_versions` is true when there are two or more (ADR-0064).

use chrono::NaiveDate;
use indexmap::IndexMap;
use mosaiq_kit::{ApiError, Database, Value};

use crate::net_sales::{measure, ASSIGNMENT_VIEW, LABEL_VIEW, SALES_VIEW};
use crate::query::{Dimension, SalesQuery};
use crate::scope::Scope;

pub const KPI: &str = "spend";
pub const DEFINITION_VERSION: i64 = 1;
/// Read through the label's run, to name the versions behind a segment.
pub const VERSION_VIEW: &str = "api_models.run_version_v1";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub period_start: Option<NaiveDate>,
    pub period_end: Option<NaiveDate>,
    pub segment_state: Option<String>,
    pub segment_code: Option<String>,
    pub store_id: Option<i32>,
    pub channel_id: Option<i32>,
    pub synthetic: bool,
    pub net_amount: String,
    pub active_customers: String,
    /// Absent when no customer bought.
    pub average_spend: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub rows: Vec<Row>,
    /// Rows over every page.
    pub total: u64,
    /// Whether any row of any page is synthetic.
    pub synthetic: bool,
    /// The model versions of the labels the figures group or filter by.
    pub model_version_ids: Vec<i64>,
}

/// Whether the report needs the labels.
pub fn reads_labels(query: &SalesQuery) -> bool {
    query.groups(Dimension::Segment) || query.segment_code.is_some()
}

/// The views the figures were read from, in the order the contract lists them.
pub fn origin_views(query: &SalesQuery, scope: &Scope) -> Vec<&'static str> {
    let mut views = vec![SALES_VIEW];
    if reads_labels(query) {
        views.push(LABEL_VIEW);
        views.push(VERSION_VIEW);
    }
    if scope.restricted {
        views.push(ASSIGNMENT_VIEW);
    }
    views
}

/// One page of the report. The statement is fixed; every value is a parameter.
pub async fn read(
    db: &Database,
    query: &SalesQuery,
    scope: &Scope,
    page: u32,
    size: u32,
) -> Result<Report, ApiError> {
    if scope.is_empty() {
        return Ok(Report::default());
    }
    let connection = db.connection().await?;
    let limit = i64::from(size);
    let offset = i64::from(page.saturating_sub(1)) * limit;
    let granularity = query.granularity.map_or("month", |g| g.code());
    let store_filter = scope.filter();
    // A store-scoped caller grouped by store alone sees every assigned store,
    // one with no sale as zeros, as net sales does.
    let zero_stores =
        (scope.restricted && query.group_by == [Dimension::Store] && !scope.store_ids.is_empty())
            .then(|| scope.store_ids.clone());
    // With no dimension the report is one row for the whole window.
    let zero_total = query.group_by.is_empty();
    let rows = connection
        .query(
            "WITH scoped AS ( \
                 SELECT l.customer_id, l.store_id::integer AS store_id, \
                        l.channel_id::integer AS channel_id, l.occurred_at, \
                        l.net_amount, l.data_origin, \
                        c.state AS segment_state, c.label_code AS segment_code, \
                        v.version_id \
                 FROM api_sales.net_purchase_line_v1 AS l \
                 LEFT JOIN api_segments.current_label_v1 AS c \
                        ON $8::boolean AND c.customer_id = l.customer_id \
                 LEFT JOIN api_models.run_version_v1 AS v \
                        ON $8::boolean AND v.run_id = c.run_id \
                 WHERE l.occurred_at >= $1 AND l.occurred_at < $2 \
                   AND ($3::integer[] IS NULL OR l.store_id = ANY($3::integer[])) \
                   AND ($4::integer IS NULL OR l.channel_id = $4::integer) \
                   AND ($5::text IS NULL OR c.label_code = $5::text)), \
             aggregated AS ( \
                 SELECT CASE WHEN $6::boolean \
                             THEN date_trunc($7::text, occurred_at AT TIME ZONE 'UTC')::date \
                        END AS period_start, \
                        CASE WHEN $9::boolean \
                             THEN coalesce(segment_state, 'no_open_assignment') END AS segment_state, \
                        CASE WHEN $9::boolean THEN segment_code END AS segment_code, \
                        CASE WHEN $10::boolean THEN store_id END AS store_id, \
                        CASE WHEN $11::boolean THEN channel_id END AS channel_id, \
                        bool_or(data_origin = 'SEEDED') AS synthetic, \
                        sum(net_amount) AS net_amount, \
                        count(DISTINCT customer_id) AS active_customers \
                 FROM scoped \
                 GROUP BY 1, 2, 3, 4, 5), \
             combined AS ( \
                 SELECT period_start, segment_state, segment_code, store_id, channel_id, \
                        synthetic, net_amount::text AS net_amount, \
                        active_customers::text AS active_customers, \
                        CASE WHEN active_customers > 0 \
                             THEN round(net_amount / active_customers, 2)::text END \
                            AS average_spend \
                 FROM aggregated \
                 UNION ALL \
                 SELECT NULL::date, NULL::text, NULL::text, z.store_id, NULL::integer, \
                        false, '0.00', '0', NULL::text \
                 FROM unnest($12::integer[]) AS z(store_id) \
                 WHERE NOT EXISTS (SELECT 1 FROM aggregated a WHERE a.store_id = z.store_id) \
                 UNION ALL \
                 SELECT NULL::date, NULL::text, NULL::text, NULL::integer, NULL::integer, \
                        false, '0.00', '0', NULL::text \
                 WHERE $13::boolean AND NOT EXISTS (SELECT 1 FROM aggregated)) \
             SELECT t.total, t.synthetic, t.versions, \
                    p.period_start, \
                    p.period_start + CASE $7::text WHEN 'day' THEN 1 WHEN 'week' THEN 7 \
                        ELSE (p.period_start + interval '1 month')::date \
                             - p.period_start END - 1 AS period_end, \
                    p.segment_state, p.segment_code, p.store_id, p.channel_id, \
                    p.synthetic, p.net_amount, p.active_customers, p.average_spend \
             FROM (SELECT count(*) AS total, coalesce(bool_or(synthetic), false) AS synthetic, \
                          (SELECT coalesce(array_agg(DISTINCT version_id ORDER BY version_id), \
                                           '{}'::bigint[]) \
                             FROM scoped WHERE version_id IS NOT NULL) AS versions \
                   FROM combined) AS t \
             LEFT JOIN LATERAL (SELECT * FROM combined \
                                 ORDER BY period_start, segment_code NULLS LAST, \
                                          segment_state, store_id, channel_id \
                                 LIMIT $14 OFFSET $15) AS p ON TRUE",
            &[
                &query.window_start(),
                &query.window_end(),
                &store_filter,
                &query.channel_id,
                &query.segment_code,
                &query.groups(Dimension::Period),
                &granularity,
                &reads_labels(query),
                &query.groups(Dimension::Segment),
                &query.groups(Dimension::Store),
                &query.groups(Dimension::Channel),
                &zero_stores,
                &zero_total,
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
        model_version_ids: first
            .map(|row| row.try_get::<_, Vec<i64>>(2))
            .transpose()?
            .unwrap_or_default(),
    };
    for row in &rows {
        // The lateral join yields one all-null row for a page past the end.
        let Some(net_amount) = row.try_get::<_, Option<String>>(10)? else {
            continue;
        };
        report.rows.push(Row {
            period_start: row.try_get(3)?,
            period_end: row.try_get(4)?,
            segment_state: row.try_get(5)?,
            segment_code: row.try_get(6)?,
            store_id: row.try_get(7)?,
            channel_id: row.try_get(8)?,
            synthetic: row.try_get(9)?,
            net_amount,
            active_customers: row.try_get(11)?,
            average_spend: row.try_get(12)?,
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
        members.insert("from_segment_code".into(), Value::Null);
        members.insert("store_id".into(), Value::from(self.store_id));
        members.insert("channel_id".into(), Value::from(self.channel_id));
        members.insert("campaign_id".into(), Value::Null);
        members.insert("model_version_id".into(), Value::Null);
        members.insert("synthetic".into(), Value::from(self.synthetic));
        let mut measures = vec![
            measure("net_amount", "currency", &self.net_amount),
            measure("active_customers", "count", &self.active_customers),
        ];
        if let Some(average) = &self.average_spend {
            measures.push(measure("average_spend", "currency", average));
        }
        members.insert("measures".into(), Value::List(measures));
        Value::Object(members)
    }
}
