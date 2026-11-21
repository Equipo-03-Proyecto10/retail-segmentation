//! Net sales (`net-sales`, definition version 1): the net amount, net units and
//! net orders of the sales that still stand, grouped by any of segment, store,
//! channel and period (F26-02).
//!
//! The figure is read from `api_sales.net_purchase_line_v1`, the one definition
//! of net units (ADR-0062): a reversal lowers `net_quantity`, a line with no
//! unit left is absent, and a sale counts at its own `occurred_at`. The web
//! reads the same definition through `public.net_sale_line`, so `/reports/`
//! and this report cannot disagree. A segment is the label a customer holds
//! now, from `api_segments.current_label_v1` (RN-21). A row is `synthetic`
//! when any sale it aggregates is `SEEDED` (ADR-0052).

use chrono::NaiveDate;
use indexmap::IndexMap;
use mosaiq_kit::{ApiError, Database, Value};

use crate::query::{Dimension, SalesQuery};
use crate::scope::Scope;

pub const KPI: &str = "net-sales";
pub const DEFINITION_VERSION: i64 = 1;
pub const SALES_VIEW: &str = "api_sales.net_purchase_line_v1";
pub const LABEL_VIEW: &str = "api_segments.current_label_v1";
pub const ASSIGNMENT_VIEW: &str = "api_users.store_assignment_v1";

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
    pub net_units: String,
    pub net_orders: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub rows: Vec<Row>,
    /// Rows over every page.
    pub total: u64,
    /// Whether any row of any page is synthetic.
    pub synthetic: bool,
}

/// Whether the report needs `api_segments.current_label_v1`.
pub fn reads_labels(query: &SalesQuery) -> bool {
    query.groups(Dimension::Segment) || query.segment_code.is_some()
}

/// The views the figures were read from, in the order the contract lists them.
pub fn origin_views(query: &SalesQuery, scope: &Scope) -> Vec<&'static str> {
    let mut views = vec![SALES_VIEW];
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
    // one with no sale as zeros (the contract's net-sales-store-scoped example).
    let zero_stores =
        (scope.restricted && query.group_by == [Dimension::Store] && !scope.store_ids.is_empty())
            .then(|| scope.store_ids.clone());
    // With no dimension the report is one row for the whole window.
    let zero_total = query.group_by.is_empty();
    let rows = connection
        .query(
            "WITH scoped AS ( \
                 SELECT l.transaction_id, l.store_id::integer AS store_id, \
                        l.channel_id::integer AS channel_id, l.occurred_at, \
                        l.net_quantity, l.net_amount, l.data_origin, \
                        c.state AS segment_state, c.label_code AS segment_code \
                 FROM api_sales.net_purchase_line_v1 AS l \
                 LEFT JOIN api_segments.current_label_v1 AS c \
                        ON $8::boolean AND c.customer_id = l.customer_id \
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
                        sum(net_amount)::text AS net_amount, \
                        sum(net_quantity)::bigint::text AS net_units, \
                        count(DISTINCT transaction_id)::text AS net_orders \
                 FROM scoped \
                 GROUP BY 1, 2, 3, 4, 5), \
             combined AS ( \
                 SELECT period_start, segment_state, segment_code, store_id, channel_id, \
                        synthetic, net_amount, net_units, net_orders \
                 FROM aggregated \
                 UNION ALL \
                 SELECT NULL::date, NULL::text, NULL::text, z.store_id, NULL::integer, \
                        false, '0.00', '0', '0' \
                 FROM unnest($12::integer[]) AS z(store_id) \
                 WHERE NOT EXISTS (SELECT 1 FROM aggregated a WHERE a.store_id = z.store_id) \
                 UNION ALL \
                 SELECT NULL::date, NULL::text, NULL::text, NULL::integer, NULL::integer, \
                        false, '0.00', '0', '0' \
                 WHERE $13::boolean AND NOT EXISTS (SELECT 1 FROM aggregated)) \
             SELECT t.total, t.synthetic, \
                    p.period_start, \
                    p.period_start + CASE $7::text WHEN 'day' THEN 1 WHEN 'week' THEN 7 \
                        ELSE (p.period_start + interval '1 month')::date \
                             - p.period_start END - 1 AS period_end, \
                    p.segment_state, p.segment_code, p.store_id, p.channel_id, \
                    p.synthetic, p.net_amount, p.net_units, p.net_orders \
             FROM (SELECT count(*) AS total, coalesce(bool_or(synthetic), false) AS synthetic \
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
    let total = rows
        .first()
        .map(|row| row.try_get::<_, i64>(0))
        .transpose()?
        .unwrap_or(0);
    let synthetic = rows
        .first()
        .map(|row| row.try_get::<_, bool>(1))
        .transpose()?
        .unwrap_or(false);
    let mut report = Report {
        rows: Vec::new(),
        total: u64::try_from(total).unwrap_or(0),
        synthetic,
    };
    for row in &rows {
        // The lateral join yields one all-null row for a page past the end.
        let Some(net_amount) = row.try_get::<_, Option<String>>(9)? else {
            continue;
        };
        report.rows.push(Row {
            period_start: row.try_get(2)?,
            period_end: row.try_get(3)?,
            segment_state: row.try_get(4)?,
            segment_code: row.try_get(5)?,
            store_id: row.try_get(6)?,
            channel_id: row.try_get(7)?,
            synthetic: row.try_get(8)?,
            net_amount,
            net_units: row.try_get(10)?,
            net_orders: row.try_get(11)?,
        });
    }
    Ok(report)
}

pub(crate) fn measure(code: &str, unit: &str, value: &str) -> Value {
    Value::object([
        ("code", Value::from(code)),
        ("kind", Value::from("OBSERVED")),
        ("unit", Value::from(unit)),
        ("value", Value::from(value)),
        ("lower", Value::Null),
        ("upper", Value::Null),
    ])
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
        members.insert(
            "measures".into(),
            Value::List(vec![
                measure("net_amount", "currency", &self.net_amount),
                measure("net_units", "count", &self.net_units),
                measure("net_orders", "count", &self.net_orders),
            ]),
        );
        Value::Object(members)
    }
}
