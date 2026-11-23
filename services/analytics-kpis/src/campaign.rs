//! Campaign performance (`campaign-performance`, definition version 1): one row per
//! measured experiment of a campaign whose window overlaps the range (F26-04, #849).
//!
//! It reads `api_experiments.campaign_effect_v1` and nothing else. Attributed sales
//! are labelled `ATTRIBUTED` and the incremental effect `INCREMENTAL`; the two are
//! separate measures and neither is computed from the other (F25-02's record,
//! ADR-0069). The effect carries its interval in `lower` and `upper`, and is shown
//! only when the stored interval is a 95% one, as the contract promises; otherwise,
//! and for a campaign without a control, the effect's value is absent. A campaign
//! has no store, so no store-scoped role reads this report.

use chrono::NaiveDate;
use indexmap::IndexMap;
use mosaiq_kit::{ApiError, Database, Value};

use crate::query::CampaignQuery;

pub const KPI: &str = "campaign-performance";
pub const DEFINITION_VERSION: i64 = 1;
pub const EFFECT_VIEW: &str = "api_experiments.campaign_effect_v1";
/// The only interval level the contract's measure may carry.
pub const CONFIDENCE: &str = "0.9500000000";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub period_start: NaiveDate,
    pub period_end: NaiveDate,
    pub campaign_id: i32,
    pub synthetic: bool,
    pub attributed_sales_amount: String,
    pub treated_customers: String,
    pub control_customers: String,
    /// `(value, lower, upper)`, absent without a 95% effect.
    pub effect: Option<(String, String, String)>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub rows: Vec<Row>,
    pub total: u64,
    pub synthetic: bool,
}

/// One page of the report. The statement is fixed; every value is a parameter.
pub async fn read(
    db: &Database,
    query: &CampaignQuery,
    page: u32,
    size: u32,
) -> Result<Report, ApiError> {
    let connection = db.connection().await?;
    let limit = i64::from(size);
    let offset = i64::from(page.saturating_sub(1)) * limit;
    let rows = connection
        .query(
            "WITH found AS ( \
                 SELECT campaign_id, experiment_id, window_start, window_end, \
                        treated_customers, control_customers, attributed_sales_amount, \
                        incremental_effect, effect_lower, effect_upper, confidence, synthetic \
                 FROM api_experiments.campaign_effect_v1 \
                 WHERE window_start <= $2 AND window_end >= $1 \
                   AND ($3::integer IS NULL OR campaign_id = $3::integer)) \
             SELECT t.total, t.synthetic, p.window_start, p.window_end, p.campaign_id, \
                    p.synthetic, p.attributed_sales_amount::text, \
                    p.treated_customers::text, p.control_customers::text, \
                    CASE WHEN p.confidence = $4::text::numeric \
                         THEN round(p.incremental_effect::numeric, 2)::text END, \
                    CASE WHEN p.confidence = $4::text::numeric \
                         THEN round(p.effect_lower::numeric, 2)::text END, \
                    CASE WHEN p.confidence = $4::text::numeric \
                         THEN round(p.effect_upper::numeric, 2)::text END \
             FROM (SELECT count(*) AS total, coalesce(bool_or(synthetic), false) AS synthetic \
                   FROM found) AS t \
             LEFT JOIN LATERAL (SELECT * FROM found \
                                ORDER BY window_start, campaign_id, experiment_id \
                                LIMIT $5 OFFSET $6) AS p ON TRUE",
            &[
                &query.from,
                &query.to,
                &query.campaign_id,
                &CONFIDENCE,
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
        // The lateral join yields one all-null row for an empty page.
        let Some(campaign_id) = row.try_get::<_, Option<i32>>(4)? else {
            continue;
        };
        let effect = match (
            row.try_get::<_, Option<String>>(9)?,
            row.try_get::<_, Option<String>>(10)?,
            row.try_get::<_, Option<String>>(11)?,
        ) {
            (Some(value), Some(lower), Some(upper)) => Some((value, lower, upper)),
            _ => None,
        };
        report.rows.push(Row {
            period_start: row.try_get(2)?,
            period_end: row.try_get(3)?,
            campaign_id,
            synthetic: row.try_get(5)?,
            attributed_sales_amount: row.try_get(6)?,
            treated_customers: row.try_get(7)?,
            control_customers: row.try_get(8)?,
            effect,
        });
    }
    Ok(report)
}

fn measure(
    code: &str,
    kind: &str,
    unit: &str,
    value: Option<&str>,
    interval: Option<(&str, &str)>,
) -> Value {
    Value::object([
        ("code", Value::from(code)),
        ("kind", Value::from(kind)),
        ("unit", Value::from(unit)),
        ("value", value.map_or(Value::Null, Value::from)),
        (
            "lower",
            interval.map_or(Value::Null, |(low, _)| Value::from(low)),
        ),
        (
            "upper",
            interval.map_or(Value::Null, |(_, high)| Value::from(high)),
        ),
    ])
}

impl Row {
    /// The row as the contract's `Row`: the four measures in the catalog's order.
    pub fn wire(&self) -> Value {
        let mut members: IndexMap<String, Value> = IndexMap::new();
        members.insert("period_start".into(), Value::from(self.period_start));
        members.insert("period_end".into(), Value::from(self.period_end));
        members.insert("segment_code".into(), Value::Null);
        members.insert("segment_state".into(), Value::Null);
        members.insert("from_segment_code".into(), Value::Null);
        members.insert("store_id".into(), Value::Null);
        members.insert("channel_id".into(), Value::Null);
        members.insert("campaign_id".into(), Value::from(self.campaign_id));
        members.insert("model_version_id".into(), Value::Null);
        members.insert("synthetic".into(), Value::from(self.synthetic));
        let effect = self.effect.as_ref();
        members.insert(
            "measures".into(),
            Value::List(vec![
                measure(
                    "attributed_sales_amount",
                    "ATTRIBUTED",
                    "currency",
                    Some(&self.attributed_sales_amount),
                    None,
                ),
                measure(
                    "treated_customers",
                    "OBSERVED",
                    "count",
                    Some(&self.treated_customers),
                    None,
                ),
                measure(
                    "control_customers",
                    "OBSERVED",
                    "count",
                    Some(&self.control_customers),
                    None,
                ),
                measure(
                    "incremental_effect",
                    "INCREMENTAL",
                    "currency",
                    effect.map(|(value, _, _)| value.as_str()),
                    effect.map(|(_, low, high)| (low.as_str(), high.as_str())),
                ),
            ]),
        );
        Value::Object(members)
    }
}
