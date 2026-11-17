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
/// store, segment and channel (`net-sales`, `spend`, `retention`).
pub fn parse_sales_query(query: &str) -> Result<SalesQuery, ApiError> {
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

    let store_id = positive_i32(&values, "store_id", &mut details);
    let channel_id = positive_i32(&values, "channel_id", &mut details);
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
