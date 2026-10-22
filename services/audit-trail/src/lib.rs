//! audit-trail-service: the read-only, attributed, credential-free view of
//! `audit_log` (RN-28, RN-29, RN-30; F14-30).
//!
//! The service reads one table and writes nothing. `fn_audit`, running as
//! `definer_audit`, is the only writer of an entry, so no `svc_*` role can
//! alter the record (ADR-0034, E-12). `fn_audit` already strips `password_hash`
//! from both payloads; this service strips it again, at any depth, before an
//! entry leaves it (RN-29).
//!
//! A payload is a JSON object in the JSON representation and a string holding
//! that object's JSON text in the XML one (`contracts/audit-trail/`).

use std::net::SocketAddr;
use std::sync::Arc;

use axum::body::Body;
use axum::extract::Request;
use axum::middleware::{self, Next};
use axum::response::Response;
use axum::Router;
use chrono::{DateTime, Utc};
use indexmap::IndexMap;
use mosaiq_kit::{
    configure_logging, ApiError, Ctx, Database, Detail, Kit, KitConfig, Page, Reply,
    Representation, Value,
};
use tokio_postgres::Row;
use uuid::Uuid;

pub const SERVICE: &str = "audit-trail";
pub const SUFFIX: &str = "audit_trail";

/// The audited tables of RN-28, and the stores boundary's `store_location`
/// (ADR-0046). The same thirteen names as the contract's `AuditedEntity`.
pub const ENTITIES: [&str; 13] = [
    "category",
    "product",
    "store",
    "customer",
    "app_user",
    "segment",
    "segment_rule",
    "campaign",
    "experiment",
    "channel",
    "role",
    "customer_segment_history",
    "store_location",
];
pub const ACTIONS: [&str; 3] = ["INSERT", "UPDATE", "DELETE"];

/// The member no entry may carry at any depth (RN-29).
const CREDENTIAL: &str = "password_hash";

tokio::task_local! {
    static REQUEST_QUERY: String;
}

// ---------- the query ----------

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Filters {
    pub entity: Option<String>,
    pub entity_pk: Option<String>,
    pub user_id: Option<Uuid>,
    pub action: Option<String>,
    pub from: Option<DateTime<Utc>>,
    pub to: Option<DateTime<Utc>>,
}

fn timestamp(text: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(text)
        .ok()
        .map(|moment| moment.with_timezone(&Utc))
}

/// Read the list's query string. Every rejected parameter is named in
/// `details` of one 422 `invalid_query`.
pub fn parse_filters(query: &str) -> Result<Filters, ApiError> {
    let values: std::collections::HashMap<String, String> =
        form_urlencoded::parse(query.as_bytes())
            .map(|(key, value)| (key.into_owned(), value.into_owned()))
            .collect();
    let mut details = Vec::new();
    let mut filters = Filters::default();

    if let Some(text) = values.get("entity") {
        if ENTITIES.contains(&text.as_str()) {
            filters.entity = Some(text.clone());
        } else {
            details.push(Detail::new("entity", "enum"));
        }
    }
    if let Some(text) = values.get("entity_pk") {
        let length = text.chars().count();
        if length == 0 {
            details.push(Detail::new("entity_pk", "too_short"));
        } else if length > 80 {
            details.push(Detail::new("entity_pk", "too_long"));
        } else if !values.contains_key("entity") {
            details.push(Detail::new("entity_pk", "requires_entity"));
        } else {
            filters.entity_pk = Some(text.clone());
        }
    }
    if let Some(text) = values.get("user_id") {
        match Uuid::parse_str(text) {
            Ok(id) => filters.user_id = Some(id),
            Err(_) => details.push(Detail::new("user_id", "invalid_format")),
        }
    }
    if let Some(text) = values.get("action") {
        if ACTIONS.contains(&text.as_str()) {
            filters.action = Some(text.clone());
        } else {
            details.push(Detail::new("action", "enum"));
        }
    }
    for (name, slot) in [("from", &mut filters.from), ("to", &mut filters.to)] {
        if let Some(text) = values.get(name) {
            match timestamp(text) {
                Some(moment) => *slot = Some(moment),
                None => details.push(Detail::new(name, "invalid_format")),
            }
        }
    }
    if let (Some(from), Some(to)) = (filters.from, filters.to) {
        if to < from {
            details.push(Detail::new("to", "before_from"));
        }
    }
    if details.is_empty() {
        Ok(filters)
    } else {
        Err(ApiError::new(
            422,
            "invalid_query",
            "A query parameter is not an allowed value.",
        )
        .with_details(details))
    }
}

// ---------- the entry, without a credential ----------

/// Remove `password_hash` from an audited row, wherever it is nested.
pub fn strip_credentials(value: &mut serde_json::Value) {
    match value {
        serde_json::Value::Object(members) => {
            members.retain(|key, _| !key.eq_ignore_ascii_case(CREDENTIAL));
            members.values_mut().for_each(strip_credentials);
        }
        serde_json::Value::Array(items) => items.iter_mut().for_each(strip_credentials),
        _ => {}
    }
}

/// A payload as the JSON text of its stripped object, or `None` for SQL NULL.
pub fn clean_payload(raw: Option<String>) -> Result<Option<String>, ApiError> {
    let Some(raw) = raw else { return Ok(None) };
    let mut parsed: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|_| ApiError::new(500, "internal_error", "An audit payload could not be read."))?;
    strip_credentials(&mut parsed);
    Ok(Some(parsed.to_string()))
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AuditEntry {
    pub audit_id: i64,
    pub user_id: Option<Uuid>,
    pub entity: String,
    pub entity_pk: String,
    pub action: String,
    pub data_before: Option<String>,
    pub data_after: Option<String>,
    pub executed_at: DateTime<Utc>,
}

impl AuditEntry {
    /// Columns 0 to 7 of the entry statements below.
    fn from_row(row: &Row) -> Result<Self, ApiError> {
        Ok(Self {
            audit_id: row.try_get(0)?,
            user_id: row.try_get(1)?,
            entity: row.try_get(2)?,
            entity_pk: row.try_get(3)?,
            action: row.try_get(4)?,
            data_before: clean_payload(row.try_get(5)?)?,
            data_after: clean_payload(row.try_get(6)?)?,
            executed_at: row.try_get(7)?,
        })
    }

    /// `user_id` and the payloads are null here, which the XML omits.
    pub fn wire(&self) -> IndexMap<String, Value> {
        IndexMap::from([
            ("audit_id".to_owned(), Value::from(self.audit_id)),
            (
                "user_id".to_owned(),
                self.user_id
                    .map_or(Value::Null, |id| Value::Text(id.to_string())),
            ),
            ("entity".to_owned(), Value::from(self.entity.as_str())),
            ("entity_pk".to_owned(), Value::from(self.entity_pk.as_str())),
            ("action".to_owned(), Value::from(self.action.as_str())),
            (
                "data_before".to_owned(),
                self.data_before
                    .clone()
                    .map_or(Value::Null, Value::JsonText),
            ),
            (
                "data_after".to_owned(),
                self.data_after.clone().map_or(Value::Null, Value::JsonText),
            ),
            ("executed_at".to_owned(), Value::from(self.executed_at)),
        ])
    }
}

// ---------- the reads ----------

/// A page of entries, newest first, and the total that match.
pub async fn list_entries(
    db: &Database,
    filters: &Filters,
    page: u32,
    size: u32,
) -> Result<(Vec<AuditEntry>, u64), ApiError> {
    let connection = db.connection().await?;
    let limit = i64::from(size);
    let offset = i64::from(page.saturating_sub(1)) * limit;
    // One statement: the total always comes back, even past the last page, as
    // a row whose entry columns are null.
    let rows = connection
        .query(
            "WITH matched AS ( \
                 SELECT audit_id, user_id, entity, entity_pk, action, \
                        data_before::text AS data_before, \
                        data_after::text AS data_after, executed_at \
                 FROM public.audit_log \
                 WHERE ($1::text IS NULL OR entity = $1) \
                   AND ($2::text IS NULL OR entity_pk = $2) \
                   AND ($3::uuid IS NULL OR user_id = $3) \
                   AND ($4::text IS NULL OR action = $4) \
                   AND ($5::timestamptz IS NULL OR executed_at >= $5) \
                   AND ($6::timestamptz IS NULL OR executed_at <= $6)) \
             SELECT p.audit_id, p.user_id, p.entity, p.entity_pk, p.action, \
                    p.data_before, p.data_after, p.executed_at, t.total \
             FROM (SELECT count(*) AS total FROM matched) AS t \
             LEFT JOIN LATERAL (SELECT * FROM matched \
                                 ORDER BY executed_at DESC, audit_id DESC \
                                 LIMIT $7 OFFSET $8) AS p ON TRUE",
            &[
                &filters.entity,
                &filters.entity_pk,
                &filters.user_id,
                &filters.action,
                &filters.from,
                &filters.to,
                &limit,
                &offset,
            ],
        )
        .await?;
    let total = rows
        .first()
        .map(|row| row.try_get::<_, i64>(8))
        .transpose()?
        .unwrap_or(0);
    let entries = rows
        .iter()
        .filter(|row| row.get::<_, Option<i64>>(0).is_some())
        .map(AuditEntry::from_row)
        .collect::<Result<Vec<_>, _>>()?;
    Ok((entries, u64::try_from(total).unwrap_or(0)))
}

pub async fn read_entry(db: &Database, audit_id: i64) -> Result<Option<AuditEntry>, ApiError> {
    let connection = db.connection().await?;
    connection
        .query_opt(
            "SELECT audit_id, user_id, entity, entity_pk, action, \
                    data_before::text, data_after::text, executed_at \
             FROM public.audit_log WHERE audit_id = $1",
            &[&audit_id],
        )
        .await?
        .as_ref()
        .map(AuditEntry::from_row)
        .transpose()
}

pub fn audit_entry_not_found() -> ApiError {
    ApiError::new(
        404,
        "audit_entry_not_found",
        "No audit entry has the requested audit_id.",
    )
}

// ---------- the operations ----------

async fn list(ctx: Ctx) -> Result<Reply, ApiError> {
    let query = REQUEST_QUERY.try_with(Clone::clone).unwrap_or_default();
    let filters = parse_filters(&query)?;
    let (entries, total) = list_entries(ctx.db(), &filters, ctx.page, ctx.size).await?;
    let items = entries.iter().map(AuditEntry::wire).collect();
    Ok(Page::new(
        "AuditEntries",
        "AuditEntry",
        "audit_entries",
        items,
        ctx.page,
        total,
    )
    .size(ctx.size)
    .into())
}

async fn get(ctx: Ctx) -> Result<Reply, ApiError> {
    let audit_id = ctx.path_i64("audit_id").ok_or_else(audit_entry_not_found)?;
    let entry = read_entry(ctx.db(), audit_id)
        .await?
        .ok_or_else(audit_entry_not_found)?;
    Ok(Representation::new("AuditEntry", entry.wire()).into())
}

async fn database_ready(db: Arc<Database>) -> bool {
    let Ok(connection) = db.connection().await else {
        return false;
    };
    connection
        .query("SELECT audit_id FROM public.audit_log LIMIT $1", &[&0i64])
        .await
        .is_ok()
}

// ---------- the layer around the kit's router ----------

/// kit-rs gives a handler no query string, so it is kept for the request.
async fn capture_query(request: Request<Body>, next: Next) -> Response {
    let query = request.uri().query().unwrap_or_default().to_owned();
    REQUEST_QUERY.scope(query, next.run(request)).await
}

pub fn router(config: KitConfig, contracts: &str) -> Result<Router, Box<dyn std::error::Error>> {
    Ok(Kit::new(SERVICE, SUFFIX, contracts, config)?
        .operation("listAuditEntries", list)?
        .operation("getAuditEntry", get)?
        .ready_check(database_ready)
        .into_router()
        .layer(middleware::from_fn(capture_query)))
}

pub fn run() -> Result<(), Box<dyn std::error::Error>> {
    configure_logging(&std::env::var("LOG_LEVEL").unwrap_or_else(|_| "info".to_owned()));
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let router = router(KitConfig::from_env(SUFFIX), "contracts")?;
        let listener = tokio::net::TcpListener::bind("0.0.0.0:5015").await?;
        axum::serve(
            listener,
            router.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await?;
        Ok::<(), Box<dyn std::error::Error>>(())
    })
}
