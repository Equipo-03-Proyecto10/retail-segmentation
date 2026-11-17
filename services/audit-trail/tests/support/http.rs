//! The service through its whole router: a throwaway signing key, the
//! committed ACL loaded into a disposable Redis, and requests in JSON or XML.
//!
//! Needs TEST_DATABASE_URL, TEST_REDIS_URL and the `openssl` command; without
//! them a test skips, or fails under MOSAIQ_REQUIRE_POSTGRES /
//! MOSAIQ_REQUIRE_REDIS.
#![allow(dead_code)]

use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use axum::Router;
use http_body_util::BodyExt;
use jsonwebtoken::{encode, Algorithm, EncodingKey, Header};
use mosaiq_kit::KitConfig;
use serde_json::json;
use tower::ServiceExt;
use uuid::Uuid;

use super::{owner_url, service_url, ADMIN};

pub const ISSUER: &str = "https://auth.test/";
pub const KID: &str = "audit-trail-test";
pub const ROLES: [&str; 7] = [
    "ADMIN",
    "ANALYST",
    "STORE_MANAGER",
    "MARKETING",
    "INVENTORY_PLANNER",
    "AUDITOR",
    "CUSTOMER",
];
/// The seed's auditor (user 6 has role 6, AUDITOR).
pub const AUDITOR: &str = "11111111-1111-1111-1111-000000000006";

/// The tests of one binary take turns: they share the Redis ACL user.
pub static TURN: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

fn base64url(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut out = String::new();
    for chunk in bytes.chunks(3) {
        let block = chunk.iter().enumerate().fold(0u32, |acc, (i, byte)| {
            acc | (u32::from(*byte) << (16 - 8 * i))
        });
        for i in 0..=chunk.len() {
            out.push(ALPHABET[((block >> (18 - 6 * i)) & 63) as usize] as char);
        }
    }
    out
}

fn from_hex(hex: &str) -> Vec<u8> {
    (0..hex.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
        .collect()
}

fn openssl(arguments: &[&str]) -> Option<Vec<u8>> {
    let output = Command::new("openssl").args(arguments).output().ok()?;
    output.status.success().then_some(output.stdout)
}

/// A throwaway RSA key, its public half as the JWKS file auth-service would
/// publish, and the private half as a PEM.
pub struct Keys {
    pub dir: PathBuf,
    pem: Vec<u8>,
}

impl Keys {
    pub fn make() -> Option<Self> {
        let dir = std::env::temp_dir().join(format!("audit-trail-test-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&dir).ok()?;
        let key = dir.join("key.pem");
        openssl(&["genrsa", "-out", key.to_str()?, "2048"])?;
        let modulus = String::from_utf8(openssl(&[
            "rsa",
            "-in",
            key.to_str()?,
            "-noout",
            "-modulus",
        ])?)
        .ok()?;
        let hex = modulus.trim().strip_prefix("Modulus=")?.to_owned();
        let jwks = json!({"keys": [{
            "kty": "RSA", "use": "sig", "alg": "RS256", "kid": KID,
            "n": base64url(&from_hex(&hex)), "e": "AQAB",
        }]});
        std::fs::write(dir.join("jwks.json"), jwks.to_string()).ok()?;
        let pem = std::fs::read(&key).ok()?;
        Some(Self { dir, pem })
    }

    /// A token for `role`, signed in as the seed's administrator.
    pub fn token(&self, role: &str, sid: &str) -> String {
        self.token_for(ADMIN, role, sid)
    }

    pub fn token_for(&self, sub: &str, role: &str, sid: &str) -> String {
        let now = chrono::Utc::now().timestamp();
        let claims = json!({
            "iss": ISSUER, "aud": "mosaiq-api", "sub": sub, "sid": sid,
            "role": role, "iat": now, "exp": now + 300,
        });
        let mut header = Header::new(Algorithm::RS256);
        header.kid = Some(KID.to_owned());
        encode(
            &header,
            &claims,
            &EncodingKey::from_rsa_pem(&self.pem).unwrap(),
        )
        .unwrap()
    }
}

impl Drop for Keys {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

pub async fn send_with(
    app: &Router,
    method: &str,
    path: &str,
    token: Option<&str>,
    accept: &str,
    correlation: Option<&str>,
) -> (StatusCode, String) {
    let mut request = Request::builder()
        .method(method)
        .uri(path)
        .header("Accept", accept);
    if let Some(token) = token {
        request = request.header("Authorization", format!("Bearer {token}"));
    }
    if let Some(correlation) = correlation {
        request = request.header("X-Correlation-ID", correlation);
    }
    let response = app
        .clone()
        .oneshot(request.body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let body = response.into_body().collect().await.unwrap().to_bytes();
    (status, String::from_utf8(body.to_vec()).unwrap())
}

pub async fn send(
    app: &Router,
    method: &str,
    path: &str,
    token: Option<&str>,
    accept: &str,
) -> (StatusCode, String) {
    send_with(app, method, path, token, accept, None).await
}

pub async fn redis_admin() -> Option<redis::aio::MultiplexedConnection> {
    let Ok(url) = std::env::var("TEST_REDIS_URL") else {
        if std::env::var("MOSAIQ_REQUIRE_REDIS").as_deref() == Ok("1") {
            panic!("TEST_REDIS_URL is missing, and MOSAIQ_REQUIRE_REDIS forbids skipping");
        }
        eprintln!("skipped: TEST_REDIS_URL is not set");
        return None;
    };
    Some(
        redis::Client::open(url)
            .unwrap()
            .get_multiplexed_async_connection()
            .await
            .unwrap(),
    )
}

/// The router on the seeded database as `svc_audit_trail`, with the
/// committed ACL loaded for the kit's Redis user and one live session.
pub struct Stack {
    pub app: Router,
    pub keys: Keys,
    pub sid: String,
    pub database: String,
    admin: redis::aio::MultiplexedConnection,
}

impl Stack {
    pub async fn start() -> Option<Self> {
        let database = owner_url()?;
        let mut admin = redis_admin().await?;
        let Some(keys) = Keys::make() else {
            if std::env::var("MOSAIQ_REQUIRE_REDIS").as_deref() == Ok("1") {
                panic!("openssl is required");
            }
            eprintln!("skipped: openssl is not available");
            return None;
        };
        let password = Uuid::new_v4().simple().to_string();
        let line = std::fs::read_to_string("redis/audit-trail.acl").unwrap();
        let rules = line.trim().replace("{{password}}", &format!(">{password}"));
        let (head, selector) = rules.split_once(" (").unwrap();
        let mut setuser = redis::cmd("ACL");
        setuser.arg("SETUSER").arg("svc_audit_trail");
        for rule in head.split(' ').skip(2) {
            setuser.arg(rule);
        }
        setuser.arg(format!("({selector}"));
        let _: i64 = redis::cmd("ACL")
            .arg("DELUSER")
            .arg("svc_audit_trail")
            .query_async(&mut admin)
            .await
            .unwrap();
        let _: String = setuser.query_async(&mut admin).await.unwrap();
        let sid = Uuid::new_v4().to_string();
        let _: String = redis::cmd("SET")
            .arg(format!("pub:auth:v1:live:{sid}"))
            .arg("1")
            .arg("EX")
            .arg(300)
            .query_async(&mut admin)
            .await
            .unwrap();
        let config = KitConfig {
            issuer: ISSUER.to_owned(),
            jwks_path: keys.dir.join("jwks.json"),
            redis_state_url: std::env::var("TEST_REDIS_URL").unwrap(),
            redis_password: Some(password),
            rate_limit_enabled: false,
            rate_limit_requests: 120,
            rate_limit_window_seconds: 60,
            database_url: Some(service_url(&database)),
            database_pool_timeout: Duration::from_secs(2),
            database_connect_timeout: Duration::from_secs(2),
        };
        let app = audit_trail::router(config, "contracts").unwrap();
        Some(Self {
            app,
            keys,
            sid,
            database,
            admin,
        })
    }

    pub fn token(&self, role: &str) -> String {
        self.keys.token(role, &self.sid)
    }

    pub async fn finish(mut self) {
        let _: i64 = redis::cmd("ACL")
            .arg("DELUSER")
            .arg("svc_audit_trail")
            .query_async(&mut self.admin)
            .await
            .unwrap();
    }
}

/// Validate an XML body against the contract's XSD, as Electron does.
pub fn assert_valid_xml(xml: &str) {
    use libxml::parser::Parser;
    use libxml::schemas::{SchemaParserContext, SchemaValidationContext};
    let mut schema = SchemaParserContext::from_file("contracts/audit-trail/audit-trail.xsd");
    let mut context = SchemaValidationContext::from_parser(&mut schema).expect("the XSD compiles");
    let document = Parser::default()
        .parse_string(xml)
        .expect("well-formed XML");
    if let Err(errors) = context.validate_document(&document) {
        let messages: Vec<String> = errors.into_iter().filter_map(|e| e.message).collect();
        panic!("the XML does not validate: {messages:?}\n{xml}");
    }
}
