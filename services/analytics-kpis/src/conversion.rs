//! Conversion after exposure (`conversion`, definition version 1): of the customers
//! exposed in a bucket, the share who bought within 7 days (F26-04, #849).
//!
//! A customer is exposed at the first recorded recommendation impression
//! (`api_recommendations.interaction_v1`) or campaign exposure
//! (`api_experiments.exposure_v1`) in the bucket, and converts with at least one net
//! purchase (`api_sales.net_purchase_line_v1`), in any store, within 7 days after that
//! instant. It is observational and never an effect: the effect of a campaign is
//! `campaign-performance`. A bucket is a period, a segment (the label the customer
//! holds now, RN-21) and a store; a customer who is exposed in two buckets counts in
//! each. An impression with no customer cannot convert and is not counted.
//!
//! A campaign exposure carries no store, and neither does it carry a channel, so a
//! report that groups or filters by store reads impressions only, and one for a
//! store-scoped caller does too. Without a store the two origins are summed. A bucket
//! whose last exposure is under 7 days before `generated_at` has not had its full
//! window, so it reports `converted_customers` and `conversion_rate` absent. The
//! report holds counts and no customer.

use chrono::{DateTime, NaiveDate, Utc};
use indexmap::IndexMap;
use mosaiq_kit::{ApiError, Database, Value};

use crate::net_sales::{measure, ASSIGNMENT_VIEW, LABEL_VIEW, SALES_VIEW};
use crate::query::{Dimension, SalesQuery};
use crate::scope::Scope;

pub const KPI: &str = "conversion";
pub const DEFINITION_VERSION: i64 = 1;
pub const INTERACTION_VIEW: &str = "api_recommendations.interaction_v1";
pub const EXPOSURE_VIEW: &str = "api_experiments.exposure_v1";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub period_start: Option<NaiveDate>,
    pub period_end: Option<NaiveDate>,
    pub segment_state: Option<String>,
    pub segment_code: Option<String>,
    pub store_id: Option<i32>,
    pub synthetic: bool,
    pub exposed_customers: String,
    /// Absent while the bucket's 7 days are not over.
    pub converted_customers: Option<String>,
    /// Absent with `converted_customers`, and when nobody was exposed.
    pub conversion_rate: Option<String>,
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

/// Whether campaign exposures are read: they carry no store, so they are left out
/// of a report that groups by store or is confined to one or more stores.
pub fn reads_campaign_exposures(query: &SalesQuery, scope: &Scope) -> bool {
    !query.groups(Dimension::Store) && scope.filter().is_none()
}

/// The views the figures were read from, in the order the contract lists them.
pub fn origin_views(query: &SalesQuery, scope: &Scope) -> Vec<&'static str> {
    let mut views = Vec::new();
    if reads_campaign_exposures(query, scope) {
        views.push(EXPOSURE_VIEW);
    }
    views.push(INTERACTION_VIEW);
    views.push(SALES_VIEW);
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
    let store_filter = scope.filter();
    let zero_stores =
        (scope.restricted && query.group_by == [Dimension::Store] && !scope.store_ids.is_empty())
            .then(|| scope.store_ids.clone());
    let zero_total = query.group_by.is_empty();
    let rows = connection
        .query(
            "WITH exposures AS ( \
                 SELECT i.customer_id, i.occurred_at AS exposed_at, \
                        i.store_id::integer AS store_id, false AS seeded \
                 FROM api_recommendations.interaction_v1 AS i \
                 WHERE i.kind = 'RECO_IMPRESSION' AND i.customer_id IS NOT NULL \
                   AND i.occurred_at >= $1 AND i.occurred_at < $2 \
                   AND ($3::integer[] IS NULL OR i.store_id = ANY($3::integer[])) \
                 UNION ALL \
                 SELECT x.customer_id, x.exposed_at, NULL::integer, x.data_origin <> 'OBSERVED' \
                 FROM api_experiments.exposure_v1 AS x \
                 WHERE $4::boolean \
                   AND x.exposed_at >= $1 AND x.exposed_at < $2), \
             scoped AS ( \
                 SELECT e.customer_id, e.exposed_at, e.store_id, e.seeded, \
                        c.state AS segment_state, c.label_code AS segment_code \
                 FROM exposures AS e \
                 LEFT JOIN api_segments.current_label_v1 AS c \
                        ON $6::boolean AND c.customer_id = e.customer_id \
                 WHERE ($5::text IS NULL OR c.label_code = $5::text)), \
             keyed AS ( \
                 SELECT customer_id, exposed_at, seeded, \
                        CASE WHEN $7::boolean \
                             THEN date_trunc($8::text, exposed_at AT TIME ZONE 'UTC')::date \
                        END AS period_start, \
                        CASE WHEN $9::boolean \
                             THEN coalesce(segment_state, 'no_open_assignment') END AS segment_state, \
                        CASE WHEN $9::boolean THEN segment_code END AS segment_code, \
                        CASE WHEN $10::boolean THEN store_id END AS store_id \
                 FROM scoped), \
             firsts AS ( \
                 SELECT customer_id, period_start, segment_state, segment_code, store_id, \
                        min(exposed_at) AS first_at, max(exposed_at) AS last_at, \
                        bool_or(seeded) AS seeded \
                 FROM keyed \
                 GROUP BY 1, 2, 3, 4, 5), \
             judged AS ( \
                 SELECT f.*, b.customer_id IS NOT NULL AS converted, \
                        coalesce(b.seeded, false) AS bought_seeded \
                 FROM firsts AS f \
                 LEFT JOIN LATERAL ( \
                     SELECT l.customer_id, bool_or(l.data_origin = 'SEEDED') AS seeded \
                     FROM api_sales.net_purchase_line_v1 AS l \
                     WHERE l.customer_id = f.customer_id \
                       AND l.occurred_at >= f.first_at \
                       AND l.occurred_at <= f.first_at + interval '7 days' \
                     GROUP BY l.customer_id) AS b ON TRUE), \
             aggregated AS ( \
                 SELECT period_start, segment_state, segment_code, store_id, \
                        bool_or(seeded OR bought_seeded) AS synthetic, \
                        count(*) AS exposed, \
                        count(*) FILTER (WHERE converted) AS converted, \
                        max(last_at) + interval '7 days' > $11::timestamptz AS pending \
                 FROM judged \
                 GROUP BY 1, 2, 3, 4), \
             combined AS ( \
                 SELECT period_start, segment_state, segment_code, store_id, synthetic, \
                        exposed::text AS exposed, \
                        CASE WHEN NOT pending THEN converted::text END AS converted, \
                        CASE WHEN NOT pending AND exposed > 0 \
                             THEN round(converted::numeric / exposed, 4)::text END AS rate \
                 FROM aggregated \
                 UNION ALL \
                 SELECT NULL::date, NULL::text, NULL::text, z.store_id, false, '0', '0', NULL::text \
                 FROM unnest($12::integer[]) AS z(store_id) \
                 WHERE NOT EXISTS (SELECT 1 FROM aggregated a WHERE a.store_id = z.store_id) \
                 UNION ALL \
                 SELECT NULL::date, NULL::text, NULL::text, NULL::integer, false, '0', '0', NULL::text \
                 WHERE $13::boolean AND NOT EXISTS (SELECT 1 FROM aggregated)) \
             SELECT t.total, t.synthetic, \
                    p.period_start, \
                    p.period_start + CASE $8::text WHEN 'day' THEN 1 WHEN 'week' THEN 7 \
                        ELSE (p.period_start + interval '1 month')::date \
                             - p.period_start END - 1 AS period_end, \
                    p.segment_state, p.segment_code, p.store_id, \
                    p.synthetic, p.exposed, p.converted, p.rate \
             FROM (SELECT count(*) AS total, coalesce(bool_or(synthetic), false) AS synthetic \
                   FROM combined) AS t \
             LEFT JOIN LATERAL (SELECT * FROM combined \
                                 ORDER BY period_start, segment_code NULLS LAST, \
                                          segment_state, store_id \
                                 LIMIT $14 OFFSET $15) AS p ON TRUE",
            &[
                &query.window_start(),
                &query.window_end(),
                &store_filter,
                &reads_campaign_exposures(query, scope),
                &query.segment_code,
                &reads_labels(query),
                &query.groups(Dimension::Period),
                &granularity,
                &query.groups(Dimension::Segment),
                &query.groups(Dimension::Store),
                &generated_at,
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
        let Some(exposed_customers) = row.try_get::<_, Option<String>>(8)? else {
            continue;
        };
        report.rows.push(Row {
            period_start: row.try_get(2)?,
            period_end: row.try_get(3)?,
            segment_state: row.try_get(4)?,
            segment_code: row.try_get(5)?,
            store_id: row.try_get(6)?,
            synthetic: row.try_get(7)?,
            exposed_customers,
            converted_customers: row.try_get(9)?,
            conversion_rate: row.try_get(10)?,
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
        let mut measures = vec![measure(
            "exposed_customers",
            "count",
            &self.exposed_customers,
        )];
        if let Some(converted) = &self.converted_customers {
            measures.push(measure("converted_customers", "count", converted));
        }
        if let Some(rate) = &self.conversion_rate {
            measures.push(measure("conversion_rate", "ratio", rate));
        }
        members.insert("measures".into(), Value::List(measures));
        Value::Object(members)
    }
}
