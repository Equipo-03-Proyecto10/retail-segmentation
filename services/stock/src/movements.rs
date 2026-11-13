//! Stock movements (F21-06, ADR-0061): the one write beyond a sale and its
//! reversal. A request is validated here, in the contract's words, and applied
//! by `stock.record_movement`, which changes `inventory` and appends the
//! movement row(s) in one transaction. Every statement is parameterized.

use std::collections::HashMap;

use chrono::{DateTime, Utc};
use indexmap::IndexMap;
use mosaiq_kit::{ApiError, Detail, Value};
use serde_json::Value as JsonValue;
use tokio_postgres::{GenericClient, Row};
use uuid::Uuid;

const REASONS: [&str; 4] = ["DAMAGE", "LOSS", "EXPIRY", "CORRECTION"];
const TYPES: [&str; 5] = ["RECEIPT", "TRANSFER", "COUNT", "ADJUSTMENT", "RESTOCK"];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MovementType {
    Receipt,
    Transfer,
    Count,
    Adjustment,
}

impl MovementType {
    pub fn code(self) -> &'static str {
        match self {
            Self::Receipt => "RECEIPT",
            Self::Transfer => "TRANSFER",
            Self::Count => "COUNT",
            Self::Adjustment => "ADJUSTMENT",
        }
    }
}

/// A validated `StockMovementRequest`. `store_id` is the origin of a transfer,
/// and `quantity` is the signed delta of an adjustment.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MovementRequest {
    pub movement_type: MovementType,
    pub store_id: i16,
    pub destination_store_id: Option<i16>,
    pub product_id: i32,
    pub quantity: i32,
    pub reference: Option<String>,
    pub reason: Option<String>,
    pub counted_by: Option<String>,
    pub note: Option<String>,
}

impl MovementRequest {
    /// The caller's `sub` and the movement's fields in one fixed form, so a
    /// JSON retry and an XML retry of the same movement hash the same.
    pub fn canonical(&self, sub: &Uuid) -> String {
        serde_json::json!([
            "v1",
            sub.to_string(),
            self.movement_type.code(),
            self.store_id,
            self.destination_store_id,
            self.product_id,
            self.quantity,
            self.reference,
            self.reason,
            self.counted_by,
            self.note,
        ])
        .to_string()
    }
}

/// One stored movement row.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Movement {
    pub movement_id: i64,
    pub movement_type: String,
    pub store_id: i16,
    pub product_id: i32,
    pub quantity_delta: i32,
    pub quantity_before: i32,
    pub quantity_after: i32,
    pub transfer_id: Option<Uuid>,
    pub reference: Option<String>,
    pub reason: Option<String>,
    pub counted_by: Option<String>,
    pub note: Option<String>,
    pub reversal_id: Option<i64>,
    pub recorded_by: Option<Uuid>,
    pub recorded_at: DateTime<Utc>,
}

/// The columns of a movement, as a literal so a statement is built at compile
/// time and never from a runtime string.
macro_rules! columns {
    () => {
        "movement_id, movement_type, store_id, product_id, quantity_delta, quantity_before, \
         quantity_after, transfer_id, reference, reason, counted_by, note, reversal_id, \
         recorded_by, recorded_at"
    };
}

macro_rules! filter {
    () => {
        "WHERE ($1::integer IS NULL OR store_id = $1) \
           AND ($2::integer IS NULL OR product_id = $2) \
           AND ($3::text IS NULL OR movement_type = $3) \
           AND ($4::uuid IS NULL OR transfer_id = $4) \
           AND ($5::timestamptz IS NULL OR recorded_at >= $5) \
           AND ($6::timestamptz IS NULL OR recorded_at < $6)"
    };
}

impl Movement {
    fn from_row(row: &Row) -> Result<Self, tokio_postgres::Error> {
        Ok(Self {
            movement_id: row.try_get(0)?,
            movement_type: row.try_get(1)?,
            store_id: row.try_get(2)?,
            product_id: row.try_get(3)?,
            quantity_delta: row.try_get(4)?,
            quantity_before: row.try_get(5)?,
            quantity_after: row.try_get(6)?,
            transfer_id: row.try_get(7)?,
            reference: row.try_get(8)?,
            reason: row.try_get(9)?,
            counted_by: row.try_get(10)?,
            note: row.try_get(11)?,
            reversal_id: row.try_get(12)?,
            recorded_by: row.try_get(13)?,
            recorded_at: row.try_get(14)?,
        })
    }

    pub fn representation(&self) -> IndexMap<String, Value> {
        let text = |value: &Option<String>| Value::from(value.clone());
        IndexMap::from([
            ("movement_id".to_owned(), Value::from(self.movement_id)),
            (
                "movement_type".to_owned(),
                Value::from(self.movement_type.clone()),
            ),
            ("store_id".to_owned(), Value::from(self.store_id)),
            ("product_id".to_owned(), Value::from(self.product_id)),
            (
                "quantity_delta".to_owned(),
                Value::from(self.quantity_delta),
            ),
            (
                "quantity_before".to_owned(),
                Value::from(self.quantity_before),
            ),
            (
                "quantity_after".to_owned(),
                Value::from(self.quantity_after),
            ),
            (
                "transfer_id".to_owned(),
                Value::from(self.transfer_id.map(|id| id.to_string())),
            ),
            ("reference".to_owned(), text(&self.reference)),
            ("reason".to_owned(), text(&self.reason)),
            ("counted_by".to_owned(), text(&self.counted_by)),
            ("note".to_owned(), text(&self.note)),
            ("reversal_id".to_owned(), Value::from(self.reversal_id)),
            (
                "recorded_by".to_owned(),
                Value::from(self.recorded_by.map(|id| id.to_string())),
            ),
            ("recorded_at".to_owned(), Value::from(self.recorded_at)),
        ])
    }
}

/// The list's filters, each optional.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct MovementFilters {
    pub store_id: Option<i32>,
    pub product_id: Option<i32>,
    pub movement_type: Option<String>,
    pub transfer_id: Option<Uuid>,
    pub from: Option<DateTime<Utc>>,
    pub to: Option<DateTime<Utc>>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MovementPage {
    pub movements: Vec<Movement>,
    pub total: u64,
}

// ---------- errors ----------

fn refusal(status: u16, code: &str, message: &str, field: &str, detail: &str) -> ApiError {
    ApiError::new(status, code, message).with_details(vec![Detail::new(field, detail)])
}

fn invalid_body(field: &str, detail: &str) -> ApiError {
    refusal(
        422,
        "invalid_body",
        "The movement violates its schema.",
        field,
        detail,
    )
}

fn invalid_quantity(detail: &str) -> ApiError {
    refusal(
        422,
        "invalid_quantity",
        "The quantity is not valid for this movement type.",
        "quantity",
        detail,
    )
}

fn unknown_store(field: &str) -> ApiError {
    refusal(
        422,
        "unknown_store",
        "No store has that id.",
        field,
        "unknown",
    )
}

fn unknown_product() -> ApiError {
    refusal(
        422,
        "unknown_product",
        "No product has that id.",
        "product_id",
        "unknown",
    )
}

fn invalid_token() -> ApiError {
    ApiError::new(401, "invalid_token", "The bearer token is invalid.")
}

pub fn idempotency_key_required() -> ApiError {
    refusal(
        400,
        "idempotency_key_required",
        "The Idempotency-Key header is missing or is not a UUID.",
        "Idempotency-Key",
        "required",
    )
}

pub fn stock_movement_not_found(movement_id: Option<i64>) -> ApiError {
    let message = match movement_id {
        Some(id) => format!("No stock movement exists with id {id}."),
        None => "The stock movement does not exist.".to_owned(),
    };
    ApiError::new(404, "stock_movement_not_found", message)
}

// ---------- reading the body ----------

fn optional_int(body: &JsonValue, name: &str) -> Result<Option<i64>, ApiError> {
    match body.get(name) {
        None | Some(JsonValue::Null) => Ok(None),
        Some(value) => value
            .as_i64()
            .map(Some)
            .ok_or_else(|| invalid_body(name, "invalid")),
    }
}

fn optional_text(body: &JsonValue, name: &str) -> Result<Option<String>, ApiError> {
    match body.get(name) {
        None | Some(JsonValue::Null) => Ok(None),
        Some(JsonValue::String(text)) => Ok(Some(text.trim().to_owned())),
        Some(_) => Err(invalid_body(name, "invalid")),
    }
}

fn required_store(body: &JsonValue, name: &str) -> Result<i16, ApiError> {
    let id = optional_int(body, name)?.ok_or_else(|| invalid_body(name, "required"))?;
    i16::try_from(id)
        .ok()
        .filter(|id| *id > 0)
        .ok_or_else(|| unknown_store(name))
}

fn quantity(value: Option<i64>, minimum: i64, detail: &str) -> Result<i32, ApiError> {
    let value = value.ok_or_else(|| invalid_quantity("required"))?;
    if value < minimum {
        return Err(invalid_quantity(detail));
    }
    i32::try_from(value).map_err(|_| invalid_quantity("out_of_range"))
}

fn required_text(code: &str, message: &str, field: &str) -> ApiError {
    refusal(422, code, message, field, "required")
}

/// Validate one `StockMovementRequest`, in the contract's words.
pub fn parse_movement(body: &JsonValue) -> Result<MovementRequest, ApiError> {
    let kind = body
        .get("movement_type")
        .and_then(JsonValue::as_str)
        .ok_or_else(|| invalid_body("movement_type", "required"))?;
    let movement_type = match kind {
        "RECEIPT" => MovementType::Receipt,
        "TRANSFER" => MovementType::Transfer,
        "COUNT" => MovementType::Count,
        "ADJUSTMENT" => MovementType::Adjustment,
        // RESTOCK is written by api_stock.restock_v1 and is never requested.
        _ => return Err(invalid_body("movement_type", "unknown_movement_type")),
    };
    let product_id =
        optional_int(body, "product_id")?.ok_or_else(|| invalid_body("product_id", "required"))?;
    let product_id = i32::try_from(product_id)
        .ok()
        .filter(|id| *id > 0)
        .ok_or_else(unknown_product)?;
    let note = optional_text(body, "note")?.filter(|note| !note.is_empty());
    let mut request = MovementRequest {
        movement_type,
        store_id: 0,
        destination_store_id: None,
        product_id,
        quantity: 0,
        reference: None,
        reason: None,
        counted_by: None,
        note,
    };
    match movement_type {
        MovementType::Receipt => {
            request.store_id = required_store(body, "store_id")?;
            request.quantity = quantity(optional_int(body, "quantity")?, 1, "must_be_positive")?;
            let reference = optional_text(body, "reference")?.filter(|text| !text.is_empty());
            request.reference = Some(reference.ok_or_else(|| {
                required_text(
                    "reference_required",
                    "A receipt must state its reference.",
                    "reference",
                )
            })?);
        }
        MovementType::Transfer => {
            request.store_id = required_store(body, "origin_store_id")?;
            let destination = required_store(body, "destination_store_id")?;
            if destination == request.store_id {
                return Err(refusal(
                    422,
                    "same_store_transfer",
                    "A transfer needs two different stores.",
                    "destination_store_id",
                    "same_as_origin",
                ));
            }
            request.destination_store_id = Some(destination);
            request.quantity = quantity(optional_int(body, "quantity")?, 1, "must_be_positive")?;
        }
        MovementType::Count => {
            request.store_id = required_store(body, "store_id")?;
            request.quantity =
                quantity(optional_int(body, "quantity")?, 0, "must_not_be_negative")?;
            let counted_by = optional_text(body, "counted_by")?.filter(|text| !text.is_empty());
            request.counted_by = Some(counted_by.ok_or_else(|| {
                required_text(
                    "counted_by_required",
                    "A count must state who counted.",
                    "counted_by",
                )
            })?);
        }
        MovementType::Adjustment => {
            request.store_id = required_store(body, "store_id")?;
            let delta = optional_int(body, "quantity_delta")?
                .ok_or_else(|| invalid_quantity("required"))?;
            if delta == 0 {
                return Err(invalid_quantity("must_be_non_zero"));
            }
            request.quantity =
                i32::try_from(delta).map_err(|_| invalid_quantity("out_of_range"))?;
            let reason = body.get("reason").and_then(JsonValue::as_str);
            let reason = reason
                .filter(|reason| REASONS.contains(reason))
                .ok_or_else(|| {
                    refusal(
                    422,
                    "invalid_reason",
                    "An adjustment must state a reason from DAMAGE, LOSS, EXPIRY or CORRECTION.",
                    "reason",
                    if body.get("reason").is_none() {
                        "required"
                    } else {
                        "not_allowed"
                    },
                )
                })?;
            request.reason = Some(reason.to_owned());
        }
    }
    Ok(request)
}

/// A refusal the schema or `stock.record_movement` raises, by constraint name.
pub fn movement_error(error: tokio_postgres::Error) -> ApiError {
    let database = error.as_db_error();
    let constraint = database
        .and_then(|database| database.constraint())
        .unwrap_or_default();
    match constraint {
        "stock_unavailable" => refusal(
            409,
            "stock_unavailable",
            "The movement would leave the store with negative stock.",
            "quantity",
            "insufficient_stock",
        ),
        "no_stock_record" => refusal(
            422,
            "no_stock_record",
            "No stock record exists for the store and product.",
            "store_id",
            "no_stock_record",
        ),
        "idempotency_key_reused" => refusal(
            409,
            "idempotency_key_reused",
            "The Idempotency-Key was already used for a different movement.",
            "Idempotency-Key",
            "reused",
        ),
        "inventory_store_id_fkey" => unknown_store("store_id"),
        "inventory_product_id_fkey" => unknown_product(),
        // A signed token for an account that does not exist.
        "movement_request_recorded_by_fkey" | "stock_movement_recorded_by_fkey" => invalid_token(),
        _ if database.is_some_and(|database| database.code().code() == "22003") => {
            invalid_quantity("out_of_range")
        }
        _ => ApiError::from(error),
    }
}

// ---------- writing ----------

/// Parse the `Idempotency-Key` header: a UUID.
pub fn parse_idempotency_key(raw: Option<&str>) -> Result<Uuid, ApiError> {
    raw.and_then(|value| Uuid::parse_str(value.trim()).ok())
        .ok_or_else(idempotency_key_required)
}

pub fn caller(sub: &str) -> Result<Uuid, ApiError> {
    Uuid::parse_str(sub).map_err(|_| invalid_token())
}

/// Apply one movement, and read back what it recorded. The statement is one
/// call to `stock.record_movement`: the `inventory` change and the movement
/// rows commit with the caller's transaction or not at all. `true` means the
/// key had been accepted before and nothing changed.
pub async fn record_movement<C>(
    client: &C,
    key: Uuid,
    sub: Uuid,
    request: &MovementRequest,
) -> Result<(Vec<Movement>, bool), ApiError>
where
    C: GenericClient + Sync,
{
    let canonical = request.canonical(&sub);
    let applied = client
        .query_one(
            "SELECT movement_ids, replayed FROM stock.record_movement(\
                 $1::uuid, $2::text, $3::uuid, $4::text, $5::smallint, $6::smallint, \
                 $7::integer, $8::integer, $9::text, $10::text, $11::text, $12::text)",
            &[
                &key,
                &canonical,
                &sub,
                &request.movement_type.code(),
                &request.store_id,
                &request.destination_store_id,
                &request.product_id,
                &request.quantity,
                &request.reference,
                &request.reason,
                &request.counted_by,
                &request.note,
            ],
        )
        .await
        .map_err(movement_error)?;
    let ids: Vec<i64> = applied.try_get(0)?;
    let replayed: bool = applied.try_get(1)?;
    let rows = client
        .query(
            concat!(
                "SELECT ",
                columns!(),
                " FROM stock.stock_movement \
                 WHERE movement_id = ANY($1::bigint[]) ORDER BY movement_id"
            ),
            &[&ids],
        )
        .await?;
    let movements = rows
        .iter()
        .map(Movement::from_row)
        .collect::<Result<Vec<_>, _>>()?;
    Ok((movements, replayed))
}

/// Who asked for a movement, as ADR-0072's event names them: the verified
/// token's subject and role, and the request's `X-Correlation-ID`.
pub struct Actor<'a> {
    pub sub: Uuid,
    pub role: &'a str,
    pub correlation_id: &'a str,
}

/// Apply one movement as the service does: `record_movement`, and for a new
/// adjustment its `CRITICAL_ACTION` domain event (ADR-0072: "stock is
/// adjusted"), both in the caller's transaction, so an adjustment never
/// commits without its event and the event never survives a rolled-back
/// adjustment. A replay changed nothing, so it records nothing.
pub async fn apply_movement<C>(
    client: &C,
    key: Uuid,
    actor: &Actor<'_>,
    request: &MovementRequest,
) -> Result<(Vec<Movement>, bool), ApiError>
where
    C: GenericClient + Sync,
{
    let (movements, replayed) = record_movement(client, key, actor.sub, request).await?;
    if !replayed && request.movement_type == MovementType::Adjustment {
        record_adjustment_event(client, key, actor, request).await?;
    }
    Ok((movements, replayed))
}

/// Record an adjustment as `CRITICAL_ACTION` with `details.action =
/// STOCK_ADJUSTED` through `api_audit.record_event_v1`, as `svc_stock`.
///
/// - The event id is derived from the `Idempotency-Key` in a stock-only
///   namespace, so the event of one request has one id and cannot collide
///   with another producer's.
/// - The subject is the stock level: the event's `store_id` and, as
///   `subject_id`, the product, which together name
///   `/v1/stock-levels/{store_id}/{product_id}`.
/// - The reason code is the adjustment's reason (DAMAGE, LOSS, EXPIRY or
///   CORRECTION).
///
/// A refused record is the producer's fault, never the client's: it fails
/// the request as a server error, and the caller's transaction rolls back.
pub async fn record_adjustment_event<C>(
    client: &C,
    key: Uuid,
    actor: &Actor<'_>,
    request: &MovementRequest,
) -> Result<(), ApiError>
where
    C: GenericClient + Sync,
{
    let correlation = (!actor.correlation_id.is_empty() && actor.correlation_id != "-")
        .then_some(actor.correlation_id);
    client
        .query_one(
            "SELECT created FROM api_audit.record_event_v1( \
                 md5('stock.adjustment:' || $1::uuid::text)::uuid, 'CRITICAL_ACTION', 'SUCCESS', \
                 now(), 'USER', $2::uuid, $3::text, $4::smallint, 'stock_level', $5::text, \
                 NULL::bigint, NULL::bigint, $6::text, \
                 jsonb_strip_nulls(jsonb_build_object( \
                     'action', 'STOCK_ADJUSTED', 'reason_code', $7::text)))",
            &[
                &key,
                &actor.sub,
                &actor.role,
                &request.store_id,
                &request.product_id.to_string(),
                &correlation,
                &request.reason,
            ],
        )
        .await
        .map(|_| ())
        .map_err(|error| {
            tracing::error!(
                "STOCK_ADJUSTED not recorded: sqlstate={:?}",
                error.code().map(|code| code.code().to_owned())
            );
            ApiError::from(error)
        })
}

// ---------- reading ----------

fn invalid_query(field: &str, detail: &str, message: &str) -> ApiError {
    refusal(422, "invalid_query", message, field, detail)
}

fn positive(query: &HashMap<String, String>, name: &str) -> Result<Option<i32>, ApiError> {
    match query.get(name) {
        None => Ok(None),
        Some(raw) => raw
            .parse::<i32>()
            .ok()
            .filter(|number| *number > 0)
            .map(Some)
            .ok_or_else(|| {
                invalid_query(
                    name,
                    "not_a_positive_integer",
                    "A query parameter is not a positive integer.",
                )
            }),
    }
}

fn instant(query: &HashMap<String, String>, name: &str) -> Result<Option<DateTime<Utc>>, ApiError> {
    match query.get(name) {
        None => Ok(None),
        // A `+` in an unencoded offset arrives as a space; no instant has one.
        Some(raw) => DateTime::parse_from_rfc3339(&raw.replace(' ', "+"))
            .map(|parsed| Some(parsed.with_timezone(&Utc)))
            .map_err(|_| {
                invalid_query(
                    name,
                    "not_a_timestamp",
                    "A query parameter is not an RFC 3339 instant with an offset.",
                )
            }),
    }
}

pub fn parse_movement_filters(
    query: &HashMap<String, String>,
) -> Result<MovementFilters, ApiError> {
    let movement_type = match query.get("movement_type") {
        None => None,
        Some(kind) if TYPES.contains(&kind.as_str()) => Some(kind.clone()),
        Some(_) => {
            return Err(invalid_query(
                "movement_type",
                "not_allowed",
                "A query parameter is not one of the movement types.",
            ))
        }
    };
    let transfer_id = match query.get("transfer_id") {
        None => None,
        Some(raw) => Some(Uuid::parse_str(raw).map_err(|_| {
            invalid_query(
                "transfer_id",
                "not_a_uuid",
                "A query parameter is not a UUID.",
            )
        })?),
    };
    Ok(MovementFilters {
        store_id: positive(query, "store_id")?,
        product_id: positive(query, "product_id")?,
        movement_type,
        transfer_id,
        from: instant(query, "from")?,
        to: instant(query, "to")?,
    })
}

pub async fn read_movements<C>(
    client: &C,
    filters: &MovementFilters,
    page: u32,
    size: u32,
) -> Result<MovementPage, tokio_postgres::Error>
where
    C: GenericClient + Sync,
{
    let offset = i64::from(page - 1) * i64::from(size);
    let limit = i64::from(size);
    let rows = client
        .query(
            concat!(
                "SELECT ",
                columns!(),
                " FROM stock.stock_movement ",
                filter!(),
                " ORDER BY recorded_at DESC, movement_id DESC LIMIT $7 OFFSET $8"
            ),
            &[
                &filters.store_id,
                &filters.product_id,
                &filters.movement_type,
                &filters.transfer_id,
                &filters.from,
                &filters.to,
                &limit,
                &offset,
            ],
        )
        .await?;
    let total: i64 = client
        .query_one(
            concat!("SELECT count(*) FROM stock.stock_movement ", filter!()),
            &[
                &filters.store_id,
                &filters.product_id,
                &filters.movement_type,
                &filters.transfer_id,
                &filters.from,
                &filters.to,
            ],
        )
        .await?
        .try_get(0)?;
    let movements = rows
        .iter()
        .map(Movement::from_row)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(MovementPage {
        movements,
        total: total as u64,
    })
}

pub async fn read_movement<C>(
    client: &C,
    movement_id: i64,
) -> Result<Option<Movement>, tokio_postgres::Error>
where
    C: GenericClient + Sync,
{
    client
        .query_opt(
            concat!(
                "SELECT ",
                columns!(),
                " FROM stock.stock_movement WHERE movement_id = $1"
            ),
            &[&movement_id],
        )
        .await?
        .as_ref()
        .map(Movement::from_row)
        .transpose()
}
