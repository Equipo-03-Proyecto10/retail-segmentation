//! check-ins-service (F14-26): one rule, RN-56. The server, never the phone,
//! decides a check-in's verdict from the haversine distance to the store's
//! geofence, and a correction is an appended review, never an edit.
//!
//! Store locations are read only through `api_stores.store_directory_v1`
//! (ADR-0044). Check-ins and reviews are INSERT-only for `svc_checkins`, and
//! every SQL statement is a fixed text with bound parameters.

use std::collections::HashMap;
use std::sync::Arc;

use axum::body::Body;
use axum::extract::Request;
use axum::middleware::{self, Next};
use axum::response::Response;
use chrono::{DateTime, Duration, Utc};
use indexmap::IndexMap;
use mosaiq_kit::{
    configure_logging, ApiError, Connection, Ctx, Database, Detail, Kit, KitConfig, Page,
    Principal, Reply, Representation, Value,
};
use rust_decimal::prelude::{FromPrimitive, ToPrimitive};
use rust_decimal::{Decimal, RoundingStrategy};
use tokio_postgres::Row;
use uuid::Uuid;

pub const SERVICE: &str = "check-ins";
pub const SUFFIX: &str = "checkins";
/// The sphere RN-56 measures on, in metres (the IUGG mean Earth radius).
pub const EARTH_RADIUS_M: f64 = 6_371_008.8;
/// How far ahead of the server a phone's clock may be.
pub const FUTURE_TOLERANCE_MINUTES: i64 = 5;

tokio::task_local! {
    static REQUEST_QUERY: String;
}

// ---------- the rule ----------

/// The five verdicts, in the order RN-56 tries them.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Reason {
    NoFix,
    StoreLocationUnknown,
    LowAccuracy,
    WithinGeofence,
    OutsideGeofence,
}

impl Reason {
    pub fn as_str(self) -> &'static str {
        match self {
            Reason::NoFix => "no_fix",
            Reason::StoreLocationUnknown => "store_location_unknown",
            Reason::LowAccuracy => "low_accuracy",
            Reason::WithinGeofence => "within_geofence",
            Reason::OutsideGeofence => "outside_geofence",
        }
    }
}

/// What the phone reported.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Fix {
    pub latitude: Decimal,
    pub longitude: Decimal,
    pub accuracy_m: Decimal,
}

/// Where the store is, from `api_stores.store_directory_v1`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Geofence {
    pub latitude: Decimal,
    pub longitude: Decimal,
    pub radius_m: i32,
}

/// The haversine distance between two points, in metres, on RN-56's sphere.
pub fn haversine_m(from: (f64, f64), to: (f64, f64)) -> f64 {
    let (lat1, lat2) = (from.0.to_radians(), to.0.to_radians());
    let half_lat = (to.0 - from.0).to_radians() / 2.0;
    let half_lon = (to.1 - from.1).to_radians() / 2.0;
    let a = half_lat.sin().powi(2) + lat1.cos() * lat2.cos() * half_lon.sin().powi(2);
    2.0 * EARTH_RADIUS_M * a.sqrt().asin()
}

/// RN-56: the first matching reason wins, and the distance is rounded to one
/// decimal before it is compared, so what is stored is what was judged.
pub fn verdict(fix: Option<Fix>, geofence: Option<Geofence>) -> (Reason, Option<Decimal>) {
    let Some(fix) = fix else {
        return (Reason::NoFix, None);
    };
    let Some(geofence) = geofence else {
        return (Reason::StoreLocationUnknown, None);
    };
    let degrees = |value: Decimal| value.to_f64().unwrap_or(f64::NAN);
    let metres = haversine_m(
        (degrees(fix.latitude), degrees(fix.longitude)),
        (degrees(geofence.latitude), degrees(geofence.longitude)),
    );
    let distance = Decimal::from_f64(metres)
        .unwrap_or(Decimal::MAX)
        .round_dp_with_strategy(1, RoundingStrategy::MidpointAwayFromZero);
    let radius = Decimal::from(geofence.radius_m);
    let reason = if fix.accuracy_m > radius {
        Reason::LowAccuracy
    } else if distance <= radius {
        Reason::WithinGeofence
    } else {
        Reason::OutsideGeofence
    };
    (reason, Some(distance))
}

// ---------- records ----------

/// One check-in as read back, with its effective verdict.
#[derive(Clone, Debug, PartialEq)]
pub struct CheckIn {
    pub check_in_id: Uuid,
    pub client_check_in_id: Uuid,
    pub user_id: Uuid,
    pub store_id: i16,
    pub latitude: Option<Decimal>,
    pub longitude: Option<Decimal>,
    pub accuracy_m: Option<Decimal>,
    pub distance_m: Option<Decimal>,
    pub reason: String,
    pub occurred_at: DateTime<Utc>,
    pub recorded_at: DateTime<Utc>,
    pub verified: bool,
}

impl CheckIn {
    fn from_row(row: &Row) -> Result<Self, ApiError> {
        Ok(Self {
            check_in_id: row.try_get(0)?,
            client_check_in_id: row.try_get(1)?,
            user_id: row.try_get(2)?,
            store_id: row.try_get(3)?,
            latitude: row.try_get(4)?,
            longitude: row.try_get(5)?,
            accuracy_m: row.try_get(6)?,
            distance_m: row.try_get(7)?,
            reason: row.try_get(8)?,
            occurred_at: row.try_get(9)?,
            recorded_at: row.try_get(10)?,
            verified: row.try_get(11)?,
        })
    }

    /// True exactly when the server's own verdict was within the geofence.
    pub fn auto_verified(&self) -> bool {
        self.reason == Reason::WithinGeofence.as_str()
    }

    fn wire(&self) -> IndexMap<String, Value> {
        IndexMap::from([
            (
                "check_in_id".to_owned(),
                Value::from(self.check_in_id.to_string()),
            ),
            (
                "client_check_in_id".to_owned(),
                Value::from(self.client_check_in_id.to_string()),
            ),
            ("user_id".to_owned(), Value::from(self.user_id.to_string())),
            ("store_id".to_owned(), Value::from(self.store_id)),
            ("latitude".to_owned(), Value::from(self.latitude)),
            ("longitude".to_owned(), Value::from(self.longitude)),
            ("accuracy_m".to_owned(), Value::from(self.accuracy_m)),
            ("distance_m".to_owned(), Value::from(self.distance_m)),
            ("reason".to_owned(), Value::from(self.reason.clone())),
            (
                "auto_verified".to_owned(),
                Value::from(self.auto_verified()),
            ),
            ("verified".to_owned(), Value::from(self.verified)),
            ("occurred_at".to_owned(), Value::from(self.occurred_at)),
            ("recorded_at".to_owned(), Value::from(self.recorded_at)),
        ])
    }

    /// The same request again: same user, store, fix and moment.
    fn same_request(&self, user_id: Uuid, request: &CheckInRequest) -> bool {
        self.user_id == user_id
            && i32::from(self.store_id) == request.store_id
            && self.occurred_at == request.occurred_at
            && self.latitude == request.fix.map(|fix| fix.latitude)
            && self.longitude == request.fix.map(|fix| fix.longitude)
            && self.accuracy_m == request.fix.map(|fix| fix.accuracy_m)
    }
}

/// One appended review.
#[derive(Clone, Debug, PartialEq)]
pub struct Review {
    pub review_id: i64,
    pub verified: bool,
    pub reason: String,
    pub reviewed_by: Uuid,
    pub reviewed_at: DateTime<Utc>,
}

impl Review {
    fn from_row(row: &Row) -> Result<Self, ApiError> {
        Ok(Self {
            review_id: row.try_get(0)?,
            verified: row.try_get(1)?,
            reason: row.try_get(2)?,
            reviewed_by: row.try_get(3)?,
            reviewed_at: row.try_get(4)?,
        })
    }

    fn wire(&self) -> IndexMap<String, Value> {
        IndexMap::from([
            ("review_id".to_owned(), Value::from(self.review_id)),
            ("verified".to_owned(), Value::from(self.verified)),
            ("reason".to_owned(), Value::from(self.reason.clone())),
            (
                "reviewed_by".to_owned(),
                Value::from(self.reviewed_by.to_string()),
            ),
            ("reviewed_at".to_owned(), Value::from(self.reviewed_at)),
        ])
    }
}

/// A validated `CheckInRequest` body.
#[derive(Clone, Debug, PartialEq)]
pub struct CheckInRequest {
    pub client_check_in_id: Uuid,
    pub store_id: i32,
    pub occurred_at: DateTime<Utc>,
    pub fix: Option<Fix>,
}

/// The list's filters, each optional.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Filters {
    pub store_id: Option<i32>,
    pub user_id: Option<Uuid>,
    pub verified: Option<bool>,
    pub from: Option<DateTime<Utc>>,
    pub to: Option<DateTime<Utc>>,
}

/// Whether a check-in was written now or is a replay of an earlier one.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Outcome {
    Created,
    Replayed,
}

// ---------- errors ----------

fn invalid_body(message: &str, field: &str, code: &str) -> ApiError {
    ApiError::new(422, "invalid_body", message).with_details(vec![Detail::new(field, code)])
}

fn invalid_query(message: &str, field: &str, code: &str) -> ApiError {
    ApiError::new(422, "invalid_query", message).with_details(vec![Detail::new(field, code)])
}

fn invalid_token() -> ApiError {
    ApiError::new(401, "invalid_token", "The bearer token is invalid.")
}

fn unknown_store(store_id: i32) -> ApiError {
    ApiError::new(
        422,
        "unknown_store",
        format!("Store {store_id} is missing or inactive."),
    )
    .with_details(vec![Detail::new("store_id", "unknown_store")])
}

fn check_in_not_found(id: Uuid) -> ApiError {
    ApiError::new(
        404,
        "check_in_not_found",
        format!("Check-in {id} was not found."),
    )
}

fn conflict() -> ApiError {
    ApiError::new(
        409,
        "client_check_in_conflict",
        "This client_check_in_id was already used with different values.",
    )
}

/// A refusal the schema raises, in the contract's words, by constraint name.
fn write_error(error: tokio_postgres::Error, store_id: i32) -> ApiError {
    let constraint = error
        .as_db_error()
        .and_then(|database| database.constraint())
        .unwrap_or_default();
    match constraint {
        // A signed token for an account that does not exist.
        "store_check_in_user_id_fkey" | "check_in_review_reviewed_by_fkey" => invalid_token(),
        "store_check_in_store_id_fkey" => unknown_store(store_id),
        "store_check_in_latitude_check" => {
            invalid_body("The fix is outside its range.", "latitude", "out_of_range")
        }
        "store_check_in_longitude_check" => {
            invalid_body("The fix is outside its range.", "longitude", "out_of_range")
        }
        "store_check_in_accuracy_m_check" => invalid_body(
            "The fix is outside its range.",
            "accuracy_m",
            "out_of_range",
        ),
        "check_in_review_reason_check" => {
            invalid_body("A review needs a reason.", "reason", "blank")
        }
        _ => ApiError::from(error),
    }
}

fn caller(actor: &Principal) -> Result<Uuid, ApiError> {
    Uuid::parse_str(&actor.sub).map_err(|_| invalid_token())
}

/// The one way a write starts: a transaction that has already run
/// `SELECT set_config('mosaiq.user_id', $1, true)` with the caller's `sub`
/// bound, so PostgreSQL knows who wrote (ADR-0034). Both writes open theirs
/// here, and a test reads the setting back from inside one.
pub async fn write_transaction<'a>(
    connection: &'a mut Connection,
    actor: &Principal,
) -> Result<deadpool_postgres::Transaction<'a>, ApiError> {
    connection.transaction(Some(actor)).await
}

// ---------- reading the body and the query ----------

fn decimal(value: &serde_json::Value) -> Option<Decimal> {
    match value {
        serde_json::Value::Number(number) => number.to_string().parse().ok(),
        serde_json::Value::String(text) => text.trim().parse().ok(),
        _ => None,
    }
}

fn timestamp(value: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(value)
        .ok()
        .map(|moment| moment.with_timezone(&Utc))
}

/// Read a `CheckInRequest`. The kit has validated it against the contract's
/// schema; this keeps the parts a schema cannot say, and refuses a partial fix
/// even if a validator ignored `dependentRequired`.
pub fn parse_check_in(body: &serde_json::Value) -> Result<CheckInRequest, ApiError> {
    let malformed = || invalid_body("The check-in is invalid.", "body", "invalid");
    let client_check_in_id = body["client_check_in_id"]
        .as_str()
        .and_then(|text| Uuid::parse_str(text).ok())
        .ok_or_else(malformed)?;
    let store_id = body["store_id"]
        .as_i64()
        .and_then(|value| i32::try_from(value).ok())
        .ok_or_else(malformed)?;
    let occurred_at = body["occurred_at"]
        .as_str()
        .and_then(timestamp)
        .ok_or_else(malformed)?;
    let parts = ["latitude", "longitude", "accuracy_m"].map(|name| {
        let value = &body[name];
        (name, (!value.is_null()).then(|| decimal(value)))
    });
    let fix = match parts {
        [(_, None), (_, None), (_, None)] => None,
        [(_, Some(Some(latitude))), (_, Some(Some(longitude))), (_, Some(Some(accuracy_m)))] => {
            Some(Fix {
                latitude,
                longitude,
                accuracy_m,
            })
        }
        _ => {
            let missing = parts
                .iter()
                .find(|(_, value)| value.is_none())
                .map_or("latitude", |(name, _)| name);
            return Err(invalid_body(
                "A fix needs latitude, longitude and accuracy_m together.",
                missing,
                "required_with_fix",
            ));
        }
    };
    Ok(CheckInRequest {
        client_check_in_id,
        store_id,
        occurred_at,
        fix,
    })
}

/// Read the list's query string. A malformed value is `invalid_query`.
pub fn parse_filters(query: &str) -> Result<Filters, ApiError> {
    let values: HashMap<String, String> = form_urlencoded::parse(query.as_bytes())
        .map(|(key, value)| (key.into_owned(), value.into_owned()))
        .collect();
    let bad = |field: &str| invalid_query("A query value is invalid.", field, "invalid");
    let mut filters = Filters::default();
    if let Some(text) = values.get("store_id") {
        filters.store_id = Some(text.parse().map_err(|_| bad("store_id"))?);
    }
    if let Some(text) = values.get("user_id") {
        filters.user_id = Some(Uuid::parse_str(text).map_err(|_| bad("user_id"))?);
    }
    if let Some(text) = values.get("verified") {
        filters.verified = Some(match text.as_str() {
            "true" => true,
            "false" => false,
            _ => return Err(bad("verified")),
        });
    }
    if let Some(text) = values.get("from") {
        filters.from = Some(timestamp(text).ok_or_else(|| bad("from"))?);
    }
    if let Some(text) = values.get("to") {
        filters.to = Some(timestamp(text).ok_or_else(|| bad("to"))?);
    }
    if let (Some(from), Some(to)) = (filters.from, filters.to) {
        if to < from {
            return Err(invalid_query("to is before from.", "to", "before_from"));
        }
    }
    Ok(filters)
}

// ---------- the write and read paths ----------

/// Record a check-in as `actor`, or answer a replay of one.
///
/// One transaction carries the actor (`mosaiq.user_id`). A known
/// `client_check_in_id` is compared before anything is written. Otherwise the
/// verdict is computed and inserted with `ON CONFLICT DO NOTHING`: if a
/// concurrent request with the same id won, the insert waits for it, writes
/// nothing, and the winner is read back and compared, so two identical posts
/// leave one row.
pub async fn record_check_in(
    db: &Database,
    actor: &Principal,
    request: &CheckInRequest,
    now: DateTime<Utc>,
) -> Result<(CheckIn, Outcome), ApiError> {
    let user_id = caller(actor)?;
    let mut connection = db.connection().await?;
    let transaction = write_transaction(&mut connection, actor).await?;
    if let Some(existing) = read_by_client_id(&*transaction, request.client_check_in_id).await? {
        return replay(existing, user_id, request);
    }
    if request.occurred_at > now + Duration::minutes(FUTURE_TOLERANCE_MINUTES) {
        return Err(ApiError::new(
            422,
            "occurred_in_future",
            "occurred_at is more than five minutes ahead of the server.",
        )
        .with_details(vec![Detail::new("occurred_at", "in_future")]));
    }
    let store_id = i16::try_from(request.store_id).map_err(|_| unknown_store(request.store_id))?;
    let store = transaction
        .query_opt(
            "SELECT is_active, latitude, longitude, geofence_radius_m \
             FROM api_stores.store_directory_v1 WHERE store_id = $1",
            &[&store_id],
        )
        .await?;
    let Some(store) = store.filter(|row| row.get::<_, bool>(0)) else {
        return Err(unknown_store(request.store_id));
    };
    let geofence = match (
        store.try_get::<_, Option<Decimal>>(1)?,
        store.try_get::<_, Option<Decimal>>(2)?,
        store.try_get::<_, Option<i32>>(3)?,
    ) {
        (Some(latitude), Some(longitude), Some(radius_m)) => Some(Geofence {
            latitude,
            longitude,
            radius_m,
        }),
        _ => None,
    };
    let (reason, distance_m) = verdict(request.fix, geofence);
    let inserted = transaction
        .query_opt(
            "INSERT INTO checkins.store_check_in \
             (client_check_in_id, user_id, store_id, latitude, longitude, accuracy_m, \
              distance_m, reason, occurred_at) \
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) \
             ON CONFLICT (client_check_in_id) DO NOTHING \
             RETURNING check_in_id, client_check_in_id, user_id, store_id, latitude, \
             longitude, accuracy_m, distance_m, reason, occurred_at, recorded_at, \
             reason = 'within_geofence'",
            &[
                &request.client_check_in_id,
                &user_id,
                &store_id,
                &request.fix.map(|fix| fix.latitude),
                &request.fix.map(|fix| fix.longitude),
                &request.fix.map(|fix| fix.accuracy_m),
                &distance_m,
                &reason.as_str(),
                &request.occurred_at,
            ],
        )
        .await
        .map_err(|error| write_error(error, request.store_id))?;
    let result = match inserted {
        Some(row) => (CheckIn::from_row(&row)?, Outcome::Created),
        None => {
            let winner = read_by_client_id(&*transaction, request.client_check_in_id)
                .await?
                .ok_or_else(conflict)?;
            replay(winner, user_id, request)?
        }
    };
    transaction.commit().await?;
    Ok(result)
}

fn replay(
    existing: CheckIn,
    user_id: Uuid,
    request: &CheckInRequest,
) -> Result<(CheckIn, Outcome), ApiError> {
    if existing.same_request(user_id, request) {
        Ok((existing, Outcome::Replayed))
    } else {
        Err(conflict())
    }
}

async fn read_by_client_id(
    client: &impl tokio_postgres::GenericClient,
    client_check_in_id: Uuid,
) -> Result<Option<CheckIn>, ApiError> {
    let row = client
        .query_opt(
            "SELECT c.check_in_id, c.client_check_in_id, c.user_id, c.store_id, c.latitude, \
             c.longitude, c.accuracy_m, c.distance_m, c.reason, c.occurred_at, c.recorded_at, \
             COALESCE(r.verified, c.reason = 'within_geofence') \
             FROM checkins.store_check_in AS c \
             LEFT JOIN LATERAL (SELECT verified FROM checkins.check_in_review \
                                 WHERE check_in_id = c.check_in_id \
                                 ORDER BY reviewed_at DESC, review_id DESC LIMIT 1) AS r ON TRUE \
             WHERE c.client_check_in_id = $1",
            &[&client_check_in_id],
        )
        .await?;
    row.as_ref().map(CheckIn::from_row).transpose()
}

/// One check-in with its reviews, oldest first.
pub async fn read_check_in(db: &Database, id: Uuid) -> Result<(CheckIn, Vec<Review>), ApiError> {
    let connection = db.connection().await?;
    let row = connection
        .query_opt(
            "SELECT c.check_in_id, c.client_check_in_id, c.user_id, c.store_id, c.latitude, \
             c.longitude, c.accuracy_m, c.distance_m, c.reason, c.occurred_at, c.recorded_at, \
             COALESCE(r.verified, c.reason = 'within_geofence') \
             FROM checkins.store_check_in AS c \
             LEFT JOIN LATERAL (SELECT verified FROM checkins.check_in_review \
                                 WHERE check_in_id = c.check_in_id \
                                 ORDER BY reviewed_at DESC, review_id DESC LIMIT 1) AS r ON TRUE \
             WHERE c.check_in_id = $1",
            &[&id],
        )
        .await?
        .ok_or_else(|| check_in_not_found(id))?;
    let check_in = CheckIn::from_row(&row)?;
    let reviews = connection
        .query(
            "SELECT review_id, verified, reason, reviewed_by, reviewed_at \
             FROM checkins.check_in_review WHERE check_in_id = $1 \
             ORDER BY reviewed_at, review_id",
            &[&id],
        )
        .await?
        .iter()
        .map(Review::from_row)
        .collect::<Result<Vec<_>, _>>()?;
    Ok((check_in, reviews))
}

/// A page of check-ins, newest first, and the total that match.
pub async fn list_check_ins(
    db: &Database,
    filters: &Filters,
    page: u32,
    size: u32,
) -> Result<(Vec<CheckIn>, u64), ApiError> {
    let connection = db.connection().await?;
    let limit = i64::from(size);
    let offset = i64::from(page.saturating_sub(1)) * limit;
    // One statement: the total always comes back, even past the last page,
    // as a row whose check-in columns are null.
    let rows = connection
        .query(
            "WITH matched AS ( \
                 SELECT c.check_in_id, c.client_check_in_id, c.user_id, c.store_id, \
                        c.latitude, c.longitude, c.accuracy_m, c.distance_m, c.reason, \
                        c.occurred_at, c.recorded_at, \
                        COALESCE(r.verified, c.reason = 'within_geofence') AS verified \
                 FROM checkins.store_check_in AS c \
                 LEFT JOIN LATERAL (SELECT verified FROM checkins.check_in_review \
                                     WHERE check_in_id = c.check_in_id \
                                     ORDER BY reviewed_at DESC, review_id DESC LIMIT 1) AS r \
                        ON TRUE \
                 WHERE ($1::int4 IS NULL OR c.store_id = $1) \
                   AND ($2::uuid IS NULL OR c.user_id = $2) \
                   AND ($3::bool IS NULL \
                        OR COALESCE(r.verified, c.reason = 'within_geofence') = $3) \
                   AND ($4::timestamptz IS NULL OR c.occurred_at >= $4) \
                   AND ($5::timestamptz IS NULL OR c.occurred_at <= $5)) \
             SELECT p.check_in_id, p.client_check_in_id, p.user_id, p.store_id, p.latitude, \
                    p.longitude, p.accuracy_m, p.distance_m, p.reason, p.occurred_at, \
                    p.recorded_at, p.verified, t.total \
             FROM (SELECT count(*) AS total FROM matched) AS t \
             LEFT JOIN LATERAL (SELECT * FROM matched \
                                 ORDER BY occurred_at DESC, check_in_id \
                                 LIMIT $6 OFFSET $7) AS p ON TRUE",
            &[
                &filters.store_id,
                &filters.user_id,
                &filters.verified,
                &filters.from,
                &filters.to,
                &limit,
                &offset,
            ],
        )
        .await?;
    let total = rows
        .first()
        .map(|row| row.try_get::<_, i64>(12))
        .transpose()?
        .unwrap_or(0);
    let items = rows
        .iter()
        .filter(|row| row.get::<_, Option<Uuid>>(0).is_some())
        .map(CheckIn::from_row)
        .collect::<Result<Vec<_>, _>>()?;
    Ok((items, u64::try_from(total).unwrap_or(0)))
}

/// Append a review as `actor`. Nothing else ever changes a verdict.
pub async fn append_review(
    db: &Database,
    actor: &Principal,
    check_in_id: Uuid,
    verified: bool,
    reason: &str,
) -> Result<Review, ApiError> {
    let reviewer = caller(actor)?;
    if reason.trim().is_empty() {
        return Err(invalid_body("A review needs a reason.", "reason", "blank"));
    }
    let mut connection = db.connection().await?;
    let transaction = write_transaction(&mut connection, actor).await?;
    if transaction
        .query_opt(
            "SELECT 1 FROM checkins.store_check_in WHERE check_in_id = $1",
            &[&check_in_id],
        )
        .await?
        .is_none()
    {
        return Err(check_in_not_found(check_in_id));
    }
    let row = transaction
        .query_one(
            "INSERT INTO checkins.check_in_review (check_in_id, verified, reason, reviewed_by) \
             VALUES ($1, $2, $3, $4) \
             RETURNING review_id, verified, reason, reviewed_by, reviewed_at",
            &[&check_in_id, &verified, &reason, &reviewer],
        )
        .await
        .map_err(|error| write_error(error, 0))?;
    let review = Review::from_row(&row)?;
    transaction.commit().await?;
    Ok(review)
}

// ---------- the operations ----------

fn principal(ctx: &Ctx) -> Result<&Principal, ApiError> {
    ctx.principal().ok_or_else(invalid_token)
}

fn check_in_id(ctx: &Ctx) -> Result<Uuid, ApiError> {
    ctx.path_uuid("check_in_id")
        .ok_or_else(|| ApiError::new(404, "check_in_not_found", "The check-in was not found."))
}

async fn create_check_in(ctx: Ctx) -> Result<Reply, ApiError> {
    let body = ctx.body.as_ref().expect("contract requires a body");
    let request = parse_check_in(body)?;
    let (check_in, outcome) =
        record_check_in(ctx.db(), principal(&ctx)?, &request, Utc::now()).await?;
    let status = match outcome {
        Outcome::Created => 201,
        Outcome::Replayed => 200,
    };
    Ok(Representation::new("CheckIn", check_in.wire())
        .status(status)
        .into())
}

async fn list(ctx: Ctx) -> Result<Reply, ApiError> {
    let query = REQUEST_QUERY.try_with(Clone::clone).unwrap_or_default();
    let filters = parse_filters(&query)?;
    let (items, total) = list_check_ins(ctx.db(), &filters, ctx.page, ctx.size).await?;
    let items = items.iter().map(CheckIn::wire).collect();
    Ok(
        Page::new("CheckIns", "CheckIn", "check_ins", items, ctx.page, total)
            .size(ctx.size)
            .into(),
    )
}

async fn get(ctx: Ctx) -> Result<Reply, ApiError> {
    let (check_in, reviews) = read_check_in(ctx.db(), check_in_id(&ctx)?).await?;
    let mut data = check_in.wire();
    data.insert(
        "reviews".to_owned(),
        Value::List(
            reviews
                .iter()
                .map(|review| Value::Object(review.wire()))
                .collect(),
        ),
    );
    Ok(Representation::new("CheckInDetail", data).into())
}

async fn create_review(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = check_in_id(&ctx)?;
    let body = ctx.body.as_ref().expect("contract requires a body");
    let verified = body["verified"]
        .as_bool()
        .ok_or_else(|| invalid_body("The review is invalid.", "verified", "invalid"))?;
    let reason = body["reason"].as_str().unwrap_or_default();
    let review = append_review(ctx.db(), principal(&ctx)?, id, verified, reason).await?;
    Ok(Representation::new("Review", review.wire())
        .status(201)
        .into())
}

async fn database_ready(db: Arc<Database>) -> bool {
    let Ok(connection) = db.connection().await else {
        return false;
    };
    connection
        .query_one(
            "SELECT (SELECT count(*) FROM checkins.store_check_in WHERE FALSE) \
                  + (SELECT count(*) FROM checkins.check_in_review WHERE FALSE) \
                  + (SELECT count(*) FROM api_stores.store_directory_v1 WHERE FALSE)",
            &[],
        )
        .await
        .is_ok()
}

/// kit-rs gives a handler no query string, so it is kept for the request.
async fn capture_query(request: Request<Body>, next: Next) -> Response {
    let query = request.uri().query().unwrap_or_default().to_owned();
    REQUEST_QUERY.scope(query, next.run(request)).await
}

pub fn run() -> Result<(), Box<dyn std::error::Error>> {
    configure_logging(&std::env::var("LOG_LEVEL").unwrap_or_else(|_| "info".to_owned()));
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let kit = Kit::new(SERVICE, SUFFIX, "contracts", KitConfig::from_env(SUFFIX))?;
        let router = kit
            .operation("createCheckIn", create_check_in)?
            .operation("listCheckIns", list)?
            .operation("getCheckIn", get)?
            .operation("createCheckInReview", create_review)?
            .ready_check(database_ready)
            .into_router()
            .layer(middleware::from_fn(capture_query));
        let listener = tokio::net::TcpListener::bind("0.0.0.0:5013").await?;
        mosaiq_kit::serve(listener, router).await?;
        Ok::<(), Box<dyn std::error::Error>>(())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dec(text: &str) -> Decimal {
        text.parse().unwrap()
    }

    const STORE: Geofence = Geofence {
        latitude: Decimal::from_parts(256866, 0, 0, false, 4),
        longitude: Decimal::from_parts(1003161, 0, 0, true, 4),
        radius_m: 150,
    };

    fn north_of_store(metres: f64, accuracy: &str) -> Fix {
        // One degree of latitude is 111,195.08 m on RN-56's sphere.
        let latitude = 25.6866 + metres / 111_195.08;
        Fix {
            latitude: Decimal::from_f64(latitude).unwrap().round_dp(9),
            longitude: dec("-100.3161"),
            accuracy_m: dec(accuracy),
        }
    }

    #[test]
    fn no_fix_wins_over_everything() {
        assert_eq!(verdict(None, Some(STORE)), (Reason::NoFix, None));
        assert_eq!(verdict(None, None), (Reason::NoFix, None));
    }

    #[test]
    fn a_store_without_a_location_is_unknown_not_refused() {
        let fix = north_of_store(10.0, "5");
        assert_eq!(
            verdict(Some(fix), None),
            (Reason::StoreLocationUnknown, None)
        );
    }

    #[test]
    fn exactly_on_the_radius_is_within() {
        let (reason, distance) = verdict(Some(north_of_store(150.0, "5")), Some(STORE));
        assert_eq!(distance, Some(dec("150.0")));
        assert_eq!(reason, Reason::WithinGeofence);
    }

    #[test]
    fn a_metre_beyond_the_radius_is_outside() {
        let (reason, distance) = verdict(Some(north_of_store(151.0, "5")), Some(STORE));
        assert_eq!(distance, Some(dec("151.0")));
        assert_eq!(reason, Reason::OutsideGeofence);
    }

    #[test]
    fn accuracy_one_metre_over_the_radius_is_low_accuracy_with_its_distance() {
        let (reason, distance) = verdict(Some(north_of_store(10.0, "151")), Some(STORE));
        assert_eq!(reason, Reason::LowAccuracy);
        assert_eq!(distance, Some(dec("10.0")));
        let (reason, _) = verdict(Some(north_of_store(10.0, "150")), Some(STORE));
        assert_eq!(reason, Reason::WithinGeofence);
    }

    #[test]
    fn the_haversine_matches_a_known_distance() {
        // Monterrey to CDMX centres: 705,926.913 m, as Python and PostgreSQL compute it.
        let metres = haversine_m((25.6866, -100.3161), (19.4326, -99.1332));
        assert!((metres - 705_926.913).abs() < 0.01, "{metres}");
    }

    #[test]
    fn a_partial_fix_is_invalid_body_naming_what_is_missing() {
        let body = serde_json::json!({
            "client_check_in_id": "0e6d5c4b-3a29-4817-96f5-a4b3c2d1e0f2",
            "store_id": 3,
            "occurred_at": "2026-10-15T14:02:00Z",
            "latitude": 19.4,
            "longitude": -99.1
        });
        let error = parse_check_in(&body).unwrap_err();
        assert_eq!(error.code, "invalid_body");
        assert_eq!(error.details[0].field, "accuracy_m");
    }

    #[test]
    fn a_complete_and_an_empty_fix_both_parse() {
        let mut body = serde_json::json!({
            "client_check_in_id": "0e6d5c4b-3a29-4817-96f5-a4b3c2d1e0f2",
            "store_id": 3,
            "occurred_at": "2026-10-15T14:02:00-06:00",
            "latitude": 19.4369,
            "longitude": -99.1401,
            "accuracy_m": 8.0
        });
        let request = parse_check_in(&body).unwrap();
        assert_eq!(request.fix.unwrap().latitude, dec("19.4369"));
        assert_eq!(
            request.occurred_at.to_rfc3339(),
            "2026-10-15T20:02:00+00:00"
        );
        for name in ["latitude", "longitude", "accuracy_m"] {
            body.as_object_mut().unwrap().remove(name);
        }
        assert_eq!(parse_check_in(&body).unwrap().fix, None);
    }

    #[test]
    fn filters_parse_and_refuse_to_before_from() {
        let filters = parse_filters(
            "store_id=3&verified=false&from=2026-10-01T00:00:00Z&to=2026-10-02T00:00:00Z",
        )
        .unwrap();
        assert_eq!(filters.store_id, Some(3));
        assert_eq!(filters.verified, Some(false));
        let error = parse_filters("from=2026-10-02T00:00:00Z&to=2026-10-01T00:00:00Z").unwrap_err();
        assert_eq!(
            (error.code.as_str(), error.details[0].code.as_str()),
            ("invalid_query", "before_from")
        );
        assert_eq!(
            parse_filters("verified=yes").unwrap_err().code,
            "invalid_query"
        );
    }
}
