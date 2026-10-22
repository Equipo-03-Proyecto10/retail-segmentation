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
    NoContent, OutboxRelay, Page, Principal, Reply, Representation, StreamConsumer, Value,
};
use redis::aio::MultiplexedConnection;
use redis::AsyncCommands;
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use tokio_postgres::Row;
use uuid::Uuid;

pub const SERVICE: &str = "stores";
pub const SUFFIX: &str = "stores";
pub const STREAM: &str = "stream:stores.v1.changed";
pub const DIRECTORY_CACHE_KEY: &str = "stores:cache:directory";
const CACHE_SECONDS: u64 = 900;

static CACHE: OnceLock<Cache> = OnceLock::new();
tokio::task_local! {
    static REQUEST_QUERY: String;
}

#[derive(Clone)]
pub struct Cache {
    client: redis::Client,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct StoreRecord {
    pub store_id: i16,
    pub name: String,
    pub city: String,
    pub state: String,
    pub is_active: bool,
    pub latitude: Option<Decimal>,
    pub longitude: Option<Decimal>,
    pub geofence_radius_m: Option<i32>,
    pub set_at: Option<DateTime<Utc>>,
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

    pub async fn get(&self) -> Option<Vec<StoreRecord>> {
        let mut connection = self.connection().await.ok()?;
        let text: Option<String> = connection.get(DIRECTORY_CACHE_KEY).await.ok()?;
        text.and_then(|value| serde_json::from_str(&value).ok())
    }

    pub async fn put(&self, stores: &[StoreRecord]) {
        let Ok(value) = serde_json::to_string(stores) else {
            return;
        };
        if let Ok(mut connection) = self.connection().await {
            let _: redis::RedisResult<()> = connection
                .set_ex(DIRECTORY_CACHE_KEY, value, CACHE_SECONDS)
                .await;
        }
    }

    pub async fn invalidate(&self) -> redis::RedisResult<()> {
        let mut connection = self.connection().await?;
        connection.del(DIRECTORY_CACHE_KEY).await
    }
}

/// Read the directory from PostgreSQL: `public.store` joined to its location.
/// The filters are applied by the caller, so one cached copy serves them all.
pub async fn read_directory(db: &Database) -> Result<Vec<StoreRecord>, ApiError> {
    let connection = db.connection().await?;
    let rows = connection
        .query(
            "SELECT s.store_id, s.name, s.city, s.state, s.is_active, \
             l.latitude, l.longitude, l.geofence_radius_m, l.set_at \
             FROM public.store AS s LEFT JOIN stores.store_location AS l USING (store_id) \
             ORDER BY s.store_id",
            &[],
        )
        .await?;
    rows.iter().map(StoreRecord::from_row).collect()
}

/// The directory, from the cache when it holds it. Every cache failure is a miss.
pub async fn cached_directory(
    db: &Database,
    cache: Option<&Cache>,
) -> Result<Vec<StoreRecord>, ApiError> {
    if let Some(cache) = cache {
        if let Some(stores) = cache.get().await {
            return Ok(stores);
        }
    }
    let stores = read_directory(db).await?;
    if let Some(cache) = cache {
        cache.put(&stores).await;
    }
    Ok(stores)
}

async fn directory(ctx: &Ctx) -> Result<Vec<StoreRecord>, ApiError> {
    cached_directory(ctx.db(), CACHE.get()).await
}

async fn list_stores(ctx: Ctx) -> Result<Reply, ApiError> {
    let query = REQUEST_QUERY.try_with(Clone::clone).unwrap_or_default();
    let (active, city) = directory_filters(&query)?;
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

fn directory_filters(query: &str) -> Result<(Option<bool>, Option<String>), ApiError> {
    let values: HashMap<String, String> = form_urlencoded::parse(query.as_bytes())
        .map(|(key, value)| (key.into_owned(), value.into_owned()))
        .collect();
    let mut details = Vec::new();
    let active = match values.get("active").map(String::as_str) {
        None => None,
        Some("true") => Some(true),
        Some("false") => Some(false),
        Some(_) => {
            details.push(Detail::new("active", "invalid_type"));
            None
        }
    };
    let city = values.get("city").cloned();
    if let Some(city) = &city {
        let length = city.chars().count();
        if !(1..=80).contains(&length) {
            details.push(Detail::new("city", "out_of_range"));
        }
    }
    if details.is_empty() {
        Ok((active, city))
    } else {
        Err(ApiError::new(
            422,
            "invalid_query",
            "A query parameter is outside its allowed values.",
        )
        .with_details(details))
    }
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

pub fn constraint_error(error: tokio_postgres::Error) -> ApiError {
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

/// Create or replace a store's location as `actor`, in one transaction that
/// carries the actor to the audit trigger. Out-of-range values come back as
/// 422 `invalid_body` through the CHECK constraint names.
pub async fn set_location(
    db: &Database,
    actor: &Principal,
    id: i16,
    latitude: Decimal,
    longitude: Decimal,
    radius: i32,
) -> Result<StoreRecord, ApiError> {
    let set_by = Uuid::parse_str(&actor.sub)
        .map_err(|_| ApiError::new(401, "invalid_token", "The bearer token is invalid."))?;
    let mut connection = db.connection().await?;
    let transaction = connection.transaction(Some(actor)).await?;
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
            &[&id, &latitude, &longitude, &radius, &set_by],
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
    Ok(store)
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
        .ok_or_else(|| ApiError::new(401, "invalid_token", "The bearer token is invalid."))?;
    let store = set_location(ctx.db(), actor, id, latitude, longitude, radius).await?;
    if let Some(cache) = CACHE.get() {
        let _ = cache.invalidate().await;
    }
    Ok(Representation::new("Store", store.wire()).into())
}

/// Remove a store's location as `actor`. A store with no location is a no-op.
pub async fn clear_location(
    db: &Database,
    actor: Option<&Principal>,
    id: i16,
) -> Result<(), ApiError> {
    let mut connection = db.connection().await?;
    let transaction = connection.transaction(actor).await?;
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
    Ok(())
}

async fn delete_store_location(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = store_id(&ctx)?;
    clear_location(ctx.db(), ctx.principal(), id).await?;
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

async fn redis_connection(
    url: String,
    password: Option<String>,
) -> redis::RedisResult<MultiplexedConnection> {
    let info = service_redis_info(&url, SUFFIX, password)?;
    redis::Client::open(info)?
        .get_multiplexed_async_connection()
        .await
}

/// The outbox relay for `stream:stores.v1.changed`. The first connection is
/// retried every second, so the service comes up during a Redis outage; after
/// that the kit rebuilds the connection after every outage (#579).
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

/// The `stores` consumer group on the same stream, reconnecting as the relay
/// does. Each event runs `handler`; the service's handler drops the cache.
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

fn spawn_stream_tasks(password: Option<String>) {
    let state_url = std::env::var("REDIS_STATE_URL")
        .unwrap_or_else(|_| "redis://redis-state:6379/0".to_owned());
    if let Ok(database_url) = std::env::var("DATABASE_URL") {
        tokio::spawn(run_relay(database_url, state_url.clone(), password.clone()));
    }
    let handler = |_event: HashMap<String, String>| async move {
        if let Some(cache) = CACHE.get() {
            cache.invalidate().await
        } else {
            Ok(())
        }
    };
    tokio::spawn(run_consumer(state_url, password, handler));
}

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
    use mosaiq_kit::Detail;

    #[test]
    fn directory_filters_decode_active_and_city() {
        assert_eq!(
            directory_filters("active=true&city=San+Nicol%C3%A1s").unwrap(),
            (Some(true), Some("San Nicolás".to_owned()))
        );
        assert_eq!(directory_filters("").unwrap(), (None, None));
        assert_eq!(
            directory_filters("active=false").unwrap(),
            (Some(false), None)
        );
    }

    #[test]
    fn directory_filters_refuse_values_outside_the_contract() {
        for (query, field, detail_code) in [
            ("active=maybe", "active", "invalid_type"),
            ("active=1", "active", "invalid_type"),
            ("active=yes", "active", "invalid_type"),
            ("active=", "active", "invalid_type"),
            ("active=True", "active", "invalid_type"),
            ("city=", "city", "out_of_range"),
        ] {
            let error = directory_filters(query).unwrap_err();
            assert_eq!(
                (error.status, error.code.as_str()),
                (422, "invalid_query"),
                "{query}"
            );
            assert_eq!(error.details, [Detail::new(field, detail_code)], "{query}");
        }
        let long = format!("city={}", "a".repeat(81));
        let error = directory_filters(&long).unwrap_err();
        assert_eq!((error.status, error.code.as_str()), (422, "invalid_query"));
        assert_eq!(error.details, [Detail::new("city", "out_of_range")]);
        let ok = format!("city={}", "a".repeat(80));
        assert!(directory_filters(&ok).is_ok());
    }
}
