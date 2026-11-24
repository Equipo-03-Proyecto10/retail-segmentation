//! The query string of a report (`contracts/analytics-kpis/openapi.yaml`):
//! `from` and `to` as UTC dates, `group_by`, `granularity` and the filters.
//! Every rejected parameter is named in `details` of one 422 `invalid_query`.

use std::collections::HashMap;

use chrono::{DateTime, Days, NaiveDate, TimeZone, Utc};
use mosaiq_kit::{ApiError, Detail};

/// The longest window a report accepts, in days (`window_too_long`).
pub const MAX_WINDOW_DAYS: i64 = 731;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Dimension {
    Segment,
    Store,
    Channel,
    Period,
}

impl Dimension {
    pub fn code(self) -> &'static str {
        match self {
            Dimension::Segment => "segment",
            Dimension::Store => "store",
            Dimension::Channel => "channel",
            Dimension::Period => "period",
        }
    }

    fn parse(text: &str) -> Option<Self> {
        match text {
            "segment" => Some(Dimension::Segment),
            "store" => Some(Dimension::Store),
            "channel" => Some(Dimension::Channel),
            "period" => Some(Dimension::Period),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Granularity {
    Day,
    Week,
    Month,
}

impl Granularity {
    pub fn code(self) -> &'static str {
        match self {
            Granularity::Day => "day",
            Granularity::Week => "week",
            Granularity::Month => "month",
        }
    }

    fn parse(text: &str) -> Option<Self> {
        match text {
            "day" => Some(Granularity::Day),
            "week" => Some(Granularity::Week),
            "month" => Some(Granularity::Month),
            _ => None,
        }
    }
}

/// A sales report's parameters, validated.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SalesQuery {
    pub from: NaiveDate,
    pub to: NaiveDate,
    /// In the order the caller gave them.
    pub group_by: Vec<Dimension>,
    /// `Some` only when `period` is grouped; `month` unless given.
    pub granularity: Option<Granularity>,
    pub store_id: Option<i32>,
    pub segment_code: Option<String>,
    pub channel_id: Option<i32>,
}

impl SalesQuery {
    pub fn groups(&self, dimension: Dimension) -> bool {
        self.group_by.contains(&dimension)
    }

    /// The first instant of the window, 00:00 UTC of `from`.
    pub fn window_start(&self) -> DateTime<Utc> {
        Utc.from_utc_datetime(&self.from.and_time(chrono::NaiveTime::MIN))
    }

    /// The first instant after the window: 00:00 UTC of the day after `to`.
    pub fn window_end(&self) -> DateTime<Utc> {
        let after = self.to.checked_add_days(Days::new(1)).unwrap_or(self.to);
        Utc.from_utc_datetime(&after.and_time(chrono::NaiveTime::MIN))
    }
}

fn values(query: &str) -> HashMap<String, String> {
    form_urlencoded::parse(query.as_bytes())
        .map(|(key, value)| (key.into_owned(), value.into_owned()))
        .collect()
}

fn date(
    values: &HashMap<String, String>,
    name: &str,
    details: &mut Vec<Detail>,
) -> Option<NaiveDate> {
    let Some(text) = values.get(name) else {
        details.push(Detail::new(name, "required"));
        return None;
    };
    // An RFC 3339 full-date: four digits, two, two.
    let bytes = text.as_bytes();
    let shaped = bytes.len() == 10
        && bytes.iter().enumerate().all(|(at, byte)| {
            matches!(at, 4 | 7) == (*byte == b'-') && (matches!(at, 4 | 7) || byte.is_ascii_digit())
        });
    match shaped
        .then(|| NaiveDate::parse_from_str(text, "%Y-%m-%d").ok())
        .flatten()
    {
        Some(day) => Some(day),
        None => {
            details.push(Detail::new(name, "invalid_format"));
            None
        }
    }
}

fn positive_i32(
    values: &HashMap<String, String>,
    name: &str,
    details: &mut Vec<Detail>,
) -> Option<i32> {
    let text = values.get(name)?;
    match text.parse::<i32>() {
        Ok(number) if number >= 1 => Some(number),
        _ => {
            details.push(Detail::new(name, "invalid_format"));
            None
        }
    }
}

fn invalid(details: Vec<Detail>) -> ApiError {
    ApiError::new(
        422,
        "invalid_query",
        "A query parameter is not an allowed value.",
    )
    .with_details(details)
}

/// Read the query string of a report that groups by `group_by` and filters by
/// store, segment and channel (`net-sales`, `spend`).
pub fn parse_sales_query(query: &str) -> Result<SalesQuery, ApiError> {
    parse_sales(query, false, true)
}

/// Read the query string of `conversion` and `recommendation-performance`
/// (F26-04). An interaction and an exposure carry no channel, so `channel` is
/// not a dimension of either report: grouping by it, or filtering by
/// `channel_id`, is 422 `dimension_not_supported`, never ignored.
pub fn parse_engagement_query(query: &str) -> Result<SalesQuery, ApiError> {
    parse_sales(query, false, false)
}

/// Read the query string of the retention report: its rows are always cohorts,
/// so `period` is a dimension whether or not the caller named it, and
/// `granularity` is allowed without it (the contract's `getRetentionReport`).
pub fn parse_retention_query(query: &str) -> Result<SalesQuery, ApiError> {
    parse_sales(query, true, true)
}

fn parse_sales(
    query: &str,
    always_period: bool,
    channel_supported: bool,
) -> Result<SalesQuery, ApiError> {
    let values = values(query);
    let mut details = Vec::new();

    let from = date(&values, "from", &mut details);
    let to = date(&values, "to", &mut details);
    if let (Some(from), Some(to)) = (from, to) {
        if to < from {
            details.push(Detail::new("to", "to_before_from"));
        } else if (to - from).num_days() + 1 > MAX_WINDOW_DAYS {
            details.push(Detail::new("to", "window_too_long"));
        }
    }

    let mut group_by: Vec<Dimension> = Vec::new();
    if let Some(text) = values.get("group_by") {
        for part in text.split(',').filter(|_| !text.is_empty()) {
            match Dimension::parse(part) {
                Some(Dimension::Channel) if !channel_supported => {
                    details.push(Detail::new("group_by", "dimension_not_supported"));
                    break;
                }
                Some(dimension) if group_by.contains(&dimension) => {
                    details.push(Detail::new("group_by", "invalid_format"));
                    break;
                }
                Some(dimension) => group_by.push(dimension),
                None => {
                    details.push(Detail::new("group_by", "enum"));
                    break;
                }
            }
        }
    }

    let mut granularity = None;
    if let Some(text) = values.get("granularity") {
        match Granularity::parse(text) {
            Some(parsed) if always_period || group_by.contains(&Dimension::Period) => {
                granularity = Some(parsed)
            }
            Some(_) => details.push(Detail::new("granularity", "requires_period")),
            None => details.push(Detail::new("granularity", "enum")),
        }
    }
    if always_period && !group_by.contains(&Dimension::Period) {
        group_by.insert(0, Dimension::Period);
    }
    if granularity.is_none() && group_by.contains(&Dimension::Period) {
        granularity = Some(Granularity::Month);
    }

    let store_id = positive_i32(&values, "store_id", &mut details);
    let channel_id = positive_i32(&values, "channel_id", &mut details);
    if !channel_supported && values.contains_key("channel_id") && channel_id.is_some() {
        details.push(Detail::new("channel_id", "dimension_not_supported"));
    }
    let segment_code = match values.get("segment_code") {
        Some(text) if (1..=40).contains(&text.chars().count()) => Some(text.clone()),
        Some(_) => {
            details.push(Detail::new("segment_code", "invalid_format"));
            None
        }
        None => None,
    };

    match (from, to, details.is_empty()) {
        (Some(from), Some(to), true) => Ok(SalesQuery {
            from,
            to,
            group_by,
            granularity,
            store_id,
            segment_code,
            channel_id,
        }),
        _ => Err(invalid(details)),
    }
}

/// A demand-coverage report's parameters, validated (`getDemandCoverageReport`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DemandQuery {
    pub from: NaiveDate,
    pub to: NaiveDate,
    pub store_id: Option<i32>,
    pub category_id: Option<i32>,
    /// The days of demand the stock should cover; 14 unless given.
    pub target_cover_days: i32,
}

impl DemandQuery {
    /// Days in the window, `to` - `from` + 1.
    pub fn window_days(&self) -> i32 {
        ((self.to - self.from).num_days() + 1) as i32
    }

    pub fn window_start(&self) -> DateTime<Utc> {
        Utc.from_utc_datetime(&self.from.and_time(chrono::NaiveTime::MIN))
    }

    pub fn window_end(&self) -> DateTime<Utc> {
        let after = self.to.checked_add_days(Days::new(1)).unwrap_or(self.to);
        Utc.from_utc_datetime(&after.and_time(chrono::NaiveTime::MIN))
    }
}

/// Read the query string of the demand-coverage report. `today` is the UTC
/// date of `generated_at`: `to` must lie before it, so a day still running
/// never lowers daily demand (`window_not_ended`).
pub fn parse_demand_query(query: &str, today: NaiveDate) -> Result<DemandQuery, ApiError> {
    let values = values(query);
    let mut details = Vec::new();

    let from = date(&values, "from", &mut details);
    let to = date(&values, "to", &mut details);
    if let (Some(from), Some(to)) = (from, to) {
        if to < from {
            details.push(Detail::new("to", "to_before_from"));
        } else if (to - from).num_days() + 1 > MAX_WINDOW_DAYS {
            details.push(Detail::new("to", "window_too_long"));
        } else if to >= today {
            details.push(Detail::new("to", "window_not_ended"));
        }
    }

    let store_id = positive_i32(&values, "store_id", &mut details);
    let category_id = positive_i32(&values, "category_id", &mut details);
    let mut target_cover_days = 14;
    if let Some(text) = values.get("target_cover_days") {
        match text.parse::<i32>() {
            Ok(days) if (1..=180).contains(&days) => target_cover_days = days,
            Ok(_) => details.push(Detail::new("target_cover_days", "out_of_range")),
            Err(_) => details.push(Detail::new("target_cover_days", "invalid_format")),
        }
    }

    match (from, to, details.is_empty()) {
        (Some(from), Some(to), true) => Ok(DemandQuery {
            from,
            to,
            store_id,
            category_id,
            target_cover_days,
        }),
        _ => Err(invalid(details)),
    }
}

/// A migration report's parameters, validated (`getMigrationReport`). A change
/// of label has no store and no channel, so only `segment` and `period` group it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MigrationQuery {
    pub from: NaiveDate,
    pub to: NaiveDate,
    /// In the order the caller gave them; `segment` and `period` only.
    pub group_by: Vec<Dimension>,
    /// `Some` only when `period` is grouped; `month` unless given.
    pub granularity: Option<Granularity>,
    /// The label entered.
    pub segment_code: Option<String>,
    /// Keeps the changes entered under one model version.
    pub model_version_id: Option<i64>,
}

impl MigrationQuery {
    pub fn groups(&self, dimension: Dimension) -> bool {
        self.group_by.contains(&dimension)
    }

    pub fn window_start(&self) -> DateTime<Utc> {
        Utc.from_utc_datetime(&self.from.and_time(chrono::NaiveTime::MIN))
    }

    pub fn window_end(&self) -> DateTime<Utc> {
        let after = self.to.checked_add_days(Days::new(1)).unwrap_or(self.to);
        Utc.from_utc_datetime(&after.and_time(chrono::NaiveTime::MIN))
    }
}

/// Read the query string of the migration report. A dimension a change does not
/// have is `dimension_not_supported`, never ignored.
pub fn parse_migration_query(query: &str) -> Result<MigrationQuery, ApiError> {
    let values = values(query);
    let mut details = Vec::new();

    let from = date(&values, "from", &mut details);
    let to = date(&values, "to", &mut details);
    if let (Some(from), Some(to)) = (from, to) {
        if to < from {
            details.push(Detail::new("to", "to_before_from"));
        } else if (to - from).num_days() + 1 > MAX_WINDOW_DAYS {
            details.push(Detail::new("to", "window_too_long"));
        }
    }

    let mut group_by: Vec<Dimension> = Vec::new();
    if let Some(text) = values.get("group_by") {
        for part in text.split(',').filter(|_| !text.is_empty()) {
            match Dimension::parse(part) {
                Some(Dimension::Store | Dimension::Channel) => {
                    details.push(Detail::new("group_by", "dimension_not_supported"));
                    break;
                }
                Some(dimension) if group_by.contains(&dimension) => {
                    details.push(Detail::new("group_by", "invalid_format"));
                    break;
                }
                Some(dimension) => group_by.push(dimension),
                None => {
                    details.push(Detail::new("group_by", "enum"));
                    break;
                }
            }
        }
    }

    let mut granularity = None;
    if let Some(text) = values.get("granularity") {
        match Granularity::parse(text) {
            Some(parsed) if group_by.contains(&Dimension::Period) => granularity = Some(parsed),
            Some(_) => details.push(Detail::new("granularity", "requires_period")),
            None => details.push(Detail::new("granularity", "enum")),
        }
    }
    if granularity.is_none() && group_by.contains(&Dimension::Period) {
        granularity = Some(Granularity::Month);
    }

    let segment_code = match values.get("segment_code") {
        Some(text) if (1..=40).contains(&text.chars().count()) => Some(text.clone()),
        Some(_) => {
            details.push(Detail::new("segment_code", "invalid_format"));
            None
        }
        None => None,
    };
    let model_version_id = match values.get("model_version_id") {
        Some(text) => match text.parse::<i64>() {
            Ok(number) if number >= 1 => Some(number),
            _ => {
                details.push(Detail::new("model_version_id", "invalid_format"));
                None
            }
        },
        None => None,
    };

    match (from, to, details.is_empty()) {
        (Some(from), Some(to), true) => Ok(MigrationQuery {
            from,
            to,
            group_by,
            granularity,
            segment_code,
            model_version_id,
        }),
        _ => Err(invalid(details)),
    }
}

/// Campaign performance's parameters, validated (F26-04). A row is a campaign, so
/// no dimension groups it and `group_by` is refused.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CampaignQuery {
    pub from: NaiveDate,
    pub to: NaiveDate,
    pub campaign_id: Option<i32>,
}

pub fn parse_campaign_query(query: &str) -> Result<CampaignQuery, ApiError> {
    let values = values(query);
    let mut details = Vec::new();

    let from = date(&values, "from", &mut details);
    let to = date(&values, "to", &mut details);
    if let (Some(from), Some(to)) = (from, to) {
        if to < from {
            details.push(Detail::new("to", "to_before_from"));
        } else if (to - from).num_days() + 1 > MAX_WINDOW_DAYS {
            details.push(Detail::new("to", "window_too_long"));
        }
    }
    if values.get("group_by").is_some_and(|text| !text.is_empty()) {
        details.push(Detail::new("group_by", "dimension_not_supported"));
    }
    let campaign_id = positive_i32(&values, "campaign_id", &mut details);

    match (from, to, details.is_empty()) {
        (Some(from), Some(to), true) => Ok(CampaignQuery {
            from,
            to,
            campaign_id,
        }),
        _ => Err(invalid(details)),
    }
}
