//! Consent, restrictions and the preference history (F21-17, RN-73, ADR-0066).
//!
//! Consent decisions and restriction decisions are appended, never edited or
//! removed: this module only ever INSERTs into the two logs, `svc_preferences`
//! holds no other privilege on them, and a trigger refuses the rest. A
//! decision is appended in the transaction that also commits its outbox row
//! (the capture trigger), so a rollback announces nothing and the published
//! views already show the change when the event is visible. The current state
//! of a purpose is its latest decision, and the state at any past instant is
//! the latest decision recorded at or before it.
//!
//! Reading the log or the restriction log of one named customer is itself
//! recorded as a `HISTORY_READ` domain event (ADR-0072) in the read's own
//! transaction, a 403 on it records one with outcome `REFUSED`, and a read
//! whose event cannot be recorded answers 503 `audit_unavailable` with no rows.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{header, Method, StatusCode};
use axum::middleware::Next;
use axum::response::Response;
use chrono::{DateTime, Utc};
use mosaiq_kit::auth::{Authenticator, JwksStore, StateRedis};
use mosaiq_kit::rendering::render_error;
use mosaiq_kit::{
    service_redis_info, ApiError, Ctx, Database, Detail, Format, KitConfig, Page, Principal, Reply,
    Representation, Value,
};
use serde_json::Value as JsonValue;
use tokio_postgres::{GenericClient, Row};
use uuid::Uuid;

use crate::{customer_not_found, path_customer, request_query, SUFFIX};

/// The first key of the two-key advisory lock that serializes the consent and
/// restriction decisions of one customer (contracts/db/advisory-locks.md), so
/// that `recorded_at` never falls as `decision_id` rises. It is not the
/// preference replace's lock, so a decision never waits on a replace.
pub const DECISION_LOCK_CLASS: i32 = 290_002;

/// The purposes in the order a consent state lists them.
pub const PURPOSES: [&str; 3] = ["PERSONALIZATION", "MARKETING_CONTACT", "PROFILING"];
const KINDS: [&str; 2] = ["CHANNEL", "INTEREST_CATEGORY"];
const PREFERENCE_SETS: [&str; 2] = ["PREFERRED_CHANNELS", "INTEREST_CATEGORIES"];

const LOCK_DECISIONS: &str = "SELECT pg_advisory_xact_lock($1, hashtext($2::uuid::text))";
const CUSTOMER_EXISTS: &str =
    "SELECT customer_id FROM api_customers.customer_ref_v1 WHERE customer_id = $1";
/// One statement, so one snapshot. The instant is `$2` or the database clock;
/// each purpose contributes its latest decision recorded at or before it.
const READ_CONSENT: &str =
    "WITH instant AS (SELECT COALESCE($2::timestamptz, clock_timestamp()) AS at) \
     SELECT i.at, ($2::timestamptz IS NOT NULL AND $2::timestamptz > clock_timestamp()), \
            d.purpose, d.decision, d.decision_id, d.recorded_at, d.recorded_by, d.source, d.basis \
     FROM api_customers.customer_ref_v1 AS r CROSS JOIN instant AS i \
     LEFT JOIN LATERAL ( \
         SELECT DISTINCT ON (c.purpose) c.purpose, c.decision, c.decision_id, c.recorded_at, \
                c.recorded_by, c.source, c.basis \
         FROM preferences.consent_decision AS c \
         WHERE c.customer_id = r.customer_id AND c.recorded_at <= i.at \
         ORDER BY c.purpose, c.decision_id DESC) AS d ON TRUE \
     WHERE r.customer_id = $1";
const INSERT_CONSENT: &str = "INSERT INTO preferences.consent_decision \
     (customer_id, purpose, decision, recorded_by, source, basis) \
     VALUES ($1, $2, $3, $4, 'STAFF_ON_BEHALF', 'EXPLICIT')";
const READ_RESTRICTIONS: &str = "SELECT \
     COALESCE((SELECT c.decision = 'GRANTED' FROM api_preferences.consent_state_v1 AS c \
               WHERE c.customer_id = r.customer_id AND c.purpose = 'PERSONALIZATION'), FALSE), \
     ARRAY(SELECT x.target_id FROM api_preferences.restriction_v1 AS x \
           WHERE x.customer_id = r.customer_id AND x.kind = 'CHANNEL' ORDER BY x.target_id), \
     ARRAY(SELECT x.target_id FROM api_preferences.restriction_v1 AS x \
           WHERE x.customer_id = r.customer_id AND x.kind = 'INTEREST_CATEGORY' \
           ORDER BY x.target_id) \
     FROM api_customers.customer_ref_v1 AS r WHERE r.customer_id = $1";
const INSERT_RESTRICTION_CHANNEL: &str = "INSERT INTO preferences.restriction_decision \
     (customer_id, kind, channel_id, decision, recorded_by, source) \
     VALUES ($1, 'CHANNEL', $2, $3, $4, 'STAFF_ON_BEHALF')";
const INSERT_RESTRICTION_CATEGORY: &str = "INSERT INTO preferences.restriction_decision \
     (customer_id, kind, category_id, decision, recorded_by, source) \
     VALUES ($1, 'INTEREST_CATEGORY', $2, $3, $4, 'STAFF_ON_BEHALF')";
const LIST_CONSENT: &str = "SELECT decision_id, customer_id, purpose, decision, recorded_at, \
            recorded_by, source, basis \
     FROM preferences.consent_decision \
     WHERE customer_id = $1 AND ($2::text IS NULL OR purpose = $2) \
     ORDER BY decision_id DESC LIMIT $3 OFFSET $4";
const COUNT_CONSENT: &str = "SELECT count(*) FROM preferences.consent_decision \
     WHERE customer_id = $1 AND ($2::text IS NULL OR purpose = $2)";
const LIST_RESTRICTIONS: &str = "SELECT decision_id, customer_id, kind, target_id, decision, \
            recorded_at, recorded_by, source \
     FROM preferences.restriction_decision \
     WHERE customer_id = $1 AND ($2::text IS NULL OR kind = $2) \
     ORDER BY decision_id DESC LIMIT $3 OFFSET $4";
const COUNT_RESTRICTIONS: &str = "SELECT count(*) FROM preferences.restriction_decision \
     WHERE customer_id = $1 AND ($2::text IS NULL OR kind = $2)";
const LIST_CHANGES: &str = "SELECT h.change_id, h.customer_id, h.preference_set, h.changed_at, \
            h.changed_by, \
            ARRAY(SELECT m.member_id FROM preferences.preference_change_member AS m \
                  WHERE m.change_id = h.change_id AND m.side = 'BEFORE' ORDER BY m.member_id), \
            ARRAY(SELECT m.member_id FROM preferences.preference_change_member AS m \
                  WHERE m.change_id = h.change_id AND m.side = 'AFTER' ORDER BY m.member_id) \
     FROM preferences.preference_change AS h \
     WHERE h.customer_id = $1 AND ($2::text IS NULL OR h.preference_set = $2) \
     ORDER BY h.change_id DESC LIMIT $3 OFFSET $4";
const COUNT_CHANGES: &str = "SELECT count(*) FROM preferences.preference_change \
     WHERE customer_id = $1 AND ($2::text IS NULL OR preference_set = $2)";

fn invalid_body(message: &str, details: Vec<Detail>) -> ApiError {
    ApiError::new(422, "invalid_body", message).with_details(details)
}

fn invalid_query(details: Vec<Detail>) -> ApiError {
    ApiError::new(422, "invalid_query", "A query parameter is malformed.").with_details(details)
}

fn invalid_token() -> ApiError {
    ApiError::new(401, "invalid_token", "The bearer token is invalid.")
}

/// The caller as the app user a decision is attributed to. A `sub` that is not
/// a user fails the foreign key on `recorded_by`, and is refused as a token.
fn actor_uuid(actor: &Principal) -> Result<Uuid, ApiError> {
    Uuid::parse_str(&actor.sub).map_err(|_| invalid_token())
}

fn actor(ctx: &Ctx) -> Result<&Principal, ApiError> {
    ctx.principal().ok_or_else(invalid_token)
}

fn constraint_of(error: &tokio_postgres::Error) -> Option<&str> {
    error
        .as_db_error()
        .filter(|database| database.code().code() == "23503")
        .and_then(|database| database.constraint())
}

/// A write failure by constraint name: a `sub` that is no user is 401, a
/// customer deleted meanwhile is 404, anything else stays the kit's 500 or 503.
pub(crate) fn write_error(error: tokio_postgres::Error) -> ApiError {
    match constraint_of(&error) {
        Some(name) if name.ends_with("_recorded_by_fkey") || name.ends_with("_changed_by_fkey") => {
            invalid_token()
        }
        Some(name) if name.ends_with("_customer_id_fkey") => customer_not_found(),
        _ => ApiError::from(error),
    }
}

pub fn audit_unavailable() -> ApiError {
    ApiError::new(
        503,
        "audit_unavailable",
        "The read could not be recorded, so it was not served.",
    )
}

fn stamp(moment: DateTime<Utc>) -> Value {
    Value::from(moment)
}

fn uuid_value(id: Option<Uuid>) -> Value {
    id.map_or(Value::Null, |id| Value::from(id.to_string()))
}

fn id_list(ids: &[i16]) -> Value {
    Value::List(ids.iter().copied().map(Value::from).collect())
}

// ------------------------------------------------------------- query rules

fn query_pairs(query: &str) -> HashMap<String, String> {
    let mut values = HashMap::new();
    for (key, value) in form_urlencoded::parse(query.as_bytes()) {
        values
            .entry(key.into_owned())
            .or_insert_with(|| value.into_owned());
    }
    values
}

/// `as_of`: an RFC 3339 instant with an offset, or none for now.
pub fn parse_as_of(query: &str) -> Result<Option<DateTime<Utc>>, ApiError> {
    match query_pairs(query).get("as_of") {
        None => Ok(None),
        Some(text) => DateTime::parse_from_rfc3339(text)
            .map(|moment| Some(moment.with_timezone(&Utc)))
            .map_err(|_| invalid_query(vec![Detail::new("as_of", "invalid_timestamp")])),
    }
}

/// A closed-vocabulary filter: its value, none when absent, or 422
/// `invalid_query` naming the parameter.
pub fn parse_filter(
    query: &str,
    name: &str,
    allowed: &[&'static str],
) -> Result<Option<&'static str>, ApiError> {
    match query_pairs(query).get(name) {
        None => Ok(None),
        Some(text) => allowed
            .iter()
            .copied()
            .find(|candidate| candidate == text)
            .map(Some)
            .ok_or_else(|| invalid_query(vec![Detail::new(name, "not_allowed")])),
    }
}

// ---------------------------------------------------------------- consent

/// One purpose's state. `decision` is `None` for `NOT_RECORDED`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PurposeState {
    pub purpose: &'static str,
    pub decision: Option<Recorded>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Recorded {
    pub decision: String,
    pub decision_id: i64,
    pub recorded_at: DateTime<Utc>,
    pub recorded_by: Option<Uuid>,
    pub source: String,
    pub basis: String,
}

/// The state of the three purposes at `as_of`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ConsentState {
    pub customer_id: Uuid,
    pub as_of: DateTime<Utc>,
    pub purposes: Vec<PurposeState>,
}

impl ConsentState {
    pub fn is_granted(&self, purpose: &str) -> bool {
        self.purposes.iter().any(|state| {
            state.purpose == purpose
                && state
                    .decision
                    .as_ref()
                    .is_some_and(|recorded| recorded.decision == "GRANTED")
        })
    }

    pub fn representation(&self) -> Representation {
        let purposes = self
            .purposes
            .iter()
            .map(|state| match &state.decision {
                Some(recorded) => Value::object([
                    ("purpose", Value::from(state.purpose)),
                    ("state", Value::from(recorded.decision.clone())),
                    ("decision_id", Value::from(recorded.decision_id)),
                    ("recorded_at", stamp(recorded.recorded_at)),
                    ("recorded_by", uuid_value(recorded.recorded_by)),
                    ("source", Value::from(recorded.source.clone())),
                    ("basis", Value::from(recorded.basis.clone())),
                ]),
                None => Value::object([
                    ("purpose", Value::from(state.purpose)),
                    ("state", Value::from("NOT_RECORDED")),
                    ("decision_id", Value::Null),
                    ("recorded_at", Value::Null),
                    ("recorded_by", Value::Null),
                    ("source", Value::Null),
                    ("basis", Value::Null),
                ]),
            })
            .collect();
        Representation::new(
            "ConsentState",
            [
                ("customer_id", Value::from(self.customer_id.to_string())),
                ("as_of", stamp(self.as_of)),
                ("purposes", Value::List(purposes)),
            ],
        )
    }
}

/// The consent state of a customer at `as_of` (now when none), in the
/// caller's snapshot. 404 when the customer does not exist, 422
/// `in_the_future` when `as_of` is after the database clock.
pub async fn read_consent<C: GenericClient + Sync>(
    client: &C,
    customer_id: Uuid,
    as_of: Option<DateTime<Utc>>,
) -> Result<ConsentState, ApiError> {
    let rows = client.query(READ_CONSENT, &[&customer_id, &as_of]).await?;
    let first = rows.first().ok_or_else(customer_not_found)?;
    let instant: DateTime<Utc> = first.try_get(0)?;
    if first.try_get::<_, bool>(1)? {
        return Err(invalid_query(vec![Detail::new("as_of", "in_the_future")]));
    }
    let mut recorded: HashMap<String, Recorded> = HashMap::new();
    for row in &rows {
        let purpose: Option<String> = row.try_get(2)?;
        if let Some(purpose) = purpose {
            recorded.insert(
                purpose,
                Recorded {
                    decision: row.try_get(3)?,
                    decision_id: row.try_get(4)?,
                    recorded_at: row.try_get(5)?,
                    recorded_by: row.try_get(6)?,
                    source: row.try_get(7)?,
                    basis: row.try_get(8)?,
                },
            );
        }
    }
    Ok(ConsentState {
        customer_id,
        as_of: instant,
        purposes: PURPOSES
            .iter()
            .map(|purpose| PurposeState {
                purpose,
                decision: recorded.remove(*purpose),
            })
            .collect(),
    })
}

/// The consent entries of a request body that already passed the contract's
/// schema, with the rule a schema cannot state: a purpose appears at most once.
pub fn parse_consent_entries(body: &JsonValue) -> Result<Vec<(String, String)>, ApiError> {
    let mut seen = HashSet::new();
    let mut entries = Vec::new();
    let mut details = Vec::new();
    for (index, item) in body["decisions"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .enumerate()
    {
        let purpose = item["purpose"].as_str().unwrap_or_default().to_owned();
        let decision = item["decision"].as_str().unwrap_or_default().to_owned();
        if !seen.insert(purpose.clone()) {
            details.push(Detail::new(
                format!("decisions[{index}].purpose"),
                "duplicate",
            ));
        }
        entries.push((purpose, decision));
    }
    if details.is_empty() {
        Ok(entries)
    } else {
        Err(invalid_body(
            "A purpose appears twice in the list of decisions.",
            details,
        ))
    }
}

/// Append the decisions as `actor`, all or nothing, and return the state they
/// leave. Each entry becomes one row even when it equals the current state,
/// which is how a programme default becomes an explicit decision.
pub async fn record_consent(
    db: &Database,
    actor: &Principal,
    customer_id: Uuid,
    entries: &[(String, String)],
) -> Result<ConsentState, ApiError> {
    let recorded_by = actor_uuid(actor)?;
    let mut connection = db.connection().await?;
    let transaction = connection.transaction(Some(actor)).await?;
    transaction
        .query_opt(CUSTOMER_EXISTS, &[&customer_id])
        .await?
        .ok_or_else(customer_not_found)?;
    transaction
        .execute(LOCK_DECISIONS, &[&DECISION_LOCK_CLASS, &customer_id])
        .await?;
    for (purpose, decision) in entries {
        transaction
            .execute(
                INSERT_CONSENT,
                &[&customer_id, purpose, decision, &recorded_by],
            )
            .await
            .map_err(write_error)?;
    }
    // ADR-0066: a customer who does not grant PROFILING keeps no inferred
    // interest, so the rows go in the transaction that records the decision.
    crate::inference::forget_unless_profiling(&*transaction, customer_id).await?;
    let state = read_consent(&*transaction, customer_id, None).await?;
    transaction.commit().await.map_err(write_error)?;
    Ok(state)
}

// ----------------------------------------------------------- restrictions

/// The effect of a customer's choices: whether personalization is allowed and
/// what they do not want done.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Restrictions {
    pub customer_id: Uuid,
    pub personalization_allowed: bool,
    pub channel_ids: Vec<i16>,
    pub category_ids: Vec<i16>,
}

impl Restrictions {
    pub fn representation(&self) -> Representation {
        Representation::new(
            "RestrictionProfile",
            [
                ("customer_id", Value::from(self.customer_id.to_string())),
                (
                    "personalization_allowed",
                    Value::from(self.personalization_allowed),
                ),
                ("restricted_channel_ids", id_list(&self.channel_ids)),
                ("restricted_category_ids", id_list(&self.category_ids)),
            ],
        )
    }
}

/// The current restrictions of a customer, read through the published views;
/// 404 when the customer does not exist.
pub async fn read_restrictions<C: GenericClient + Sync>(
    client: &C,
    customer_id: Uuid,
) -> Result<Restrictions, ApiError> {
    let row = client
        .query_opt(READ_RESTRICTIONS, &[&customer_id])
        .await?
        .ok_or_else(customer_not_found)?;
    Ok(Restrictions {
        customer_id,
        personalization_allowed: row.try_get(0)?,
        channel_ids: row.try_get(1)?,
        category_ids: row.try_get(2)?,
    })
}

/// One entry of a restriction request: `target` is `None` when the id is
/// outside the `smallint` key range, which no channel or category can have.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RestrictionEntry {
    pub kind: &'static str,
    pub target: Option<i16>,
    pub decision: String,
}

/// The restriction entries of a body that already passed the contract's
/// schema, with the rule a schema cannot state: a `(kind, target_id)` pair
/// appears at most once.
pub fn parse_restriction_entries(body: &JsonValue) -> Result<Vec<RestrictionEntry>, ApiError> {
    let mut seen = HashSet::new();
    let mut entries = Vec::new();
    let mut details = Vec::new();
    for (index, item) in body["decisions"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .enumerate()
    {
        let kind = KINDS
            .iter()
            .copied()
            .find(|candidate| Some(*candidate) == item["kind"].as_str())
            .unwrap_or("CHANNEL");
        let raw = item["target_id"].as_i64();
        if !seen.insert((kind, raw)) {
            details.push(Detail::new(format!("decisions[{index}]"), "duplicate"));
        }
        entries.push(RestrictionEntry {
            kind,
            target: raw.and_then(|id| i16::try_from(id).ok()),
            decision: item["decision"].as_str().unwrap_or_default().to_owned(),
        });
    }
    if details.is_empty() {
        Ok(entries)
    } else {
        Err(invalid_body(
            "The same restriction appears twice in the list of decisions.",
            details,
        ))
    }
}

fn unknown_target(kind: &str, index: usize) -> ApiError {
    let (code, message) = if kind == "CHANNEL" {
        ("unknown_channel", "A channel id references no channel.")
    } else {
        ("unknown_category", "A category id references no category.")
    };
    ApiError::new(422, code, message).with_details(vec![Detail::new(
        format!("decisions[{index}].target_id"),
        "unknown",
    )])
}

/// Append the restriction decisions as `actor`, all or nothing, and return the
/// current restrictions. The first entry that names no channel or category is
/// the one the 422 names, and nothing is appended.
pub async fn record_restrictions(
    db: &Database,
    actor: &Principal,
    customer_id: Uuid,
    entries: &[RestrictionEntry],
) -> Result<Restrictions, ApiError> {
    let recorded_by = actor_uuid(actor)?;
    let mut connection = db.connection().await?;
    let transaction = connection.transaction(Some(actor)).await?;
    transaction
        .query_opt(CUSTOMER_EXISTS, &[&customer_id])
        .await?
        .ok_or_else(customer_not_found)?;
    transaction
        .execute(LOCK_DECISIONS, &[&DECISION_LOCK_CLASS, &customer_id])
        .await?;
    for (index, entry) in entries.iter().enumerate() {
        let Some(target) = entry.target else {
            return Err(unknown_target(entry.kind, index));
        };
        let statement = if entry.kind == "CHANNEL" {
            INSERT_RESTRICTION_CHANNEL
        } else {
            INSERT_RESTRICTION_CATEGORY
        };
        transaction
            .execute(
                statement,
                &[&customer_id, &target, &entry.decision, &recorded_by],
            )
            .await
            .map_err(|error| match constraint_of(&error) {
                Some("restriction_decision_channel_id_fkey")
                | Some("restriction_decision_category_id_fkey") => {
                    unknown_target(entry.kind, index)
                }
                _ => write_error(error),
            })?;
    }
    let state = read_restrictions(&*transaction, customer_id).await?;
    transaction.commit().await.map_err(write_error)?;
    Ok(state)
}

// -------------------------------------------------------- history of reads

/// Record one `HISTORY_READ` of a customer's consent history, naming who,
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
                 '{\"history\": \"CONSENT\"}'::jsonb)",
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

/// Which of the two logs a logged read lists.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Log {
    Consent,
    Restriction,
}

fn offset(page: u32, size: u32) -> (i64, i64) {
    (i64::from(size), i64::from(page - 1) * i64::from(size))
}

fn consent_row(row: &Row) -> Result<IndexMapRow, ApiError> {
    Ok(object([
        ("decision_id", Value::from(row.try_get::<_, i64>(0)?)),
        (
            "customer_id",
            Value::from(row.try_get::<_, Uuid>(1)?.to_string()),
        ),
        ("purpose", Value::from(row.try_get::<_, String>(2)?)),
        ("decision", Value::from(row.try_get::<_, String>(3)?)),
        ("recorded_at", stamp(row.try_get(4)?)),
        ("recorded_by", uuid_value(row.try_get(5)?)),
        ("source", Value::from(row.try_get::<_, String>(6)?)),
        ("basis", Value::from(row.try_get::<_, String>(7)?)),
    ]))
}

fn restriction_row(row: &Row) -> Result<IndexMapRow, ApiError> {
    Ok(object([
        ("decision_id", Value::from(row.try_get::<_, i64>(0)?)),
        (
            "customer_id",
            Value::from(row.try_get::<_, Uuid>(1)?.to_string()),
        ),
        ("kind", Value::from(row.try_get::<_, String>(2)?)),
        ("target_id", Value::from(row.try_get::<_, i16>(3)?)),
        ("decision", Value::from(row.try_get::<_, String>(4)?)),
        ("recorded_at", stamp(row.try_get(5)?)),
        ("recorded_by", uuid_value(row.try_get(6)?)),
        ("source", Value::from(row.try_get::<_, String>(7)?)),
    ]))
}

type IndexMapRow = indexmap::IndexMap<String, Value>;

fn object<const N: usize>(pairs: [(&str, Value); N]) -> IndexMapRow {
    pairs
        .into_iter()
        .map(|(key, value)| (key.to_owned(), value))
        .collect()
}

/// One page of a customer's consent or restriction log, newest first, with
/// its `HISTORY_READ` recorded first in the same transaction: no row leaves
/// without the event, and an unknown customer's 404 rolls the event back.
#[allow(clippy::too_many_arguments)]
pub async fn read_logged_log(
    db: &Database,
    principal: &Principal,
    correlation_id: &str,
    customer: Uuid,
    log: Log,
    filter: Option<&str>,
    page: u32,
    size: u32,
) -> Result<(Vec<IndexMapRow>, u64), ApiError> {
    let mut connection = db.connection().await?;
    let transaction = connection.transaction(Some(principal)).await?;
    record_history_read(
        &*transaction,
        principal,
        correlation_id,
        customer,
        "SUCCESS",
    )
    .await?;
    transaction
        .query_opt(CUSTOMER_EXISTS, &[&customer])
        .await?
        .ok_or_else(customer_not_found)?;
    let (limit, skip) = offset(page, size);
    let (list, count) = match log {
        Log::Consent => (LIST_CONSENT, COUNT_CONSENT),
        Log::Restriction => (LIST_RESTRICTIONS, COUNT_RESTRICTIONS),
    };
    let total: i64 = transaction
        .query_one(count, &[&customer, &filter])
        .await?
        .try_get(0)?;
    let rows = transaction
        .query(list, &[&customer, &filter, &limit, &skip])
        .await?;
    let items = rows
        .iter()
        .map(|row| match log {
            Log::Consent => consent_row(row),
            Log::Restriction => restriction_row(row),
        })
        .collect::<Result<Vec<_>, _>>()?;
    transaction
        .commit()
        .await
        .map_err(|_| audit_unavailable())?;
    Ok((items, total as u64))
}

/// One page of a customer's preference history, newest first.
pub async fn read_changes(
    db: &Database,
    customer: Uuid,
    filter: Option<&str>,
    page: u32,
    size: u32,
) -> Result<(Vec<IndexMapRow>, u64), ApiError> {
    let connection = db.connection().await?;
    connection
        .query_opt(CUSTOMER_EXISTS, &[&customer])
        .await?
        .ok_or_else(customer_not_found)?;
    let (limit, skip) = offset(page, size);
    let total: i64 = connection
        .query_one(COUNT_CHANGES, &[&customer, &filter])
        .await?
        .try_get(0)?;
    let rows = connection
        .query(LIST_CHANGES, &[&customer, &filter, &limit, &skip])
        .await?;
    let items = rows
        .iter()
        .map(|row| {
            Ok(object([
                ("change_id", Value::from(row.try_get::<_, i64>(0)?)),
                (
                    "customer_id",
                    Value::from(row.try_get::<_, Uuid>(1)?.to_string()),
                ),
                ("preference_set", Value::from(row.try_get::<_, String>(2)?)),
                ("before_ids", id_list(&row.try_get::<_, Vec<i16>>(5)?)),
                ("after_ids", id_list(&row.try_get::<_, Vec<i16>>(6)?)),
                ("changed_at", stamp(row.try_get(3)?)),
                (
                    "changed_by",
                    Value::from(row.try_get::<_, Uuid>(4)?.to_string()),
                ),
            ]))
        })
        .collect::<Result<Vec<_>, ApiError>>()?;
    Ok((items, total as u64))
}

// ---------------------------------------------------------------- handlers

pub async fn get_consent_state(ctx: Ctx) -> Result<Reply, ApiError> {
    let customer = path_customer(&ctx)?;
    let as_of = parse_as_of(&request_query())?;
    let connection = ctx.db().connection().await?;
    let state = read_consent(&***connection, customer, as_of).await?;
    Ok(state.representation().into())
}

pub async fn record_consent_decisions(ctx: Ctx) -> Result<Reply, ApiError> {
    let customer = path_customer(&ctx)?;
    let body = ctx.body.as_ref().expect("contract requires a body");
    let entries = parse_consent_entries(body)?;
    let state = record_consent(ctx.db(), actor(&ctx)?, customer, &entries).await?;
    Ok(state.representation().status(201).into())
}

pub async fn get_restriction_profile(ctx: Ctx) -> Result<Reply, ApiError> {
    let customer = path_customer(&ctx)?;
    let connection = ctx.db().connection().await?;
    let state = read_restrictions(&***connection, customer).await?;
    Ok(state.representation().into())
}

pub async fn record_restriction_decisions(ctx: Ctx) -> Result<Reply, ApiError> {
    let customer = path_customer(&ctx)?;
    let body = ctx.body.as_ref().expect("contract requires a body");
    let entries = parse_restriction_entries(body)?;
    let state = record_restrictions(ctx.db(), actor(&ctx)?, customer, &entries).await?;
    Ok(state.representation().status(201).into())
}

pub async fn list_consent_decisions(ctx: Ctx) -> Result<Reply, ApiError> {
    let customer = path_customer(&ctx)?;
    let filter = parse_filter(&request_query(), "purpose", &PURPOSES)?;
    let (items, total) = read_logged_log(
        ctx.db(),
        actor(&ctx)?,
        &ctx.correlation_id,
        customer,
        Log::Consent,
        filter,
        ctx.page,
        ctx.size,
    )
    .await?;
    Ok(Page::new(
        "ConsentDecisions",
        "ConsentDecision",
        "consent_decisions",
        items,
        ctx.page,
        total,
    )
    .size(ctx.size)
    .into())
}

pub async fn list_restriction_decisions(ctx: Ctx) -> Result<Reply, ApiError> {
    let customer = path_customer(&ctx)?;
    let filter = parse_filter(&request_query(), "kind", &KINDS)?;
    let (items, total) = read_logged_log(
        ctx.db(),
        actor(&ctx)?,
        &ctx.correlation_id,
        customer,
        Log::Restriction,
        filter,
        ctx.page,
        ctx.size,
    )
    .await?;
    Ok(Page::new(
        "RestrictionDecisions",
        "RestrictionDecision",
        "restriction_decisions",
        items,
        ctx.page,
        total,
    )
    .size(ctx.size)
    .into())
}

pub async fn list_preference_changes(ctx: Ctx) -> Result<Reply, ApiError> {
    let customer = path_customer(&ctx)?;
    let filter = parse_filter(&request_query(), "preference_set", &PREFERENCE_SETS)?;
    let (items, total) = read_changes(ctx.db(), customer, filter, ctx.page, ctx.size).await?;
    Ok(Page::new(
        "PreferenceChanges",
        "PreferenceChange",
        "preference_changes",
        items,
        ctx.page,
        total,
    )
    .size(ctx.size)
    .into())
}

// ------------------------------------------------------------ refusal log

/// What the refusal layer needs: the kit's own verification of the token, and
/// the database to record in.
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

/// The customer of `GET /v1/preference-profiles/{id}/consent-decisions` or
/// `.../restriction-decisions`: the two reads that are logged.
pub fn logged_read_customer(method: &Method, path: &str) -> Option<Uuid> {
    if method != Method::GET {
        return None;
    }
    let rest = path.strip_prefix("/v1/preference-profiles/")?;
    let (id, tail) = rest.split_once('/')?;
    if !matches!(tail, "consent-decisions" | "restriction-decisions") || id.len() != 36 {
        return None;
    }
    Uuid::parse_str(id).ok()
}

/// A 403 on a customer's consent or restriction log is itself a logged read,
/// with outcome `REFUSED`. The kit refuses the role before any handler runs,
/// so the refusal is recorded around the router; when it cannot be, the answer
/// is 503, never a plain 403.
pub async fn log_refusals(
    State(log): State<Arc<RefusalLog>>,
    request: Request<Body>,
    next: Next,
) -> Response {
    let customer = logged_read_customer(request.method(), request.uri().path());
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

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn as_of_is_an_rfc_3339_instant_or_absent() {
        assert_eq!(parse_as_of("").unwrap(), None);
        let moment = parse_as_of("as_of=2026-11-05T14%3A30%3A00%2B02%3A00")
            .unwrap()
            .unwrap();
        assert_eq!(moment.to_rfc3339(), "2026-11-05T12:30:00+00:00");
        assert_eq!(
            parse_as_of("as_of=2026-11-05T14:30:00Z").unwrap().unwrap(),
            "2026-11-05T14:30:00Z".parse::<DateTime<Utc>>().unwrap()
        );
        for bad in [
            "as_of=",
            "as_of=yesterday",
            "as_of=2026-11-05",
            "as_of=2026-11-05T14:30:00",
        ] {
            let error = parse_as_of(bad).unwrap_err();
            assert_eq!(
                (error.status, error.code.as_str()),
                (422, "invalid_query"),
                "{bad}"
            );
            assert_eq!(
                error.details,
                [Detail::new("as_of", "invalid_timestamp")],
                "{bad}"
            );
        }
    }

    #[test]
    fn a_filter_is_one_of_its_values() {
        assert_eq!(parse_filter("", "purpose", &PURPOSES).unwrap(), None);
        assert_eq!(
            parse_filter("purpose=PROFILING&page=2", "purpose", &PURPOSES).unwrap(),
            Some("PROFILING")
        );
        for bad in ["purpose=", "purpose=profiling", "purpose=ALL"] {
            let error = parse_filter(bad, "purpose", &PURPOSES).unwrap_err();
            assert_eq!((error.status, error.code.as_str()), (422, "invalid_query"));
            assert_eq!(error.details, [Detail::new("purpose", "not_allowed")]);
        }
    }

    #[test]
    fn a_purpose_appears_once_and_a_restriction_once() {
        let ok = json!({"decisions": [
            {"purpose": "PROFILING", "decision": "WITHDRAWN"},
            {"purpose": "PERSONALIZATION", "decision": "GRANTED"}]});
        assert_eq!(parse_consent_entries(&ok).unwrap().len(), 2);
        let twice = json!({"decisions": [
            {"purpose": "PROFILING", "decision": "WITHDRAWN"},
            {"purpose": "PROFILING", "decision": "GRANTED"}]});
        let error = parse_consent_entries(&twice).unwrap_err();
        assert_eq!(
            error.details,
            [Detail::new("decisions[1].purpose", "duplicate")]
        );

        let restrictions = json!({"decisions": [
            {"kind": "CHANNEL", "target_id": 5, "decision": "IMPOSED"},
            {"kind": "INTEREST_CATEGORY", "target_id": 5, "decision": "IMPOSED"},
            {"kind": "CHANNEL", "target_id": 5, "decision": "LIFTED"},
            {"kind": "CHANNEL", "target_id": 70000, "decision": "IMPOSED"}]});
        let error = parse_restriction_entries(&restrictions).unwrap_err();
        assert_eq!(error.details, [Detail::new("decisions[2]", "duplicate")]);
        let distinct = json!({"decisions": [
            {"kind": "CHANNEL", "target_id": 5, "decision": "IMPOSED"},
            {"kind": "CHANNEL", "target_id": 70000, "decision": "IMPOSED"}]});
        let entries = parse_restriction_entries(&distinct).unwrap();
        assert_eq!(entries[0].target, Some(5));
        assert_eq!(entries[1].target, None, "outside the key range");
    }

    #[test]
    fn only_the_two_logged_reads_are_recognised() {
        let id = "7b3ad781-c5de-4f40-ae50-1fa825ce2b25";
        for tail in ["consent-decisions", "restriction-decisions"] {
            let path = format!("/v1/preference-profiles/{id}/{tail}");
            assert_eq!(
                logged_read_customer(&Method::GET, &path),
                Some(Uuid::parse_str(id).unwrap())
            );
            assert_eq!(logged_read_customer(&Method::POST, &path), None);
        }
        for path in [
            format!("/v1/preference-profiles/{id}/consent"),
            format!("/v1/preference-profiles/{id}/preference-changes"),
            format!("/v1/preference-profiles/{id}"),
            "/v1/preference-profiles/not-a-uuid/consent-decisions".to_owned(),
        ] {
            assert_eq!(logged_read_customer(&Method::GET, &path), None, "{path}");
        }
    }
}
