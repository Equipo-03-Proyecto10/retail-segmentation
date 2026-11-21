//! Cohort retention (`retention`, definition version 1): for the customers whose
//! first net purchase ever falls in a bucket, how many bought again in the next
//! bucket (F26-03).
//!
//! By hand: three customers first buy in March and two of them buy again in
//! April; the March cohort is 3, retained is 2 and the rate is 0.6667. The first
//! purchase is read over every net purchase there is, not over the window: `from`
//! and `to` select the cohorts by the instant of that first purchase. Store and
//! channel are those of the first purchase, and the retained purchase may be
//! anywhere. A cohort whose next bucket has not ended at `generated_at` reports
//! no retained count and no rate, so a half-finished bucket is never read as a
//! loss. Sales are net of reversals (ADR-0062): a customer whose only purchase
//! was returned in full has no first purchase and is in no cohort.

use chrono::{DateTime, NaiveDate, Utc};
use indexmap::IndexMap;
use mosaiq_kit::{ApiError, Database, Value};

use crate::net_sales::{measure, ASSIGNMENT_VIEW, LABEL_VIEW, SALES_VIEW};
use crate::query::{Dimension, SalesQuery};
use crate::scope::Scope;
use crate::spend::VERSION_VIEW;

pub const KPI: &str = "retention";
pub const DEFINITION_VERSION: i64 = 1;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub period_start: NaiveDate,
    pub period_end: NaiveDate,
    pub segment_state: Option<String>,
    pub segment_code: Option<String>,
    pub store_id: Option<i32>,
    pub channel_id: Option<i32>,
    pub synthetic: bool,
    pub cohort_customers: String,
    /// Absent while the next bucket has not ended.
    pub retained_customers: Option<String>,
    /// Absent while the next bucket has not ended.
    pub retention_rate: Option<String>,
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
    generated_at: DateTime<Utc>,
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
    let rows = connection
        .query(
            "WITH firsts AS ( \
                 SELECT DISTINCT ON (l.customer_id) l.customer_id, l.occurred_at, \
                        l.store_id::integer AS store_id, l.channel_id::integer AS channel_id, \
                        l.data_origin \
                 FROM api_sales.net_purchase_line_v1 AS l \
                 WHERE l.customer_id IS NOT NULL \
                 ORDER BY l.customer_id, l.occurred_at, l.transaction_id), \
             cohort AS ( \
                 SELECT f.customer_id, f.store_id, f.channel_id, f.data_origin, \
                        date_trunc($6::text, f.occurred_at AT TIME ZONE 'UTC')::date AS bucket, \
                        CASE $6::text WHEN 'day' THEN interval '1 day' \
                             WHEN 'week' THEN interval '7 days' \
                             ELSE interval '1 month' END AS step, \
                        c.state AS segment_state, c.label_code AS segment_code, v.version_id \
                 FROM firsts AS f \
                 LEFT JOIN api_segments.current_label_v1 AS c \
                        ON $7::boolean AND c.customer_id = f.customer_id \
                 LEFT JOIN api_models.run_version_v1 AS v \
                        ON $7::boolean AND v.run_id = c.run_id \
                 WHERE f.occurred_at >= $1 AND f.occurred_at < $2 \
                   AND ($3::integer[] IS NULL OR f.store_id = ANY($3::integer[])) \
                   AND ($4::integer IS NULL OR f.channel_id = $4::integer) \
                   AND ($5::text IS NULL OR c.label_code = $5::text)), \
             bounded AS ( \
                 SELECT k.*, \
                        (k.bucket + k.step)::date AS next_start, \
                        (k.bucket + 2 * k.step)::date AS next_end \
                 FROM cohort AS k), \
             judged AS ( \
                 SELECT b.*, \
                        (b.next_end::timestamp AT TIME ZONE 'UTC') <= $11::timestamptz AS ended, \
                        EXISTS (SELECT 1 FROM api_sales.net_purchase_line_v1 AS n \
                                 WHERE n.customer_id = b.customer_id \
                                   AND n.occurred_at >= b.next_start::timestamp AT TIME ZONE 'UTC' \
                                   AND n.occurred_at < b.next_end::timestamp AT TIME ZONE 'UTC') \
                            AS bought_again, \
                        EXISTS (SELECT 1 FROM api_sales.net_purchase_line_v1 AS n \
                                 WHERE n.customer_id = b.customer_id \
                                   AND n.data_origin = 'SEEDED' \
                                   AND n.occurred_at >= b.next_start::timestamp AT TIME ZONE 'UTC' \
                                   AND n.occurred_at < b.next_end::timestamp AT TIME ZONE 'UTC') \
                            AS seeded_again \
                 FROM bounded AS b), \
             aggregated AS ( \
                 SELECT bucket AS period_start, \
                        CASE WHEN $8::boolean \
                             THEN coalesce(segment_state, 'no_open_assignment') END AS segment_state, \
                        CASE WHEN $8::boolean THEN segment_code END AS segment_code, \
                        CASE WHEN $9::boolean THEN store_id END AS store_id, \
                        CASE WHEN $10::boolean THEN channel_id END AS channel_id, \
                        bool_or(data_origin = 'SEEDED' OR (ended AND seeded_again)) AS synthetic, \
                        count(*) AS cohort_customers, \
                        CASE WHEN bool_and(ended) \
                             THEN count(*) FILTER (WHERE bought_again) END AS retained_customers \
                 FROM judged \
                 GROUP BY 1, 2, 3, 4, 5), \
             combined AS ( \
                 SELECT period_start, segment_state, segment_code, store_id, channel_id, \
                        synthetic, cohort_customers::text AS cohort_customers, \
                        retained_customers::text AS retained_customers, \
                        round(retained_customers::numeric / cohort_customers, 4)::text \
                            AS retention_rate \
                 FROM aggregated) \
             SELECT t.total, t.synthetic, t.versions, \
                    p.period_start, \
                    p.period_start + CASE $6::text WHEN 'day' THEN 1 WHEN 'week' THEN 7 \
                        ELSE (p.period_start + interval '1 month')::date \
                             - p.period_start END - 1 AS period_end, \
                    p.segment_state, p.segment_code, p.store_id, p.channel_id, \
                    p.synthetic, p.cohort_customers, p.retained_customers, p.retention_rate \
             FROM (SELECT count(*) AS total, coalesce(bool_or(synthetic), false) AS synthetic, \
                          (SELECT coalesce(array_agg(DISTINCT version_id ORDER BY version_id), \
                                           '{}'::bigint[]) \
                             FROM cohort WHERE version_id IS NOT NULL) AS versions \
                   FROM combined) AS t \
             LEFT JOIN LATERAL (SELECT * FROM combined \
                                 ORDER BY period_start, segment_code NULLS LAST, \
                                          segment_state, store_id, channel_id \
                                 LIMIT $12 OFFSET $13) AS p ON TRUE",
            &[
                &query.window_start(),
                &query.window_end(),
                &scope.filter(),
                &query.channel_id,
                &query.segment_code,
                &granularity,
                &reads_labels(query),
                &query.groups(Dimension::Segment),
                &query.groups(Dimension::Store),
                &query.groups(Dimension::Channel),
                &generated_at,
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
        let Some(period_start) = row.try_get::<_, Option<NaiveDate>>(3)? else {
            continue;
        };
        let Some(period_end) = row.try_get::<_, Option<NaiveDate>>(4)? else {
            continue;
        };
        report.rows.push(Row {
            period_start,
            period_end,
            segment_state: row.try_get(5)?,
            segment_code: row.try_get(6)?,
            store_id: row.try_get(7)?,
            channel_id: row.try_get(8)?,
            synthetic: row.try_get(9)?,
            cohort_customers: row.try_get(10)?,
            retained_customers: row.try_get(11)?,
            retention_rate: row.try_get(12)?,
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
        let mut measures = vec![measure("cohort_customers", "count", &self.cohort_customers)];
        if let (Some(retained), Some(rate)) = (&self.retained_customers, &self.retention_rate) {
            measures.push(measure("retained_customers", "count", retained));
            measures.push(measure("retention_rate", "ratio", rate));
        }
        members.insert("measures".into(), Value::List(measures));
        Value::Object(members)
    }
}
