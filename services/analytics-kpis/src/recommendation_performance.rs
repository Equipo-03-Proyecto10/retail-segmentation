//! Recommendation performance (`recommendation-performance`, definition version 1):
//! impressions, clicks and purchases, grouped by any of segment, store and period
//! (F26-04, #849, ADR-0090).
//!
//! Each measure is counted from its own origin and none is derived from another.
//! Impressions and clicks are the rows of `api_recommendations.interaction_v1` of
//! their kind; purchases are the rows of `api_recommendations.recommended_purchase_v1`.
//! A purchase is therefore never inferred from a click, and a click never raises an
//! impression. An interaction carries no channel, so `channel` is not a dimension.
//! An interaction or a purchase falls in the window at its own `occurred_at`. A
//! segment is the label a customer holds now (RN-21); an interaction with no customer
//! has none and reads `no_open_assignment`. The report holds counts and no customer.

use chrono::NaiveDate;
use indexmap::IndexMap;
use mosaiq_kit::{ApiError, Database, Value};

use crate::net_sales::{measure, ASSIGNMENT_VIEW, LABEL_VIEW};
use crate::query::{Dimension, SalesQuery};
use crate::scope::Scope;

pub const KPI: &str = "recommendation-performance";
pub const DEFINITION_VERSION: i64 = 1;
pub const INTERACTION_VIEW: &str = "api_recommendations.interaction_v1";
pub const PURCHASE_VIEW: &str = "api_recommendations.recommended_purchase_v1";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub period_start: Option<NaiveDate>,
    pub period_end: Option<NaiveDate>,
    pub segment_state: Option<String>,
    pub segment_code: Option<String>,
    pub store_id: Option<i32>,
    pub synthetic: bool,
    pub impressions: String,
    pub clicks: String,
    pub purchases: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub rows: Vec<Row>,
    /// Rows over every page.
    pub total: u64,
    /// Whether any row of any page is synthetic.
    pub synthetic: bool,
}

/// Whether the report needs the labels.
pub fn reads_labels(query: &SalesQuery) -> bool {
    query.groups(Dimension::Segment) || query.segment_code.is_some()
}

/// The views the figures were read from, in the order the contract lists them.
pub fn origin_views(query: &SalesQuery, scope: &Scope) -> Vec<&'static str> {
    let mut views = vec![INTERACTION_VIEW, PURCHASE_VIEW];
    if reads_labels(query) {
        views.push(LABEL_VIEW);
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
    // one with no event as zeros, as net sales does.
    let zero_stores =
        (scope.restricted && query.group_by == [Dimension::Store] && !scope.store_ids.is_empty())
            .then(|| scope.store_ids.clone());
    // With no dimension the report is one row for the whole window.
    let zero_total = query.group_by.is_empty();
    let rows = connection
        .query(
            "WITH events AS ( \
                 SELECT i.kind, i.customer_id, i.store_id::integer AS store_id, \
                        i.occurred_at, false AS seeded \
                 FROM api_recommendations.interaction_v1 AS i \
                 WHERE i.occurred_at >= $1 AND i.occurred_at < $2 \
                   AND ($3::integer[] IS NULL OR i.store_id = ANY($3::integer[])) \
                 UNION ALL \
                 SELECT 'PURCHASE', p.customer_id, p.store_id::integer, \
                        p.occurred_at, p.data_origin = 'SEEDED' \
                 FROM api_recommendations.recommended_purchase_v1 AS p \
                 WHERE p.occurred_at >= $1 AND p.occurred_at < $2 \
                   AND ($3::integer[] IS NULL OR p.store_id = ANY($3::integer[]))), \
             scoped AS ( \
                 SELECT e.kind, e.store_id, e.occurred_at, e.seeded, \
                        c.state AS segment_state, c.label_code AS segment_code \
                 FROM events AS e \
                 LEFT JOIN api_segments.current_label_v1 AS c \
                        ON $5::boolean AND c.customer_id = e.customer_id \
                 WHERE ($4::text IS NULL OR c.label_code = $4::text)), \
             aggregated AS ( \
                 SELECT CASE WHEN $6::boolean \
                             THEN date_trunc($7::text, occurred_at AT TIME ZONE 'UTC')::date \
                        END AS period_start, \
                        CASE WHEN $8::boolean \
                             THEN coalesce(segment_state, 'no_open_assignment') END AS segment_state, \
                        CASE WHEN $8::boolean THEN segment_code END AS segment_code, \
                        CASE WHEN $9::boolean THEN store_id END AS store_id, \
                        bool_or(seeded) AS synthetic, \
                        count(*) FILTER (WHERE kind = 'RECO_IMPRESSION') AS impressions, \
                        count(*) FILTER (WHERE kind = 'RECO_CLICK') AS clicks, \
                        count(*) FILTER (WHERE kind = 'PURCHASE') AS purchases \
                 FROM scoped \
                 GROUP BY 1, 2, 3, 4), \
             combined AS ( \
                 SELECT period_start, segment_state, segment_code, store_id, synthetic, \
                        impressions::text AS impressions, clicks::text AS clicks, \
                        purchases::text AS purchases \
                 FROM aggregated \
                 UNION ALL \
                 SELECT NULL::date, NULL::text, NULL::text, z.store_id, false, '0', '0', '0' \
                 FROM unnest($10::integer[]) AS z(store_id) \
                 WHERE NOT EXISTS (SELECT 1 FROM aggregated a WHERE a.store_id = z.store_id) \
                 UNION ALL \
                 SELECT NULL::date, NULL::text, NULL::text, NULL::integer, false, '0', '0', '0' \
                 WHERE $11::boolean AND NOT EXISTS (SELECT 1 FROM aggregated)) \
             SELECT t.total, t.synthetic, \
                    p.period_start, \
                    p.period_start + CASE $7::text WHEN 'day' THEN 1 WHEN 'week' THEN 7 \
                        ELSE (p.period_start + interval '1 month')::date \
                             - p.period_start END - 1 AS period_end, \
                    p.segment_state, p.segment_code, p.store_id, \
                    p.synthetic, p.impressions, p.clicks, p.purchases \
             FROM (SELECT count(*) AS total, coalesce(bool_or(synthetic), false) AS synthetic \
                   FROM combined) AS t \
             LEFT JOIN LATERAL (SELECT * FROM combined \
                                 ORDER BY period_start, segment_code NULLS LAST, \
                                          segment_state, store_id \
                                 LIMIT $12 OFFSET $13) AS p ON TRUE",
            &[
                &query.window_start(),
                &query.window_end(),
                &store_filter,
                &query.segment_code,
                &reads_labels(query),
                &query.groups(Dimension::Period),
                &granularity,
                &query.groups(Dimension::Segment),
                &query.groups(Dimension::Store),
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
    };
    for row in &rows {
        // The lateral join yields one all-null row for a page past the end.
        let Some(impressions) = row.try_get::<_, Option<String>>(8)? else {
            continue;
        };
        report.rows.push(Row {
            period_start: row.try_get(2)?,
            period_end: row.try_get(3)?,
            segment_state: row.try_get(4)?,
            segment_code: row.try_get(5)?,
            store_id: row.try_get(6)?,
            synthetic: row.try_get(7)?,
            impressions,
            clicks: row.try_get(9)?,
            purchases: row.try_get(10)?,
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
        members.insert("channel_id".into(), Value::Null);
        members.insert("campaign_id".into(), Value::Null);
        members.insert("model_version_id".into(), Value::Null);
        members.insert("synthetic".into(), Value::from(self.synthetic));
        members.insert(
            "measures".into(),
            Value::List(vec![
                measure("impressions", "count", &self.impressions),
                measure("clicks", "count", &self.clicks),
                measure("purchases", "count", &self.purchases),
            ]),
        );
        Value::Object(members)
    }
}
