//! The service through its whole router: roles, methods, and what a client
//! receives in JSON and in XML.
//!
//! Needs TEST_DATABASE_URL (the seeded database, as its owner), TEST_REDIS_URL
//! (an administrator's URL to a disposable Redis) and the `openssl` command,
//! which makes a throwaway signing key. Without them it skips, or fails under
//! MOSAIQ_REQUIRE_POSTGRES / MOSAIQ_REQUIRE_REDIS.

mod support;

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
use support::{connect, owner_url, remove_test_rows, service_url, ADMIN};
use tower::ServiceExt;
use uuid::Uuid;

const ISSUER: &str = "https://auth.test/";
const KID: &str = "audit-trail-test";
const ROLES: [&str; 7] = [
    "ADMIN",
    "ANALYST",
    "STORE_MANAGER",
    "MARKETING",
    "INVENTORY_PLANNER",
    "AUDITOR",
    "CUSTOMER",
];
const PATHS: [&str; 2] = ["/v1/audit-entries", "/v1/audit-entries/1"];

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
struct Keys {
    dir: PathBuf,
    pem: Vec<u8>,
}

impl Keys {
    fn make() -> Option<Self> {
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

    fn token(&self, role: &str, sid: &str) -> String {
        let now = chrono::Utc::now().timestamp();
        let claims = json!({
            "iss": ISSUER, "aud": "mosaiq-api", "sub": ADMIN, "sid": sid,
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

async fn send(
    app: &Router,
    method: &str,
    path: &str,
    token: Option<&str>,
    accept: &str,
) -> (StatusCode, String) {
    let mut request = Request::builder()
        .method(method)
        .uri(path)
        .header("Accept", accept);
    if let Some(token) = token {
        request = request.header("Authorization", format!("Bearer {token}"));
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

async fn redis_admin() -> Option<redis::aio::MultiplexedConnection> {
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

#[tokio::test]
async fn roles_methods_and_both_representations() {
    let Some(database) = owner_url() else { return };
    let Some(mut admin) = redis_admin().await else {
        return;
    };
    let Some(keys) = Keys::make() else {
        if std::env::var("MOSAIQ_REQUIRE_REDIS").as_deref() == Ok("1") {
            panic!("openssl is required");
        }
        eprintln!("skipped: openssl is not available");
        return;
    };

    // The committed ACL line, loaded for the user the kit signs in as.
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

    // Only ADMIN and AUDITOR read; every other role is refused on both paths.
    for role in ROLES {
        let token = keys.token(role, &sid);
        for path in PATHS {
            let (status, body) = send(&app, "GET", path, Some(&token), "application/json").await;
            if matches!(role, "ADMIN" | "AUDITOR") {
                assert_eq!(status, 200, "{role} {path}: {body}");
            } else {
                assert_eq!(status, 403, "{role} {path}");
                assert!(body.contains("\"forbidden_role\""), "{body}");
            }
        }
    }

    // There is no write: every other method on either path is 405.
    let token = keys.token("ADMIN", &sid);
    for method in ["POST", "PUT", "PATCH", "DELETE"] {
        for path in PATHS {
            let (status, _) = send(&app, method, path, Some(&token), "application/json").await;
            assert_eq!(status, 405, "{method} {path}");
        }
    }

    // A credential written straight into the table reaches no client.
    let owner = connect(&database).await;
    let key = format!("http-test-{}", Uuid::new_v4());
    let id: i64 = owner
        .query_one(
            "INSERT INTO public.audit_log (entity, entity_pk, action, data_before, data_after) \
             VALUES ('app_user', $1, 'UPDATE', \
                     '{\"password_hash\":\"$argon2id$before\",\"name\":\"A\"}'::jsonb, \
                     '{\"password_hash\":\"$argon2id$after\",\"nested\":{\"password_hash\":\"x\"}}'::jsonb) \
             RETURNING audit_id",
            &[&key],
        )
        .await
        .unwrap()
        .get(0);
    let auditor = keys.token("AUDITOR", &sid);
    let list = format!("/v1/audit-entries?entity=app_user&entity_pk={key}");
    let single = format!("/v1/audit-entries/{id}");
    for path in [list.as_str(), single.as_str()] {
        let (status, json_body) = send(&app, "GET", path, Some(&auditor), "application/json").await;
        assert_eq!(status, 200, "{json_body}");
        assert!(!json_body.contains("password_hash") && !json_body.contains("argon2id"));
        let parsed: serde_json::Value = serde_json::from_str(&json_body).unwrap();
        let entry = if path == list {
            &parsed["audit_entries"][0]
        } else {
            &parsed
        };
        assert_eq!(entry["data_before"]["name"], "A", "{json_body}");
        assert!(entry["data_after"]["nested"].is_object());
        assert!(entry["user_id"].is_null());

        let (status, xml) = send(&app, "GET", path, Some(&auditor), "application/xml").await;
        assert_eq!(status, 200, "{xml}");
        assert!(
            !xml.contains("password_hash") && !xml.contains("argon2id"),
            "{xml}"
        );
        assert!(xml.contains("<data_after>{"), "{xml}");
        assert!(!xml.contains("<user_id"), "{xml}");
    }

    // The two error answers of the contract.
    let (status, body) = send(
        &app,
        "GET",
        "/v1/audit-entries/999999999",
        Some(&auditor),
        "application/json",
    )
    .await;
    assert_eq!(status, 404);
    assert!(body.contains("audit_entry_not_found"), "{body}");
    let (status, body) = send(
        &app,
        "GET",
        "/v1/audit-entries?entity=audit_log",
        Some(&auditor),
        "application/json",
    )
    .await;
    assert_eq!(status, 422);
    assert!(
        body.contains("invalid_query") && body.contains("\"field\":\"entity\""),
        "{body}"
    );

    remove_test_rows(&owner, &key).await;
    let _: i64 = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_audit_trail")
        .query_async(&mut admin)
        .await
        .unwrap();
}
