//! Domain events (ADR-0072, F21-25): the decisions the producers record
//! through `api_audit.record_event_v1` into `audit_events.domain_event`, read
//! here as `domain-events`, and the record of every read of either log.
//!
//! The service never writes the table directly: it holds `SELECT` only, and
//! records its own `AUDIT_READ` through the same function every producer uses,
//! in the transaction of the read. A read whose event cannot be recorded
//! answers 503 `audit_unavailable` and returns nothing (ADR-0072 section 4).

use std::collections::HashMap;

use chrono::{DateTime, Utc};
use indexmap::IndexMap;
use mosaiq_kit::{ApiError, Detail, Principal, Value};
use tokio_postgres::{GenericClient, Row};
use uuid::Uuid;

pub const KINDS: [&str; 7] = [
    "CALCULATION_RUN",
    "MODEL_PUBLISHED",
    "MODEL_ROLLED_BACK",
    "PERMISSION_CHANGED",
    "CRITICAL_ACTION",
    "HISTORY_READ",
    "AUDIT_READ",
];
pub const OUTCOMES: [&str; 2] = ["SUCCESS", "REFUSED"];
pub const SUBJECT_TYPES: [&str; 11] = [
    "user",
    "customer",
    "model_version",
    "calculation",
    "sale",
    "sale_reversal",
    "stock_level",
    "campaign",
    "experiment",
    "audit_entry",
    "domain_event",
];
/// The filter names `audit-entries` accepts, in the contract's order.
pub const ENTRY_FILTERS: [&str; 8] = [
    "entity",
    "entity_pk",
    "user_id",
    "action",
    "from",
    "to",
    "page",
    "size",
];
/// The filter names `domain-events` accepts, in the contract's order.
pub const EVENT_FILTERS: [&str; 13] = [
    "kind",
    "actor_user_id",
    "actor_role",
    "model_version_id",
    "subject_type",
    "subject_id",
    "store_id",
    "outcome",
    "source",
    "from",
    "to",
    "page",
    "size",
];
pub const RANGE_FILTERS: [&str; 2] = ["from", "to"];

/// An optional `recorded_at` range, both ends inclusive.
pub type Range = (Option<DateTime<Utc>>, Option<DateTime<Utc>>);

fn query_pairs(query: &str) -> HashMap<String, String> {
    form_urlencoded::parse(query.as_bytes())
        .map(|(key, value)| (key.into_owned(), value.into_owned()))
        .collect()
}

fn timestamp(text: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(text)
        .ok()
        .map(|moment| moment.with_timezone(&Utc))
}

fn invalid_query(details: Vec<Detail>) -> ApiError {
    ApiError::new(
        422,
        "invalid_query",
        "A query parameter is not an allowed value.",
    )
    .with_details(details)
}

fn is_role_code(text: &str) -> bool {
    let mut chars = text.chars();
    (2..=30).contains(&text.len())
        && chars.next().is_some_and(|first| first.is_ascii_uppercase())
        && chars.all(|c| c.is_ascii_uppercase() || c == '_')
}

fn is_source(text: &str) -> bool {
    text.strip_prefix("svc_").is_some_and(|rest| {
        let mut chars = rest.chars();
        (1..=60).contains(&rest.len())
            && chars.next().is_some_and(|first| first.is_ascii_lowercase())
            && chars.all(|c| c.is_ascii_lowercase() || c == '_')
    })
}

/// The names of the filters a request used, from a closed list, never their
/// values: what an `AUDIT_READ` records.
pub fn filter_names(query: &str, allowed: &[&str]) -> Vec<String> {
    let present = query_pairs(query);
    allowed
        .iter()
        .filter(|name| present.contains_key(**name))
        .map(|name| (*name).to_owned())
        .collect()
}

// ---------- the query ----------

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct EventFilters {
    pub kind: Option<String>,
    pub actor_user_id: Option<Uuid>,
    pub actor_role: Option<String>,
    pub model_version_id: Option<i64>,
    pub subject_type: Option<String>,
    pub subject_id: Option<String>,
    pub store_id: Option<i16>,
    pub outcome: Option<String>,
    pub source: Option<String>,
    pub from: Option<DateTime<Utc>>,
    pub to: Option<DateTime<Utc>>,
}

fn parse_range(values: &HashMap<String, String>, details: &mut Vec<Detail>) -> Range {
    let mut bounds = [None, None];
    for (slot, name) in bounds.iter_mut().zip(RANGE_FILTERS) {
        if let Some(text) = values.get(name) {
            match timestamp(text) {
                Some(moment) => *slot = Some(moment),
                None => details.push(Detail::new(name, "invalid_format")),
            }
        }
    }
    if let [Some(from), Some(to)] = bounds {
        if to < from {
            details.push(Detail::new("to", "before_from"));
        }
    }
    (bounds[0], bounds[1])
}

/// Read `listDomainEvents`' query. Every rejected parameter is named in one
/// 422 `invalid_query`.
pub fn parse_event_filters(query: &str) -> Result<EventFilters, ApiError> {
    let values = query_pairs(query);
    let mut details = Vec::new();
    let mut filters = EventFilters::default();
    let closed = |name: &str, allowed: &[&str], details: &mut Vec<Detail>| {
        values.get(name).and_then(|text| {
            if allowed.contains(&text.as_str()) {
                Some(text.clone())
            } else {
                details.push(Detail::new(name, "enum"));
                None
            }
        })
    };
    filters.kind = closed("kind", &KINDS, &mut details);
    filters.outcome = closed("outcome", &OUTCOMES, &mut details);
    filters.subject_type = closed("subject_type", &SUBJECT_TYPES, &mut details);
    if let Some(text) = values.get("actor_user_id") {
        match Uuid::parse_str(text) {
            Ok(id) => filters.actor_user_id = Some(id),
            Err(_) => details.push(Detail::new("actor_user_id", "invalid_format")),
        }
    }
    if let Some(text) = values.get("actor_role") {
        if is_role_code(text) {
            filters.actor_role = Some(text.clone());
        } else {
            details.push(Detail::new("actor_role", "invalid_format"));
        }
    }
    if let Some(text) = values.get("model_version_id") {
        match text.parse::<i64>() {
            Ok(id) if id >= 1 => filters.model_version_id = Some(id),
            _ => details.push(Detail::new("model_version_id", "invalid_format")),
        }
    }
    if let Some(text) = values.get("subject_id") {
        let length = text.chars().count();
        if !(1..=80).contains(&length) {
            details.push(Detail::new("subject_id", "invalid_format"));
        } else if !values.contains_key("subject_type") {
            details.push(Detail::new("subject_id", "requires_subject_type"));
        } else {
            filters.subject_id = Some(text.clone());
        }
    }
    if let Some(text) = values.get("store_id") {
        match text.parse::<i16>() {
            Ok(id) if id >= 1 => filters.store_id = Some(id),
            _ => details.push(Detail::new("store_id", "invalid_format")),
        }
    }
    if let Some(text) = values.get("source") {
        if is_source(text) {
            filters.source = Some(text.clone());
        } else {
            details.push(Detail::new("source", "invalid_format"));
        }
    }
    (filters.from, filters.to) = parse_range(&values, &mut details);
    if details.is_empty() {
        Ok(filters)
    } else {
        Err(invalid_query(details))
    }
}

/// Read `verifyDomainEvents`' optional range.
pub fn parse_verification_range(query: &str) -> Result<Range, ApiError> {
    let mut details = Vec::new();
    let range = parse_range(&query_pairs(query), &mut details);
    if details.is_empty() {
        Ok(range)
    } else {
        Err(invalid_query(details))
    }
}

// ---------- the event ----------

#[derive(Clone, Debug, PartialEq)]
pub struct DomainEvent {
    pub event_id: Uuid,
    pub kind: String,
    pub outcome: String,
    pub occurred_at: DateTime<Utc>,
    pub recorded_at: DateTime<Utc>,
    pub actor_type: String,
    pub actor_user_id: Option<Uuid>,
    pub actor_role: Option<String>,
    pub source: String,
    pub store_id: Option<i16>,
    pub subject_type: Option<String>,
    pub subject_id: Option<String>,
    pub model_version_id: Option<i64>,
    pub rule_set_version: Option<i64>,
    pub correlation_id: Option<String>,
    pub details: serde_json::Value,
    pub prev_hash: String,
    pub entry_hash: String,
}

/// The event's columns in `DomainEvent::from_row`'s order, joined into the
/// fixed statements below at compile time.
macro_rules! columns {
    () => {
        "event_id, kind, outcome, occurred_at, recorded_at, actor_type, \
         actor_user_id, actor_role, source, store_id, subject_type, subject_id, \
         model_version_id, rule_set_version, correlation_id, details::text, prev_hash, \
         entry_hash"
    };
}

/// Each kind's XML details element, and its members in the XSD's order. An
/// array member is written once per item under its singular name.
fn details_layout(kind: &str) -> (&'static str, &'static [(&'static str, &'static str)]) {
    match kind {
        "CALCULATION_RUN" => (
            "CalculationRun",
            &[
                ("calculation", "calculation"),
                ("status", "status"),
                ("input_count", "input_count"),
                ("output_count", "output_count"),
                ("parameters_digest", "parameters_digest"),
                ("window_from", "window_from"),
                ("window_to", "window_to"),
                ("data_origin", "data_origin"),
            ],
        ),
        "MODEL_PUBLISHED" | "MODEL_ROLLED_BACK" => (
            "ModelChange",
            &[
                ("previous_version_id", "previous_version_id"),
                ("reason_code", "reason_code"),
            ],
        ),
        "PERMISSION_CHANGED" => (
            "PermissionChange",
            &[
                ("change", "change"),
                ("from_role", "from_role"),
                ("to_role", "to_role"),
                ("store_ids", "store_id"),
            ],
        ),
        "CRITICAL_ACTION" => (
            "CriticalAction",
            &[("action", "action"), ("reason_code", "reason_code")],
        ),
        "HISTORY_READ" => ("HistoryRead", &[("history", "history")]),
        _ => (
            "AuditRead",
            &[("resource", "resource"), ("filters", "filter")],
        ),
    }
}

impl DomainEvent {
    fn from_row(row: &Row) -> Result<Self, ApiError> {
        let details: String = row.try_get(15)?;
        Ok(Self {
            event_id: row.try_get(0)?,
            kind: row.try_get(1)?,
            outcome: row.try_get(2)?,
            occurred_at: row.try_get(3)?,
            recorded_at: row.try_get(4)?,
            actor_type: row.try_get(5)?,
            actor_user_id: row.try_get(6)?,
            actor_role: row.try_get(7)?,
            source: row.try_get(8)?,
            store_id: row.try_get(9)?,
            subject_type: row.try_get(10)?,
            subject_id: row.try_get(11)?,
            model_version_id: row.try_get(12)?,
            rule_set_version: row.try_get(13)?,
            correlation_id: row.try_get(14)?,
            details: serde_json::from_str(&details).map_err(|_| {
                ApiError::new(500, "internal_error", "A domain event could not be read.")
            })?,
            prev_hash: row.try_get(16)?,
            entry_hash: row.try_get(17)?,
        })
    }

    /// The details as the representation carries them: the stored object in
    /// JSON; in XML, one element named for the kind's details type with its
    /// members in the XSD's order.
    fn details_value(&self, xml: bool) -> Value {
        if !xml {
            return Value::from(&self.details);
        }
        let (element, members) = details_layout(&self.kind);
        let mut inner = IndexMap::new();
        for (key, name) in members {
            if let Some(value) = self.details.get(*key) {
                inner.insert((*name).to_owned(), Value::from(value));
            }
        }
        Value::Object(IndexMap::from([(element.to_owned(), Value::Object(inner))]))
    }

    /// Null members stay null in JSON and are omitted in XML.
    pub fn wire(&self, xml: bool) -> IndexMap<String, Value> {
        IndexMap::from([
            (
                "event_id".to_owned(),
                Value::from(self.event_id.to_string()),
            ),
            ("kind".to_owned(), Value::from(self.kind.as_str())),
            ("outcome".to_owned(), Value::from(self.outcome.as_str())),
            ("occurred_at".to_owned(), Value::from(self.occurred_at)),
            ("recorded_at".to_owned(), Value::from(self.recorded_at)),
            (
                "actor_type".to_owned(),
                Value::from(self.actor_type.as_str()),
            ),
            (
                "actor_user_id".to_owned(),
                Value::from(self.actor_user_id.map(|id| id.to_string())),
            ),
            (
                "actor_role".to_owned(),
                Value::from(self.actor_role.clone()),
            ),
            ("source".to_owned(), Value::from(self.source.as_str())),
            ("store_id".to_owned(), Value::from(self.store_id)),
            (
                "subject_type".to_owned(),
                Value::from(self.subject_type.clone()),
            ),
            (
                "subject_id".to_owned(),
                Value::from(self.subject_id.clone()),
            ),
            (
                "model_version_id".to_owned(),
                Value::from(self.model_version_id),
            ),
            (
                "rule_set_version".to_owned(),
                Value::from(self.rule_set_version),
            ),
            (
                "correlation_id".to_owned(),
                Value::from(self.correlation_id.clone()),
            ),
            ("details".to_owned(), self.details_value(xml)),
            ("prev_hash".to_owned(), Value::from(self.prev_hash.as_str())),
            (
                "entry_hash".to_owned(),
                Value::from(self.entry_hash.as_str()),
            ),
        ])
    }
}

// ---------- the reads ----------

/// A page of events, newest first, and the total that match.
pub async fn list_events<C: GenericClient + Sync>(
    client: &C,
    filters: &EventFilters,
    page: u32,
    size: u32,
) -> Result<(Vec<DomainEvent>, u64), ApiError> {
    let limit = i64::from(size);
    let offset = i64::from(page.saturating_sub(1)) * limit;
    const STATEMENT: &str = concat!(
        "WITH matched AS ( SELECT ",
        columns!(),
        ", seq FROM audit_events.domain_event \
             WHERE ($1::text IS NULL OR kind = $1) \
               AND ($2::uuid IS NULL OR actor_user_id = $2) \
               AND ($3::text IS NULL OR actor_role = $3) \
               AND ($4::bigint IS NULL OR model_version_id = $4) \
               AND ($5::text IS NULL OR subject_type = $5) \
               AND ($6::text IS NULL OR subject_id = $6) \
               AND ($7::smallint IS NULL OR store_id = $7) \
               AND ($8::text IS NULL OR outcome = $8) \
               AND ($9::text IS NULL OR source = $9) \
               AND ($10::timestamptz IS NULL OR recorded_at >= $10) \
               AND ($11::timestamptz IS NULL OR recorded_at <= $11)) \
         SELECT p.*, t.total \
         FROM (SELECT count(*) AS total FROM matched) AS t \
         LEFT JOIN LATERAL (SELECT * FROM matched \
                             ORDER BY recorded_at DESC, seq DESC \
                             LIMIT $12 OFFSET $13) AS p ON TRUE"
    );
    let rows = client
        .query(
            STATEMENT,
            &[
                &filters.kind,
                &filters.actor_user_id,
                &filters.actor_role,
                &filters.model_version_id,
                &filters.subject_type,
                &filters.subject_id,
                &filters.store_id,
                &filters.outcome,
                &filters.source,
                &filters.from,
                &filters.to,
                &limit,
                &offset,
            ],
        )
        .await?;
    let total = rows
        .first()
        .map(|row| row.try_get::<_, i64>(19))
        .transpose()?
        .unwrap_or(0);
    let events = rows
        .iter()
        .filter(|row| row.get::<_, Option<Uuid>>(0).is_some())
        .map(DomainEvent::from_row)
        .collect::<Result<Vec<_>, _>>()?;
    Ok((events, u64::try_from(total).unwrap_or(0)))
}

pub async fn read_event<C: GenericClient + Sync>(
    client: &C,
    event_id: Uuid,
) -> Result<Option<DomainEvent>, ApiError> {
    const STATEMENT: &str = concat!(
        "SELECT ",
        columns!(),
        " FROM audit_events.domain_event WHERE event_id = $1"
    );
    client
        .query_opt(STATEMENT, &[&event_id])
        .await?
        .as_ref()
        .map(DomainEvent::from_row)
        .transpose()
}

pub fn domain_event_not_found() -> ApiError {
    ApiError::new(
        404,
        "domain_event_not_found",
        "No domain event has the requested event_id.",
    )
}

// ---------- the chain ----------

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Verification {
    pub checked: u64,
    pub head_hash: Option<String>,
    pub first_broken_event_id: Option<Uuid>,
    pub reason: Option<String>,
}

impl Verification {
    pub fn ok(&self) -> bool {
        self.first_broken_event_id.is_none()
    }

    pub fn wire(&self) -> IndexMap<String, Value> {
        IndexMap::from([
            (
                "status".to_owned(),
                Value::from(if self.ok() { "ok" } else { "broken" }),
            ),
            (
                "checked".to_owned(),
                Value::Int(i64::try_from(self.checked).unwrap_or(i64::MAX)),
            ),
            ("head_hash".to_owned(), Value::from(self.head_hash.clone())),
            (
                "first_broken_event_id".to_owned(),
                Value::from(self.first_broken_event_id.map(|id| id.to_string())),
            ),
            ("reason".to_owned(), Value::from(self.reason.clone())),
        ])
    }
}

/// Recompute every event's hash with the schema's own canonical form, and
/// check each link against the event recorded just before it in the whole
/// chain, then report on the events whose `recorded_at` is in range.
pub async fn verify_chain<C: GenericClient + Sync>(
    client: &C,
    from: Option<DateTime<Utc>>,
    to: Option<DateTime<Utc>>,
) -> Result<Verification, ApiError> {
    let row = client
        .query_one(
            "WITH chain AS ( \
                 SELECT d.seq, d.event_id, d.recorded_at, d.entry_hash, \
                        CASE \
                          WHEN d.entry_hash <> audit_events.entry_hash_v1(d) \
                            THEN 'entry_hash_mismatch' \
                          WHEN d.prev_hash <> coalesce( \
                                 lag(d.entry_hash) OVER (ORDER BY d.seq), repeat('0', 64)) \
                            THEN 'prev_hash_mismatch' \
                        END AS broken \
                 FROM audit_events.domain_event AS d), \
             ranged AS ( \
                 SELECT * FROM chain \
                 WHERE ($1::timestamptz IS NULL OR recorded_at >= $1) \
                   AND ($2::timestamptz IS NULL OR recorded_at <= $2)) \
             SELECT count(*), \
                    (array_agg(entry_hash ORDER BY seq DESC))[1], \
                    (array_agg(event_id ORDER BY seq) FILTER (WHERE broken IS NOT NULL))[1], \
                    (array_agg(broken ORDER BY seq) FILTER (WHERE broken IS NOT NULL))[1] \
             FROM ranged",
            &[&from, &to],
        )
        .await?;
    let checked: i64 = row.try_get(0)?;
    Ok(Verification {
        checked: u64::try_from(checked).unwrap_or(0),
        head_hash: row.try_get(1)?,
        first_broken_event_id: row.try_get(2)?,
        reason: row.try_get(3)?,
    })
}

// ---------- the record of a read ----------

/// One read of either log, as its `AUDIT_READ` names it.
#[derive(Clone, Debug)]
pub struct AuditRead {
    pub resource: &'static str,
    pub filters: Vec<String>,
    pub subject: Option<(&'static str, String)>,
}

pub fn audit_unavailable() -> ApiError {
    ApiError::new(
        503,
        "audit_unavailable",
        "The read could not be recorded, so it was not served.",
    )
}

/// Record the read through `api_audit.record_event_v1`, in the read's
/// transaction. Any failure is 503 `audit_unavailable`, never a partial
/// answer: the caller then drops the transaction and returns no rows.
pub async fn record_audit_read<C: GenericClient + Sync>(
    client: &C,
    principal: &Principal,
    correlation_id: &str,
    read: &AuditRead,
) -> Result<(), ApiError> {
    let details = serde_json::json!({
        "resource": read.resource,
        "filters": read.filters,
    })
    .to_string();
    let (subject_type, subject_id) = match &read.subject {
        Some((kind, id)) => (Some(*kind), Some(id.as_str())),
        None => (None, None),
    };
    let correlation =
        (!correlation_id.is_empty() && correlation_id != "-").then_some(correlation_id);
    client
        .query_one(
            "SELECT created FROM api_audit.record_event_v1( \
                 $1, 'AUDIT_READ', 'SUCCESS', now(), 'USER', $2::text::uuid, $3, \
                 NULL::smallint, $4, $5, NULL::bigint, NULL::bigint, $6, $7::text::jsonb)",
            &[
                &Uuid::new_v4(),
                &principal.sub,
                &principal.role,
                &subject_type,
                &subject_id,
                &correlation,
                &details,
            ],
        )
        .await
        .map(|_| ())
        .map_err(|error| {
            tracing::error!(
                "AUDIT_READ not recorded: sqlstate={:?}",
                error.code().map(|code| code.code().to_owned())
            );
            audit_unavailable()
        })
}
