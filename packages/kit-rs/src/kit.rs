//! Contract-driven axum integration.

use std::collections::HashMap;
use std::future::Future;
use std::net::SocketAddr;
use std::panic::AssertUnwindSafe;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::{Arc, LazyLock};
use std::task::{Context, Poll};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::body::{Body, Bytes};
use axum::extract::{ConnectInfo, Request};
use axum::http::{header, HeaderMap, HeaderValue, Method, Response, StatusCode};
use axum::middleware::{self, Next};
use axum::routing::{on, MethodFilter, MethodRouter};
use axum::Router;
use http_body_util::{BodyExt, Limited};
use indexmap::IndexMap;
use regex::Regex;
use serde_json::Value as JsonValue;

use crate::auth::{service_redis_info, Authenticator, JwksStore, Principal, StateRedis};
use crate::contract::{Contract, KitError, Operation, PathMatcher, PathValue};
use crate::database::Database;
use crate::logging::{self, LogContext};
use crate::rendering::{render_error, render_reply, Format};
use crate::types::{ApiError, Detail, Reply};

/// The largest request body any operation accepts.
pub const MAX_BODY_BYTES: usize = 65_536;

static CORRELATION: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[0-9A-Za-z-]{1,64}$").expect("valid regex"));

const NOT_FOUND: &str = "The requested URL was not found on the server. If you entered the URL \
                         manually please check your spelling and try again.";
const METHOD_NOT_ALLOWED: &str = "The method is not allowed for the requested URL.";

type BoxFuture<T> = Pin<Box<dyn Future<Output = T> + Send>>;
type Handler = Arc<dyn Fn(Ctx) -> BoxFuture<Result<Reply, ApiError>> + Send + Sync>;
type ReadyCheck = Arc<dyn Fn(Arc<Database>) -> BoxFuture<bool> + Send + Sync>;

/// Everything the kit reads from the environment, with kit-py's defaults.
#[derive(Clone, Debug)]
pub struct KitConfig {
    pub issuer: String,
    pub jwks_path: PathBuf,
    pub redis_state_url: String,
    pub redis_password: Option<String>,
    pub rate_limit_enabled: bool,
    pub rate_limit_requests: u64,
    pub rate_limit_window_seconds: u64,
    pub database_url: Option<String>,
    pub database_pool_timeout: Duration,
    pub database_connect_timeout: Duration,
}

fn env_bool(name: &str, default: bool) -> bool {
    std::env::var(name).map_or(default, |value| {
        matches!(
            value.trim().to_lowercase().as_str(),
            "1" | "true" | "yes" | "on"
        )
    })
}

fn env_int(name: &str, default: u64, minimum: u64) -> u64 {
    std::env::var(name)
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|value| *value >= minimum)
        .unwrap_or(default)
}

fn env_seconds(name: &str, default: f64) -> Duration {
    std::env::var(name)
        .ok()
        .and_then(|value| value.parse::<f64>().ok())
        .filter(|value| value.is_finite() && *value >= 0.0)
        .map_or(Duration::from_secs_f64(default), Duration::from_secs_f64)
}

impl KitConfig {
    /// Read the variables kit-py reads; `suffix` names `REDIS_<SUFFIX>_PASSWORD`.
    pub fn from_env(suffix: &str) -> Self {
        let text =
            |name: &str, default: &str| std::env::var(name).unwrap_or_else(|_| default.to_owned());
        Self {
            issuer: text("AUTH_ISSUER", "https://auth.invalid/"),
            jwks_path: PathBuf::from(text("AUTH_JWKS_PATH", "/run/mosaiq/jwks.json")),
            redis_state_url: text("REDIS_STATE_URL", "redis://redis-state:6379/0"),
            redis_password: std::env::var(format!("REDIS_{}_PASSWORD", suffix.to_uppercase())).ok(),
            rate_limit_enabled: env_bool("RATE_LIMIT_ENABLED", true),
            rate_limit_requests: env_int("RATE_LIMIT_REQUESTS", 120, 1),
            rate_limit_window_seconds: env_int("RATE_LIMIT_WINDOW_SECONDS", 60, 1),
            database_url: std::env::var("DATABASE_URL")
                .ok()
                .filter(|url| !url.is_empty()),
            database_pool_timeout: env_seconds("DATABASE_POOL_TIMEOUT_SECONDS", 2.0),
            database_connect_timeout: Duration::from_secs(env_int(
                "DATABASE_CONNECT_TIMEOUT_SECONDS",
                2,
                1,
            )),
        }
    }
}

/// What a handler receives for one request.
pub struct Ctx {
    pub principal: Option<Principal>,
    pub body: Option<JsonValue>,
    pub page: u32,
    pub size: u32,
    pub path: HashMap<String, PathValue>,
    pub correlation_id: String,
    db: Arc<Database>,
}

impl Ctx {
    pub fn db(&self) -> &Database {
        &self.db
    }

    pub fn principal(&self) -> Option<&Principal> {
        self.principal.as_ref()
    }

    pub fn path_i64(&self, name: &str) -> Option<i64> {
        match self.path.get(name)? {
            PathValue::Integer(value) => Some(*value),
            _ => None,
        }
    }

    pub fn path_uuid(&self, name: &str) -> Option<uuid::Uuid> {
        match self.path.get(name)? {
            PathValue::Uuid(value) => Some(*value),
            _ => None,
        }
    }

    pub fn path_text(&self, name: &str) -> Option<&str> {
        match self.path.get(name)? {
            PathValue::Text(value) => Some(value),
            _ => None,
        }
    }
}

struct Shared {
    suffix: String,
    contract: Contract,
    auth: Authenticator,
    db: Arc<Database>,
    config: KitConfig,
}

/// Per-request values set before routing.
#[derive(Clone)]
struct RequestMeta {
    correlation_id: String,
    preferred: Format,
}

struct Route {
    operation: Arc<Operation>,
    matchers: Arc<Vec<(String, PathMatcher)>>,
    handler: Handler,
}

/// Install shared mechanisms around a service's staged contract.
pub struct Kit {
    service: String,
    shared: Arc<Shared>,
    routes: IndexMap<String, Vec<Route>>,
    ready_checks: Vec<ReadyCheck>,
}

impl Kit {
    /// Load `contracts_dir/<service>/` and connect lazily to `redis-state` and PostgreSQL.
    pub fn new(
        service: &str,
        suffix: &str,
        contracts_dir: impl AsRef<Path>,
        config: KitConfig,
    ) -> Result<Self, KitError> {
        let contract = Contract::load(contracts_dir, service)?;
        let info = service_redis_info(
            &config.redis_state_url,
            suffix,
            config.redis_password.clone(),
        )
        .map_err(|error| KitError(format!("REDIS_STATE_URL is invalid: {error}")))?;
        let redis = StateRedis::new(info)
            .map_err(|error| KitError(format!("redis-state client: {error}")))?;
        let auth = Authenticator::new(
            config.issuer.clone(),
            JwksStore::new(config.jwks_path.clone()),
            redis,
        );
        let db = Arc::new(Database::new(
            config.database_url.as_deref(),
            config.database_pool_timeout,
            config.database_connect_timeout,
        ));
        Ok(Self {
            service: service.to_owned(),
            shared: Arc::new(Shared {
                suffix: suffix.to_owned(),
                contract,
                auth,
                db,
                config,
            }),
            routes: IndexMap::new(),
            ready_checks: Vec::new(),
        })
    }

    pub fn contract(&self) -> &Contract {
        &self.shared.contract
    }

    pub fn database(&self) -> Arc<Database> {
        Arc::clone(&self.shared.db)
    }

    pub fn authenticator(&self) -> &Authenticator {
        &self.shared.auth
    }

    /// Register the route declared by an OpenAPI operation.
    pub fn operation<F, Fut>(mut self, operation_id: &str, handler: F) -> Result<Self, KitError>
    where
        F: Fn(Ctx) -> Fut + Send + Sync + 'static,
        Fut: Future<Output = Result<Reply, ApiError>> + Send + 'static,
    {
        let operation = self.shared.contract.operation(operation_id)?.clone();
        if operation.protected && operation.roles.is_empty() {
            return Err(KitError(format!(
                "Protected operation {operation_id:?} has no x-mosaiq-roles."
            )));
        }
        if operation.request_schema.is_some()
            && (operation.xml_name.is_none() || operation.xml_namespace.is_none())
        {
            return Err(KitError(format!(
                "Operation {operation_id:?} has a request body without an XML root."
            )));
        }
        let routes = self.routes.entry(operation.path.clone()).or_default();
        if routes
            .iter()
            .any(|route| route.operation.method == operation.method)
        {
            return Err(KitError(format!(
                "Operation {operation_id:?} is already registered."
            )));
        }
        let matchers = Arc::new(operation.path_matchers()?);
        let handler: Handler = Arc::new(move |ctx| Box::pin(handler(ctx)));
        routes.push(Route {
            operation: Arc::new(operation),
            matchers,
            handler,
        });
        Ok(self)
    }

    /// Add a readiness check; `false` makes `/health/ready` answer 503
    /// `schema_not_ready`.
    pub fn ready_check<F, Fut>(mut self, check: F) -> Self
    where
        F: Fn(Arc<Database>) -> Fut + Send + Sync + 'static,
        Fut: Future<Output = bool> + Send + 'static,
    {
        self.ready_checks
            .push(Arc::new(move |db| Box::pin(check(db))));
        self
    }

    /// Build the service router: the registered operations, the meta routes,
    /// 404 and 405 in the error envelope, and the correlation id.
    pub fn into_router(self) -> Router {
        let shared = self.shared;
        let mut router = Router::new();
        for (path, routes) in self.routes {
            let mut methods: Vec<Method> = Vec::new();
            let mut method_router: MethodRouter = MethodRouter::new();
            for route in routes {
                let Ok(method) = Method::from_bytes(route.operation.method.as_bytes()) else {
                    continue;
                };
                let Ok(filter) = MethodFilter::try_from(method.clone()) else {
                    continue;
                };
                methods.push(method);
                let shared = Arc::clone(&shared);
                method_router = method_router.on(filter, move |request: Request| {
                    let shared = Arc::clone(&shared);
                    let operation = Arc::clone(&route.operation);
                    let matchers = Arc::clone(&route.matchers);
                    let handler = Arc::clone(&route.handler);
                    async move { dispatch(shared, operation, matchers, handler, request).await }
                });
            }
            router = router.route(&path, with_method_fallback(method_router, methods));
        }
        let contract = &shared.contract;
        let openapi = contract.openapi_bytes.clone();
        let xsd = contract.xsd_bytes.clone();
        router = router
            .route(
                "/health/live",
                get_only(on(MethodFilter::GET, || async {
                    StatusCode::NO_CONTENT.into_response_body()
                })),
            )
            .route(
                "/openapi.yaml",
                get_only(on(MethodFilter::GET, move || {
                    let openapi = openapi.clone();
                    async move { bytes_response(openapi, "application/yaml") }
                })),
            )
            .route(
                "/schema.xsd",
                get_only(on(MethodFilter::GET, move || {
                    let xsd = xsd.clone();
                    async move { bytes_response(xsd, "application/xml") }
                })),
            );
        for (version, payload) in versioned_schemas(&contract.root, &self.service) {
            router = router.route(
                &format!("/schema.{version}.xsd"),
                get_only(on(MethodFilter::GET, move || {
                    let payload = payload.clone();
                    async move { bytes_response(payload, "application/xml") }
                })),
            );
        }
        let checks = Arc::new(self.ready_checks);
        let ready_shared = Arc::clone(&shared);
        router = router.route(
            "/health/ready",
            get_only(on(MethodFilter::GET, move |request: Request| {
                let checks = Arc::clone(&checks);
                let shared = Arc::clone(&ready_shared);
                async move { ready(shared, checks, request).await }
            })),
        );
        router
            .fallback(|request: Request| async move {
                error_response(&request, &ApiError::new(404, "not_found", NOT_FOUND), None)
            })
            .layer(middleware::from_fn(start_request))
    }
}

trait IntoResponseBody {
    fn into_response_body(self) -> Response<Body>;
}

impl IntoResponseBody for StatusCode {
    fn into_response_body(self) -> Response<Body> {
        let mut response = Response::new(Body::empty());
        *response.status_mut() = self;
        response
    }
}

fn bytes_response(payload: Bytes, content_type: &'static str) -> Response<Body> {
    let mut response = Response::new(Body::from(payload));
    response
        .headers_mut()
        .insert(header::CONTENT_TYPE, HeaderValue::from_static(content_type));
    response
}

fn versioned_schemas(root: &Path, service: &str) -> Vec<(String, Bytes)> {
    let prefix = format!("{service}.v");
    let mut found: Vec<(String, Bytes)> = std::fs::read_dir(root.join(service))
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|entry| {
            let name = entry.file_name().into_string().ok()?;
            let version = name
                .strip_prefix(&format!("{service}."))?
                .strip_suffix(".xsd")?
                .to_owned();
            if !name.starts_with(&prefix) {
                return None;
            }
            let payload = std::fs::read(entry.path()).ok()?;
            Some((version, Bytes::from(payload)))
        })
        .collect();
    found.sort_by(|left, right| left.0.cmp(&right.0));
    found
}

fn get_only(router: MethodRouter) -> MethodRouter {
    with_method_fallback(router, vec![Method::GET])
}

/// Answer an undeclared method with 405 and `Allow`, as Flask does.
fn with_method_fallback(router: MethodRouter, methods: Vec<Method>) -> MethodRouter {
    let mut allowed: Vec<String> = methods.iter().map(ToString::to_string).collect();
    if methods.contains(&Method::GET) && !methods.contains(&Method::HEAD) {
        allowed.push("HEAD".to_owned());
    }
    allowed.sort();
    let allow = allowed.join(", ");
    router.fallback(move |request: Request| {
        let allow = allow.clone();
        async move {
            error_response(
                &request,
                &ApiError::new(405, "method_not_allowed", METHOD_NOT_ALLOWED)
                    .with_header("Allow", allow),
                None,
            )
        }
    })
}

/// Correlation id, preferred error format and the log context, around every route.
async fn start_request(mut request: Request, next: Next) -> Response<Body> {
    let supplied = request
        .headers()
        .get("X-Correlation-ID")
        .and_then(|value| value.to_str().ok())
        .unwrap_or("");
    let correlation_id = if CORRELATION.is_match(supplied) {
        supplied.to_owned()
    } else {
        uuid::Uuid::new_v4().simple().to_string()
    };
    let preferred = preferred_format(request.headers());
    let client = client_address(&request);
    request.extensions_mut().insert(RequestMeta {
        correlation_id: correlation_id.clone(),
        preferred,
    });
    let context = LogContext {
        correlation_id: correlation_id.clone(),
        actor: "-".to_owned(),
        client,
    };
    let mut response = logging::scope(context, next.run(request)).await;
    if let Ok(value) = HeaderValue::from_str(&correlation_id) {
        response.headers_mut().insert("X-Correlation-ID", value);
    }
    response
}

fn client_address(request: &Request) -> String {
    request
        .headers()
        .get("X-Real-IP")
        .and_then(|value| value.to_str().ok())
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            request
                .extensions()
                .get::<ConnectInfo<SocketAddr>>()
                .map(|info| info.0.ip().to_string())
        })
        .unwrap_or_else(|| "-".to_owned())
}

fn meta(request: &Request) -> RequestMeta {
    request
        .extensions()
        .get::<RequestMeta>()
        .cloned()
        .unwrap_or(RequestMeta {
            correlation_id: "-".to_owned(),
            preferred: Format::Json,
        })
}

fn error_response(request: &Request, error: &ApiError, format: Option<Format>) -> Response<Body> {
    let meta = meta(request);
    render_error(
        error,
        &meta.correlation_id,
        format.unwrap_or(meta.preferred),
    )
}

/// One `Accept` member: its media range, specificity and quality.
struct AcceptItem {
    kind: String,
    subtype: String,
    quality: f64,
}

impl AcceptItem {
    fn specificity(&self) -> (bool, bool) {
        (self.kind != "*", self.subtype != "*")
    }

    fn matches(&self, media: &str) -> bool {
        let (kind, subtype) = media.split_once('/').unwrap_or((media, ""));
        (self.kind == "*" && self.subtype == "*")
            || (self.kind == kind && (self.subtype == "*" || self.subtype == subtype))
    }
}

fn accept_items(accept: &str) -> Vec<AcceptItem> {
    let mut items: Vec<AcceptItem> = accept
        .split(',')
        .filter_map(|member| {
            let mut parts = member.split(';');
            let range = parts.next()?.trim().to_lowercase();
            if range.is_empty() {
                return None;
            }
            let mut quality = 1.0;
            for parameter in parts {
                if let Some((name, value)) = parameter.split_once('=') {
                    if name.trim().eq_ignore_ascii_case("q") {
                        quality = value.trim().parse::<f64>().ok()?.clamp(0.0, 1.0);
                    }
                }
            }
            let (kind, subtype) = if range == "*" {
                ("*".to_owned(), "*".to_owned())
            } else {
                let (kind, subtype) = range.split_once('/')?;
                (kind.trim().to_owned(), subtype.trim().to_owned())
            };
            Some(AcceptItem {
                kind,
                subtype,
                quality,
            })
        })
        .collect();
    // Werkzeug orders by specificity, then quality, most first.
    items.sort_by(|left, right| {
        (right.specificity(), right.quality)
            .partial_cmp(&(left.specificity(), left.quality))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    items
}

/// Werkzeug's `best_match` over JSON and XML, with the winner's quality.
fn best_match(accept: &str) -> Option<(Format, f64)> {
    let items = accept_items(accept);
    let mut result = None;
    let mut best_quality = -1.0;
    let mut best_specificity = (false, false);
    let mut has_best = false;
    for (media, format) in [
        ("application/json", Format::Json),
        ("application/xml", Format::Xml),
    ] {
        for item in &items {
            if item.quality <= 0.0 || item.quality < best_quality {
                continue;
            }
            if item.matches(media)
                && (item.quality > best_quality
                    || !has_best
                    || item.specificity() > best_specificity)
            {
                result = Some(format);
                best_quality = item.quality;
                best_specificity = item.specificity();
                has_best = true;
            }
        }
    }
    let format = result?;
    let media = match format {
        Format::Json => "application/json",
        Format::Xml => "application/xml",
    };
    let quality = items
        .iter()
        .find(|item| item.matches(media))
        .map_or(0.0, |item| item.quality);
    Some((format, quality))
}

fn accept_header(headers: &HeaderMap) -> Option<&str> {
    let accept = headers.get(header::ACCEPT)?.to_str().unwrap_or("");
    (!accept.is_empty() && accept.trim() != "*/*").then_some(accept)
}

fn preferred_format(headers: &HeaderMap) -> Format {
    let Some(accept) = accept_header(headers) else {
        return Format::Json;
    };
    match best_match(accept) {
        Some((Format::Xml, quality)) if quality > 0.0 => Format::Xml,
        _ => Format::Json,
    }
}

fn negotiate(headers: &HeaderMap) -> Result<Format, ApiError> {
    let Some(accept) = accept_header(headers) else {
        return Ok(Format::Json);
    };
    match best_match(accept) {
        Some((format, quality)) if quality > 0.0 => Ok(format),
        _ => Err(ApiError::new(
            406,
            "not_acceptable",
            "The requested response format is unsupported.",
        )),
    }
}

fn query_value(request: &Request, name: &str) -> Option<String> {
    let query = request.uri().query()?;
    form_urlencoded::parse(query.as_bytes())
        .find(|(key, _)| key == name)
        .map(|(_, value)| value.into_owned())
}

fn pagination(request: &Request) -> Result<(u32, u32), ApiError> {
    let mut details = Vec::new();
    let mut values = [1u32, 20u32];
    for (index, (name, default, minimum, maximum)) in
        [("page", 1u64, 1u64, None), ("size", 20, 1, Some(100u64))]
            .into_iter()
            .enumerate()
    {
        let raw = query_value(request, name);
        if let Some(raw) = &raw {
            if raw.is_empty() || !raw.bytes().all(|byte| byte.is_ascii_digit()) {
                details.push(Detail::new(name, "invalid_type"));
                continue;
            }
        }
        let value = match &raw {
            None => Some(default),
            Some(raw) => raw.parse::<u64>().ok(),
        };
        match value {
            Some(value)
                if value >= minimum
                    && maximum.is_none_or(|maximum| value <= maximum)
                    && value <= u64::from(u32::MAX) =>
            {
                values[index] = value as u32;
            }
            _ => details.push(Detail::new(name, "out_of_range")),
        }
    }
    if details.is_empty() {
        Ok((values[0], values[1]))
    } else {
        Err(ApiError::new(
            422,
            "invalid_pagination",
            "Pagination values are outside their allowed ranges.",
        )
        .with_details(details))
    }
}

async fn capped_body(request: Request) -> Result<(axum::http::request::Parts, Bytes), ApiError> {
    let declared = request
        .headers()
        .get(header::CONTENT_LENGTH)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.trim().parse::<u64>().ok());
    if declared.is_some_and(|length| length > MAX_BODY_BYTES as u64) {
        return Err(ApiError::payload_too_large());
    }
    let (parts, body) = request.into_parts();
    let collected = Limited::new(body, MAX_BODY_BYTES)
        .collect()
        .await
        .map_err(|error| {
            if error.is::<http_body_util::LengthLimitError>() {
                ApiError::payload_too_large()
            } else {
                ApiError::malformed_body()
            }
        })?;
    Ok((parts, collected.to_bytes()))
}

async fn rate_limit(
    shared: &Shared,
    client: String,
    principal: Option<&Principal>,
) -> Result<(), ApiError> {
    let config = &shared.config;
    if !config.rate_limit_enabled {
        return Ok(());
    }
    let caller = principal.map_or_else(
        || {
            if client == "-" {
                "unknown".to_owned()
            } else {
                client
            }
        },
        |principal| principal.sub.clone(),
    );
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |elapsed| elapsed.as_secs());
    let window = config.rate_limit_window_seconds;
    let key = format!("{}:rl:{caller}:{}", shared.suffix, now / window);
    let outcome = shared
        .auth
        .redis()
        .run(|mut connection| async move {
            let (_created, count): (Option<String>, u64) = redis::pipe()
                .cmd("SET")
                .arg(&key)
                .arg(0)
                .arg("EX")
                .arg(2 * window)
                .arg("NX")
                .cmd("INCR")
                .arg(&key)
                .query_async(&mut connection)
                .await?;
            Ok(count)
        })
        .await;
    match outcome {
        Ok(count) if count > config.rate_limit_requests => {
            let retry_after = (window - now % window).max(1);
            Err(
                ApiError::new(429, "rate_limited", "The request limit was exceeded.")
                    .with_header("Retry-After", retry_after.to_string()),
            )
        }
        Ok(_) => Ok(()),
        Err(error) => {
            tracing::warn!("Rate-limit storage failed: {error}");
            Ok(())
        }
    }
}

fn media_type(headers: &HeaderMap) -> String {
    headers
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.split(';').next())
        .map(|value| value.trim().to_lowercase())
        .unwrap_or_default()
}

/// Polls a future and turns a panic into an error.
struct CatchUnwind<F>(Pin<Box<F>>);

impl<F: Future> Future for CatchUnwind<F> {
    type Output = Result<F::Output, ()>;

    fn poll(mut self: Pin<&mut Self>, context: &mut Context<'_>) -> Poll<Self::Output> {
        let inner = self.0.as_mut();
        match std::panic::catch_unwind(AssertUnwindSafe(|| inner.poll(context))) {
            Ok(Poll::Pending) => Poll::Pending,
            Ok(Poll::Ready(value)) => Poll::Ready(Ok(value)),
            Err(_) => Poll::Ready(Err(())),
        }
    }
}

async fn dispatch(
    shared: Arc<Shared>,
    operation: Arc<Operation>,
    matchers: Arc<Vec<(String, PathMatcher)>>,
    handler: Handler,
    request: Request,
) -> Response<Body> {
    let meta = meta(&request);
    let mut format = meta.preferred;
    match handle(
        &shared,
        &operation,
        &matchers,
        &handler,
        &meta,
        &mut format,
        request,
    )
    .await
    {
        Ok(response) => response,
        Err(error) => render_error(&error, &meta.correlation_id, format),
    }
}

async fn handle(
    shared: &Shared,
    operation: &Operation,
    matchers: &[(String, PathMatcher)],
    handler: &Handler,
    meta: &RequestMeta,
    format: &mut Format,
    request: Request,
) -> Result<Response<Body>, ApiError> {
    let path = path_values(&operation.path, request.uri().path(), matchers)
        .ok_or_else(|| ApiError::new(404, "not_found", NOT_FOUND))?;
    let (parts, raw) = capped_body(request).await?;
    let request = Request::from_parts(parts, Body::empty());
    let mut principal = None;
    if operation.protected {
        let authorization = request
            .headers()
            .get(header::AUTHORIZATION)
            .and_then(|value| value.to_str().ok());
        let verified = shared.auth.verify(authorization).await?;
        logging::set_actor(&verified.sub);
        if !operation.roles.is_empty() && !operation.roles.contains(&verified.role) {
            return Err(ApiError::new(
                403,
                "forbidden_role",
                "The authenticated role cannot perform this operation.",
            ));
        }
        principal = Some(verified);
    }
    rate_limit(shared, client_address(&request), principal.as_ref()).await?;
    *format = negotiate(request.headers())?;
    let mut body = None;
    if operation.request_schema.is_some() {
        let parsed = match media_type(request.headers()).as_str() {
            "application/json" => shared.contract.parse_json(&raw)?,
            "application/xml" => shared.contract.parse_xml(&raw, operation)?,
            _ => {
                return Err(ApiError::new(
                    415,
                    "unsupported_media_type",
                    "The request format is unsupported.",
                ))
            }
        };
        shared.contract.validate(&parsed, operation)?;
        body = Some(parsed);
    }
    let (page, size) = if operation.paginated() {
        pagination(&request)?
    } else {
        (1, 20)
    };
    let ctx = Ctx {
        principal,
        body,
        page,
        size,
        path,
        correlation_id: meta.correlation_id.clone(),
        db: Arc::clone(&shared.db),
    };
    let reply = match CatchUnwind(Box::pin(handler(ctx))).await {
        Ok(reply) => reply?,
        Err(()) => {
            tracing::error!("Unhandled service error in {}", operation.operation_id);
            return Err(ApiError::internal());
        }
    };
    Ok(render_reply(
        &reply,
        *format,
        &shared.contract.namespace,
        size,
    ))
}

/// Match the request path against the template's typed segments.
fn path_values(
    template: &str,
    path: &str,
    matchers: &[(String, PathMatcher)],
) -> Option<HashMap<String, PathValue>> {
    let mut values = HashMap::new();
    let mut matchers = matchers.iter();
    let template_segments: Vec<&str> = template.split('/').collect();
    let path_segments: Vec<&str> = path.split('/').collect();
    if template_segments.len() != path_segments.len() {
        return None;
    }
    for (expected, actual) in template_segments.iter().zip(path_segments) {
        if expected.starts_with('{') && expected.ends_with('}') {
            let (name, matcher) = matchers.next()?;
            let decoded = percent_encoding::percent_decode_str(actual)
                .decode_utf8()
                .ok()?;
            values.insert(name.clone(), matcher.matches(&decoded)?);
        } else if *expected != actual {
            return None;
        }
    }
    Some(values)
}

async fn ready(
    shared: Arc<Shared>,
    checks: Arc<Vec<ReadyCheck>>,
    request: Request,
) -> Response<Body> {
    let mut is_ready = true;
    for check in checks.iter() {
        match CatchUnwind(Box::pin(check(Arc::clone(&shared.db)))).await {
            Ok(true) => {}
            Ok(false) => {
                is_ready = false;
                break;
            }
            Err(()) => {
                tracing::warn!("Readiness check failed with a panic");
                is_ready = false;
                break;
            }
        }
    }
    if is_ready {
        StatusCode::NO_CONTENT.into_response_body()
    } else {
        error_response(
            &request,
            &ApiError::new(
                503,
                "schema_not_ready",
                "The required service schema has not been applied.",
            ),
            None,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn negotiation_follows_werkzeug() {
        assert_eq!(
            best_match("application/xml").map(|m| m.0),
            Some(Format::Xml)
        );
        assert_eq!(
            best_match("application/json;q=0.5, application/xml").map(|m| m.0),
            Some(Format::Xml)
        );
        assert_eq!(best_match("application/*").map(|m| m.0), Some(Format::Json));
        assert_eq!(best_match("text/plain"), None);
        assert_eq!(best_match("application/xml;q=0"), None);
    }

    #[test]
    fn path_values_respect_the_template() {
        let matchers = vec![(
            "note_id".to_owned(),
            PathMatcher::Integer {
                minimum: Some(1),
                maximum: Some(10),
            },
        )];
        let template = "/v1/notes/{note_id}";
        assert_eq!(
            path_values(template, "/v1/notes/3", &matchers).and_then(|v| v.get("note_id").cloned()),
            Some(PathValue::Integer(3))
        );
        assert!(path_values(template, "/v1/notes/0", &matchers).is_none());
        assert!(path_values(template, "/v1/notes/-1", &matchers).is_none());
        assert!(path_values(template, "/v1/notes/11", &matchers).is_none());
    }
}
