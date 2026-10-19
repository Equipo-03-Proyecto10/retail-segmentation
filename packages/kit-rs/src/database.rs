//! Small PostgreSQL pool with audit attribution and stable availability errors.

use std::ops::{Deref, DerefMut};
use std::str::FromStr;
use std::time::Duration;

use deadpool_postgres::{
    Manager, ManagerConfig, Object, Pool, PoolError, RecyclingMethod, Runtime,
};
use tokio_postgres::NoTls;

use crate::auth::Principal;
use crate::types::ApiError;

/// The most connections a service process opens (ADR-0045).
pub const MAX_CONNECTIONS: usize = 3;

/// Classes whose errors mean the database, not the statement, failed:
/// connection exception, insufficient resources, operator intervention and
/// system error, as psycopg's `OperationalError` groups them.
const UNAVAILABLE_CLASSES: [&str; 4] = ["08", "53", "57", "58"];

/// Driver failures of the connection itself, by their `Display` text; the
/// driver does not expose its error kind. Column, row and parameter errors are
/// the caller's bugs and stay 500.
const UNAVAILABLE_KINDS: [&str; 9] = [
    "error communicating with the server",
    "unexpected message from server",
    "error performing TLS handshake",
    "connection closed",
    "error parsing response from server",
    "error encoding message to server",
    "authentication error",
    "error connecting to server",
    "timeout waiting for server",
];

/// Map a driver error to the public error a handler returns.
pub fn map_postgres_error(error: &tokio_postgres::Error) -> ApiError {
    let unavailable = match error.code() {
        None => UNAVAILABLE_KINDS.contains(&error.to_string().as_str()),
        Some(state) => UNAVAILABLE_CLASSES.contains(&&state.code()[..2]),
    };
    if unavailable || error.is_closed() {
        ApiError::database_unavailable()
    } else {
        tracing::error!("Database statement failed: {error}");
        ApiError::internal()
    }
}

impl From<tokio_postgres::Error> for ApiError {
    fn from(error: tokio_postgres::Error) -> Self {
        map_postgres_error(&error)
    }
}

impl From<PoolError> for ApiError {
    fn from(error: PoolError) -> Self {
        match error {
            PoolError::Backend(error) => map_postgres_error(&error),
            _ => ApiError::database_unavailable(),
        }
    }
}

/// The service database pool.
pub struct Database {
    pool: Option<Pool>,
}

impl Database {
    /// Open a lazy pool of at most three connections. No `url` means every
    /// checkout answers 503 `database_unavailable`.
    pub fn new(url: Option<&str>, pool_timeout: Duration, connect_timeout: Duration) -> Self {
        let pool = url.and_then(|url| {
            let mut config = match tokio_postgres::Config::from_str(url) {
                Ok(config) => config,
                Err(error) => {
                    tracing::error!("DATABASE_URL cannot be parsed: {error}");
                    return None;
                }
            };
            if config.get_connect_timeout().is_none() {
                config.connect_timeout(connect_timeout);
            }
            let manager = Manager::from_config(
                config,
                NoTls,
                ManagerConfig {
                    recycling_method: RecyclingMethod::Fast,
                },
            );
            Pool::builder(manager)
                .max_size(MAX_CONNECTIONS)
                .runtime(Runtime::Tokio1)
                .wait_timeout(Some(pool_timeout))
                .create_timeout(Some(pool_timeout.max(connect_timeout)))
                .recycle_timeout(Some(pool_timeout))
                .build()
                .map_err(|error| tracing::error!("The database pool cannot be built: {error}"))
                .ok()
        });
        Self { pool }
    }

    pub fn max_size(&self) -> usize {
        self.pool.as_ref().map_or(0, |pool| pool.status().max_size)
    }

    /// Check out a connection.
    pub async fn connection(&self) -> Result<Connection, ApiError> {
        let pool = self
            .pool
            .as_ref()
            .ok_or_else(ApiError::database_unavailable)?;
        Ok(Connection(pool.get().await?))
    }

    pub fn close(&self) {
        if let Some(pool) = &self.pool {
            pool.close();
        }
    }
}

/// A pooled connection; dereferences to the `tokio_postgres` client.
pub struct Connection(Object);

impl Connection {
    /// Open a transaction for a request write, attaching the verified caller
    /// as the local audit actor with a bound parameter. The relay never uses
    /// this: its writes carry no request actor.
    pub async fn transaction(
        &mut self,
        actor: Option<&Principal>,
    ) -> Result<deadpool_postgres::Transaction<'_>, ApiError> {
        let transaction = self.0.transaction().await?;
        if let Some(principal) = actor {
            transaction
                .execute(
                    "SELECT set_config('mosaiq.user_id', $1, true)",
                    &[&principal.sub],
                )
                .await?;
        }
        Ok(transaction)
    }
}

impl Deref for Connection {
    type Target = Object;

    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

impl DerefMut for Connection {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.0
    }
}
