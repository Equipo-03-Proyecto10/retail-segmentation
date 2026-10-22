//! segments-service: what the web's segmentation runs recorded, read-only
//! (F14-20, RN-20, RN-21). The label vocabulary with its sizes in the latest
//! run, a customer's current assignment and the assignment history.
//!
//! Reads are cached on redis-cache under `segments:cache:{run_id}:*`. The
//! current run id comes from PostgreSQL and moves when the `segments` consumer
//! group sees `stream:segments.v1.run_recorded`, so the old run's keys are
//! simply never asked for again and expire by their TTL; nothing scans.

use std::collections::HashMap;
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use axum::body::Body;
use axum::extract::Request;
use axum::middleware::{self, Next};
use axum::response::Response;
use chrono::{DateTime, Utc};
use mosaiq_kit::{
    configure_logging, service_redis_info, ApiError, Ctx, Database, Detail, Kit, KitConfig,
    OutboxRelay, Page, Reply, Representation, StreamConsumer, Value,
};
use redis::aio::MultiplexedConnection;
use redis::AsyncCommands;
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use tokio_postgres::Row;
use uuid::Uuid;

pub const SERVICE: &str = "segments";
pub const SUFFIX: &str = "segments";
pub const STREAM: &str = "stream:segments.v1.run_recorded";
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

/// `customer_id` of `getCurrentAssignment`: required, a UUID.
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

async fn list_segment_assignments(ctx: Ctx) -> Result<Reply, ApiError> {
    let filter = assignment_filter(&request_info().query)?;
    let (rows, total) = read_assignments(ctx.db(), &filter, ctx.page, ctx.size).await?;
    Ok(assignments_reply(&rows, ctx.page, ctx.size, total))
}

async fn database_ready(db: Arc<Database>) -> bool {
    let Ok(connection) = db.connection().await else {
        return false;
    };
    connection
        .query("SELECT 1 FROM api_segments.current_label_v1 LIMIT 1", &[])
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

/// The outbox relay for `stream:segments.v1.run_recorded`. The first
/// connection is retried every second, so the service comes up during a Redis
/// outage; after that the kit rebuilds the connection after every outage.
pub async fn run_relay(database_url: String, redis_url: String, password: Option<String>) {
    loop {
        if let Ok(redis) = redis_connection(redis_url.clone(), password.clone()).await {
            match OutboxRelay::new(&database_url, SUFFIX, [STREAM], redis) {
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
        let _ = READER.set(reader.clone());
        let kit = Kit::new(SERVICE, SUFFIX, "contracts", KitConfig::from_env(SUFFIX))?;
        spawn_stream_tasks(password, reader, kit.database());
        let router = kit
            .operation("listSegments", list_segments)?
            .operation("getSegment", get_segment)?
            .operation("getCurrentAssignment", get_current_assignment)?
            .operation("listSegmentAssignments", list_segment_assignments)?
            .ready_check(database_ready)
            .into_router()
            .layer(middleware::from_fn(capture_request));
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
}
