use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use axum::body::Body;
use axum::extract::Request;
use axum::middleware::{self, Next};
use axum::response::Response;
use chrono::{DateTime, Utc};
use indexmap::IndexMap;
use mosaiq_kit::{
    configure_logging, service_redis_info, ApiError, Ctx, Database, Detail, Kit, KitConfig,
    NoContent, OutboxRelay, Page, Reply, Representation, StreamConsumer, Value,
};
use redis::aio::MultiplexedConnection;
use redis::AsyncCommands;
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use tokio_postgres::Row;
use uuid::Uuid;

const SERVICE: &str = "stores";
const SUFFIX: &str = "stores";
const STREAM: &str = "stream:stores.v1.changed";
const DIRECTORY_CACHE_KEY: &str = "stores:cache:directory";
const CACHE_SECONDS: u64 = 900;

static CACHE: OnceLock<Cache> = OnceLock::new();
tokio::task_local! {
    static REQUEST_QUERY: String;
}

#[derive(Clone)]
struct Cache {
    client: redis::Client,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct StoreRecord {
    store_id: i16,
    name: String,
    city: String,
    state: String,
    is_active: bool,
    latitude: Option<Decimal>,
    longitude: Option<Decimal>,
    geofence_radius_m: Option<i32>,
    set_at: Option<DateTime<Utc>>,
}

impl StoreRecord {
    fn from_row(row: &Row) -> Result<Self, ApiError> {
        Ok(Self {
            store_id: row.try_get(0)?,
            name: row.try_get(1)?,
            city: row.try_get(2)?,
            state: row.try_get(3)?,
            is_active: row.try_get(4)?,
            latitude: row.try_get(5)?,
            longitude: row.try_get(6)?,
            geofence_radius_m: row.try_get(7)?,
            set_at: row.try_get(8)?,
        })
    }

    fn wire(&self) -> IndexMap<String, Value> {
        let location = match (
            self.latitude,
            self.longitude,
            self.geofence_radius_m,
            self.set_at,
        ) {
            (Some(latitude), Some(longitude), Some(radius), Some(set_at)) => Value::object([
                ("latitude", Value::from(latitude)),
                ("longitude", Value::from(longitude)),
                ("geofence_radius_m", Value::from(radius)),
                ("set_at", Value::from(set_at)),
            ]),
            _ => Value::Null,
        };
        IndexMap::from([
            ("store_id".to_owned(), Value::from(self.store_id)),
            ("name".to_owned(), Value::from(self.name.clone())),
            ("city".to_owned(), Value::from(self.city.clone())),
            ("state".to_owned(), Value::from(self.state.clone())),
            ("is_active".to_owned(), Value::from(self.is_active)),
            ("location".to_owned(), location),
        ])
    }
}

impl Cache {
    async fn connection(&self) -> redis::RedisResult<MultiplexedConnection> {
        let config = redis::AsyncConnectionConfig::new()
            .set_connection_timeout(Duration::from_millis(200))
            .set_response_timeout(Duration::from_millis(200));
        self.client
            .get_multiplexed_async_connection_with_config(&config)
            .await
    }

    async fn get(&self) -> Option<Vec<StoreRecord>> {
        let mut connection = self.connection().await.ok()?;
        let text: Option<String> = connection.get(DIRECTORY_CACHE_KEY).await.ok()?;
        text.and_then(|value| serde_json::from_str(&value).ok())
    }

    async fn put(&self, stores: &[StoreRecord]) {
        let Ok(value) = serde_json::to_string(stores) else {
            return;
        };
        if let Ok(mut connection) = self.connection().await {
            let _: redis::RedisResult<()> = connection
                .set_ex(DIRECTORY_CACHE_KEY, value, CACHE_SECONDS)
                .await;
        }
    }

    async fn invalidate(&self) -> redis::RedisResult<()> {
        let mut connection = self.connection().await?;
        connection.del(DIRECTORY_CACHE_KEY).await
    }
}

async fn directory(ctx: &Ctx) -> Result<Vec<StoreRecord>, ApiError> {
    if let Some(cache) = CACHE.get() {
        if let Some(stores) = cache.get().await {
            return Ok(stores);
        }
    }
    let connection = ctx.db().connection().await?;
    let no_active: Option<bool> = None;
    let no_city: Option<String> = None;
    let rows = connection
        .query(
            "SELECT s.store_id, s.name, s.city, s.state, s.is_active, \
             l.latitude, l.longitude, l.geofence_radius_m, l.set_at \
             FROM public.store AS s LEFT JOIN stores.store_location AS l USING (store_id) \
             WHERE ($1::boolean IS NULL OR s.is_active = $1) \
               AND ($2::text IS NULL OR s.city = $2) \
             ORDER BY s.store_id",
            &[&no_active, &no_city],
        )
        .await?;
    let stores = rows
        .iter()
        .map(StoreRecord::from_row)
        .collect::<Result<Vec<_>, _>>()?;
    if let Some(cache) = CACHE.get() {
        cache.put(&stores).await;
    }
    Ok(stores)
}

async fn list_stores(ctx: Ctx) -> Result<Reply, ApiError> {
    let query = REQUEST_QUERY.try_with(Clone::clone).unwrap_or_default();
    let (active, city) = directory_filters(&query);
    let stores: Vec<_> = directory(&ctx)
        .await?
        .into_iter()
        .filter(|store| active.is_none_or(|active| store.is_active == active))
        .filter(|store| city.as_ref().is_none_or(|city| store.city == *city))
        .collect();
    let total = stores.len() as u64;
    let offset = u64::from(ctx.page - 1) * u64::from(ctx.size);
    let items = stores
        .into_iter()
        .skip(offset as usize)
        .take(ctx.size as usize)
        .map(|store| store.wire())
        .collect();
    Ok(
        Page::new("Stores", "Store", "stores", items, ctx.page, total)
            .size(ctx.size)
            .into(),
    )
}

fn directory_filters(query: &str) -> (Option<bool>, Option<String>) {
    let values: HashMap<String, String> = form_urlencoded::parse(query.as_bytes())
        .map(|(key, value)| (key.into_owned(), value.into_owned()))
        .collect();
    let active = values.get("active").and_then(|value| match value.as_str() {
        "true" => Some(true),
        "false" => Some(false),
        _ => None,
    });
    let city = values
        .get("city")
        .filter(|value| !value.is_empty())
        .cloned();
    (active, city)
}

fn store_id(ctx: &Ctx) -> Result<i16, ApiError> {
    ctx.path_i64("store_id")
        .and_then(|value| i16::try_from(value).ok())
        .ok_or_else(store_not_found)
}

fn store_not_found() -> ApiError {
    ApiError::new(404, "store_not_found", "The store does not exist.")
}

async fn get_store(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = store_id(&ctx)?;
    let store = directory(&ctx)
        .await?
        .into_iter()
        .find(|store| store.store_id == id)
        .ok_or_else(store_not_found)?;
    Ok(Representation::new("Store", store.wire()).into())
}

fn constraint_error(error: tokio_postgres::Error) -> ApiError {
    let constraint = error
        .as_db_error()
        .and_then(|database| database.constraint())
        .unwrap_or_default();
    let field = match constraint {
        "store_location_latitude_check" => Some("latitude"),
        "store_location_longitude_check" => Some("longitude"),
        "store_location_geofence_radius_m_check" => Some("geofence_radius_m"),
        _ => None,
    };
    field.map_or_else(
        || ApiError::from(error),
        |field| {
            ApiError::new(
                422,
                "invalid_body",
                "The location contains a value outside its allowed range.",
            )
            .with_details(vec![Detail::new(field, "out_of_range")])
        },
    )
}

async fn put_store_location(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = store_id(&ctx)?;
    let body = ctx.body.as_ref().expect("contract requires a body");
    let latitude = body["latitude"]
        .to_string()
        .parse::<Decimal>()
        .map_err(|_| ApiError::new(422, "invalid_body", "The location is invalid."))?;
    let longitude = body["longitude"]
        .to_string()
        .parse::<Decimal>()
        .map_err(|_| ApiError::new(422, "invalid_body", "The location is invalid."))?;
    let radius = body["geofence_radius_m"]
        .as_i64()
        .and_then(|value| i32::try_from(value).ok())
        .ok_or_else(|| ApiError::new(422, "invalid_body", "The location is invalid."))?;
    let actor = ctx
        .principal()
        .and_then(|principal| Uuid::parse_str(&principal.sub).ok())
        .ok_or_else(|| ApiError::new(401, "invalid_token", "The bearer token is invalid."))?;
    let mut connection = ctx.db().connection().await?;
    let transaction = connection.transaction(ctx.principal()).await?;
    if transaction
        .query_opt(
            "SELECT store_id FROM public.store WHERE store_id = $1",
            &[&id],
        )
        .await?
        .is_none()
    {
        return Err(store_not_found());
    }
    transaction
        .execute(
            "INSERT INTO stores.store_location \
             (store_id, latitude, longitude, geofence_radius_m, set_by, set_at) \
             VALUES ($1, $2, $3, $4, $5, now()) \
             ON CONFLICT (store_id) DO UPDATE SET latitude = EXCLUDED.latitude, \
             longitude = EXCLUDED.longitude, geofence_radius_m = EXCLUDED.geofence_radius_m, \
             set_by = EXCLUDED.set_by, set_at = now()",
            &[&id, &latitude, &longitude, &radius, &actor],
        )
        .await
        .map_err(constraint_error)?;
    let row = transaction
        .query_one(
            "SELECT s.store_id, s.name, s.city, s.state, s.is_active, \
             l.latitude, l.longitude, l.geofence_radius_m, l.set_at \
             FROM public.store AS s JOIN stores.store_location AS l USING (store_id) \
             WHERE s.store_id = $1",
            &[&id],
        )
        .await?;
    let store = StoreRecord::from_row(&row)?;
    transaction.commit().await?;
    if let Some(cache) = CACHE.get() {
        let _ = cache.invalidate().await;
    }
    Ok(Representation::new("Store", store.wire()).into())
}

async fn delete_store_location(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = store_id(&ctx)?;
    let mut connection = ctx.db().connection().await?;
    let transaction = connection.transaction(ctx.principal()).await?;
    if transaction
        .query_opt(
            "SELECT store_id FROM public.store WHERE store_id = $1",
            &[&id],
        )
        .await?
        .is_none()
    {
        return Err(store_not_found());
    }
    transaction
        .execute(
            "DELETE FROM stores.store_location WHERE store_id = $1",
            &[&id],
        )
        .await?;
    transaction.commit().await?;
    if let Some(cache) = CACHE.get() {
        let _ = cache.invalidate().await;
    }
    Ok(NoContent::default().into())
}

async fn database_ready(db: Arc<Database>) -> bool {
    let Ok(connection) = db.connection().await else {
        return false;
    };
    connection
        .query_one("SELECT count(*) FROM stores.store_location", &[])
        .await
        .is_ok()
}

async fn redis_connection(url: &str, password: Option<String>) -> Option<MultiplexedConnection> {
    let info = service_redis_info(url, SUFFIX, password).ok()?;
    redis::Client::open(info)
        .ok()?
        .get_multiplexed_async_connection()
        .await
        .ok()
}

fn spawn_stream_tasks(password: Option<String>) {
    let state_url = std::env::var("REDIS_STATE_URL")
        .unwrap_or_else(|_| "redis://redis-state:6379/0".to_owned());
    if let Ok(database_url) = std::env::var("DATABASE_URL") {
        let relay_url = state_url.clone();
        let relay_password = password.clone();
        tokio::spawn(async move {
            loop {
                if let Some(redis) = redis_connection(&relay_url, relay_password.clone()).await {
                    match OutboxRelay::new(&database_url, SUFFIX, [STREAM], redis) {
                        Ok(relay) => relay.run_forever().await,
                        Err(error) => tracing::error!("Outbox relay configuration failed: {error}"),
                    }
                }
                tracing::warn!("Stream relay is waiting for redis-state");
                tokio::time::sleep(Duration::from_secs(1)).await;
            }
        });
    }
    tokio::spawn(async move {
        loop {
            if let Some(redis) = redis_connection(&state_url, password.clone()).await {
                let handler = |_event: HashMap<String, String>| async move {
                    if let Some(cache) = CACHE.get() {
                        cache.invalidate().await
                    } else {
                        Ok(())
                    }
                };
                match StreamConsumer::new(redis, STREAM, handler) {
                    Ok(consumer) => consumer.run_forever().await,
                    Err(error) => {
                        tracing::error!("Stream consumer configuration failed: {error}")
                    }
                }
            }
            tracing::warn!("Stream consumer is waiting for redis-state");
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
    });
}

async fn capture_query(request: Request<Body>, next: Next) -> Response {
    let query = request.uri().query().unwrap_or_default().to_owned();
    REQUEST_QUERY.scope(query, next.run(request)).await
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    configure_logging(&std::env::var("LOG_LEVEL").unwrap_or_else(|_| "info".to_owned()));
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let password = std::env::var("REDIS_STORES_PASSWORD").ok();
        let cache_url = std::env::var("REDIS_CACHE_URL")
            .unwrap_or_else(|_| "redis://redis-cache:6379/0".to_owned());
        if let Ok(info) = service_redis_info(&cache_url, SUFFIX, password.clone()) {
            if let Ok(client) = redis::Client::open(info) {
                let _ = CACHE.set(Cache { client });
            }
        }
        spawn_stream_tasks(password);
        let kit = Kit::new(SERVICE, SUFFIX, "contracts", KitConfig::from_env(SUFFIX))?;
        let router = kit
            .operation("listStores", list_stores)?
            .operation("getStore", get_store)?
            .operation("putStoreLocation", put_store_location)?
            .operation("deleteStoreLocation", delete_store_location)?
            .ready_check(database_ready)
            .into_router()
            .layer(middleware::from_fn(capture_query));
        let listener = tokio::net::TcpListener::bind("0.0.0.0:5005").await?;
        axum::serve(
            listener,
            router.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await?;
        Ok::<(), Box<dyn std::error::Error>>(())
    })
}

#[cfg(test)]
mod tests {
    use super::directory_filters;

    #[test]
    fn directory_filters_decode_active_and_city() {
        assert_eq!(
            directory_filters("active=true&city=San+Nicol%C3%A1s"),
            (Some(true), Some("San Nicolás".to_owned()))
        );
        assert_eq!(directory_filters("active=false&city="), (Some(false), None));
    }
}
