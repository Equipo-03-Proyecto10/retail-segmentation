//! Availability by store, product and channel (F21-07, ADR-0061). It is read
//! from `api_stock.availability_v1`, the view recommendations reads too, so
//! the endpoint and the published view are one definition and cannot
//! disagree. Nothing is computed here: the view applies the rule and names the
//! reason. Every statement is parameterized.

use std::collections::HashMap;

use chrono::{DateTime, Utc};
use indexmap::IndexMap;
use mosaiq_kit::{ApiError, Detail, Value};
use tokio_postgres::{GenericClient, Row};

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct AvailabilityFilters {
    pub store_id: Option<i32>,
    pub product_id: Option<i32>,
    pub channel_id: Option<i32>,
    pub available: Option<bool>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Availability {
    pub store_id: i16,
    pub product_id: i32,
    pub channel_id: i16,
    pub is_available: bool,
    pub reason: String,
    pub quantity_on_hand: Option<i32>,
    pub updated_at: Option<DateTime<Utc>>,
}

impl Availability {
    fn from_row(row: &Row) -> Result<Self, tokio_postgres::Error> {
        Ok(Self {
            store_id: row.try_get(0)?,
            product_id: row.try_get(1)?,
            channel_id: row.try_get(2)?,
            is_available: row.try_get(3)?,
            reason: row.try_get(4)?,
            quantity_on_hand: row.try_get(5)?,
            updated_at: row.try_get(6)?,
        })
    }

    /// JSON carries `null` for a pair with no stock record; XML omits the
    /// element (the kit renders `Value::Null` that way), as the contract says.
    pub fn representation(&self) -> IndexMap<String, Value> {
        IndexMap::from([
            ("store_id".to_owned(), Value::from(self.store_id)),
            ("product_id".to_owned(), Value::from(self.product_id)),
            ("channel_id".to_owned(), Value::from(self.channel_id)),
            ("is_available".to_owned(), Value::from(self.is_available)),
            ("reason".to_owned(), Value::from(self.reason.as_str())),
            (
                "quantity_on_hand".to_owned(),
                self.quantity_on_hand.map_or(Value::Null, Value::from),
            ),
            (
                "updated_at".to_owned(),
                self.updated_at.map_or(Value::Null, Value::from),
            ),
        ])
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AvailabilityPage {
    pub availabilities: Vec<Availability>,
    pub total: u64,
}

/// The rows and the count share one filter; both are whole literals, so no
/// SQL text is assembled at run time.
macro_rules! filtered {
    ($head:literal, $tail:literal) => {
        concat!(
            $head,
            " FROM api_stock.availability_v1 \
             WHERE ($1::integer IS NULL OR store_id = $1) \
               AND ($2::integer IS NULL OR product_id = $2) \
               AND ($3::integer IS NULL OR channel_id = $3) \
               AND ($4::boolean IS NULL OR is_available = $4)",
            $tail
        )
    };
}

pub fn parse_availability_filters(
    query: &HashMap<String, String>,
) -> Result<AvailabilityFilters, ApiError> {
    let mut invalid: Vec<Detail> = Vec::new();
    let mut integer = |name: &str| match query.get(name) {
        None => None,
        Some(raw) => match raw.parse::<i32>().ok().filter(|number| *number > 0) {
            Some(value) => Some(value),
            None => {
                invalid.push(Detail::new(name, "not_a_positive_integer"));
                None
            }
        },
    };
    let store_id = integer("store_id");
    let product_id = integer("product_id");
    let channel_id = integer("channel_id");
    let available = match query.get("available").map(String::as_str) {
        None => None,
        Some("true") => Some(true),
        Some("false") => Some(false),
        Some(_) => {
            invalid.push(Detail::new("available", "not_a_boolean"));
            None
        }
    };
    if invalid.is_empty() {
        return Ok(AvailabilityFilters {
            store_id,
            product_id,
            channel_id,
            available,
        });
    }
    let message = if invalid
        .iter()
        .all(|detail| detail.code == "not_a_positive_integer")
    {
        "A query parameter is not a positive integer."
    } else {
        "A query parameter is not valid."
    };
    Err(ApiError::new(422, "invalid_query", message).with_details(invalid))
}

pub async fn read_availabilities<C>(
    client: &C,
    filters: &AvailabilityFilters,
    page: u32,
    size: u32,
) -> Result<AvailabilityPage, tokio_postgres::Error>
where
    C: GenericClient + Sync,
{
    let offset = i64::from(page - 1) * i64::from(size);
    let limit = i64::from(size);
    let rows = client
        .query(
            filtered!(
                "SELECT store_id, product_id, channel_id, is_available, reason, quantity_on_hand, updated_at",
                " ORDER BY store_id, product_id, channel_id LIMIT $5 OFFSET $6"
            ),
            &[
                &filters.store_id,
                &filters.product_id,
                &filters.channel_id,
                &filters.available,
                &limit,
                &offset,
            ],
        )
        .await?;
    let total = client
        .query_one(
            filtered!("SELECT count(*)", ""),
            &[
                &filters.store_id,
                &filters.product_id,
                &filters.channel_id,
                &filters.available,
            ],
        )
        .await?
        .try_get::<_, i64>(0)?;
    let availabilities = rows
        .iter()
        .map(Availability::from_row)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(AvailabilityPage {
        availabilities,
        total: total as u64,
    })
}

/// One pair's rows, one per channel. Empty exactly when the store or the
/// product does not exist: a pair with no stock record still has its rows.
pub async fn read_availability<C>(
    client: &C,
    store_id: i32,
    product_id: i32,
) -> Result<Vec<Availability>, tokio_postgres::Error>
where
    C: GenericClient + Sync,
{
    client
        .query(
            "SELECT store_id, product_id, channel_id, is_available, reason, \
                    quantity_on_hand, updated_at \
             FROM api_stock.availability_v1 \
             WHERE store_id = $1::integer AND product_id = $2 ORDER BY channel_id",
            &[&store_id, &product_id],
        )
        .await?
        .iter()
        .map(Availability::from_row)
        .collect()
}

pub fn availability_not_found(store_id: i64, product_id: i64) -> ApiError {
    ApiError::new(
        404,
        "availability_not_found",
        format!("No store or product exists for store {store_id} and product {product_id}."),
    )
}
