//! RS256 token verification and fail-closed session liveness.

use std::collections::HashMap;
use std::future::Future;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use jsonwebtoken::{decode, decode_header, Algorithm, DecodingKey, Validation};
use redis::aio::MultiplexedConnection;
use redis::{AsyncConnectionConfig, ConnectionInfo, IntoConnectionInfo};
use serde_json::{Map, Value as JsonValue};

use crate::types::ApiError;

const LIVENESS_PREFIX: &str = "pub:auth:v1:live:";
const AUDIENCE: &str = "mosaiq-api";
const UNKNOWN_KID_RELOAD: Duration = Duration::from_secs(30);
const STATE_TIMEOUT: Duration = Duration::from_millis(200);

/// Incremented on every SIGHUP; each store reloads when it sees a new value.
static RELOAD_GENERATION: AtomicU64 = AtomicU64::new(0);
static SIGHUP_LISTENER: OnceLock<()> = OnceLock::new();

fn install_sighup_listener() {
    SIGHUP_LISTENER.get_or_init(|| {
        if let Ok(handle) = tokio::runtime::Handle::try_current() {
            handle.spawn(async {
                let Ok(mut hangups) =
                    tokio::signal::unix::signal(tokio::signal::unix::SignalKind::hangup())
                else {
                    tracing::warn!("SIGHUP cannot be observed; JWKS reloads on unknown kid only");
                    return;
                };
                while hangups.recv().await.is_some() {
                    RELOAD_GENERATION.fetch_add(1, Ordering::SeqCst);
                }
            });
        }
    });
}

/// Mark every key set stale, as a SIGHUP does.
pub fn request_jwks_reload() {
    RELOAD_GENERATION.fetch_add(1, Ordering::SeqCst);
}

struct JwksState {
    keys: HashMap<String, DecodingKey>,
    loaded: bool,
    generation: u64,
    unknown_reload_at: Option<Instant>,
    reloads: u64,
}

/// A reloadable, public-key-only JWKS.
pub struct JwksStore {
    path: PathBuf,
    state: Mutex<JwksState>,
}

#[derive(Debug)]
pub struct UnknownKey;

impl JwksStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        install_sighup_listener();
        Self {
            path: path.into(),
            state: Mutex::new(JwksState {
                keys: HashMap::new(),
                loaded: false,
                generation: RELOAD_GENERATION.load(Ordering::SeqCst),
                unknown_reload_at: None,
                reloads: 0,
            }),
        }
    }

    /// How many times the file has been read; for tests.
    pub fn reloads(&self) -> u64 {
        self.state.lock().map(|state| state.reloads).unwrap_or(0)
    }

    fn reload(&self, state: &mut JwksState) -> Result<(), UnknownKey> {
        state.reloads += 1;
        state.loaded = true;
        state.generation = RELOAD_GENERATION.load(Ordering::SeqCst);
        let text = std::fs::read_to_string(&self.path).map_err(|_| UnknownKey)?;
        let document: JsonValue = serde_json::from_str(&text).map_err(|_| UnknownKey)?;
        let mut keys = HashMap::new();
        for item in document
            .get("keys")
            .and_then(JsonValue::as_array)
            .into_iter()
            .flatten()
        {
            let Some(kid) = item.get("kid").and_then(JsonValue::as_str) else {
                continue;
            };
            let field = |name: &str| item.get(name).and_then(JsonValue::as_str);
            // A key that declares another algorithm can never verify an RS256 token.
            if field("kty") != Some("RSA") || field("alg").is_some_and(|alg| alg != "RS256") {
                continue;
            }
            if let (Some(n), Some(e)) = (field("n"), field("e")) {
                if let Ok(key) = DecodingKey::from_rsa_components(n, e) {
                    keys.insert(kid.to_owned(), key);
                }
            }
        }
        state.keys = keys;
        Ok(())
    }

    /// Return the key for `kid`, reloading on SIGHUP or, at most once every
    /// 30 seconds, on an unknown key id.
    pub fn key(&self, kid: &str) -> Result<DecodingKey, UnknownKey> {
        self.key_at(kid, Instant::now())
    }

    pub fn key_at(&self, kid: &str, now: Instant) -> Result<DecodingKey, UnknownKey> {
        let mut state = self.state.lock().map_err(|_| UnknownKey)?;
        let mut reloaded = false;
        if !state.loaded || state.generation != RELOAD_GENERATION.load(Ordering::SeqCst) {
            self.reload(&mut state)?;
            reloaded = true;
        }
        if let Some(key) = state.keys.get(kid) {
            return Ok(key.clone());
        }
        if reloaded {
            state.unknown_reload_at = Some(now);
        } else if state
            .unknown_reload_at
            .is_none_or(|at| now.saturating_duration_since(at) >= UNKNOWN_KID_RELOAD)
        {
            state.unknown_reload_at = Some(now);
            self.reload(&mut state)?;
        }
        state.keys.get(kid).cloned().ok_or(UnknownKey)
    }
}

/// Why a `redis-state` call failed.
#[derive(Debug)]
pub enum StateError {
    Timeout,
    Redis(redis::RedisError),
}

impl std::fmt::Display for StateError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StateError::Timeout => f.write_str("redis-state did not answer within 200 ms"),
            StateError::Redis(error) => write!(f, "{error}"),
        }
    }
}

/// Connection details for the restricted `svc_<suffix>` user on `redis-state`.
///
/// Credentials in the URL are discarded: the user is always `svc_<suffix>` and
/// the password comes from `REDIS_<SUFFIX>_PASSWORD`.
pub fn service_redis_info(
    url: &str,
    suffix: &str,
    password: Option<String>,
) -> redis::RedisResult<ConnectionInfo> {
    let mut info = url.into_connection_info()?;
    info.redis.username = Some(format!("svc_{suffix}"));
    info.redis.password = password;
    Ok(info)
}

/// A lazily connected `redis-state` client with a 200 ms budget per call and
/// no retries.
pub struct StateRedis {
    client: redis::Client,
    connection: tokio::sync::Mutex<Option<MultiplexedConnection>>,
}

impl StateRedis {
    pub fn new(info: ConnectionInfo) -> redis::RedisResult<Self> {
        Ok(Self {
            client: redis::Client::open(info)?,
            connection: tokio::sync::Mutex::new(None),
        })
    }

    async fn connection(&self) -> redis::RedisResult<MultiplexedConnection> {
        let mut slot = self.connection.lock().await;
        if let Some(connection) = slot.as_ref() {
            return Ok(connection.clone());
        }
        let config = AsyncConnectionConfig::new()
            .set_connection_timeout(STATE_TIMEOUT)
            .set_response_timeout(STATE_TIMEOUT);
        let connection = self
            .client
            .get_multiplexed_async_connection_with_config(&config)
            .await?;
        *slot = Some(connection.clone());
        Ok(connection)
    }

    async fn forget(&self) {
        *self.connection.lock().await = None;
    }

    /// Run `operation` on the shared connection within 200 ms.
    pub async fn run<T, F, Fut>(&self, operation: F) -> Result<T, StateError>
    where
        F: FnOnce(MultiplexedConnection) -> Fut,
        Fut: Future<Output = redis::RedisResult<T>>,
    {
        let attempt = async {
            let connection = self.connection().await?;
            operation(connection).await
        };
        match tokio::time::timeout(STATE_TIMEOUT, attempt).await {
            Ok(Ok(value)) => Ok(value),
            Ok(Err(error)) => {
                if error.is_io_error()
                    || error.is_connection_dropped()
                    || error.is_unrecoverable_error()
                    || error.is_timeout()
                {
                    self.forget().await;
                }
                Err(StateError::Redis(error))
            }
            Err(_) => {
                self.forget().await;
                Err(StateError::Timeout)
            }
        }
    }
}

/// The verified claims of a bearer token.
#[derive(Clone, Debug)]
pub struct Principal {
    pub sub: String,
    pub sid: String,
    pub role: String,
    pub claims: Map<String, JsonValue>,
}

fn claim_text(value: &JsonValue) -> String {
    match value {
        JsonValue::String(text) => text.clone(),
        other => other.to_string(),
    }
}

/// Verify bearer tokens and require a live Redis session.
pub struct Authenticator {
    issuer: String,
    jwks: JwksStore,
    redis: StateRedis,
}

impl Authenticator {
    pub fn new(issuer: String, jwks: JwksStore, redis: StateRedis) -> Self {
        Self {
            issuer,
            jwks,
            redis,
        }
    }

    pub fn redis(&self) -> &StateRedis {
        &self.redis
    }

    pub fn jwks(&self) -> &JwksStore {
        &self.jwks
    }

    fn verify_token(&self, authorization: Option<&str>) -> Result<Principal, ApiError> {
        let token = authorization
            .and_then(|value| value.strip_prefix("Bearer "))
            .filter(|token| !token.is_empty() && !token.contains(char::is_whitespace))
            .ok_or_else(ApiError::invalid_token)?;
        let header = decode_header(token).map_err(|_| ApiError::invalid_token())?;
        if header.alg != Algorithm::RS256 {
            return Err(ApiError::invalid_token());
        }
        let kid = header.kid.ok_or_else(ApiError::invalid_token)?;
        let key = self.jwks.key(&kid).map_err(|_| ApiError::invalid_token())?;
        let mut validation = Validation::new(Algorithm::RS256);
        validation.leeway = 0;
        validation.validate_nbf = true;
        validation.set_audience(&[AUDIENCE]);
        validation.set_issuer(&[self.issuer.as_str()]);
        validation.set_required_spec_claims(&["exp", "iss", "aud", "sub"]);
        let claims = decode::<Map<String, JsonValue>>(token, &key, &validation)
            .map_err(|_| ApiError::invalid_token())?
            .claims;
        // The audience is the single string `mosaiq-api`; a list is refused
        // even when it contains it.
        if claims.get("aud").and_then(JsonValue::as_str) != Some(AUDIENCE) {
            return Err(ApiError::invalid_token());
        }
        if let Some(issued_at) = claims.get("iat") {
            let issued_at = issued_at.as_f64().ok_or_else(ApiError::invalid_token)?;
            if issued_at > jsonwebtoken::get_current_timestamp() as f64 {
                return Err(ApiError::invalid_token());
            }
        }
        let required = |name: &str| claims.get(name).map(claim_text);
        match (required("sub"), required("sid"), required("role")) {
            (Some(sub), Some(sid), Some(role)) => Ok(Principal {
                sub,
                sid,
                role,
                claims,
            }),
            _ => Err(ApiError::invalid_token()),
        }
    }

    /// Verify the `Authorization` header and the session's liveness key.
    pub async fn verify(&self, authorization: Option<&str>) -> Result<Principal, ApiError> {
        let principal = self.verify_token(authorization)?;
        let key = format!("{LIVENESS_PREFIX}{}", principal.sid);
        let live = self
            .redis
            .run(|mut connection| async move {
                redis::cmd("EXISTS")
                    .arg(&key)
                    .query_async::<i64>(&mut connection)
                    .await
            })
            .await
            .map_err(|_| {
                ApiError::new(503, "auth_unavailable", "Session state cannot be checked.")
            })?;
        if live == 0 {
            return Err(ApiError::new(
                401,
                "session_revoked",
                "The token session is no longer live.",
            ));
        }
        Ok(principal)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store_with(contents: &str) -> (tempdir::Dir, JwksStore) {
        let dir = tempdir::Dir::new();
        let path = dir.path().join("jwks.json");
        std::fs::write(&path, contents).unwrap();
        let store = JwksStore::new(&path);
        (dir, store)
    }

    mod tempdir {
        use std::path::{Path, PathBuf};

        pub struct Dir(PathBuf);

        impl Dir {
            pub fn new() -> Self {
                let path = std::env::temp_dir().join(format!("kit-rs-{}", uuid::Uuid::new_v4()));
                std::fs::create_dir_all(&path).unwrap();
                Self(path)
            }

            pub fn path(&self) -> &Path {
                &self.0
            }
        }

        impl Drop for Dir {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
    }

    // One test: SIGHUP's generation counter is process-wide.
    #[test]
    fn unknown_key_reloads_are_limited_and_explicit_reloads_are_honored() {
        unknown_key_reloads_at_most_once_per_thirty_seconds();
        explicit_reload_is_always_honored();
    }

    fn unknown_key_reloads_at_most_once_per_thirty_seconds() {
        let (_dir, store) = store_with(r#"{"keys": []}"#);
        let now = Instant::now();
        assert!(store.key_at("missing", now).is_err());
        let first = store.reloads();
        assert!(store.key_at("missing", now).is_err());
        assert!(store
            .key_at("missing", now + Duration::from_secs(29))
            .is_err());
        assert_eq!(store.reloads(), first);
        assert!(store
            .key_at("missing", now + Duration::from_secs(30))
            .is_err());
        assert_eq!(store.reloads(), first + 1);
    }

    fn explicit_reload_is_always_honored() {
        let source = concat!(env!("CARGO_MANIFEST_DIR"), "/../kit-vectors/jwks.json");
        let (_dir, store) = store_with(&std::fs::read_to_string(source).unwrap());
        assert!(store.key("kit-vector-key").is_ok());
        let before = store.reloads();
        request_jwks_reload();
        assert!(store.key("kit-vector-key").is_ok());
        assert_eq!(store.reloads(), before + 1);
    }
}
