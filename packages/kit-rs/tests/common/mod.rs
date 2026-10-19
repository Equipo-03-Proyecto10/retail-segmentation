//! Fixtures shared by the integration tests, mirroring kit-py's conftest.
#![allow(dead_code)]

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::LazyLock;
use std::time::Duration;

use axum::body::Body;
use axum::http::{HeaderMap, Request, StatusCode};
use axum::Router;
use chrono::{TimeZone, Utc};
use http_body_util::BodyExt;
use indexmap::IndexMap;
use mosaiq_kit::{money, ApiError, Ctx, Kit, KitConfig, Page, Reply, Representation, Value};
use regex::Regex;
use rust_decimal::Decimal;
use serde_json::Value as JsonValue;
use tower::ServiceExt;

pub const LIVE_SESSION: &str = "087357a4-7d4b-4bdd-936d-8e403790303e";
pub const WRITER_SUB: &str = "3b6a2b8b-3e24-4ba6-9ef9-fb3e6b4c9ad1";
pub const ISSUER: &str = "https://kit-vectors.invalid/auth";
pub const EXAMPLE_NAMESPACE: &str = "https://mosaiq.maxthecoder.online/xml/kit-example/v1";
pub const COMMON_NAMESPACE: &str = "https://mosaiq.maxthecoder.online/xml/common/v1";

pub fn suffix() -> String {
    let suffix = std::env::var("KIT_TEST_SUFFIX").unwrap_or_else(|_| "kit_test".to_owned());
    assert!(
        Regex::new(r"^[a-z][a-z0-9_]*$").unwrap().is_match(&suffix),
        "KIT_TEST_SUFFIX must be a lowercase SQL identifier."
    );
    suffix
}

pub fn source_suffix() -> String {
    std::env::var("KIT_TEST_SOURCE_SUFFIX").unwrap_or_else(|_| "kit_source".to_owned())
}

pub fn own_stream() -> String {
    format!("{}.v1.changed", suffix())
}

pub fn consumed_stream() -> String {
    format!("{}.v1.changed", source_suffix())
}

pub fn kit_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

pub fn repository() -> PathBuf {
    kit_root().join("../..").canonicalize().unwrap()
}

pub fn vectors() -> PathBuf {
    repository().join("packages/kit-vectors")
}

pub fn tokens() -> HashMap<String, Option<String>> {
    serde_json::from_slice(&std::fs::read(vectors().join("tokens.json")).unwrap()).unwrap()
}

pub fn token(name: &str) -> String {
    tokens()[name].clone().unwrap_or_default()
}

pub fn expected() -> HashMap<String, JsonValue> {
    serde_json::from_slice(&std::fs::read(vectors().join("expected.json")).unwrap()).unwrap()
}

/// A missing service fails under `KIT_REQUIRE_SERVICES=1` and skips otherwise.
pub fn service_missing(message: &str) -> bool {
    if std::env::var("KIT_REQUIRE_SERVICES").as_deref() == Ok("1") {
        panic!("{message}");
    }
    eprintln!("skipped: {message}");
    true
}

/// A scratch directory removed on drop.
pub struct TempDir(PathBuf);

impl TempDir {
    pub fn new() -> Self {
        let path = std::env::temp_dir().join(format!("kit-rs-test-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&path).unwrap();
        Self(path)
    }

    pub fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

pub fn copy_tree(source: &Path, target: &Path) {
    std::fs::create_dir_all(target).unwrap();
    for entry in std::fs::read_dir(source).unwrap() {
        let entry = entry.unwrap();
        let destination = target.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &destination);
        } else {
            std::fs::copy(entry.path(), destination).unwrap();
        }
    }
}

/// The example contract staged as an image stages it.
pub static CONTRACTS: LazyLock<TempDir> = LazyLock::new(|| {
    let target = TempDir::new();
    copy_tree(
        &kit_root().join("examples/contracts/kit-example"),
        &target.path().join("kit-example"),
    );
    copy_tree(
        &repository().join("contracts/common/v1"),
        &target.path().join("common/v1"),
    );
    target
});

pub fn contracts_dir() -> PathBuf {
    CONTRACTS.path().to_owned()
}

pub fn redis_url() -> Option<String> {
    let url = std::env::var("KIT_TEST_REDIS_URL").ok();
    if url.is_none() {
        service_missing("KIT_TEST_REDIS_URL is not set");
    }
    url
}

pub fn database_url() -> Option<String> {
    let url = std::env::var("KIT_TEST_DATABASE_URL").ok();
    if url.is_none() {
        service_missing("KIT_TEST_DATABASE_URL is not set");
    }
    url
}

pub async fn admin() -> redis::aio::MultiplexedConnection {
    let client = redis::Client::open(redis_url().unwrap()).unwrap();
    client.get_multiplexed_async_connection().await.unwrap()
}

/// Render the service user from the rule block in `docs/datastores/redis-design.md`.
pub fn redis_acl(password: &str) -> (String, Vec<String>) {
    let design =
        std::fs::read_to_string(repository().join("docs/datastores/redis-design.md")).unwrap();
    let marker = "Each service commits `services/<name>/redis/<name>.acl`, one line, built from\n\
                  these rules:";
    let rules = design
        .split_once(marker)
        .unwrap()
        .1
        .split_once("```text")
        .unwrap()
        .1
        .split_once("```")
        .unwrap()
        .0;
    let rendered = rules
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .collect::<Vec<_>>()
        .join(" ")
        .replace("<suffix>", &suffix())
        .replace("<own streams>", &own_stream())
        .replace("<consumed streams>", &consumed_stream())
        .replace("{{password}}", &format!(">{password}"));
    let arguments: Vec<String> = Regex::new(r"\([^)]*\)|\S+")
        .unwrap()
        .find_iter(&rendered)
        .map(|found| found.as_str().to_owned())
        .collect();
    assert_eq!(
        arguments[..2],
        ["user".to_owned(), format!("svc_{}", suffix())],
        "The Redis ACL template did not render a service user."
    );
    (arguments[1].clone(), arguments[2..].to_vec())
}

/// The ACL user's password, created once per test binary, with a live session key.
static SERVICE_PASSWORD: tokio::sync::OnceCell<String> = tokio::sync::OnceCell::const_new();

pub async fn service_password() -> String {
    SERVICE_PASSWORD
        .get_or_init(|| async {
            let password = uuid::Uuid::new_v4().simple().to_string();
            let (user, rules) = redis_acl(&password);
            let mut admin = admin().await;
            let _: i64 = redis::cmd("ACL")
                .arg("DELUSER")
                .arg(&user)
                .query_async(&mut admin)
                .await
                .unwrap();
            let _: String = redis::cmd("ACL")
                .arg("SETUSER")
                .arg(&user)
                .arg(&rules)
                .query_async(&mut admin)
                .await
                .unwrap();
            let _: String = redis::cmd("SET")
                .arg(format!("pub:auth:v1:live:{LIVE_SESSION}"))
                .arg("1")
                .arg("EX")
                .arg(3600)
                .query_async(&mut admin)
                .await
                .unwrap();
            password
        })
        .await
        .clone()
}

/// A connection as the service's own ACL user.
pub async fn service_connection() -> redis::aio::MultiplexedConnection {
    let password = service_password().await;
    let info =
        mosaiq_kit::service_redis_info(&redis_url().unwrap(), &suffix(), Some(password)).unwrap();
    redis::Client::open(info)
        .unwrap()
        .get_multiplexed_async_connection()
        .await
        .unwrap()
}

/// The configuration kit-py's `configured_environment` sets.
pub async fn config() -> KitConfig {
    KitConfig {
        issuer: ISSUER.to_owned(),
        jwks_path: vectors().join("jwks.json"),
        redis_state_url: redis_url().unwrap(),
        redis_password: Some(service_password().await),
        rate_limit_enabled: false,
        rate_limit_requests: 120,
        rate_limit_window_seconds: 60,
        database_url: std::env::var("KIT_TEST_DATABASE_URL").ok(),
        database_pool_timeout: Duration::from_secs(2),
        database_connect_timeout: Duration::from_secs(2),
    }
}

fn sample_note(note_id: i64, created_by: Value) -> IndexMap<String, Value> {
    IndexMap::from([
        ("note_id".to_owned(), Value::Int(note_id)),
        ("title".to_owned(), Value::from("One")),
        ("amount".to_owned(), money(Decimal::new(125, 1))),
        ("created_by".to_owned(), created_by),
        (
            "created_at".to_owned(),
            Value::from(Utc.with_ymd_and_hms(2026, 10, 15, 12, 30, 0).unwrap()),
        ),
    ])
}

async fn list_notes(ctx: Ctx) -> Result<Reply, ApiError> {
    let items = if ctx.page == 1 {
        vec![sample_note(1, Value::Null)]
    } else {
        Vec::new()
    };
    Ok(Page::new("Notes", "Note", "notes", items, ctx.page, 1).into())
}

async fn get_note(ctx: Ctx) -> Result<Reply, ApiError> {
    let note_id = ctx.path_i64("note_id").unwrap();
    Ok(Representation::new("Note", sample_note(note_id, Value::Null)).into())
}

async fn create_note(ctx: Ctx) -> Result<Reply, ApiError> {
    let body = ctx.body.clone().unwrap();
    let principal = ctx.principal().unwrap();
    Ok(Representation::new(
        "Note",
        [
            ("note_id", Value::Int(46)),
            ("title", Value::from(&body["title"])),
            ("amount", Value::from(&body["amount"])),
            ("created_by", Value::from(principal.sub.as_str())),
            (
                "created_at",
                Value::from(Utc.with_ymd_and_hms(2026, 10, 15, 12, 30, 0).unwrap()),
            ),
        ],
    )
    .status(201)
    .header("Location", "/v1/notes/46")
    .into())
}

pub fn kit(config: KitConfig) -> Kit {
    Kit::new("kit-example", &suffix(), contracts_dir(), config).unwrap()
}

/// kit-py's `app` fixture: the example contract with in-memory handlers.
pub async fn app() -> Option<Router> {
    redis_url()?;
    Some(app_with(config().await))
}

pub fn app_with(config: KitConfig) -> Router {
    kit(config)
        .operation("listNotes", list_notes)
        .unwrap()
        .operation("getNote", get_note)
        .unwrap()
        .operation("createNote", create_note)
        .unwrap()
        .ready_check(|_db| async { true })
        .into_router()
}

pub struct Answer {
    pub status: StatusCode,
    pub headers: HeaderMap,
    pub body: Vec<u8>,
}

impl Answer {
    pub fn json(&self) -> JsonValue {
        serde_json::from_slice(&self.body)
            .unwrap_or_else(|_| panic!("not JSON: {}", String::from_utf8_lossy(&self.body)))
    }

    pub fn code(&self) -> String {
        self.json()["code"].as_str().unwrap_or_default().to_owned()
    }

    pub fn content_type(&self) -> &str {
        self.headers
            .get("content-type")
            .and_then(|value| value.to_str().ok())
            .unwrap_or("")
    }

    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers.get(name).and_then(|value| value.to_str().ok())
    }

    pub fn text(&self) -> String {
        String::from_utf8_lossy(&self.body).into_owned()
    }
}

pub struct Call {
    method: String,
    uri: String,
    headers: Vec<(String, String)>,
    body: Vec<u8>,
}

pub fn call(method: &str, uri: &str) -> Call {
    Call {
        method: method.to_owned(),
        uri: uri.to_owned(),
        headers: Vec::new(),
        body: Vec::new(),
    }
}

impl Call {
    pub fn header(mut self, name: &str, value: &str) -> Self {
        self.headers.push((name.to_owned(), value.to_owned()));
        self
    }

    pub fn bearer(self, token_name: &str) -> Self {
        let value = token(token_name);
        if value.is_empty() {
            self
        } else {
            self.header("Authorization", &format!("Bearer {value}"))
        }
    }

    pub fn body(mut self, content_type: &str, body: impl Into<Vec<u8>>) -> Self {
        self.body = body.into();
        self.header("Content-Type", content_type)
    }

    pub fn json(self, body: JsonValue) -> Self {
        self.body("application/json", serde_json::to_vec(&body).unwrap())
    }

    pub async fn send(self, router: &Router) -> Answer {
        let mut request = Request::builder()
            .method(self.method.as_str())
            .uri(&self.uri);
        for (name, value) in &self.headers {
            request = request.header(name, value);
        }
        let response = router
            .clone()
            .oneshot(request.body(Body::from(self.body)).unwrap())
            .await
            .unwrap();
        let status = response.status();
        let headers = response.headers().clone();
        let body = response
            .into_body()
            .collect()
            .await
            .unwrap()
            .to_bytes()
            .to_vec();
        Answer {
            status,
            headers,
            body,
        }
    }
}

/// Validate an XML document against an XSD file with libxml2.
pub fn xsd_valid(xsd: &Path, document: &[u8]) -> Result<(), Vec<String>> {
    let mut parser = libxml::schemas::SchemaParserContext::from_file(xsd.to_str().unwrap());
    let mut context =
        libxml::schemas::SchemaValidationContext::from_parser(&mut parser).map_err(|errors| {
            errors
                .into_iter()
                .filter_map(|e| e.message)
                .collect::<Vec<_>>()
        })?;
    let document = libxml::parser::Parser::default()
        .parse_string(document)
        .map_err(|error| vec![format!("{error:?}")])?;
    context
        .validate_document(&document)
        .map_err(|errors| errors.into_iter().filter_map(|e| e.message).collect())
}

pub fn assert_common_xml(answer: &Answer, code: &str) {
    assert_eq!(
        answer.content_type(),
        "application/xml",
        "{}",
        answer.text()
    );
    let xsd = contracts_dir().join("common/v1/common.xsd");
    xsd_valid(&xsd, &answer.body).unwrap();
    let text = answer.text();
    assert!(
        text.contains(&format!("<Error xmlns=\"{COMMON_NAMESPACE}\"")),
        "{text}"
    );
    assert!(text.contains(&format!("<code>{code}</code>")), "{text}");
}
