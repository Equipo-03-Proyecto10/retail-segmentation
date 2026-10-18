//! The kit: meta routes, the request context, and the operation pipeline in
//! kit-py `_dispatch`'s order.

use std::cell::RefCell;
use std::collections::HashMap;
use std::future::Future;
use std::path::PathBuf;
use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::{FromRequestParts, Path, Request, State};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{MethodFilter, get, on};
use bytes::Bytes;
use futures::future::BoxFuture;
use http::{HeaderValue, Method, StatusCode, header};
use tower_http::catch_panic::CatchPanicLayer;

use crate::contract::{Contract, ContractError};
use crate::error::ApiError;
use crate::logging::{CONTEXT, LogContext};
use crate::render::{Reply, render_error, render_reply};
use crate::request::{
    Format, capped_body, correlation_id, media_type, negotiate, pagination, preferred_format,
};

/// A readiness check a service supplies; `false` answers 503
/// `schema_not_ready`.
pub type ReadyCheck = Arc<dyn Fn() -> BoxFuture<'static, bool> + Send + Sync>;

struct Inner {
    service: String,
    suffix: String,
    contract: Contract,
    ready_checks: Vec<ReadyCheck>,
}

/// Shared mechanisms around a service's staged contract (kit-py `Kit`).
#[derive(Clone)]
pub struct Kit {
    inner: Arc<Inner>,
}

/// Builds a [`Kit`].
pub struct KitBuilder {
    service: String,
    suffix: String,
    contracts_dir: PathBuf,
    ready_checks: Vec<ReadyCheck>,
}

impl KitBuilder {
    /// The staged contracts directory; `contracts` by default.
    pub fn contracts_dir(mut self, path: impl Into<PathBuf>) -> Self {
        self.contracts_dir = path.into();
        self
    }

    /// Add a readiness check, such as "my schema section is applied".
    pub fn ready_check<F, Fut>(mut self, check: F) -> Self
    where
        F: Fn() -> Fut + Send + Sync + 'static,
        Fut: Future<Output = bool> + Send + 'static,
    {
        self.ready_checks.push(Arc::new(move || {
            Box::pin(check()) as BoxFuture<'static, bool>
        }));
        self
    }

    pub fn build(self) -> Result<Kit, ContractError> {
        let contract = Contract::load(&self.contracts_dir, &self.service)?;
        Ok(Kit {
            inner: Arc::new(Inner {
                service: self.service,
                suffix: self.suffix,
                contract,
                ready_checks: self.ready_checks,
            }),
        })
    }
}

impl Kit {
    /// `service` names the contract (`contracts/<service>/`); `suffix` is the
    /// service's `svc_` role suffix.
    pub fn builder(service: impl Into<String>, suffix: impl Into<String>) -> KitBuilder {
        KitBuilder {
            service: service.into(),
            suffix: suffix.into(),
            contracts_dir: PathBuf::from("contracts"),
            ready_checks: Vec::new(),
        }
    }

    pub fn service(&self) -> &str {
        &self.inner.service
    }

    pub fn suffix(&self) -> &str {
        &self.inner.suffix
    }

    pub fn contracts_dir(&self) -> &std::path::Path {
        &self.inner.contract.root
    }

    /// Start collecting the service's operation routes.
    pub fn routes(&self) -> Routes {
        Routes {
            kit: self.clone(),
            router: Router::new(),
        }
    }
}

/// An operation as the contract declares it.
#[derive(Clone, Debug)]
pub struct Operation {
    pub id: String,
    pub method: Method,
    /// An axum path, `/v1/notes/{note_id}`.
    pub path: String,
    /// Declared security: a bearer token is required.
    pub protected: bool,
    /// `x-mosaiq-roles`; empty means any authenticated role.
    pub roles: Vec<String>,
    /// The operation declares a request body.
    pub accepts_body: bool,
    /// The operation declares `page` and `size`.
    pub paginated: bool,
}

/// What a handler receives.
#[derive(Clone, Debug)]
pub struct Context {
    pub correlation_id: String,
    /// The negotiated response format.
    pub format: Format,
    /// Path parameters, by name.
    pub path: HashMap<String, String>,
    /// The raw query string.
    pub query: Option<String>,
    /// The request body, at most 64 KiB.
    pub body: Bytes,
    /// Validated pagination; 1 and 20 for an unpaged operation.
    pub page: u64,
    pub size: u64,
}

/// The format an error must be rendered in, once known, and the error.
#[derive(Clone)]
struct PendingError {
    error: ApiError,
    format: Option<Format>,
}

/// What the context middleware knows about the request.
#[derive(Clone)]
struct RequestInfo {
    correlation_id: String,
}

fn pending(error: ApiError, format: Option<Format>) -> Response {
    let mut response = Response::new(Body::empty());
    *response.status_mut() =
        StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    response
        .extensions_mut()
        .insert(PendingError { error, format });
    response
}

/// Collects operation routes, then wraps them in the kit's mechanisms.
pub struct Routes {
    kit: Kit,
    router: Router,
}

impl Routes {
    /// Register a handler for an operation.
    #[doc(hidden)]
    pub fn route_operation<H, Fut>(mut self, operation: Operation, handler: H) -> Self
    where
        H: Fn(Context) -> Fut + Clone + Send + Sync + 'static,
        Fut: Future<Output = Result<Reply, ApiError>> + Send + 'static,
    {
        let filter = MethodFilter::try_from(operation.method.clone())
            .unwrap_or_else(|_| panic!("unsupported method for {}", operation.id));
        let kit = self.kit.clone();
        let path = operation.path.clone();
        let operation = Arc::new(operation);
        self.router = self.router.route(
            &path,
            on(filter, move |request: Request| {
                let kit = kit.clone();
                let operation = operation.clone();
                let handler = handler.clone();
                async move { dispatch(kit, operation, handler, request).await }
            }),
        );
        self
    }

    /// The service's router: operations, meta routes, routing errors in the
    /// envelope, the correlation id on every response.
    pub fn into_router(self) -> Router {
        let kit = self.kit;
        let mut router = self
            .router
            .route("/health/live", get(|| async { StatusCode::NO_CONTENT }))
            .route("/health/ready", get(ready).with_state(kit.clone()))
            .route("/openapi.yaml", get(openapi).with_state(kit.clone()))
            .route("/schema.xsd", get(schema).with_state(kit.clone()));
        for (version, bytes) in &kit.inner.contract.versioned_xsd {
            let bytes = bytes.clone();
            router = router.route(
                &format!("/schema.{version}.xsd"),
                get(move || {
                    let bytes = bytes.clone();
                    async move { file(bytes, "application/xml") }
                }),
            );
        }
        router
            .fallback(|| async {
                pending(
                    ApiError::new(
                        404,
                        "not_found",
                        "The requested URL was not found on the server. If you entered the \
                         URL manually please check your spelling and try again.",
                    ),
                    None,
                )
            })
            .method_not_allowed_fallback(|| async {
                pending(
                    ApiError::new(
                        405,
                        "method_not_allowed",
                        "The method is not allowed for the requested URL.",
                    ),
                    None,
                )
            })
            .layer(CatchPanicLayer::custom(
                |panic: Box<dyn std::any::Any + Send>| {
                    let message = panic
                        .downcast_ref::<String>()
                        .map(String::as_str)
                        .or_else(|| panic.downcast_ref::<&str>().copied())
                        .unwrap_or("panic");
                    tracing::error!("Unhandled service error: {message}");
                    pending(ApiError::internal(), None)
                },
            ))
            .layer(middleware::from_fn(request_context))
    }
}

fn file(bytes: Bytes, content_type: &'static str) -> Response {
    let mut response = Response::new(Body::from(bytes));
    response
        .headers_mut()
        .insert(header::CONTENT_TYPE, HeaderValue::from_static(content_type));
    response
}

async fn openapi(State(kit): State<Kit>) -> Response {
    file(kit.inner.contract.openapi.clone(), "application/yaml")
}

async fn schema(State(kit): State<Kit>) -> Response {
    file(kit.inner.contract.xsd.clone(), "application/xml")
}

/// 204 when every check passes; any failure, panic included, is the
/// service's own 503 `schema_not_ready`.
async fn ready(State(kit): State<Kit>) -> Response {
    for check in &kit.inner.ready_checks {
        let passed = tokio::spawn(check()).await.unwrap_or(false);
        if !passed {
            tracing::warn!("Readiness check failed");
            return pending(
                ApiError::new(
                    503,
                    "schema_not_ready",
                    "The required service schema has not been applied.",
                ),
                None,
            );
        }
    }
    StatusCode::NO_CONTENT.into_response()
}

/// Correlation id, log context and the error envelope for every response.
async fn request_context(mut request: Request, next: Next) -> Response {
    let headers = request.headers();
    let correlation_id = correlation_id(headers);
    let preferred = preferred_format(headers);
    let client = headers
        .get("X-Real-IP")
        .and_then(|value| value.to_str().ok())
        .filter(|value| !value.is_empty())
        .unwrap_or("-")
        .to_owned();
    request.extensions_mut().insert(RequestInfo {
        correlation_id: correlation_id.clone(),
    });
    let context = RefCell::new(LogContext {
        correlation_id: correlation_id.clone(),
        actor: "-".into(),
        client,
    });
    let mut response = CONTEXT.scope(context, next.run(request)).await;
    if let Some(pending) = response.extensions_mut().remove::<PendingError>() {
        let allow = response.headers().get(header::ALLOW).cloned();
        response = render_error(
            &pending.error,
            &correlation_id,
            pending.format.unwrap_or(preferred),
        );
        if let Some(allow) = allow {
            response.headers_mut().insert(header::ALLOW, allow);
        }
    }
    if let Ok(value) = HeaderValue::from_str(&correlation_id) {
        response.headers_mut().insert("X-Correlation-ID", value);
    }
    response
}

async fn dispatch<H, Fut>(
    kit: Kit,
    operation: Arc<Operation>,
    handler: H,
    request: Request,
) -> Response
where
    H: Fn(Context) -> Fut,
    Fut: Future<Output = Result<Reply, ApiError>>,
{
    let (mut parts, body) = request.into_parts();
    let correlation_id = parts.extensions.get::<RequestInfo>().map_or_else(
        || correlation_id(&parts.headers),
        |info| info.correlation_id.clone(),
    );
    let path = Path::<HashMap<String, String>>::from_request_parts(&mut parts, &())
        .await
        .map(|Path(values)| values)
        .unwrap_or_default();
    // Before anything else, authentication included.
    let body = match capped_body(&parts.headers, body).await {
        Ok(body) => body,
        Err(error) => return pending(error, None),
    };
    let format = match negotiate(&parts.headers) {
        Ok(format) => format,
        Err(error) => return pending(error, None),
    };
    if operation.accepts_body {
        let media = media_type(&parts.headers);
        if media != "application/json" && media != "application/xml" {
            return pending(
                ApiError::new(
                    415,
                    "unsupported_media_type",
                    "The request format is unsupported.",
                ),
                Some(format),
            );
        }
    }
    let (page, size) = if operation.paginated {
        match pagination(parts.uri.query()) {
            Ok(values) => values,
            Err(error) => return pending(error, Some(format)),
        }
    } else {
        (1, 20)
    };
    let context = Context {
        correlation_id,
        format,
        path,
        query: parts.uri.query().map(str::to_owned),
        body,
        page,
        size,
    };
    let reply = match handler(context).await {
        Ok(reply) => reply,
        Err(error) => return pending(error, Some(format)),
    };
    match render_reply(&reply, format, &kit.inner.contract.namespace, size) {
        Ok(response) => response,
        Err(error) => pending(error, Some(format)),
    }
}
