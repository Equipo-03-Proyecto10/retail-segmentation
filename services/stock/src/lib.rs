use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use axum::extract::Request;
use axum::middleware::{self, Next};
use axum::response::Response;
use axum::Router;
use chrono::{DateTime, Utc};
use indexmap::IndexMap;
use mosaiq_kit::{
    service_redis_info, ApiError, Ctx, Database, Detail, Kit, KitConfig, OutboxRelay, Page, Reply,
    Representation, Value,
};
use redis::aio::MultiplexedConnection;
use tokio_postgres::{GenericClient, Row};

const SERVICE: &str = "stock";
pub const SUFFIX: &str = "stock";
pub const STREAM: &str = "stream:stock.v1.changed";

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct StockFilters {
    pub store_id: Option<i32>,
    pub product_id: Option<i32>,
    pub below: Option<i32>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StockLevel {
    pub store_id: i16,
    pub product_id: i32,
    pub quantity_on_hand: i32,
    pub updated_at: DateTime<Utc>,
}

impl StockLevel {
    fn from_row(row: &Row) -> Result<Self, tokio_postgres::Error> {
        Ok(Self {
            store_id: row.try_get(0)?,
            product_id: row.try_get(1)?,
            quantity_on_hand: row.try_get(2)?,
            updated_at: row.try_get(3)?,
        })
    }

    fn representation(&self) -> IndexMap<String, Value> {
        IndexMap::from([
            ("store_id".to_owned(), Value::from(self.store_id)),
            ("product_id".to_owned(), Value::from(self.product_id)),
            (
                "quantity_on_hand".to_owned(),
                Value::from(self.quantity_on_hand),
            ),
            ("updated_at".to_owned(), Value::from(self.updated_at)),
        ])
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StockPage {
    pub levels: Vec<StockLevel>,
    pub page: u32,
    pub size: u32,
    pub total: u64,
}

tokio::task_local! {
    static QUERY: HashMap<String, String>;
}

fn invalid_query(fields: Vec<&str>) -> ApiError {
    ApiError::new(
        422,
        "invalid_query",
        "A query parameter is not a positive integer.",
    )
    .with_details(
        fields
            .into_iter()
            .map(|field| Detail::new(field, "not_a_positive_integer"))
            .collect(),
    )
}

fn positive_i32(raw: Option<&String>) -> Option<Result<i32, ()>> {
    raw.map(|value| {
        value
            .parse::<i32>()
            .ok()
            .filter(|number| *number > 0)
            .ok_or(())
    })
}

pub fn parse_filters(query: &HashMap<String, String>) -> Result<StockFilters, ApiError> {
    let mut invalid = Vec::new();
    let mut parse = |name| match positive_i32(query.get(name)) {
        None => None,
        Some(Ok(value)) => Some(value),
        Some(Err(())) => {
            invalid.push(name);
            None
        }
    };
    let filters = StockFilters {
        store_id: parse("store_id"),
        product_id: parse("product_id"),
        below: parse("below"),
    };
    if invalid.is_empty() {
        Ok(filters)
    } else {
        Err(invalid_query(invalid))
    }
}

async fn capture_query(request: Request, next: Next) -> Response {
    let values = request
        .uri()
        .query()
        .map(|query| {
            form_urlencoded::parse(query.as_bytes())
                .map(|(key, value)| (key.into_owned(), value.into_owned()))
                .collect()
        })
        .unwrap_or_default();
    QUERY.scope(values, next.run(request)).await
}

pub async fn read_stock_levels<C>(
    client: &C,
    filters: &StockFilters,
    page: u32,
    size: u32,
) -> Result<StockPage, tokio_postgres::Error>
where
    C: GenericClient + Sync,
{
    let offset = i64::from(page - 1) * i64::from(size);
    let limit = i64::from(size);
    let rows = client
        .query(
            "SELECT store_id, product_id, quantity_on_hand, updated_at \
             FROM api_stock.stock_level_v1 \
             WHERE ($1::integer IS NULL OR store_id = $1) \
               AND ($2::integer IS NULL OR product_id = $2) \
               AND ($3::integer IS NULL OR quantity_on_hand < $3) \
             ORDER BY store_id, product_id LIMIT $4 OFFSET $5",
            &[
                &filters.store_id,
                &filters.product_id,
                &filters.below,
                &limit,
                &offset,
            ],
        )
        .await?;
    let total = client
        .query_one(
            "SELECT count(*) FROM api_stock.stock_level_v1 \
             WHERE ($1::integer IS NULL OR store_id = $1) \
               AND ($2::integer IS NULL OR product_id = $2) \
               AND ($3::integer IS NULL OR quantity_on_hand < $3)",
            &[&filters.store_id, &filters.product_id, &filters.below],
        )
        .await?
        .try_get::<_, i64>(0)?;
    let levels = rows
        .iter()
        .map(StockLevel::from_row)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(StockPage {
        levels,
        page,
        size,
        total: total as u64,
    })
}

pub async fn read_stock_level<C>(
    client: &C,
    store_id: i32,
    product_id: i32,
) -> Result<Option<StockLevel>, tokio_postgres::Error>
where
    C: GenericClient + Sync,
{
    client
        .query_opt(
            "SELECT store_id, product_id, quantity_on_hand, updated_at \
             FROM api_stock.stock_level_v1 \
             WHERE store_id = $1::integer AND product_id = $2",
            &[&store_id, &product_id],
        )
        .await?
        .as_ref()
        .map(StockLevel::from_row)
        .transpose()
}

pub fn stock_level_or_not_found(level: Option<StockLevel>) -> Result<StockLevel, ApiError> {
    level.ok_or_else(stock_level_not_found)
}

async fn list_stock_levels(ctx: Ctx) -> Result<Reply, ApiError> {
    let query = QUERY.try_with(Clone::clone).unwrap_or_default();
    let filters = parse_filters(&query)?;
    let connection = ctx.db().connection().await?;
    let result = read_stock_levels(&***connection, &filters, ctx.page, ctx.size).await?;
    let levels = result
        .levels
        .iter()
        .map(StockLevel::representation)
        .collect();
    Ok(Page::new(
        "StockLevels",
        "StockLevel",
        "stock_levels",
        levels,
        result.page,
        result.total,
    )
    .size(result.size)
    .into())
}

async fn get_stock_level(ctx: Ctx) -> Result<Reply, ApiError> {
    let store_id = ctx
        .path_i64("store_id")
        .and_then(|value| i32::try_from(value).ok())
        .ok_or_else(stock_level_not_found)?;
    let product_id = ctx
        .path_i64("product_id")
        .and_then(|value| i32::try_from(value).ok())
        .ok_or_else(stock_level_not_found)?;
    let connection = ctx.db().connection().await?;
    let level =
        stock_level_or_not_found(read_stock_level(&***connection, store_id, product_id).await?)?;
    Ok(Representation::new("StockLevel", level.representation()).into())
}

fn stock_level_not_found() -> ApiError {
    ApiError::new(
        404,
        "stock_level_not_found",
        "The stock level does not exist.",
    )
}

async fn database_ready(db: Arc<Database>) -> bool {
    let Ok(connection) = db.connection().await else {
        return false;
    };
    connection
        .query(
            "SELECT store_id FROM api_stock.stock_level_v1 LIMIT $1",
            &[&0i64],
        )
        .await
        .is_ok()
}

pub fn router(config: KitConfig, contracts: &str) -> Result<Router, Box<dyn std::error::Error>> {
    Ok(Kit::new(SERVICE, SUFFIX, contracts, config)?
        .operation("listStockLevels", list_stock_levels)?
        .operation("getStockLevel", get_stock_level)?
        .ready_check(database_ready)
        .into_router()
        .layer(middleware::from_fn(capture_query)))
}

async fn redis_connection(
    url: String,
    password: Option<String>,
) -> redis::RedisResult<MultiplexedConnection> {
    let info = service_redis_info(&url, SUFFIX, password)?;
    redis::Client::open(info)?
        .get_multiplexed_async_connection()
        .await
}

/// The outbox relay for `stream:stock.v1.changed`. The first connection is
/// retried every second, so the service comes up during a Redis outage; after
/// that the kit rebuilds the connection after every outage (#579).
pub async fn run_relay(database_url: String, redis_url: String, password: Option<String>) {
    loop {
        let result = async {
            let connection = redis_connection(redis_url.clone(), password.clone()).await?;
            let relay =
                OutboxRelay::new(&database_url, SUFFIX, [STREAM], connection).map_err(|error| {
                    redis::RedisError::from((
                        redis::ErrorKind::ClientError,
                        "relay configuration",
                        error.to_string(),
                    ))
                })?;
            relay
                .run_with_reconnect(|| redis_connection(redis_url.clone(), password.clone()))
                .await;
            Ok::<(), redis::RedisError>(())
        }
        .await;
        if let Err(error) = result {
            tracing::error!("Stock outbox relay connection failed: {error}");
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
    }
}

async fn relay(config: KitConfig) {
    let Some(database_url) = config.database_url else {
        tracing::error!("DATABASE_URL is missing; the stock outbox relay is disabled");
        return;
    };
    run_relay(database_url, config.redis_state_url, config.redis_password).await;
}

pub async fn serve(config: KitConfig, contracts: &str) -> Result<(), Box<dyn std::error::Error>> {
    tokio::spawn(relay(config.clone()));
    let app = router(config, contracts)?;
    let listener = tokio::net::TcpListener::bind("0.0.0.0:5006").await?;
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .await?;
    Ok(())
}
