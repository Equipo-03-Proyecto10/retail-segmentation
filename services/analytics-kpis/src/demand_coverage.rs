//! Demand by segment beside stock on hand (`getDemandCoverageReport`, F26-05):
//! for inventory planning, one row per store and category with the net units
//! demanded in the window, the units on hand now, the days of cover and the
//! units needed to reach `target_cover_days`, and the demand split by segment.
//!
//! Demand is net of reversals (ADR-0062), from `api_sales.net_purchase_line_v1`,
//! placed in the category the product has now (`api_products.product_ref_v1`).
//! Stock is `api_stock.stock_level_v1` as of `generated_at`. The buyer's segment
//! is the label held at `generated_at` (`api_segments.current_label_v1`, RN-21).
//! Only active products count, in demand and in stock. No row, segment or field
//! carries a customer identifier: the buyer is joined to a label inside the
//! statement and never selected.

use indexmap::IndexMap;
use mosaiq_kit::{ApiError, Database, Value};

use crate::query::DemandQuery;
use crate::scope::Scope;

pub const PRODUCT_VIEW: &str = "api_products.product_ref_v1";
pub const SALES_VIEW: &str = "api_sales.net_purchase_line_v1";
pub const LABEL_VIEW: &str = "api_segments.current_label_v1";
pub const STOCK_VIEW: &str = "api_stock.stock_level_v1";
pub const ASSIGNMENT_VIEW: &str = "api_users.store_assignment_v1";

/// The calculation, in words, as the contract's operation states it. Every
/// response carries it.
pub const CALCULATION: &str = "daily_demand = net_units / window_days, to 4 places. days_of_cover = on_hand / daily_demand, to 2 places, absent when net_units is 0. replenishment_units = max(0, ceil(net_units * target_cover_days / window_days) - on_hand). Demand is net of reversals over the window, on_hand is read at generated_at, and only active products count.";

/// The views the figures were read from, in the order the contract lists them.
pub fn origin_views(scope: &Scope) -> Vec<&'static str> {
    let mut views = vec![PRODUCT_VIEW, SALES_VIEW, LABEL_VIEW, STOCK_VIEW];
    if scope.restricted {
        views.push(ASSIGNMENT_VIEW);
    }
    views
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SegmentDemand {
    pub segment_code: Option<String>,
    pub segment_state: String,
    pub net_units: i64,
    pub share: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub store_id: i32,
    pub category_id: i32,
    pub net_units: i64,
    pub daily_demand: String,
    pub on_hand: i64,
    pub days_of_cover: Option<String>,
    pub replenishment_units: i64,
    pub synthetic: bool,
    pub segments: Vec<SegmentDemand>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub rows: Vec<Row>,
    /// Rows over every page.
    pub total: u64,
    /// Whether any row of any page is synthetic.
    pub synthetic: bool,
}

fn internal() -> ApiError {
    ApiError::new(
        500,
        "internal_error",
        "The service could not complete the request.",
    )
}

/// One page of the report. The statement is fixed; every value is a parameter.
pub async fn read(
    db: &Database,
    query: &DemandQuery,
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
    let store_filter = scope.filter();
    let rows = connection
        .query(
            "WITH active AS ( \
                 SELECT product_id, category_id::integer AS category_id \
                 FROM api_products.product_ref_v1 \
                 WHERE is_active AND ($4::integer IS NULL OR category_id = $4::integer)), \
             lines AS ( \
                 SELECT l.store_id::integer AS store_id, a.category_id, l.net_quantity, \
                        l.data_origin, \
                        coalesce(c.state, 'no_open_assignment') AS segment_state, \
                        c.label_code AS segment_code \
                 FROM api_sales.net_purchase_line_v1 AS l \
                 JOIN active AS a ON a.product_id = l.product_id \
                 LEFT JOIN api_segments.current_label_v1 AS c ON c.customer_id = l.customer_id \
                 WHERE l.occurred_at >= $1 AND l.occurred_at < $2 \
                   AND ($3::integer[] IS NULL OR l.store_id = ANY($3::integer[]))), \
             demand AS ( \
                 SELECT store_id, category_id, sum(net_quantity)::bigint AS net_units, \
                        bool_or(data_origin = 'SEEDED') AS synthetic \
                 FROM lines GROUP BY store_id, category_id), \
             split AS ( \
                 SELECT store_id, category_id, segment_state, segment_code, \
                        sum(net_quantity)::bigint AS net_units \
                 FROM lines GROUP BY store_id, category_id, segment_state, segment_code), \
             stock AS ( \
                 SELECT s.store_id::integer AS store_id, a.category_id, \
                        sum(s.quantity_on_hand)::bigint AS on_hand \
                 FROM api_stock.stock_level_v1 AS s \
                 JOIN active AS a ON a.product_id = s.product_id \
                 WHERE $3::integer[] IS NULL OR s.store_id = ANY($3::integer[]) \
                 GROUP BY s.store_id, a.category_id), \
             cells AS ( \
                 SELECT coalesce(d.store_id, s.store_id) AS store_id, \
                        coalesce(d.category_id, s.category_id) AS category_id, \
                        coalesce(d.net_units, 0) AS net_units, \
                        coalesce(s.on_hand, 0) AS on_hand, \
                        coalesce(d.synthetic, false) AS synthetic \
                 FROM demand AS d \
                 FULL JOIN stock AS s \
                        ON s.store_id = d.store_id AND s.category_id = d.category_id) \
             SELECT t.total, t.synthetic, \
                    p.store_id, p.category_id, p.net_units, \
                    round(p.net_units::numeric / $5::integer, 4)::text AS daily_demand, \
                    p.on_hand, \
                    CASE WHEN p.net_units > 0 \
                         THEN round(p.on_hand::numeric \
                                    / round(p.net_units::numeric / $5::integer, 4), 2)::text \
                    END AS days_of_cover, \
                    greatest(0, (p.net_units * $6::integer + $5::integer - 1) / $5::integer \
                                - p.on_hand) AS replenishment_units, \
                    p.synthetic, \
                    (SELECT coalesce(json_agg(json_build_object( \
                                 'segment_code', x.segment_code, \
                                 'segment_state', x.segment_state, \
                                 'net_units', x.net_units, \
                                 'share', round(x.net_units::numeric / p.net_units, 4)::text) \
                             ORDER BY x.net_units DESC, x.segment_code NULLS LAST, \
                                      x.segment_state), '[]'::json)::text \
                     FROM split AS x \
                     WHERE x.store_id = p.store_id AND x.category_id = p.category_id) AS segments \
             FROM (SELECT count(*) AS total, coalesce(bool_or(synthetic), false) AS synthetic \
                   FROM cells) AS t \
             LEFT JOIN LATERAL (SELECT * FROM cells \
                                 ORDER BY store_id, category_id \
                                 LIMIT $7 OFFSET $8) AS p ON TRUE",
            &[
                &query.window_start(),
                &query.window_end(),
                &store_filter,
                &query.category_id,
                &query.window_days(),
                &query.target_cover_days,
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
        let Some(store_id) = row.try_get::<_, Option<i32>>(2)? else {
            continue;
        };
        let text: String = row.try_get(10)?;
        let parsed: Vec<serde_json::Value> = serde_json::from_str(&text).map_err(|_| internal())?;
        let mut segments = Vec::new();
        for item in &parsed {
            segments.push(SegmentDemand {
                segment_code: item["segment_code"].as_str().map(str::to_owned),
                segment_state: item["segment_state"]
                    .as_str()
                    .ok_or_else(internal)?
                    .to_owned(),
                net_units: item["net_units"].as_i64().ok_or_else(internal)?,
                share: item["share"].as_str().ok_or_else(internal)?.to_owned(),
            });
        }
        report.rows.push(Row {
            store_id,
            category_id: row.try_get(3)?,
            net_units: row.try_get(4)?,
            daily_demand: row.try_get(5)?,
            on_hand: row.try_get(6)?,
            days_of_cover: row.try_get(7)?,
            replenishment_units: row.try_get(8)?,
            synthetic: row.try_get(9)?,
            segments,
        });
    }
    Ok(report)
}

impl Row {
    /// The row as the contract's `DemandCoverageRow`; a null member is omitted
    /// from the XML by the kit.
    pub fn wire(&self) -> Value {
        let mut members: IndexMap<String, Value> = IndexMap::new();
        members.insert("store_id".into(), Value::from(self.store_id));
        members.insert("category_id".into(), Value::from(self.category_id));
        members.insert("net_units".into(), Value::from(self.net_units));
        members.insert(
            "daily_demand".into(),
            Value::from(self.daily_demand.as_str()),
        );
        members.insert("on_hand".into(), Value::from(self.on_hand));
        members.insert(
            "days_of_cover".into(),
            Value::from(self.days_of_cover.clone()),
        );
        members.insert(
            "replenishment_units".into(),
            Value::from(self.replenishment_units),
        );
        members.insert("synthetic".into(), Value::from(self.synthetic));
        members.insert(
            "segments".into(),
            Value::List(
                self.segments
                    .iter()
                    .map(|segment| {
                        Value::object([
                            ("segment_code", Value::from(segment.segment_code.clone())),
                            ("segment_state", Value::from(segment.segment_state.as_str())),
                            ("net_units", Value::from(segment.net_units)),
                            ("share", Value::from(segment.share.as_str())),
                        ])
                    })
                    .collect(),
            ),
        );
        Value::Object(members)
    }
}
