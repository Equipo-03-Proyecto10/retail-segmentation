//! A store's operating status, weekly opening hours, enabled channels and
//! maximum discount (RN-53, F21-12). Each is a fact of its own, replaced whole
//! by ADMIN in one transaction that carries the actor to the audit trigger;
//! the capture triggers announce the change on `stream:stores.v1.changed`
//! from the same transaction.
//!
//! A replacement that changes nothing writes nothing, so it is not audited and
//! not announced, and `updated_at` keeps saying when the fact last changed.
//! `public.store.is_active` is a different fact that only the web writes: this
//! module never touches it.

use std::collections::HashSet;

use chrono::{DateTime, NaiveTime, Utc};
use indexmap::IndexMap;
use mosaiq_kit::{money, ApiError, Ctx, Database, Detail, Principal, Reply, Representation, Value};
use rust_decimal::Decimal;
use serde_json::Value as Json;
use tokio_postgres::GenericClient;
use uuid::Uuid;

use crate::{store_id, store_not_found};

pub const STATUSES: [&str; 3] = ["OPEN", "TEMPORARILY_CLOSED", "CLOSED"];
const DEFAULT_STATUS: &str = "OPEN";
const TIME_FORMAT: &str = "%H:%M";

/// One open day. A day with no interval is closed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Interval {
    pub day_of_week: i16,
    pub opens_at: NaiveTime,
    pub closes_at: NaiveTime,
}

/// The weekly hours as stored: `time_zone` is `None` exactly when `intervals`
/// is empty.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Hours {
    pub time_zone: Option<String>,
    pub intervals: Vec<Interval>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Status {
    pub status: String,
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct StoredHours {
    pub hours: Hours,
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Channels {
    pub channel_ids: Vec<i16>,
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct CommercialRules {
    pub max_discount_percent: Option<Decimal>,
    pub updated_at: Option<DateTime<Utc>>,
}

fn invalid_body(message: &str, details: Vec<Detail>) -> ApiError {
    ApiError::new(422, "invalid_body", message).with_details(details)
}

fn actor_uuid(actor: &Principal) -> Result<Uuid, ApiError> {
    Uuid::parse_str(&actor.sub)
        .map_err(|_| ApiError::new(401, "invalid_token", "The bearer token is invalid."))
}

/// A write whose foreign key names the store or the actor: the store was
/// deleted by the web between the check and the write (404), or the session's
/// `sub` is not a user (401). An unknown channel is the caller's input (422).
fn write_error(error: tokio_postgres::Error) -> ApiError {
    let constraint = error
        .as_db_error()
        .and_then(|database| database.constraint())
        .unwrap_or_default();
    if constraint == "store_channel_channel_id_fkey" {
        return unknown_channel();
    }
    if constraint.ends_with("_store_id_fkey") && constraint != "store_channel_store_id_fkey" {
        return store_not_found();
    }
    crate::constraint_error(error)
}

fn unknown_channel() -> ApiError {
    invalid_body(
        "The channel set names a channel that does not exist.",
        vec![Detail::new("channel_ids", "unknown_channel")],
    )
}

fn stamp(moment: Option<DateTime<Utc>>) -> Value {
    moment.map_or(Value::Null, Value::from)
}

// ---------------------------------------------------------------- status

fn status_wire(state: &Status) -> IndexMap<String, Value> {
    IndexMap::from([
        ("status".to_owned(), Value::from(state.status.clone())),
        ("updated_at".to_owned(), stamp(state.updated_at)),
    ])
}

/// The stored status, or `OPEN` with no `updated_at` when it was never written.
pub async fn read_status<C: GenericClient + Sync>(client: &C, id: i16) -> Result<Status, ApiError> {
    let row = client
        .query_opt(
            "SELECT t.status, t.set_at FROM public.store AS s \
             LEFT JOIN stores.store_status AS t USING (store_id) WHERE s.store_id = $1",
            &[&id],
        )
        .await?
        .ok_or_else(store_not_found)?;
    let status: Option<String> = row.try_get(0)?;
    Ok(Status {
        status: status.unwrap_or_else(|| DEFAULT_STATUS.to_owned()),
        updated_at: row.try_get(1)?,
    })
}

/// Replace the status as `actor`. `status` must be one of [`STATUSES`].
pub async fn replace_status(
    db: &Database,
    actor: &Principal,
    id: i16,
    status: &str,
) -> Result<Status, ApiError> {
    if !STATUSES.contains(&status) {
        return Err(invalid_body(
            "The status is not allowed.",
            vec![Detail::new("status", "not_allowed")],
        ));
    }
    let set_by = actor_uuid(actor)?;
    let mut connection = db.connection().await?;
    let transaction = connection.transaction(Some(actor)).await?;
    read_status(&*transaction, id).await?;
    transaction
        .execute(
            "INSERT INTO stores.store_status AS t (store_id, status, set_by) \
             VALUES ($1, $2, $3) \
             ON CONFLICT (store_id) DO UPDATE \
             SET status = EXCLUDED.status, set_by = EXCLUDED.set_by, set_at = now() \
             WHERE t.status IS DISTINCT FROM EXCLUDED.status",
            &[&id, &status, &set_by],
        )
        .await
        .map_err(write_error)?;
    let state = read_status(&*transaction, id).await?;
    transaction.commit().await?;
    Ok(state)
}

async fn get_store_status(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = store_id(&ctx)?;
    let connection = ctx.db().connection().await?;
    let state = read_status(&***connection, id).await?;
    Ok(Representation::new("StoreStatus", status_wire(&state)).into())
}

async fn put_store_status(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = store_id(&ctx)?;
    let body = ctx.body.as_ref().expect("contract requires a body");
    let status = body["status"].as_str().unwrap_or_default();
    let actor = actor(&ctx)?;
    let state = replace_status(ctx.db(), actor, id, status).await?;
    Ok(Representation::new("StoreStatus", status_wire(&state)).into())
}

fn actor(ctx: &Ctx) -> Result<&Principal, ApiError> {
    ctx.principal()
        .ok_or_else(|| ApiError::new(401, "invalid_token", "The bearer token is invalid."))
}

// ----------------------------------------------------------------- hours

fn hours_wire(state: &StoredHours) -> IndexMap<String, Value> {
    let intervals = state
        .hours
        .intervals
        .iter()
        .map(|interval| {
            Value::object([
                ("day_of_week", Value::from(interval.day_of_week)),
                (
                    "opens_at",
                    Value::from(interval.opens_at.format(TIME_FORMAT).to_string()),
                ),
                (
                    "closes_at",
                    Value::from(interval.closes_at.format(TIME_FORMAT).to_string()),
                ),
            ])
        })
        .collect();
    IndexMap::from([
        (
            "time_zone".to_owned(),
            state
                .hours
                .time_zone
                .clone()
                .map_or(Value::Null, Value::from),
        ),
        ("intervals".to_owned(), Value::List(intervals)),
        ("updated_at".to_owned(), stamp(state.updated_at)),
    ])
}

/// The hours of a request body that already passed the contract's schema, with
/// the rules a schema cannot state: at most one interval per day, each closing
/// later than it opens, and a time zone exactly when there are intervals.
/// Every violation is reported, as `intervals.<n>.<field>` or `time_zone`.
pub fn parse_hours(body: &Json) -> Result<Hours, ApiError> {
    let mut details = Vec::new();
    let time_zone = body["time_zone"].as_str().map(str::to_owned);
    let mut intervals = Vec::new();
    let mut days = HashSet::new();
    for (index, item) in body["intervals"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .enumerate()
    {
        let day = item["day_of_week"]
            .as_i64()
            .and_then(|day| i16::try_from(day).ok());
        let opens = item["opens_at"]
            .as_str()
            .and_then(|text| NaiveTime::parse_from_str(text, TIME_FORMAT).ok());
        let closes = item["closes_at"]
            .as_str()
            .and_then(|text| NaiveTime::parse_from_str(text, TIME_FORMAT).ok());
        let (Some(day), Some(opens_at), Some(closes_at)) = (day, opens, closes) else {
            details.push(Detail::new(format!("intervals.{index}"), "invalid"));
            continue;
        };
        if !days.insert(day) {
            details.push(Detail::new(
                format!("intervals.{index}.day_of_week"),
                "duplicate_day",
            ));
        }
        if closes_at <= opens_at {
            details.push(Detail::new(
                format!("intervals.{index}.closes_at"),
                "closes_before_opens",
            ));
        }
        intervals.push(Interval {
            day_of_week: day,
            opens_at,
            closes_at,
        });
    }
    match (&time_zone, intervals.is_empty()) {
        (None, false) => details.push(Detail::new("time_zone", "time_zone_required")),
        (Some(_), true) => details.push(Detail::new("time_zone", "time_zone_without_hours")),
        _ => {}
    }
    if !details.is_empty() {
        return Err(invalid_body(
            "The opening hours violate the weekly schedule rules.",
            details,
        ));
    }
    intervals.sort_by_key(|interval| interval.day_of_week);
    Ok(Hours {
        time_zone,
        intervals,
    })
}

/// The stored hours, or none, no zone and no `updated_at` when never written.
pub async fn read_hours<C: GenericClient + Sync>(
    client: &C,
    id: i16,
) -> Result<StoredHours, ApiError> {
    let rows = client
        .query(
            "SELECT h.time_zone, h.set_at, i.day_of_week, i.opens_at, i.closes_at \
             FROM public.store AS s \
             LEFT JOIN stores.store_hours AS h USING (store_id) \
             LEFT JOIN stores.store_hours_interval AS i ON i.store_id = h.store_id \
             WHERE s.store_id = $1 ORDER BY i.day_of_week",
            &[&id],
        )
        .await?;
    let first = rows.first().ok_or_else(store_not_found)?;
    let mut intervals = Vec::new();
    for row in &rows {
        let day: Option<i16> = row.try_get(2)?;
        if let Some(day_of_week) = day {
            intervals.push(Interval {
                day_of_week,
                opens_at: row.try_get(3)?,
                closes_at: row.try_get(4)?,
            });
        }
    }
    Ok(StoredHours {
        hours: Hours {
            time_zone: first.try_get(0)?,
            intervals,
        },
        updated_at: first.try_get(1)?,
    })
}

/// Replace the whole weekly schedule as `actor`. A time zone must be one that
/// PostgreSQL knows, so a name a later reader cannot resolve is never stored.
pub async fn replace_hours(
    db: &Database,
    actor: &Principal,
    id: i16,
    hours: &Hours,
) -> Result<StoredHours, ApiError> {
    let set_by = actor_uuid(actor)?;
    let mut connection = db.connection().await?;
    let transaction = connection.transaction(Some(actor)).await?;
    read_hours(&*transaction, id).await?;
    if let Some(zone) = &hours.time_zone {
        let known: bool = transaction
            .query_one(
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name = $1)",
                &[zone],
            )
            .await?
            .try_get(0)?;
        if !known {
            return Err(invalid_body(
                "The time zone is not an IANA time zone name.",
                vec![Detail::new("time_zone", "invalid_format")],
            ));
        }
    }
    // The header is created first, so two first writes queue on its key. A
    // header that exists is locked and compared: nothing changed, nothing is
    // written.
    let created = transaction
        .execute(
            "INSERT INTO stores.store_hours (store_id, time_zone, set_by) \
             VALUES ($1, $2, $3) ON CONFLICT (store_id) DO NOTHING",
            &[&id, &hours.time_zone, &set_by],
        )
        .await
        .map_err(write_error)?;
    if created == 0 {
        transaction
            .query_one(
                "SELECT store_id FROM stores.store_hours WHERE store_id = $1 FOR UPDATE",
                &[&id],
            )
            .await?;
        let current = read_hours(&*transaction, id).await?;
        if current.hours == *hours {
            transaction.commit().await?;
            return Ok(current);
        }
        transaction
            .execute(
                "UPDATE stores.store_hours SET time_zone = $2, set_by = $3, set_at = now() \
                 WHERE store_id = $1",
                &[&id, &hours.time_zone, &set_by],
            )
            .await
            .map_err(write_error)?;
    }
    let days: Vec<i16> = hours
        .intervals
        .iter()
        .map(|interval| interval.day_of_week)
        .collect();
    transaction
        .execute(
            "DELETE FROM stores.store_hours_interval \
             WHERE store_id = $1 AND day_of_week <> ALL ($2)",
            &[&id, &days],
        )
        .await?;
    for interval in &hours.intervals {
        transaction
            .execute(
                "INSERT INTO stores.store_hours_interval AS t \
                 (store_id, day_of_week, opens_at, closes_at) VALUES ($1, $2, $3, $4) \
                 ON CONFLICT (store_id, day_of_week) DO UPDATE \
                 SET opens_at = EXCLUDED.opens_at, closes_at = EXCLUDED.closes_at \
                 WHERE (t.opens_at, t.closes_at) IS DISTINCT FROM \
                       (EXCLUDED.opens_at, EXCLUDED.closes_at)",
                &[
                    &id,
                    &interval.day_of_week,
                    &interval.opens_at,
                    &interval.closes_at,
                ],
            )
            .await
            .map_err(write_error)?;
    }
    let state = read_hours(&*transaction, id).await?;
    transaction.commit().await.map_err(write_error)?;
    Ok(state)
}

async fn get_store_hours(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = store_id(&ctx)?;
    let connection = ctx.db().connection().await?;
    let state = read_hours(&***connection, id).await?;
    Ok(Representation::new("StoreHours", hours_wire(&state)).into())
}

async fn put_store_hours(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = store_id(&ctx)?;
    let body = ctx.body.as_ref().expect("contract requires a body");
    let hours = parse_hours(body)?;
    let state = replace_hours(ctx.db(), actor(&ctx)?, id, &hours).await?;
    Ok(Representation::new("StoreHours", hours_wire(&state)).into())
}

// -------------------------------------------------------------- channels

fn channels_wire(state: &Channels) -> IndexMap<String, Value> {
    IndexMap::from([
        (
            "channel_ids".to_owned(),
            Value::List(state.channel_ids.iter().copied().map(Value::from).collect()),
        ),
        ("updated_at".to_owned(), stamp(state.updated_at)),
    ])
}

/// The channel ids of a request body that already passed the contract's schema.
/// A repeated id is `duplicate_channel`, an id no channel can have (outside
/// the `smallint` range) is `unknown_channel`; both name `channel_ids`.
pub fn parse_channels(body: &Json) -> Result<Vec<i16>, ApiError> {
    let mut seen = HashSet::new();
    let mut ids = Vec::new();
    let mut details = Vec::new();
    for item in body["channel_ids"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
    {
        let Some(channel_id) = item.as_i64().and_then(|id| i16::try_from(id).ok()) else {
            details.push(Detail::new("channel_ids", "unknown_channel"));
            continue;
        };
        if !seen.insert(channel_id) {
            details.push(Detail::new("channel_ids", "duplicate_channel"));
        }
        ids.push(channel_id);
    }
    details.dedup();
    if !details.is_empty() {
        return Err(invalid_body(
            "The channel set repeats or names a channel that does not exist.",
            details,
        ));
    }
    ids.sort_unstable();
    Ok(ids)
}

/// The enabled channel ids, or none and no `updated_at` when never written.
pub async fn read_channels<C: GenericClient + Sync>(
    client: &C,
    id: i16,
) -> Result<Channels, ApiError> {
    let rows = client
        .query(
            "SELECT t.set_at, c.channel_id FROM public.store AS s \
             LEFT JOIN stores.store_channel_set AS t USING (store_id) \
             LEFT JOIN stores.store_channel AS c ON c.store_id = t.store_id \
             WHERE s.store_id = $1 ORDER BY c.channel_id",
            &[&id],
        )
        .await?;
    let first = rows.first().ok_or_else(store_not_found)?;
    let mut channel_ids = Vec::new();
    for row in &rows {
        let channel_id: Option<i16> = row.try_get(1)?;
        channel_ids.extend(channel_id);
    }
    Ok(Channels {
        channel_ids,
        updated_at: first.try_get(0)?,
    })
}

/// Replace the whole set as `actor`, atomically. `channel_ids` is ascending
/// and free of repeats; an id no channel has is refused by the foreign key to
/// `channel`, which this service cannot read (sales is its read owner).
pub async fn replace_channels(
    db: &Database,
    actor: &Principal,
    id: i16,
    channel_ids: &[i16],
) -> Result<Channels, ApiError> {
    let set_by = actor_uuid(actor)?;
    let mut connection = db.connection().await?;
    let transaction = connection.transaction(Some(actor)).await?;
    read_channels(&*transaction, id).await?;
    let created = transaction
        .execute(
            "INSERT INTO stores.store_channel_set (store_id, set_by) VALUES ($1, $2) \
             ON CONFLICT (store_id) DO NOTHING",
            &[&id, &set_by],
        )
        .await
        .map_err(write_error)?;
    if created == 0 {
        transaction
            .query_one(
                "SELECT store_id FROM stores.store_channel_set WHERE store_id = $1 FOR UPDATE",
                &[&id],
            )
            .await?;
        let current = read_channels(&*transaction, id).await?;
        if current.channel_ids == channel_ids {
            transaction.commit().await?;
            return Ok(current);
        }
        transaction
            .execute(
                "UPDATE stores.store_channel_set SET set_by = $2, set_at = now() \
                 WHERE store_id = $1",
                &[&id, &set_by],
            )
            .await
            .map_err(write_error)?;
    }
    let wanted = channel_ids.to_vec();
    transaction
        .execute(
            "DELETE FROM stores.store_channel \
             WHERE store_id = $1 AND channel_id <> ALL ($2)",
            &[&id, &wanted],
        )
        .await?;
    transaction
        .execute(
            "INSERT INTO stores.store_channel (store_id, channel_id) \
             SELECT $1, c FROM unnest($2::smallint[]) AS c ON CONFLICT DO NOTHING",
            &[&id, &wanted],
        )
        .await
        .map_err(write_error)?;
    let state = read_channels(&*transaction, id).await?;
    transaction.commit().await.map_err(write_error)?;
    Ok(state)
}

async fn get_store_channels(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = store_id(&ctx)?;
    let connection = ctx.db().connection().await?;
    let state = read_channels(&***connection, id).await?;
    Ok(Representation::new("StoreChannels", channels_wire(&state)).into())
}

async fn put_store_channels(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = store_id(&ctx)?;
    let body = ctx.body.as_ref().expect("contract requires a body");
    let channel_ids = parse_channels(body)?;
    let state = replace_channels(ctx.db(), actor(&ctx)?, id, &channel_ids).await?;
    Ok(Representation::new("StoreChannels", channels_wire(&state)).into())
}

// ------------------------------------------------------ commercial rules

fn rules_wire(state: &CommercialRules) -> IndexMap<String, Value> {
    IndexMap::from([
        (
            "max_discount_percent".to_owned(),
            state.max_discount_percent.map_or(Value::Null, money),
        ),
        ("updated_at".to_owned(), stamp(state.updated_at)),
    ])
}

/// The cap of a request body that already passed the contract's schema: a
/// two-decimal string from 0.00 to 100.00, or null for no cap.
pub fn parse_discount(body: &Json) -> Result<Option<Decimal>, ApiError> {
    match body["max_discount_percent"].as_str() {
        None => Ok(None),
        Some(text) => match text.parse::<Decimal>() {
            Ok(value) if value >= Decimal::ZERO && value <= Decimal::ONE_HUNDRED => Ok(Some(value)),
            _ => Err(invalid_body(
                "The maximum discount is outside its allowed range.",
                vec![Detail::new("max_discount_percent", "out_of_range")],
            )),
        },
    }
}

/// The stored rules, or no cap and no `updated_at` when never written.
pub async fn read_rules<C: GenericClient + Sync>(
    client: &C,
    id: i16,
) -> Result<CommercialRules, ApiError> {
    let row = client
        .query_opt(
            "SELECT r.max_discount_percent, r.set_at FROM public.store AS s \
             LEFT JOIN stores.store_commercial_rule AS r USING (store_id) \
             WHERE s.store_id = $1",
            &[&id],
        )
        .await?
        .ok_or_else(store_not_found)?;
    Ok(CommercialRules {
        max_discount_percent: row.try_get(0)?,
        updated_at: row.try_get(1)?,
    })
}

/// Replace the rules as `actor`; `None` removes the cap.
pub async fn replace_rules(
    db: &Database,
    actor: &Principal,
    id: i16,
    max_discount_percent: Option<Decimal>,
) -> Result<CommercialRules, ApiError> {
    let set_by = actor_uuid(actor)?;
    let mut connection = db.connection().await?;
    let transaction = connection.transaction(Some(actor)).await?;
    read_rules(&*transaction, id).await?;
    transaction
        .execute(
            "INSERT INTO stores.store_commercial_rule AS t \
             (store_id, max_discount_percent, set_by) VALUES ($1, $2, $3) \
             ON CONFLICT (store_id) DO UPDATE \
             SET max_discount_percent = EXCLUDED.max_discount_percent, \
                 set_by = EXCLUDED.set_by, set_at = now() \
             WHERE t.max_discount_percent IS DISTINCT FROM EXCLUDED.max_discount_percent",
            &[&id, &max_discount_percent, &set_by],
        )
        .await
        .map_err(|error| {
            if error
                .as_db_error()
                .and_then(|database| database.constraint())
                == Some("store_commercial_rule_max_discount_percent_check")
            {
                invalid_body(
                    "The maximum discount is outside its allowed range.",
                    vec![Detail::new("max_discount_percent", "out_of_range")],
                )
            } else {
                write_error(error)
            }
        })?;
    let state = read_rules(&*transaction, id).await?;
    transaction.commit().await?;
    Ok(state)
}

async fn get_store_commercial_rules(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = store_id(&ctx)?;
    let connection = ctx.db().connection().await?;
    let state = read_rules(&***connection, id).await?;
    Ok(Representation::new("StoreCommercialRules", rules_wire(&state)).into())
}

async fn put_store_commercial_rules(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = store_id(&ctx)?;
    let body = ctx.body.as_ref().expect("contract requires a body");
    let cap = parse_discount(body)?;
    let state = replace_rules(ctx.db(), actor(&ctx)?, id, cap).await?;
    Ok(Representation::new("StoreCommercialRules", rules_wire(&state)).into())
}

/// Register the eight operations of F21-12 on the kit.
pub fn register(kit: mosaiq_kit::Kit) -> Result<mosaiq_kit::Kit, Box<dyn std::error::Error>> {
    Ok(kit
        .operation("getStoreStatus", get_store_status)?
        .operation("putStoreStatus", put_store_status)?
        .operation("getStoreHours", get_store_hours)?
        .operation("putStoreHours", put_store_hours)?
        .operation("getStoreChannels", get_store_channels)?
        .operation("putStoreChannels", put_store_channels)?
        .operation("getStoreCommercialRules", get_store_commercial_rules)?
        .operation("putStoreCommercialRules", put_store_commercial_rules)?)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn codes(error: &ApiError) -> Vec<(String, String)> {
        error
            .details
            .iter()
            .map(|d| (d.field.clone(), d.code.clone()))
            .collect()
    }

    #[test]
    fn hours_are_sorted_and_need_a_zone_exactly_when_open() {
        let hours = parse_hours(&json!({
            "time_zone": "America/Mexico_City",
            "intervals": [
                {"day_of_week": 6, "opens_at": "10:00", "closes_at": "18:00"},
                {"day_of_week": 1, "opens_at": "09:00", "closes_at": "21:00"}
            ]
        }))
        .unwrap();
        assert_eq!(hours.intervals[0].day_of_week, 1);
        assert_eq!(hours.intervals[1].day_of_week, 6);
        let cleared = parse_hours(&json!({"time_zone": null, "intervals": []})).unwrap();
        assert_eq!(cleared.time_zone, None);
        assert!(cleared.intervals.is_empty());

        let missing = parse_hours(&json!({
            "time_zone": null,
            "intervals": [{"day_of_week": 1, "opens_at": "09:00", "closes_at": "21:00"}]
        }))
        .unwrap_err();
        assert_eq!(
            codes(&missing),
            [("time_zone".to_owned(), "time_zone_required".to_owned())]
        );
        let extra = parse_hours(&json!({"time_zone": "UTC", "intervals": []})).unwrap_err();
        assert_eq!(
            codes(&extra),
            [("time_zone".to_owned(), "time_zone_without_hours".to_owned())]
        );
    }

    #[test]
    fn hours_refuse_a_repeated_day_and_an_interval_that_does_not_close_later() {
        let error = parse_hours(&json!({
            "time_zone": "UTC",
            "intervals": [
                {"day_of_week": 2, "opens_at": "09:00", "closes_at": "21:00"},
                {"day_of_week": 2, "opens_at": "10:00", "closes_at": "10:00"},
                {"day_of_week": 3, "opens_at": "22:00", "closes_at": "06:00"}
            ]
        }))
        .unwrap_err();
        assert_eq!(
            codes(&error),
            [
                (
                    "intervals.1.day_of_week".to_owned(),
                    "duplicate_day".to_owned()
                ),
                (
                    "intervals.1.closes_at".to_owned(),
                    "closes_before_opens".to_owned()
                ),
                (
                    "intervals.2.closes_at".to_owned(),
                    "closes_before_opens".to_owned()
                ),
            ]
        );
    }

    #[test]
    fn channels_are_sorted_and_a_repeat_or_impossible_id_is_refused() {
        assert_eq!(
            parse_channels(&json!({"channel_ids": [4, 1, 3]})).unwrap(),
            vec![1, 3, 4]
        );
        assert!(parse_channels(&json!({"channel_ids": []}))
            .unwrap()
            .is_empty());
        let repeated = parse_channels(&json!({"channel_ids": [1, 1]})).unwrap_err();
        assert_eq!(
            codes(&repeated),
            [("channel_ids".to_owned(), "duplicate_channel".to_owned())]
        );
        let impossible = parse_channels(&json!({"channel_ids": [40000]})).unwrap_err();
        assert_eq!(
            codes(&impossible),
            [("channel_ids".to_owned(), "unknown_channel".to_owned())]
        );
    }

    #[test]
    fn the_discount_is_a_percentage_or_null() {
        assert_eq!(
            parse_discount(&json!({"max_discount_percent": null})).unwrap(),
            None
        );
        assert_eq!(
            parse_discount(&json!({"max_discount_percent": "0.00"})).unwrap(),
            Some(Decimal::ZERO)
        );
        assert_eq!(
            parse_discount(&json!({"max_discount_percent": "100.00"})).unwrap(),
            Some(Decimal::ONE_HUNDRED)
        );
        assert!(parse_discount(&json!({"max_discount_percent": "100.01"})).is_err());
    }
}
