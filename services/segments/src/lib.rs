//! segments-service: what the web's segmentation runs recorded, read-only
//! (F14-20, RN-20, RN-21). The label vocabulary with its sizes in the latest
//! run, a customer's current assignment and the assignment history.
//!
//! Reads are cached on redis-cache under `segments:cache:{run_id}:*`. The
//! current run id comes from PostgreSQL and moves when the `segments` consumer
//! group sees `stream:segments.v1.run_recorded`, so the old run's keys are
//! simply never asked for again and expire by their TTL; nothing scans.
//!
//! A customer's assignment history names one person, so its read is logged
//! (ADR-0072, F21-25): `GET /v1/segment-assignments?customer_id=` records a
//! `HISTORY_READ` through `api_audit.record_event_v1` in the read's own
//! transaction, a 403 on it records one with outcome `REFUSED`, and a read
//! whose event cannot be recorded answers 503 `audit_unavailable` with no rows.

use std::collections::{BTreeMap, HashMap};
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{header, Method, StatusCode};
use axum::middleware::{self, Next};
use axum::response::Response;
use axum::Router;
use chrono::{DateTime, Utc};
use mosaiq_kit::auth::{Authenticator, JwksStore, StateRedis};
use mosaiq_kit::rendering::render_error;
use mosaiq_kit::{
    configure_logging, service_redis_info, ApiError, Ctx, Database, Detail, Format, Kit, KitConfig,
    OutboxRelay, Page, Principal, Reply, Representation, StreamConsumer, Value,
};
use redis::aio::MultiplexedConnection;
use redis::AsyncCommands;
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use tokio_postgres::{GenericClient, Row};
use uuid::Uuid;

pub const SERVICE: &str = "segments";
pub const SUFFIX: &str = "segments";
pub const STREAM: &str = "stream:segments.v1.run_recorded";
/// A customer's label changed (F22-15). Produced here and consumed by recommendations;
/// this service reads no entry of it.
pub const ASSIGNMENT_STREAM: &str = "stream:segments.v1.assignment_changed";
const CACHE_SECONDS: u64 = 900;
/// The run tracker before PostgreSQL has been asked.
const RUN_UNSET: i64 = i64::MIN;
/// The run tracker when no run exists yet (identity ids start at 1).
const NO_RUN: i64 = 0;

static READER: OnceLock<Reader> = OnceLock::new();
tokio::task_local! {
    static REQUEST: RequestInfo;
}

#[derive(Clone, Default)]
struct RequestInfo {
    query: String,
    accept: String,
}

// ---------------------------------------------------------------------------
// Records
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
pub struct SegmentItem {
    pub label_code: String,
    pub name: String,
    pub description: Option<String>,
    pub ordinal_position: i16,
    pub size: i64,
}

impl SegmentItem {
    fn wire(&self) -> Representation {
        Representation::new(
            "Segment",
            [
                ("label_code", Value::from(self.label_code.clone())),
                ("name", Value::from(self.name.clone())),
                ("description", Value::from(self.description.clone())),
                ("ordinal_position", Value::from(self.ordinal_position)),
                ("size", Value::from(self.size)),
            ],
        )
    }
}

#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
pub struct SegmentList {
    pub run_id: Option<i64>,
    pub run_at: Option<DateTime<Utc>>,
    pub unassigned_count: i64,
    pub segments: Vec<SegmentItem>,
}

#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
pub struct CurrentAssignment {
    pub customer_id: String,
    pub state: String,
    pub label_code: Option<String>,
    pub r_score: Option<i16>,
    pub f_score: Option<i16>,
    pub m_score: Option<i16>,
    pub since: Option<DateTime<Utc>>,
    pub run_id: Option<i64>,
}

impl CurrentAssignment {
    fn wire(&self) -> Representation {
        Representation::new(
            "CurrentAssignment",
            [
                ("customer_id", Value::from(self.customer_id.clone())),
                ("state", Value::from(self.state.clone())),
                ("label_code", Value::from(self.label_code.clone())),
                ("r_score", Value::from(self.r_score)),
                ("f_score", Value::from(self.f_score)),
                ("m_score", Value::from(self.m_score)),
                ("since", Value::from(self.since)),
                ("run_id", Value::from(self.run_id)),
            ],
        )
    }
}

/// A customer's current state as a floor role reads it (ADR-0060): the label
/// and `since`, never the R, F and M scores or `run_id`. The type has no field
/// for them, so no code path can render them for a floor role.
#[derive(Clone, Debug, PartialEq)]
pub struct FloorAssignment {
    pub customer_id: String,
    pub state: String,
    pub label_code: Option<String>,
    pub since: Option<DateTime<Utc>>,
}

impl FloorAssignment {
    fn wire(&self) -> Representation {
        Representation::new(
            "FloorCurrentAssignment",
            [
                ("customer_id", Value::from(self.customer_id.clone())),
                ("state", Value::from(self.state.clone())),
                ("label_code", Value::from(self.label_code.clone())),
                ("since", Value::from(self.since)),
            ],
        )
    }
}

impl From<&CurrentAssignment> for FloorAssignment {
    /// The floor view keeps the state, the label and `since` exactly as the
    /// current assignment reports them, and drops everything else.
    fn from(current: &CurrentAssignment) -> Self {
        Self {
            customer_id: current.customer_id.clone(),
            state: current.state.clone(),
            label_code: current.label_code.clone(),
            since: current.since,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Assignment {
    pub customer_id: Uuid,
    pub run_id: i64,
    pub label_code: Option<String>,
    pub r_score: Option<i16>,
    pub f_score: Option<i16>,
    pub m_score: Option<i16>,
    pub valid_from: DateTime<Utc>,
    pub valid_to: Option<DateTime<Utc>>,
}

const RECENCY_STABLE_DAYS: i64 = 7;
const MONETARY_STABLE_PERCENT: i64 = 10;

#[derive(Clone, Debug, PartialEq)]
pub struct MigrationWindow {
    pub from: DateTime<Utc>,
    pub to: DateTime<Utc>,
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct MigrationState {
    pub state: String,
    pub label_code: Option<String>,
}

impl MigrationState {
    fn new(label: Option<String>, present: bool) -> Self {
        match (present, label) {
            (false, _) => Self {
                state: "new".to_owned(),
                label_code: None,
            },
            (true, Some(code)) => Self {
                state: "label".to_owned(),
                label_code: Some(code),
            },
            (true, None) => Self {
                state: "unassigned".to_owned(),
                label_code: None,
            },
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Snapshot {
    pub customer_id: Uuid,
    pub state: MigrationState,
    pub run_id: Option<i64>,
    pub model_version_id: Option<i64>,
    pub run_at: Option<DateTime<Utc>>,
    pub valid_from: Option<DateTime<Utc>>,
    pub cause: Option<String>,
    pub recency_last_purchase_at: Option<DateTime<Utc>>,
    pub frequency_count: Option<i32>,
    pub monetary_total: Option<String>,
    pub r_score: Option<i16>,
    pub f_score: Option<i16>,
    pub m_score: Option<i16>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Migration {
    pub customer_id: Uuid,
    pub previous: Snapshot,
    pub new: Snapshot,
    pub movement: String,
    pub changed_at: Option<DateTime<Utc>>,
    pub methodological: bool,
    pub responsible_variables: Vec<String>,
    pub rank_only: bool,
}

impl Migration {
    fn wire(&self) -> Representation {
        Representation::new(
            "SegmentMigration",
            [
                ("customer_id", Value::from(self.customer_id.to_string())),
                (
                    "previous_state",
                    Value::from(self.previous.state.state.clone()),
                ),
                (
                    "previous_label_code",
                    Value::from(self.previous.state.label_code.clone()),
                ),
                ("new_state", Value::from(self.new.state.state.clone())),
                (
                    "new_label_code",
                    Value::from(self.new.state.label_code.clone()),
                ),
                ("movement", Value::from(self.movement.clone())),
                ("changed_at", Value::from(self.changed_at)),
                ("cause", Value::from(self.new.cause.clone())),
                ("previous_run_id", Value::from(self.previous.run_id)),
                ("new_run_id", Value::from(self.new.run_id)),
                (
                    "previous_model_version_id",
                    Value::from(self.previous.model_version_id),
                ),
                (
                    "new_model_version_id",
                    Value::from(self.new.model_version_id),
                ),
                ("methodological", Value::from(self.methodological)),
                (
                    "responsible_variables",
                    Value::List(
                        self.responsible_variables
                            .iter()
                            .cloned()
                            .map(Value::from)
                            .collect(),
                    ),
                ),
                ("rank_only", Value::from(self.rank_only)),
            ],
        )
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct MigrationTotal {
    pub state: MigrationState,
    pub before_count: i64,
    pub after_count: i64,
    pub entries: i64,
    pub exits: i64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct MigrationCell {
    pub previous: MigrationState,
    pub new: MigrationState,
    pub movement: String,
    pub customer_count: i64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct MigrationMatrix {
    pub window: MigrationWindow,
    pub from_model_version_id: Option<i64>,
    pub to_model_version_id: Option<i64>,
    pub methodological: bool,
    pub customer_count: i64,
    pub totals: Vec<MigrationTotal>,
    pub cells: Vec<MigrationCell>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct MigrationMeasure {
    pub variable: String,
    pub unit: String,
    pub previous_value: Option<String>,
    pub new_value: Option<String>,
    pub judgement: String,
    pub not_compared_reason: Option<String>,
    pub rank_changed: bool,
    pub statement: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct MigrationExplanation {
    pub window: MigrationWindow,
    pub migration: Migration,
    pub measures: Vec<MigrationMeasure>,
}

impl Assignment {
    fn from_row(row: &Row) -> Result<Self, ApiError> {
        Ok(Self {
            customer_id: row.try_get(0)?,
            run_id: row.try_get(1)?,
            label_code: row.try_get(2)?,
            r_score: row.try_get(3)?,
            f_score: row.try_get(4)?,
            m_score: row.try_get(5)?,
            valid_from: row.try_get(6)?,
            valid_to: row.try_get(7)?,
        })
    }

    fn wire(&self) -> Representation {
        Representation::new(
            "SegmentAssignment",
            [
                ("customer_id", Value::from(self.customer_id.to_string())),
                ("run_id", Value::from(self.run_id)),
                ("label_code", Value::from(self.label_code.clone())),
                ("r_score", Value::from(self.r_score)),
                ("f_score", Value::from(self.f_score)),
                ("m_score", Value::from(self.m_score)),
                ("valid_from", Value::from(self.valid_from)),
                ("valid_to", Value::from(self.valid_to)),
            ],
        )
    }
}

// ---------------------------------------------------------------------------
// Errors and query parsing
// ---------------------------------------------------------------------------

fn segment_not_found() -> ApiError {
    ApiError::new(404, "segment_not_found", "The segment does not exist.")
}

fn customer_not_found() -> ApiError {
    ApiError::new(404, "customer_not_found", "The customer does not exist.")
}

fn invalid_query(details: Vec<Detail>) -> ApiError {
    ApiError::new(
        422,
        "invalid_query",
        "A query parameter is outside its allowed values.",
    )
    .with_details(details)
}

fn parse_customer_id(raw: &str) -> Option<Uuid> {
    if raw.len() != 36 {
        return None;
    }
    Uuid::parse_str(raw).ok()
}

fn query_pairs(query: &str) -> HashMap<String, String> {
    let mut values = HashMap::new();
    for (key, value) in form_urlencoded::parse(query.as_bytes()) {
        values
            .entry(key.into_owned())
            .or_insert_with(|| value.into_owned());
    }
    values
}

/// `customer_id` of `getCurrentAssignment` and `getFloorCurrentAssignment`:
/// required, a UUID.
pub fn current_customer(query: &str) -> Result<Uuid, ApiError> {
    match query_pairs(query).get("customer_id") {
        None => Err(invalid_query(vec![Detail::new("customer_id", "required")])),
        Some(raw) => parse_customer_id(raw)
            .ok_or_else(|| invalid_query(vec![Detail::new("customer_id", "invalid_type")])),
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum Filter {
    Customer { id: Uuid, open: bool },
    Label { code: String, open: bool },
}

/// The query of `listSegmentAssignments`: exactly one of `customer_id` and
/// `label_code`, and an optional boolean `open`.
pub fn assignment_filter(query: &str) -> Result<Filter, ApiError> {
    let values = query_pairs(query);
    let mut details = Vec::new();
    let open = match values.get("open").map(String::as_str) {
        None => false,
        Some("true") => true,
        Some("false") => false,
        Some(_) => {
            details.push(Detail::new("open", "invalid_type"));
            false
        }
    };
    let customer = values.get("customer_id");
    let label = values.get("label_code");
    match (customer, label) {
        (Some(_), Some(_)) => details.push(Detail::new("customer_id", "conflicting_filters")),
        (None, None) => details.push(Detail::new("customer_id", "required")),
        _ => {}
    }
    let mut filter = None;
    if let (Some(raw), None) = (customer, label) {
        match parse_customer_id(raw) {
            Some(id) => filter = Some(Filter::Customer { id, open }),
            None => details.push(Detail::new("customer_id", "invalid_type")),
        }
    }
    if let (None, Some(code)) = (customer, label) {
        if code.is_empty() || code.chars().count() > 40 {
            details.push(Detail::new("label_code", "out_of_range"));
        } else {
            filter = Some(Filter::Label {
                code: code.clone(),
                open,
            });
        }
    }
    match filter {
        Some(filter) if details.is_empty() => Ok(filter),
        _ => Err(invalid_query(details)),
    }
}

fn parse_instant(
    field: &str,
    value: Option<&String>,
    details: &mut Vec<Detail>,
) -> Option<DateTime<Utc>> {
    let Some(value) = value else {
        details.push(Detail::new(field, "required"));
        return None;
    };
    match DateTime::parse_from_rfc3339(value) {
        Ok(value) => Some(value.with_timezone(&Utc)),
        Err(_) => {
            details.push(Detail::new(field, "invalid_type"));
            None
        }
    }
}

pub fn migration_window(query: &str) -> Result<MigrationWindow, ApiError> {
    let values = query_pairs(query);
    let mut details = Vec::new();
    let from = parse_instant("from", values.get("from"), &mut details);
    let to = parse_instant("to", values.get("to"), &mut details);
    if let (Some(from), Some(to)) = (from, to) {
        if from >= to {
            details.push(Detail::new("from", "invalid_range"));
        }
        let now = Utc::now();
        if from > now {
            details.push(Detail::new("from", "future"));
        }
        if to > now {
            details.push(Detail::new("to", "future"));
        }
        if details.is_empty() {
            return Ok(MigrationWindow { from, to });
        }
    }
    Err(invalid_query(details))
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct MigrationFilter {
    pub customer_id: Option<Uuid>,
    pub previous_state: Option<String>,
    pub previous_label_code: Option<String>,
    pub new_state: Option<String>,
    pub new_label_code: Option<String>,
    pub direction: Option<String>,
    pub label_code: Option<String>,
    pub methodological: Option<bool>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct MigrationQuery {
    pub window: MigrationWindow,
    pub filter: MigrationFilter,
}

fn parse_state(field: &str, value: Option<&String>, details: &mut Vec<Detail>) -> Option<String> {
    match value.map(String::as_str) {
        None => None,
        Some("label" | "unassigned" | "new") => value.cloned(),
        Some(_) => {
            details.push(Detail::new(field, "invalid_value"));
            None
        }
    }
}

pub fn migration_query(query: &str) -> Result<MigrationQuery, ApiError> {
    let window = migration_window(query)?;
    let values = query_pairs(query);
    let mut details = Vec::new();
    let customer_id = values
        .get("customer_id")
        .and_then(|raw| match parse_customer_id(raw) {
            Some(id) => Some(id),
            None => {
                details.push(Detail::new("customer_id", "invalid_type"));
                None
            }
        });
    let previous_state = parse_state("previous_state", values.get("previous_state"), &mut details);
    let new_state = parse_state("new_state", values.get("new_state"), &mut details);
    let previous_label_code = values.get("previous_label_code").cloned();
    let new_label_code = values.get("new_label_code").cloned();
    let direction = match values.get("direction").map(String::as_str) {
        None => None,
        Some("entries" | "exits") => values.get("direction").cloned(),
        Some(_) => {
            details.push(Detail::new("direction", "invalid_value"));
            None
        }
    };
    let label_code = values.get("label_code").cloned();
    let methodological = match values.get("methodological").map(String::as_str) {
        None => None,
        Some("true") => Some(true),
        Some("false") => Some(false),
        Some(_) => {
            details.push(Detail::new("methodological", "invalid_type"));
            None
        }
    };
    let cell = previous_state.is_some()
        || previous_label_code.is_some()
        || new_state.is_some()
        || new_label_code.is_some();
    if cell && (previous_state.is_none() || new_state.is_none()) {
        details.push(Detail::new("previous_state", "incomplete_cell"));
    }
    for (field, state, label) in [
        (
            "previous_label_code",
            previous_state.as_deref(),
            previous_label_code.as_ref(),
        ),
        (
            "new_label_code",
            new_state.as_deref(),
            new_label_code.as_ref(),
        ),
    ] {
        if (state == Some("label")) != label.is_some() {
            details.push(Detail::new(field, "inconsistent_state"));
        }
    }
    if direction.is_some() && label_code.is_none() {
        details.push(Detail::new("label_code", "required"));
    }
    if direction.is_some() && cell {
        details.push(Detail::new("direction", "conflicting_filters"));
    }
    for (field, label) in [
        ("previous_label_code", previous_label_code.as_ref()),
        ("new_label_code", new_label_code.as_ref()),
        ("label_code", label_code.as_ref()),
    ] {
        if label.is_some_and(|value| value.is_empty() || value.chars().count() > 40) {
            details.push(Detail::new(field, "out_of_range"));
        }
    }
    if !details.is_empty() {
        return Err(invalid_query(details));
    }
    Ok(MigrationQuery {
        window,
        filter: MigrationFilter {
            customer_id,
            previous_state,
            previous_label_code,
            new_state,
            new_label_code,
            direction,
            label_code,
            methodological,
        },
    })
}

pub fn explanation_query(query: &str) -> Result<(Uuid, MigrationWindow), ApiError> {
    let customer = current_customer(query)?;
    Ok((customer, migration_window(query)?))
}

// ---------------------------------------------------------------------------
// The cache
// ---------------------------------------------------------------------------

#[derive(Clone)]
pub struct Cache {
    client: redis::Client,
}

impl Cache {
    pub fn new(client: redis::Client) -> Self {
        Self { client }
    }

    async fn connection(&self) -> redis::RedisResult<MultiplexedConnection> {
        let config = redis::AsyncConnectionConfig::new()
            .set_connection_timeout(Duration::from_millis(200))
            .set_response_timeout(Duration::from_millis(200));
        self.client
            .get_multiplexed_async_connection_with_config(&config)
            .await
    }

    /// A hit, or None for a miss and for every Redis failure.
    pub async fn get<T: DeserializeOwned>(&self, key: &str) -> Option<T> {
        let mut connection = self.connection().await.ok()?;
        let text: Option<String> = connection.get(key).await.ok()?;
        text.and_then(|value| serde_json::from_str(&value).ok())
    }

    /// Store with a TTL; a failure only costs the next read a miss.
    pub async fn put<T: Serialize>(&self, key: &str, value: &T) {
        let Ok(text) = serde_json::to_string(value) else {
            return;
        };
        if let Ok(mut connection) = self.connection().await {
            let _: redis::RedisResult<()> = connection.set_ex(key, text, CACHE_SECONDS).await;
        }
    }
}

pub fn list_key(run: i64) -> String {
    format!("segments:cache:{run}:list")
}

pub fn current_key(run: i64, customer: &str) -> String {
    format!("segments:cache:{run}:current:{customer}")
}

/// Reads through the cache. The run tracker is the run whose keys are asked
/// for; PostgreSQL sets it, the consumer moves it, and a miss that reads a
/// newer run advances it.
#[derive(Clone)]
pub struct Reader {
    cache: Option<Cache>,
    run: Arc<AtomicI64>,
}

impl Reader {
    pub fn new(cache: Option<Cache>) -> Self {
        Self {
            cache,
            run: Arc::new(AtomicI64::new(RUN_UNSET)),
        }
    }

    /// The run whose keys are read now; 0 before any run, None before asked.
    pub fn tracked_run(&self) -> Option<i64> {
        match self.run.load(Ordering::SeqCst) {
            RUN_UNSET => None,
            run => Some(run),
        }
    }

    /// Move reads to the latest run in PostgreSQL. Idempotent, so a repeated
    /// event does no harm.
    pub async fn refresh_run(&self, db: &Database) -> Result<i64, ApiError> {
        let connection = db.connection().await?;
        let row = connection
            .query_one(
                "SELECT COALESCE(max(run_id), $1::bigint) FROM public.segmentation_run",
                &[&NO_RUN],
            )
            .await?;
        let run: i64 = row.try_get(0)?;
        self.run.store(run, Ordering::SeqCst);
        Ok(run)
    }

    async fn run(&self, db: &Database) -> Result<i64, ApiError> {
        match self.tracked_run() {
            Some(run) => Ok(run),
            None => self.refresh_run(db).await,
        }
    }

    fn advance(&self, run: i64) {
        let _ = self
            .run
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |held| {
                (held == RUN_UNSET || run > held).then_some(run)
            });
    }

    pub async fn list(&self, db: &Database) -> Result<SegmentList, ApiError> {
        let Some(cache) = &self.cache else {
            return read_list(db).await;
        };
        let run = self.run(db).await?;
        if let Some(hit) = cache.get::<SegmentList>(&list_key(run)).await {
            return Ok(hit);
        }
        let fresh = read_list(db).await?;
        let actual = fresh.run_id.unwrap_or(NO_RUN);
        self.advance(actual);
        cache.put(&list_key(actual), &fresh).await;
        Ok(fresh)
    }

    pub async fn segment(&self, db: &Database, label_code: &str) -> Result<SegmentItem, ApiError> {
        self.list(db)
            .await?
            .segments
            .into_iter()
            .find(|segment| segment.label_code == label_code)
            .ok_or_else(segment_not_found)
    }

    pub async fn current(
        &self,
        db: &Database,
        customer: Uuid,
    ) -> Result<CurrentAssignment, ApiError> {
        let id = customer.to_string();
        let Some(cache) = &self.cache else {
            return read_current(db, customer).await;
        };
        let run = self.run(db).await?;
        let key = current_key(run, &id);
        if let Some(hit) = cache.get::<CurrentAssignment>(&key).await {
            return Ok(hit);
        }
        let fresh = read_current(db, customer).await?;
        cache.put(&key, &fresh).await;
        Ok(fresh)
    }
}

fn reader() -> &'static Reader {
    READER.get_or_init(|| Reader::new(None))
}

// ---------------------------------------------------------------------------
// PostgreSQL
// ---------------------------------------------------------------------------

/// The labels with their sizes in the latest run, in one statement so the run
/// and its counts come from one snapshot.
pub async fn read_list(db: &Database) -> Result<SegmentList, ApiError> {
    let connection = db.connection().await?;
    let rows = connection
        .query(
            "WITH latest AS ( \
                 SELECT run_id, run_at FROM public.segmentation_run \
                 ORDER BY run_id DESC LIMIT 1) \
             SELECT l.label_code, l.name, l.description, l.ordinal_position, \
                    (SELECT count(*) FROM public.customer_segment_history AS h \
                      WHERE h.run_id = (SELECT run_id FROM latest) \
                        AND h.label_code = l.label_code) AS size, \
                    (SELECT run_id FROM latest), \
                    (SELECT run_at FROM latest), \
                    (SELECT count(*) FROM public.customer_segment_history AS h \
                      WHERE h.run_id = (SELECT run_id FROM latest) \
                        AND h.label_code IS NULL) AS unassigned \
             FROM public.segment_label AS l \
             ORDER BY l.ordinal_position",
            &[],
        )
        .await?;
    let mut list = SegmentList {
        run_id: None,
        run_at: None,
        unassigned_count: 0,
        segments: Vec::with_capacity(rows.len()),
    };
    for row in &rows {
        list.run_id = row.try_get(5)?;
        list.run_at = row.try_get(6)?;
        list.unassigned_count = row.try_get(7)?;
        list.segments.push(SegmentItem {
            label_code: row.try_get(0)?,
            name: row.try_get(1)?,
            description: row.try_get(2)?,
            ordinal_position: row.try_get(3)?,
            size: row.try_get(4)?,
        });
    }
    Ok(list)
}

/// The customer's state from the contract view, and the scores from the open
/// history row. A customer absent from the view is 404.
pub async fn read_current(db: &Database, customer: Uuid) -> Result<CurrentAssignment, ApiError> {
    let connection = db.connection().await?;
    let row = connection
        .query_opt(
            "SELECT v.state, v.label_code, v.since, v.run_id, \
                    h.r_score, h.f_score, h.m_score \
             FROM api_segments.current_label_v1 AS v \
             LEFT JOIN public.customer_segment_history AS h \
                    ON h.customer_id = v.customer_id AND h.valid_to IS NULL \
             WHERE v.customer_id = $1 \
             LIMIT 1",
            &[&customer],
        )
        .await?
        .ok_or_else(customer_not_found)?;
    let state: String = row.try_get(0)?;
    let assigned = state == "assigned";
    let score = |index: usize| -> Result<Option<i16>, ApiError> {
        if assigned {
            Ok(row.try_get(index)?)
        } else {
            Ok(None)
        }
    };
    Ok(CurrentAssignment {
        customer_id: customer.to_string(),
        r_score: score(4)?,
        f_score: score(5)?,
        m_score: score(6)?,
        label_code: row.try_get(1)?,
        since: row.try_get(2)?,
        run_id: row.try_get(3)?,
        state,
    })
}

/// One page of history: a customer's rows newest first, or a label's rows.
/// Returns the page and the total.
pub async fn read_assignments(
    db: &Database,
    filter: &Filter,
    page: u32,
    size: u32,
) -> Result<(Vec<Assignment>, u64), ApiError> {
    let connection = db.connection().await?;
    let client: &tokio_postgres::Client = &connection;
    read_assignments_in(client, filter, page, size).await
}

/// [`read_assignments`] on a given client, such as a logged read's transaction.
pub async fn read_assignments_in<C: GenericClient + Sync>(
    connection: &C,
    filter: &Filter,
    page: u32,
    size: u32,
) -> Result<(Vec<Assignment>, u64), ApiError> {
    let limit = i64::from(size);
    let offset = i64::from(page - 1) * limit;
    let (total, rows) = match filter {
        Filter::Customer { id, open } => {
            if connection
                .query_opt(
                    "SELECT 1 FROM api_customers.customer_ref_v1 WHERE customer_id = $1",
                    &[id],
                )
                .await?
                .is_none()
            {
                return Err(customer_not_found());
            }
            let total: i64 = connection
                .query_one(
                    "SELECT count(*) FROM public.customer_segment_history \
                     WHERE customer_id = $1 AND (NOT $2::boolean OR valid_to IS NULL)",
                    &[id, open],
                )
                .await?
                .try_get(0)?;
            let rows = connection
                .query(
                    "SELECT customer_id, run_id, label_code, r_score, f_score, m_score, \
                            valid_from, valid_to \
                     FROM public.customer_segment_history \
                     WHERE customer_id = $1 AND (NOT $2::boolean OR valid_to IS NULL) \
                     ORDER BY valid_from DESC, history_id DESC \
                     LIMIT $3 OFFSET $4",
                    &[id, open, &limit, &offset],
                )
                .await?;
            (total, rows)
        }
        Filter::Label { code, open } => {
            if connection
                .query_opt(
                    "SELECT 1 FROM public.segment_label WHERE label_code = $1",
                    &[code],
                )
                .await?
                .is_none()
            {
                return Err(segment_not_found());
            }
            let total: i64 = connection
                .query_one(
                    "SELECT count(*) FROM public.customer_segment_history \
                     WHERE label_code = $1 AND (NOT $2::boolean OR valid_to IS NULL)",
                    &[code, open],
                )
                .await?
                .try_get(0)?;
            let rows = connection
                .query(
                    "SELECT customer_id, run_id, label_code, r_score, f_score, m_score, \
                            valid_from, valid_to \
                     FROM public.customer_segment_history \
                     WHERE label_code = $1 AND (NOT $2::boolean OR valid_to IS NULL) \
                     ORDER BY customer_id, valid_from DESC, history_id DESC \
                     LIMIT $3 OFFSET $4",
                    &[code, open, &limit, &offset],
                )
                .await?;
            (total, rows)
        }
    };
    let assignments = rows
        .iter()
        .map(Assignment::from_row)
        .collect::<Result<Vec<_>, _>>()?;
    Ok((assignments, total as u64))
}

fn snapshot_from_row(row: &Row, offset: usize, customer_id: Uuid) -> Result<Snapshot, ApiError> {
    let run_id: Option<i64> = row.try_get(offset)?;
    let label_code: Option<String> = row.try_get(offset + 1)?;
    Ok(Snapshot {
        customer_id,
        state: MigrationState::new(label_code, run_id.is_some()),
        run_id,
        model_version_id: row.try_get(offset + 2)?,
        run_at: row.try_get(offset + 3)?,
        valid_from: row.try_get(offset + 4)?,
        cause: row.try_get(offset + 5)?,
        recency_last_purchase_at: row.try_get(offset + 6)?,
        frequency_count: row.try_get(offset + 7)?,
        monetary_total: row.try_get(offset + 8)?,
        r_score: row.try_get(offset + 9)?,
        f_score: row.try_get(offset + 10)?,
        m_score: row.try_get(offset + 11)?,
    })
}

fn recency_value(snapshot: &Snapshot) -> Option<i64> {
    Some(
        snapshot
            .run_at?
            .signed_duration_since(snapshot.recency_last_purchase_at?)
            .num_days(),
    )
}

fn money_cents(value: Option<&String>) -> Option<i64> {
    let value = value?;
    let (whole, fraction) = value.split_once('.').unwrap_or((value, "0"));
    let fraction = format!("{fraction:0<2}");
    Some(whole.parse::<i64>().ok()? * 100 + fraction[..2].parse::<i64>().ok()?)
}

pub fn recency_judgement(previous: i64, new: i64) -> &'static str {
    if (new - previous).abs() <= RECENCY_STABLE_DAYS {
        "stable"
    } else {
        "changed"
    }
}

pub fn frequency_judgement(previous: i32, new: i32) -> &'static str {
    if previous == new {
        "stable"
    } else {
        "changed"
    }
}

pub fn monetary_judgement(previous: &str, new: &str) -> &'static str {
    let (Some(previous), Some(new)) = (
        money_cents(Some(&previous.to_owned())),
        money_cents(Some(&new.to_owned())),
    ) else {
        return "changed";
    };
    let stable = if previous == 0 {
        new == 0
    } else {
        (new - previous).abs() * 100 <= previous * MONETARY_STABLE_PERCENT
    };
    if stable {
        "stable"
    } else {
        "changed"
    }
}

fn measure_changes(previous: &Snapshot, new: &Snapshot) -> Vec<String> {
    let mut changed = Vec::new();
    if matches!((recency_value(previous), recency_value(new)), (Some(a), Some(b)) if recency_judgement(a, b) == "changed")
    {
        changed.push("recency".to_owned());
    }
    if matches!((previous.frequency_count, new.frequency_count), (Some(a), Some(b)) if frequency_judgement(a, b) == "changed")
    {
        changed.push("frequency".to_owned());
    }
    if let (Some(a), Some(b)) = (
        money_cents(previous.monetary_total.as_ref()),
        money_cents(new.monetary_total.as_ref()),
    ) {
        let previous_text = format!("{}.{:02}", a / 100, a % 100);
        let new_text = format!("{}.{:02}", b / 100, b % 100);
        if monetary_judgement(&previous_text, &new_text) == "changed" {
            changed.push("monetary".to_owned());
        }
    }
    changed
}

pub fn migration_movement(
    previous: &MigrationState,
    new: &MigrationState,
    ordinals: &HashMap<String, i16>,
) -> String {
    let (Some(before), Some(after)) = (&previous.label_code, &new.label_code) else {
        return "not_ranked".to_owned();
    };
    if before == after {
        return "unchanged".to_owned();
    }
    match (ordinals.get(before), ordinals.get(after)) {
        (Some(before), Some(after)) if after < before => "improved".to_owned(),
        _ => "declined".to_owned(),
    }
}

fn build_migration(
    previous: Snapshot,
    new: Snapshot,
    window: &MigrationWindow,
    ordinals: &HashMap<String, i16>,
) -> Migration {
    let responsible_variables = measure_changes(&previous, &new);
    let methodological = previous.model_version_id.is_none()
        || new.model_version_id.is_none()
        || previous.model_version_id != new.model_version_id
        || new.cause.as_deref() == Some("VERSION_CHANGE");
    let label_changed = previous.state.label_code.is_some()
        && new.state.label_code.is_some()
        && previous.state.label_code != new.state.label_code;
    Migration {
        customer_id: new.customer_id,
        movement: migration_movement(&previous.state, &new.state, ordinals),
        changed_at: new.valid_from.filter(|instant| *instant > window.from),
        rank_only: label_changed && responsible_variables.is_empty(),
        previous,
        new,
        methodological,
        responsible_variables,
    }
}

async fn label_ordinals<C: GenericClient + Sync>(
    connection: &C,
) -> Result<HashMap<String, i16>, ApiError> {
    connection
        .query(
            "SELECT label_code, ordinal_position FROM public.segment_label",
            &[],
        )
        .await?
        .iter()
        .map(|row| Ok((row.try_get(0)?, row.try_get(1)?)))
        .collect::<Result<_, ApiError>>()
}

async fn validate_migration_filter<C: GenericClient + Sync>(
    connection: &C,
    filter: &MigrationFilter,
) -> Result<(), ApiError> {
    if let Some(customer) = filter.customer_id {
        if connection
            .query_opt(
                "SELECT 1 FROM api_customers.customer_ref_v1 WHERE customer_id = $1",
                &[&customer],
            )
            .await?
            .is_none()
        {
            return Err(customer_not_found());
        }
    }
    for label in [
        filter.previous_label_code.as_ref(),
        filter.new_label_code.as_ref(),
        filter.label_code.as_ref(),
    ]
    .into_iter()
    .flatten()
    {
        if connection
            .query_opt(
                "SELECT 1 FROM public.segment_label WHERE label_code = $1",
                &[label],
            )
            .await?
            .is_none()
        {
            return Err(segment_not_found());
        }
    }
    Ok(())
}

pub async fn read_migrations(
    db: &Database,
    query: &MigrationQuery,
) -> Result<Vec<Migration>, ApiError> {
    let connection = db.connection().await?;
    let client: &tokio_postgres::Client = &connection;
    validate_migration_filter(client, &query.filter).await?;
    // `cause` is NULL for now: nothing records why a history row exists on this schema yet
    // (ADR-0063's cause column arrives with rfm-engine's apply_assignment), and the contract
    // reports NULL for a row written before causes were recorded. A `VERSION_CHANGE` cause
    // is already honoured by `build_migration` once a row carries one.
    let rows = connection
        .query(
            "WITH ids AS ( \
                 SELECT customer_id FROM api_customers.customer_ref_v1) \
             SELECT ids.customer_id, \
                    b.run_id, b.label_code, bv.version_id, br.run_at, b.valid_from, \
                    NULL::text, b.recency_last_purchase_at, \
                    b.frequency_count, b.monetary_total::text, b.r_score, b.f_score, b.m_score, \
                    a.run_id, a.label_code, av.version_id, ar.run_at, a.valid_from, \
                    NULL::text, a.recency_last_purchase_at, \
                    a.frequency_count, a.monetary_total::text, a.r_score, a.f_score, a.m_score \
               FROM ids \
               LEFT JOIN public.customer_segment_history AS b \
                 ON b.customer_id = ids.customer_id AND b.valid_from <= $1 \
                AND (b.valid_to IS NULL OR b.valid_to > $1) \
               LEFT JOIN public.segmentation_run AS br ON br.run_id = b.run_id \
               LEFT JOIN api_models.run_version_v1 AS bv ON bv.run_id = b.run_id \
               LEFT JOIN public.customer_segment_history AS a \
                 ON a.customer_id = ids.customer_id AND a.valid_from <= $2 \
                AND (a.valid_to IS NULL OR a.valid_to > $2) \
               LEFT JOIN public.segmentation_run AS ar ON ar.run_id = a.run_id \
               LEFT JOIN api_models.run_version_v1 AS av ON av.run_id = a.run_id \
              ORDER BY ids.customer_id",
            &[&query.window.from, &query.window.to],
        )
        .await?;
    let ordinals = label_ordinals(client).await?;
    let mut migrations = Vec::with_capacity(rows.len());
    for row in &rows {
        let customer_id: Uuid = row.try_get(0)?;
        let previous = snapshot_from_row(row, 1, customer_id)?;
        let new = snapshot_from_row(row, 13, customer_id)?;
        migrations.push(build_migration(previous, new, &query.window, &ordinals));
    }
    migrations.retain(|migration| migration_matches(migration, &query.filter));
    migrations.sort_by(|a, b| {
        b.changed_at
            .cmp(&a.changed_at)
            .then_with(|| a.customer_id.cmp(&b.customer_id))
    });
    Ok(migrations)
}

fn migration_matches(migration: &Migration, filter: &MigrationFilter) -> bool {
    if filter
        .customer_id
        .is_some_and(|id| id != migration.customer_id)
        || filter
            .previous_state
            .as_ref()
            .is_some_and(|state| state != &migration.previous.state.state)
        || filter
            .previous_label_code
            .as_ref()
            .is_some_and(|label| Some(label) != migration.previous.state.label_code.as_ref())
        || filter
            .new_state
            .as_ref()
            .is_some_and(|state| state != &migration.new.state.state)
        || filter
            .new_label_code
            .as_ref()
            .is_some_and(|label| Some(label) != migration.new.state.label_code.as_ref())
        || filter
            .methodological
            .is_some_and(|value| value != migration.methodological)
    {
        return false;
    }
    match (filter.direction.as_deref(), filter.label_code.as_ref()) {
        (Some("entries"), Some(label)) => {
            migration.new.state.label_code.as_ref() == Some(label)
                && migration.previous.state.label_code.as_ref() != Some(label)
        }
        (Some("exits"), Some(label)) => {
            migration.previous.state.label_code.as_ref() == Some(label)
                && migration.new.state.label_code.as_ref() != Some(label)
        }
        (None, Some(label)) => {
            migration.previous.state.label_code.as_ref() == Some(label)
                || migration.new.state.label_code.as_ref() == Some(label)
        }
        _ => true,
    }
}

async fn version_at<C: GenericClient + Sync>(
    connection: &C,
    instant: DateTime<Utc>,
) -> Result<Option<i64>, ApiError> {
    Ok(connection
        .query_opt(
            "SELECT v.version_id \
               FROM public.segmentation_run AS r \
               JOIN api_models.run_version_v1 AS v ON v.run_id = r.run_id \
              WHERE r.run_at <= $1 \
              ORDER BY r.run_at DESC, r.run_id DESC LIMIT 1",
            &[&instant],
        )
        .await?
        .map(|row| row.get(0)))
}

fn state_order(state: &MigrationState, ordinals: &HashMap<String, i16>) -> (i16, String) {
    match (&*state.state, state.label_code.as_ref()) {
        ("label", Some(label)) => (*ordinals.get(label).unwrap_or(&i16::MAX), label.clone()),
        ("unassigned", _) => (i16::MAX - 1, String::new()),
        _ => (i16::MAX, String::new()),
    }
}

/// The matrix's totals and cells from the customers' migrations (RN-59: every customer is in
/// exactly one cell, so the cells and the totals reconcile). Pure, so the web's matrix pins it.
pub fn tally(
    migrations: &[Migration],
    ordinals: &HashMap<String, i16>,
) -> (Vec<MigrationTotal>, Vec<MigrationCell>) {
    let mut totals: BTreeMap<MigrationState, (i64, i64, i64, i64)> = BTreeMap::new();
    let mut cells: BTreeMap<(MigrationState, MigrationState, String), i64> = BTreeMap::new();
    for migration in migrations {
        totals
            .entry(migration.previous.state.clone())
            .or_default()
            .0 += 1;
        totals.entry(migration.new.state.clone()).or_default().1 += 1;
        if migration.previous.state != migration.new.state {
            totals.entry(migration.new.state.clone()).or_default().2 += 1;
            totals
                .entry(migration.previous.state.clone())
                .or_default()
                .3 += 1;
        }
        *cells
            .entry((
                migration.previous.state.clone(),
                migration.new.state.clone(),
                migration.movement.clone(),
            ))
            .or_default() += 1;
    }
    let mut totals: Vec<_> = totals
        .into_iter()
        .map(
            |(state, (before_count, after_count, entries, exits))| MigrationTotal {
                state,
                before_count,
                after_count,
                entries,
                exits,
            },
        )
        .collect();
    totals.sort_by_key(|total| state_order(&total.state, ordinals));
    let mut cells: Vec<_> = cells
        .into_iter()
        .map(
            |((previous, new, movement), customer_count)| MigrationCell {
                previous,
                new,
                movement,
                customer_count,
            },
        )
        .collect();
    cells.sort_by_key(|cell| {
        (
            state_order(&cell.previous, ordinals),
            state_order(&cell.new, ordinals),
        )
    });
    (totals, cells)
}

pub async fn read_migration_matrix(
    db: &Database,
    window: MigrationWindow,
) -> Result<MigrationMatrix, ApiError> {
    let query = MigrationQuery {
        window: window.clone(),
        filter: MigrationFilter::default(),
    };
    let migrations = read_migrations(db, &query).await?;
    let connection = db.connection().await?;
    let client: &tokio_postgres::Client = &connection;
    let ordinals = label_ordinals(client).await?;
    let from_model_version_id = version_at(client, window.from).await?;
    let to_model_version_id = version_at(client, window.to).await?;
    let methodological = from_model_version_id.is_none()
        || to_model_version_id.is_none()
        || from_model_version_id != to_model_version_id;
    let (totals, cells) = tally(&migrations, &ordinals);
    Ok(MigrationMatrix {
        window,
        from_model_version_id,
        to_model_version_id,
        methodological,
        customer_count: migrations.len() as i64,
        totals,
        cells,
    })
}

fn measure_reason(previous: &Snapshot, new: &Snapshot) -> Option<String> {
    if previous.run_id.is_none() {
        Some("new_customer".to_owned())
    } else if previous.state.state == "unassigned" || new.state.state == "unassigned" {
        Some("unassigned".to_owned())
    } else {
        None
    }
}

fn score_changed(previous: Option<i16>, new: Option<i16>) -> bool {
    matches!((previous, new), (Some(a), Some(b)) if a != b)
}

fn recency_measure(previous: &Snapshot, new: &Snapshot) -> MigrationMeasure {
    let before = recency_value(previous);
    let after = recency_value(new);
    let reason = measure_reason(previous, new).filter(|_| before.is_none() || after.is_none());
    let (judgement, rank_changed, statement) = match (before, after, reason.as_deref()) {
        (_, _, Some("new_customer")) => (
            "not_compared",
            false,
            "Recency is not compared: the customer was new at the earlier instant.".to_owned(),
        ),
        (_, _, Some(_)) | (None, _, _) | (_, None, _) => (
            "not_compared",
            false,
            "Recency is not compared: an assignment was unassigned.".to_owned(),
        ),
        (Some(a), Some(b), None) => {
            let stable = recency_judgement(a, b) == "stable";
            let rank = stable && score_changed(previous.r_score, new.r_score);
            let text = if a == b {
                format!("Recency is stable at {b} days.")
            } else if stable {
                let suffix = if rank {
                    ", but its score moved because the population or model changed"
                } else {
                    ""
                };
                format!("Recency is stable: {a} days against {b}, within the 7-day drift{suffix}.")
            } else {
                format!("Recency changed: the last purchase was {a} days before the earlier assignment and {b} days before the later one.")
            };
            (if stable { "stable" } else { "changed" }, rank, text)
        }
    };
    MigrationMeasure {
        variable: "recency".to_owned(),
        unit: "days".to_owned(),
        previous_value: before.map(|value| value.to_string()),
        new_value: after.map(|value| value.to_string()),
        judgement: judgement.to_owned(),
        not_compared_reason: reason,
        rank_changed,
        statement,
    }
}

fn frequency_measure(previous: &Snapshot, new: &Snapshot) -> MigrationMeasure {
    let before = previous.frequency_count;
    let after = new.frequency_count;
    let reason = measure_reason(previous, new).filter(|_| before.is_none() || after.is_none());
    let (judgement, rank_changed, statement) = match (before, after, reason.as_deref()) {
        (_, _, Some("new_customer")) => (
            "not_compared",
            false,
            "Frequency is not compared: the customer was new at the earlier instant.".to_owned(),
        ),
        (_, _, Some(_)) | (None, _, _) | (_, None, _) => (
            "not_compared",
            false,
            "Frequency is not compared: an assignment was unassigned.".to_owned(),
        ),
        (Some(a), Some(b), None) => {
            let stable = frequency_judgement(a, b) == "stable";
            let rank = stable && score_changed(previous.f_score, new.f_score);
            let text = if stable {
                format!("Frequency is stable at {b} purchases.")
            } else {
                format!("Frequency changed from {a} purchases to {b} purchases.")
            };
            (if stable { "stable" } else { "changed" }, rank, text)
        }
    };
    MigrationMeasure {
        variable: "frequency".to_owned(),
        unit: "purchases".to_owned(),
        previous_value: before.map(|value| value.to_string()),
        new_value: after.map(|value| value.to_string()),
        judgement: judgement.to_owned(),
        not_compared_reason: reason,
        rank_changed,
        statement,
    }
}

fn monetary_measure(previous: &Snapshot, new: &Snapshot) -> MigrationMeasure {
    let before = previous.monetary_total.clone();
    let after = new.monetary_total.clone();
    let reason = measure_reason(previous, new).filter(|_| before.is_none() || after.is_none());
    let a = money_cents(before.as_ref());
    let b = money_cents(after.as_ref());
    let (judgement, rank_changed, statement) = match (a, b, reason.as_deref()) {
        (_, _, Some("new_customer")) => (
            "not_compared",
            false,
            "Monetary is not compared: the customer was new at the earlier instant.".to_owned(),
        ),
        (_, _, Some(_)) | (None, _, _) | (_, None, _) => (
            "not_compared",
            false,
            "Monetary is not compared: an assignment was unassigned.".to_owned(),
        ),
        (Some(a), Some(b), None) => {
            let stable = monetary_judgement(
                before.as_deref().unwrap_or("0.00"),
                after.as_deref().unwrap_or("0.00"),
            ) == "stable";
            let rank = stable && score_changed(previous.m_score, new.m_score);
            let left = before.as_deref().unwrap_or("0.00");
            let right = after.as_deref().unwrap_or("0.00");
            let text = if a == b {
                format!("Monetary is stable at {right} MXN.")
            } else if stable {
                format!("Monetary is stable from {left} MXN to {right} MXN, within 10% of the earlier value.")
            } else {
                format!("Monetary changed from {left} MXN to {right} MXN, more than 10% of the earlier value.")
            };
            (if stable { "stable" } else { "changed" }, rank, text)
        }
    };
    MigrationMeasure {
        variable: "monetary".to_owned(),
        unit: "MXN".to_owned(),
        previous_value: before,
        new_value: after,
        judgement: judgement.to_owned(),
        not_compared_reason: reason,
        rank_changed,
        statement,
    }
}

pub async fn read_migration_explanation(
    db: &Database,
    customer_id: Uuid,
    window: MigrationWindow,
) -> Result<MigrationExplanation, ApiError> {
    let query = MigrationQuery {
        window: window.clone(),
        filter: MigrationFilter {
            customer_id: Some(customer_id),
            ..MigrationFilter::default()
        },
    };
    let migration = read_migrations(db, &query)
        .await?
        .into_iter()
        .next()
        .ok_or_else(customer_not_found)?;
    let measures = vec![
        recency_measure(&migration.previous, &migration.new),
        frequency_measure(&migration.previous, &migration.new),
        monetary_measure(&migration.previous, &migration.new),
    ];
    Ok(MigrationExplanation {
        window,
        migration,
        measures,
    })
}

// ---------------------------------------------------------------------------
// Logged reads (ADR-0072)
// ---------------------------------------------------------------------------

pub fn audit_unavailable() -> ApiError {
    ApiError::new(
        503,
        "audit_unavailable",
        "The read could not be recorded, so it was not served.",
    )
}

/// Record one `HISTORY_READ` of a customer's segment history, naming who,
/// which customer and when, never what was read. Any failure is 503
/// `audit_unavailable`.
pub async fn record_history_read<C: GenericClient + Sync>(
    client: &C,
    principal: &Principal,
    correlation_id: &str,
    customer: Uuid,
    outcome: &str,
) -> Result<(), ApiError> {
    let correlation =
        (!correlation_id.is_empty() && correlation_id != "-").then_some(correlation_id);
    client
        .query_one(
            "SELECT created FROM api_audit.record_event_v1( \
                 $1, 'HISTORY_READ', $2, now(), 'USER', $3::text::uuid, $4, \
                 NULL::smallint, 'customer', $5, NULL::bigint, NULL::bigint, $6, \
                 '{\"history\": \"SEGMENT\"}'::jsonb)",
            &[
                &Uuid::new_v4(),
                &outcome,
                &principal.sub,
                &principal.role,
                &customer.to_string(),
                &correlation,
            ],
        )
        .await
        .map(|_| ())
        .map_err(|error| {
            tracing::error!(
                "HISTORY_READ not recorded: sqlstate={:?}",
                error.code().map(|code| code.code().to_owned())
            );
            audit_unavailable()
        })
}

/// A customer's history, read in one transaction with its `HISTORY_READ`:
/// the event is recorded first, so no row leaves without it, and an unknown
/// customer's 404 rolls it back with the read.
pub async fn read_logged_history(
    db: &Database,
    principal: &Principal,
    correlation_id: &str,
    filter: &Filter,
    page: u32,
    size: u32,
) -> Result<(Vec<Assignment>, u64), ApiError> {
    let Filter::Customer { id, .. } = filter else {
        return read_assignments(db, filter, page, size).await;
    };
    let mut connection = db.connection().await?;
    let transaction = connection.transaction(Some(principal)).await?;
    record_history_read(&*transaction, principal, correlation_id, *id, "SUCCESS").await?;
    let result = read_assignments_in(&*transaction, filter, page, size).await?;
    transaction
        .commit()
        .await
        .map_err(|_| audit_unavailable())?;
    Ok(result)
}

/// What the refusal layer needs: the kit's own verification of the token,
/// and the database to record in.
pub struct RefusalLog {
    auth: Authenticator,
    db: Arc<Database>,
}

impl RefusalLog {
    pub fn new(config: &KitConfig, db: Arc<Database>) -> Result<Self, Box<dyn std::error::Error>> {
        let info = service_redis_info(
            &config.redis_state_url,
            SUFFIX,
            config.redis_password.clone(),
        )?;
        Ok(Self {
            auth: Authenticator::new(
                config.issuer.clone(),
                JwksStore::new(config.jwks_path.clone()),
                StateRedis::new(info)?,
            ),
            db,
        })
    }

    async fn record(
        &self,
        authorization: Option<&str>,
        correlation: &str,
        customer: Uuid,
    ) -> Result<(), ApiError> {
        // The kit answered 403 only after verifying this token; it is
        // verified again here, so the refusal is attributed to a verified
        // subject. A token that no longer verifies cannot be attributed.
        let principal = self
            .auth
            .verify(authorization)
            .await
            .map_err(|_| audit_unavailable())?;
        let mut connection = self
            .db
            .connection()
            .await
            .map_err(|_| audit_unavailable())?;
        let transaction = connection
            .transaction(Some(&principal))
            .await
            .map_err(|_| audit_unavailable())?;
        record_history_read(&*transaction, &principal, correlation, customer, "REFUSED").await?;
        transaction.commit().await.map_err(|_| audit_unavailable())
    }
}

/// A 403 on a customer's history is itself a logged read, with outcome
/// `REFUSED`. The kit refuses the role before any handler runs, so the
/// refusal is recorded around the router; when it cannot be, the answer is
/// 503, never a plain 403.
async fn log_refusals(
    State(log): State<Arc<RefusalLog>>,
    request: Request<Body>,
    next: Next,
) -> Response {
    let customer = (request.method() == Method::GET
        && request.uri().path() == "/v1/segment-assignments")
        .then(|| {
            query_pairs(request.uri().query().unwrap_or_default())
                .get("customer_id")
                .and_then(|raw| parse_customer_id(raw))
        })
        .flatten();
    let authorization = request
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned);
    let response = next.run(request).await;
    let Some(customer) = customer else {
        return response;
    };
    if response.status() != StatusCode::FORBIDDEN {
        return response;
    }
    let correlation = response
        .headers()
        .get("X-Correlation-ID")
        .and_then(|value| value.to_str().ok())
        .unwrap_or("-")
        .to_owned();
    match log
        .record(authorization.as_deref(), &correlation, customer)
        .await
    {
        Ok(()) => response,
        Err(error) => {
            let xml = response
                .headers()
                .get(header::CONTENT_TYPE)
                .and_then(|value| value.to_str().ok())
                .is_some_and(|value| value.starts_with("application/xml"));
            let mut refused = render_error(
                &error,
                &correlation,
                if xml { Format::Xml } else { Format::Json },
            );
            if let Ok(value) = correlation.parse() {
                refused.headers_mut().insert("X-Correlation-ID", value);
            }
            refused
        }
    }
}

// ---------------------------------------------------------------------------
// Replies
// ---------------------------------------------------------------------------

/// The label list. JSON names the array `segments`; XML repeats `Segment`
/// (the kit has no reply that carries both scalars and a renamed list).
pub fn list_reply(list: &SegmentList, xml: bool) -> Reply {
    let mut items = Representation::new(
        "Segments",
        [
            ("run_id", Value::from(list.run_id)),
            ("run_at", Value::from(list.run_at)),
            ("unassigned_count", Value::from(list.unassigned_count)),
        ],
    );
    let segments = Value::List(
        list.segments
            .iter()
            .map(|segment| Value::Object(segment.wire().data))
            .collect(),
    );
    items.data.insert(
        if xml { "Segment" } else { "segments" }.to_owned(),
        segments,
    );
    items.into()
}

pub fn segment_reply(segment: &SegmentItem) -> Reply {
    segment.wire().into()
}

pub fn current_reply(current: &CurrentAssignment) -> Reply {
    current.wire().into()
}

pub fn floor_reply(floor: &FloorAssignment) -> Reply {
    floor.wire().into()
}

pub fn assignments_reply(assignments: &[Assignment], page: u32, size: u32, total: u64) -> Reply {
    Page::new(
        "SegmentAssignments",
        "SegmentAssignment",
        "assignments",
        assignments.iter().map(|row| row.wire().data).collect(),
        page,
        total,
    )
    .size(size)
    .into()
}

pub fn migrations_reply(
    window: &MigrationWindow,
    migrations: &[Migration],
    page: u32,
    size: u32,
    total: u64,
    xml: bool,
) -> Reply {
    let mut reply = Representation::new(
        "SegmentMigrations",
        [
            ("from", Value::from(window.from)),
            ("to", Value::from(window.to)),
        ],
    );
    reply.data.insert(
        if xml {
            "SegmentMigration"
        } else {
            "migrations"
        }
        .to_owned(),
        Value::List(
            migrations
                .iter()
                .map(|row| Value::Object(row.wire().data))
                .collect(),
        ),
    );
    reply.data.insert(
        if xml { "common:PageInfo" } else { "page_info" }.to_owned(),
        Value::Object(
            Representation::new(
                "PageInfo",
                [
                    (if xml { "common:page" } else { "page" }, Value::from(page)),
                    (if xml { "common:size" } else { "size" }, Value::from(size)),
                    (
                        if xml { "common:total" } else { "total" },
                        Value::from(total as i64),
                    ),
                ],
            )
            .data,
        ),
    );
    reply.into()
}

pub fn matrix_reply(matrix: &MigrationMatrix, xml: bool) -> Reply {
    let totals = Value::List(
        matrix
            .totals
            .iter()
            .map(|total| {
                Value::Object(
                    Representation::new(
                        "MigrationTotal",
                        [
                            ("state", Value::from(total.state.state.clone())),
                            ("label_code", Value::from(total.state.label_code.clone())),
                            ("before_count", Value::from(total.before_count)),
                            ("after_count", Value::from(total.after_count)),
                            ("entries", Value::from(total.entries)),
                            ("exits", Value::from(total.exits)),
                        ],
                    )
                    .data,
                )
            })
            .collect(),
    );
    let cells = Value::List(
        matrix
            .cells
            .iter()
            .map(|cell| {
                Value::Object(
                    Representation::new(
                        "MigrationCell",
                        [
                            ("previous_state", Value::from(cell.previous.state.clone())),
                            (
                                "previous_label_code",
                                Value::from(cell.previous.label_code.clone()),
                            ),
                            ("new_state", Value::from(cell.new.state.clone())),
                            ("new_label_code", Value::from(cell.new.label_code.clone())),
                            ("movement", Value::from(cell.movement.clone())),
                            ("customer_count", Value::from(cell.customer_count)),
                        ],
                    )
                    .data,
                )
            })
            .collect(),
    );
    let mut reply = Representation::new(
        "SegmentMigrationMatrix",
        [
            ("from", Value::from(matrix.window.from)),
            ("to", Value::from(matrix.window.to)),
            (
                "from_model_version_id",
                Value::from(matrix.from_model_version_id),
            ),
            (
                "to_model_version_id",
                Value::from(matrix.to_model_version_id),
            ),
            ("methodological", Value::from(matrix.methodological)),
            ("customer_count", Value::from(matrix.customer_count)),
        ],
    );
    reply.data.insert(
        if xml { "MigrationTotal" } else { "totals" }.to_owned(),
        totals,
    );
    reply.data.insert(
        if xml { "MigrationCell" } else { "cells" }.to_owned(),
        cells,
    );
    reply.into()
}

pub fn explanation_reply(explanation: &MigrationExplanation, xml: bool) -> Reply {
    let measures = Value::List(
        explanation
            .measures
            .iter()
            .map(|measure| {
                Value::Object(
                    Representation::new(
                        "MigrationMeasure",
                        [
                            ("variable", Value::from(measure.variable.clone())),
                            ("unit", Value::from(measure.unit.clone())),
                            (
                                "previous_value",
                                Value::from(measure.previous_value.clone()),
                            ),
                            ("new_value", Value::from(measure.new_value.clone())),
                            ("judgement", Value::from(measure.judgement.clone())),
                            (
                                "not_compared_reason",
                                Value::from(measure.not_compared_reason.clone()),
                            ),
                            ("rank_changed", Value::from(measure.rank_changed)),
                            ("statement", Value::from(measure.statement.clone())),
                        ],
                    )
                    .data,
                )
            })
            .collect(),
    );
    let mut reply = Representation::new(
        "SegmentMigrationExplanation",
        [
            ("from", Value::from(explanation.window.from)),
            ("to", Value::from(explanation.window.to)),
            (
                "migration",
                Value::Object(explanation.migration.wire().data),
            ),
        ],
    );
    reply.data.insert(
        if xml { "MigrationMeasure" } else { "measures" }.to_owned(),
        measures,
    );
    reply.into()
}

/// Whether the kit will answer this `Accept` in XML: the best quality wins,
/// an exact media type beats `application/*` beats `*/*`, ties go to JSON.
pub fn prefers_xml(accept: &str) -> bool {
    let mut json = (0.0_f64, -1_i8);
    let mut xml = (0.0_f64, -1_i8);
    for item in accept.split(',') {
        let mut parts = item.split(';');
        let media = parts.next().unwrap_or("").trim().to_ascii_lowercase();
        let quality = parts
            .filter_map(|parameter| parameter.trim().strip_prefix("q="))
            .find_map(|value| value.trim().parse::<f64>().ok())
            .unwrap_or(1.0);
        let specificity = match media.as_str() {
            "*/*" => 0,
            "application/*" => 1,
            "application/json" | "application/xml" => 2,
            _ => continue,
        };
        for (target, name) in [
            (&mut json, "application/json"),
            (&mut xml, "application/xml"),
        ] {
            let matches = media == name || specificity < 2;
            if matches && quality > 0.0 && (quality, specificity) > (target.0, target.1) {
                *target = (quality, specificity);
            }
        }
    }
    xml.0 > 0.0 && (xml.0, xml.1) > (json.0, json.1)
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

fn request_info() -> RequestInfo {
    REQUEST.try_with(Clone::clone).unwrap_or_default()
}

async fn list_segments(ctx: Ctx) -> Result<Reply, ApiError> {
    let list = reader().list(ctx.db()).await?;
    Ok(list_reply(&list, prefers_xml(&request_info().accept)))
}

async fn get_segment(ctx: Ctx) -> Result<Reply, ApiError> {
    let code = ctx.path_text("label_code").ok_or_else(segment_not_found)?;
    let segment = reader().segment(ctx.db(), code).await?;
    Ok(segment_reply(&segment))
}

async fn get_current_assignment(ctx: Ctx) -> Result<Reply, ApiError> {
    let customer = current_customer(&request_info().query)?;
    let current = reader().current(ctx.db(), customer).await?;
    Ok(current_reply(&current))
}

/// The floor roles' read (ADR-0060). The kit admits only the roles the
/// contract lists, and the reply is built from `FloorAssignment`, which holds
/// no score and no `run_id`, whatever the cache or the database returned.
async fn get_floor_current_assignment(ctx: Ctx) -> Result<Reply, ApiError> {
    let customer = current_customer(&request_info().query)?;
    let current = reader().current(ctx.db(), customer).await?;
    Ok(floor_reply(&FloorAssignment::from(&current)))
}

async fn list_segment_assignments(ctx: Ctx) -> Result<Reply, ApiError> {
    let filter = assignment_filter(&request_info().query)?;
    let (rows, total) = match ctx.principal() {
        Some(principal) => {
            read_logged_history(
                ctx.db(),
                principal,
                &ctx.correlation_id,
                &filter,
                ctx.page,
                ctx.size,
            )
            .await?
        }
        // Every operation is protected; without a principal there is no
        // reader to record, so nothing is read.
        None => return Err(audit_unavailable()),
    };
    Ok(assignments_reply(&rows, ctx.page, ctx.size, total))
}

async fn get_segment_migration_matrix(ctx: Ctx) -> Result<Reply, ApiError> {
    let window = migration_window(&request_info().query)?;
    let matrix = read_migration_matrix(ctx.db(), window).await?;
    Ok(matrix_reply(&matrix, prefers_xml(&request_info().accept)))
}

async fn list_segment_migrations(ctx: Ctx) -> Result<Reply, ApiError> {
    let query = migration_query(&request_info().query)?;
    let rows = read_migrations(ctx.db(), &query).await?;
    let total = rows.len() as u64;
    let offset =
        usize::try_from(u64::from(ctx.page - 1) * u64::from(ctx.size)).unwrap_or(usize::MAX);
    let page: Vec<_> = rows
        .into_iter()
        .skip(offset)
        .take(ctx.size as usize)
        .collect();
    Ok(migrations_reply(
        &query.window,
        &page,
        ctx.page,
        ctx.size,
        total,
        prefers_xml(&request_info().accept),
    ))
}

async fn get_segment_migration_explanation(ctx: Ctx) -> Result<Reply, ApiError> {
    let (customer, window) = explanation_query(&request_info().query)?;
    let explanation = read_migration_explanation(ctx.db(), customer, window).await?;
    Ok(explanation_reply(
        &explanation,
        prefers_xml(&request_info().accept),
    ))
}

async fn database_ready(db: Arc<Database>) -> bool {
    let Ok(connection) = db.connection().await else {
        return false;
    };
    connection
        .query(
            "SELECT (SELECT count(*) FROM api_segments.current_label_v1), \
                    (SELECT count(*) FROM api_models.run_version_v1)",
            &[],
        )
        .await
        .is_ok()
}

// ---------------------------------------------------------------------------
// Streams
// ---------------------------------------------------------------------------

async fn redis_connection(
    url: String,
    password: Option<String>,
) -> redis::RedisResult<MultiplexedConnection> {
    let info = service_redis_info(&url, SUFFIX, password)?;
    redis::Client::open(info)?
        .get_multiplexed_async_connection()
        .await
}

/// The outbox relay for `stream:segments.v1.run_recorded` and
/// `stream:segments.v1.assignment_changed`. The first
/// connection is retried every second, so the service comes up during a Redis
/// outage; after that the kit rebuilds the connection after every outage.
pub async fn run_relay(database_url: String, redis_url: String, password: Option<String>) {
    loop {
        if let Ok(redis) = redis_connection(redis_url.clone(), password.clone()).await {
            match OutboxRelay::new(&database_url, SUFFIX, [STREAM, ASSIGNMENT_STREAM], redis) {
                Ok(relay) => {
                    relay
                        .run_with_reconnect(|| {
                            redis_connection(redis_url.clone(), password.clone())
                        })
                        .await;
                }
                Err(error) => tracing::error!("Outbox relay configuration failed: {error}"),
            }
        }
        tracing::warn!("Stream relay is waiting for redis-state");
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}

/// The `segments` consumer group on the same stream, reconnecting as the
/// relay does. Each event runs `handler`; the service's handler moves reads
/// to the latest run.
pub async fn run_consumer<H, Fut, E>(redis_url: String, password: Option<String>, handler: H)
where
    H: FnMut(HashMap<String, String>) -> Fut + Clone + Send + 'static,
    Fut: std::future::Future<Output = Result<(), E>> + Send + 'static,
    E: std::fmt::Display + 'static,
{
    loop {
        if let Ok(redis) = redis_connection(redis_url.clone(), password.clone()).await {
            match StreamConsumer::new(redis, STREAM, handler.clone()) {
                Ok(consumer) => {
                    consumer
                        .run_with_reconnect(|| {
                            redis_connection(redis_url.clone(), password.clone())
                        })
                        .await;
                }
                Err(error) => {
                    tracing::error!("Stream consumer configuration failed: {error}")
                }
            }
        }
        tracing::warn!("Stream consumer is waiting for redis-state");
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}

fn spawn_stream_tasks(password: Option<String>, reader: Reader, db: Arc<Database>) {
    let state_url = std::env::var("REDIS_STATE_URL")
        .unwrap_or_else(|_| "redis://redis-state:6379/0".to_owned());
    if let Ok(database_url) = std::env::var("DATABASE_URL") {
        tokio::spawn(run_relay(database_url, state_url.clone(), password.clone()));
    }
    let handler = move |_event: HashMap<String, String>| {
        let reader = reader.clone();
        let db = Arc::clone(&db);
        async move { reader.refresh_run(&db).await.map(|_| ()) }
    };
    tokio::spawn(run_consumer(state_url, password, handler));
}

async fn capture_request(request: Request<Body>, next: Next) -> Response {
    let info = RequestInfo {
        query: request.uri().query().unwrap_or_default().to_owned(),
        accept: request
            .headers()
            .get(axum::http::header::ACCEPT)
            .and_then(|value| value.to_str().ok())
            .unwrap_or_default()
            .to_owned(),
    };
    REQUEST.scope(info, next.run(request)).await
}

/// The service's router: the kit's operations, the request capture, and the
/// layer that records a refused history read. Also returns the database.
pub fn router(
    config: KitConfig,
    contracts: &str,
    reader: Reader,
) -> Result<(Router, Arc<Database>), Box<dyn std::error::Error>> {
    let _ = READER.set(reader);
    let kit = Kit::new(SERVICE, SUFFIX, contracts, config.clone())?;
    let db = kit.database();
    let refusals = Arc::new(RefusalLog::new(&config, Arc::clone(&db))?);
    let router = kit
        .operation("listSegments", list_segments)?
        .operation("getSegment", get_segment)?
        .operation("getCurrentAssignment", get_current_assignment)?
        .operation("getFloorCurrentAssignment", get_floor_current_assignment)?
        .operation("listSegmentAssignments", list_segment_assignments)?
        .operation("getSegmentMigrationMatrix", get_segment_migration_matrix)?
        .operation("listSegmentMigrations", list_segment_migrations)?
        .operation(
            "getSegmentMigrationExplanation",
            get_segment_migration_explanation,
        )?
        .ready_check(database_ready)
        .into_router()
        .layer(middleware::from_fn_with_state(refusals, log_refusals))
        .layer(middleware::from_fn(capture_request));
    Ok((router, db))
}

pub fn run() -> Result<(), Box<dyn std::error::Error>> {
    configure_logging(&std::env::var("LOG_LEVEL").unwrap_or_else(|_| "info".to_owned()));
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let password = std::env::var("REDIS_SEGMENTS_PASSWORD").ok();
        let cache_url = std::env::var("REDIS_CACHE_URL")
            .unwrap_or_else(|_| "redis://redis-cache:6379/0".to_owned());
        let cache = service_redis_info(&cache_url, SUFFIX, password.clone())
            .ok()
            .and_then(|info| redis::Client::open(info).ok())
            .map(Cache::new);
        let reader = Reader::new(cache);
        let (router, db) = router(KitConfig::from_env(SUFFIX), "contracts", reader.clone())?;
        spawn_stream_tasks(password, reader, db);
        let listener = tokio::net::TcpListener::bind("0.0.0.0:5010").await?;
        mosaiq_kit::serve(listener, router).await?;
        Ok::<(), Box<dyn std::error::Error>>(())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn current_customer_needs_one_uuid() {
        let id = "7b3ad781-c5de-4f40-ae50-1fa825ce2b25";
        assert_eq!(
            current_customer(&format!("customer_id={id}")).unwrap(),
            Uuid::parse_str(id).unwrap()
        );
        for query in ["", "customer_id=", "customer_id=abc", "customer_id=%20"] {
            let error = current_customer(query).unwrap_err();
            assert_eq!((error.status, error.code.as_str()), (422, "invalid_query"));
        }
    }

    #[test]
    fn assignment_filter_wants_exactly_one_of_customer_and_label() {
        let id = "7b3ad781-c5de-4f40-ae50-1fa825ce2b25";
        assert_eq!(
            assignment_filter("label_code=LOYAL&open=true").unwrap(),
            Filter::Label {
                code: "LOYAL".to_owned(),
                open: true
            }
        );
        assert!(matches!(
            assignment_filter(&format!("customer_id={id}")).unwrap(),
            Filter::Customer { open: false, .. }
        ));
        for query in [
            "".to_owned(),
            "open=true".to_owned(),
            format!("customer_id={id}&label_code=LOYAL"),
            "label_code=".to_owned(),
            "label_code=LOYAL&open=maybe".to_owned(),
            "customer_id=nope".to_owned(),
        ] {
            let error = assignment_filter(&query).unwrap_err();
            assert_eq!(
                (error.status, error.code.as_str()),
                (422, "invalid_query"),
                "{query}"
            );
        }
    }

    #[test]
    fn the_floor_view_keeps_state_label_and_since_and_drops_the_rest() {
        use mosaiq_kit::rendering::{render_json, render_xml};
        let since = "2026-10-12T03:00:00Z".parse::<DateTime<Utc>>().unwrap();
        let assigned = CurrentAssignment {
            customer_id: "7b3ad781-c5de-4f40-ae50-1fa825ce2b25".to_owned(),
            state: "assigned".to_owned(),
            label_code: Some("LOYAL".to_owned()),
            r_score: Some(4),
            f_score: Some(5),
            m_score: Some(4),
            since: Some(since),
            run_id: Some(42),
        };
        let floor = FloorAssignment::from(&assigned);
        assert_eq!(floor.state, "assigned");
        assert_eq!(floor.label_code.as_deref(), Some("LOYAL"));
        assert_eq!(floor.since, Some(since));
        let json = render_json(&floor_reply(&floor), 20).unwrap();
        assert_eq!(
            json,
            serde_json::json!({
                "customer_id": "7b3ad781-c5de-4f40-ae50-1fa825ce2b25",
                "state": "assigned",
                "label_code": "LOYAL",
                "since": "2026-10-12T03:00:00Z",
            })
        );
        let namespace = "https://mosaiq.maxthecoder.online/xml/segments/v1";
        let xml = render_xml(&floor_reply(&floor), namespace, 20).unwrap();
        assert!(xml.contains("<FloorCurrentAssignment"), "{xml}");
        for withheld in ["score", "run_id"] {
            assert!(!xml.contains(withheld), "{xml}");
        }

        // The unassigned states stay as they are: nulls in JSON, absent in XML.
        let none = CurrentAssignment {
            state: "no_open_assignment".to_owned(),
            label_code: None,
            r_score: None,
            f_score: None,
            m_score: None,
            since: None,
            run_id: None,
            ..assigned
        };
        let json = render_json(&floor_reply(&FloorAssignment::from(&none)), 20).unwrap();
        assert_eq!(json["state"], "no_open_assignment");
        assert!(json["label_code"].is_null() && json["since"].is_null());
        assert_eq!(json.as_object().unwrap().len(), 4);
        let xml = render_xml(&floor_reply(&FloorAssignment::from(&none)), namespace, 20).unwrap();
        assert!(
            !xml.contains("label_code") && !xml.contains("<since>"),
            "{xml}"
        );
    }

    #[test]
    fn xml_is_chosen_only_when_it_outranks_json() {
        assert!(prefers_xml("application/xml"));
        assert!(prefers_xml("application/json;q=0.5, application/xml"));
        assert!(!prefers_xml(""));
        assert!(!prefers_xml("*/*"));
        assert!(!prefers_xml("application/json"));
        assert!(!prefers_xml("application/xml, application/json"));
        assert!(!prefers_xml("application/xml;q=0.2, application/json"));
        assert!(prefers_xml("application/xml, */*;q=0.1"));
        // Equal quality: the more specific match wins, as the kit's best_match.
        assert!(prefers_xml("application/*;q=0.9, application/xml;q=0.9"));
        assert!(!prefers_xml(
            "application/json;q=0.5, application/xml;q=0.5, */*;q=0.9"
        ));
    }

    #[test]
    fn keys_hold_the_run() {
        assert_eq!(list_key(42), "segments:cache:42:list");
        assert_eq!(current_key(42, "abc"), "segments:cache:42:current:abc");
    }

    fn snapshot(label: Option<&str>, version: Option<i64>, cause: Option<&str>) -> Snapshot {
        Snapshot {
            customer_id: Uuid::nil(),
            state: MigrationState::new(label.map(str::to_owned), true),
            run_id: Some(1),
            model_version_id: version,
            run_at: None,
            valid_from: None,
            cause: cause.map(str::to_owned),
            recency_last_purchase_at: None,
            frequency_count: None,
            monetary_total: None,
            r_score: None,
            f_score: None,
            m_score: None,
        }
    }

    fn window() -> MigrationWindow {
        let from = DateTime::parse_from_rfc3339("2026-09-01T00:00:00Z").unwrap();
        MigrationWindow {
            from: from.with_timezone(&Utc),
            to: from.with_timezone(&Utc) + chrono::Duration::days(30),
        }
    }

    #[test]
    fn a_move_is_methodological_across_versions_unknown_versions_and_version_change_causes() {
        let ordinals = HashMap::from([("LOYAL".to_owned(), 2), ("CHAMPION".to_owned(), 1)]);
        let cases = [
            (Some(1), Some(1), None, false),
            (Some(1), Some(2), None, true),
            (None, Some(1), None, true),
            (Some(1), None, None, true),
            (Some(1), Some(1), Some("VERSION_CHANGE"), true),
            (Some(1), Some(1), Some("SALE"), false),
        ];
        for (before, after, cause, expected) in cases {
            let migration = build_migration(
                snapshot(Some("LOYAL"), before, None),
                snapshot(Some("CHAMPION"), after, cause),
                &window(),
                &ordinals,
            );
            assert_eq!(
                migration.methodological, expected,
                "{before:?} {after:?} {cause:?}"
            );
            assert_eq!(migration.movement, "improved");
        }
    }

    #[test]
    fn a_label_change_with_no_variable_changed_is_rank_only() {
        let ordinals = HashMap::from([("LOYAL".to_owned(), 2), ("CHAMPION".to_owned(), 1)]);
        let migration = build_migration(
            snapshot(Some("LOYAL"), Some(1), None),
            snapshot(Some("CHAMPION"), Some(1), None),
            &window(),
            &ordinals,
        );
        assert!(migration.responsible_variables.is_empty() && migration.rank_only);
    }

    #[test]
    fn the_tally_puts_every_customer_in_one_cell_and_reconciles_the_totals() {
        let ordinals = HashMap::from([("LOYAL".to_owned(), 2), ("CHAMPION".to_owned(), 1)]);
        let states = [
            (Some("LOYAL"), Some("CHAMPION")),
            (Some("CHAMPION"), Some("CHAMPION")),
            (Some("LOYAL"), None),
            (None, Some("LOYAL")),
        ];
        let migrations: Vec<_> = states
            .iter()
            .map(|(before, after)| {
                build_migration(
                    snapshot(*before, Some(1), None),
                    snapshot(*after, Some(1), None),
                    &window(),
                    &ordinals,
                )
            })
            .collect();
        let (totals, cells) = tally(&migrations, &ordinals);
        assert_eq!(cells.iter().map(|c| c.customer_count).sum::<i64>(), 4);
        for total in &totals {
            assert_eq!(
                total.before_count + total.entries - total.exits,
                total.after_count
            );
        }
        assert_eq!(totals.iter().map(|t| t.before_count).sum::<i64>(), 4);
        assert_eq!(totals.iter().map(|t| t.after_count).sum::<i64>(), 4);
    }
}
