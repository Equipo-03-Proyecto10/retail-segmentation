//! A read of one customer's segment history is logged, and fails closed
//! (F21-25, ADR-0072 section 4), through the service's whole router.
//!
//! `GET /v1/segment-assignments?customer_id=` records one `HISTORY_READ`
//! naming who, which customer and when, never what was returned; a 403 on it
//! records one with outcome `REFUSED`; a read by label records nothing; and
//! when the event cannot be recorded the read answers 503 `audit_unavailable`
//! with no rows. Needs TEST_DATABASE_URL, TEST_REDIS_URL and `openssl`.

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
use segments::Reader;
use serde_json::{json, Value};
use support::{admin, connect, drop_acl, load_acl, owner_url, redis_url, service_url};
use tokio_postgres::Client;
use uuid::Uuid;

const ISSUER: &str = "https://auth.test/";
const KID: &str = "segments-test";
const CUSTOMER: &str = "00000000-0000-0000-0000-000000000007";
const ANALYST: &str = "11111111-1111-1111-1111-000000000002";
const PLANNER: &str = "11111111-1111-1111-1111-000000000005";
const REVOKE: &str = "REVOKE EXECUTE ON FUNCTION api_audit.record_event_v1(uuid, text, text, \
     timestamptz, text, uuid, text, smallint, text, text, bigint, bigint, text, jsonb) \
     FROM svc_segments";
const GRANT: &str = "GRANT EXECUTE ON FUNCTION api_audit.record_event_v1(uuid, text, text, \
     timestamptz, text, uuid, text, smallint, text, text, bigint, bigint, text, jsonb) \
     TO svc_segments";

/// The tests share the ACL user and revoke a grant; they take turns.
static TURN: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

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

fn openssl(arguments: &[&str]) -> Vec<u8> {
    let output = Command::new("openssl")
        .args(arguments)
        .output()
        .expect("openssl is installed");
    assert!(output.status.success(), "openssl {arguments:?}");
    output.stdout
}

/// A throwaway signing key and its public half as a JWKS file.
struct Keys {
    dir: PathBuf,
    pem: Vec<u8>,
}

impl Keys {
    fn make() -> Self {
        let dir = std::env::temp_dir().join(format!("segments-test-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let key = dir.join("key.pem");
        let path = key.to_str().unwrap();
        openssl(&["genrsa", "-out", path, "2048"]);
        let modulus =
            String::from_utf8(openssl(&["rsa", "-in", path, "-noout", "-modulus"])).unwrap();
        let hex = modulus.trim().strip_prefix("Modulus=").unwrap();
        let bytes: Vec<u8> = (0..hex.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
            .collect();
        let jwks = json!({"keys": [{
            "kty": "RSA", "use": "sig", "alg": "RS256", "kid": KID,
            "n": base64url(&bytes), "e": "AQAB",
        }]});
        std::fs::write(dir.join("jwks.json"), jwks.to_string()).unwrap();
        let pem = std::fs::read(&key).unwrap();
        Self { dir, pem }
    }

    fn token(&self, sub: &str, role: &str, sid: &str) -> String {
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

struct Stack {
    app: Router,
    keys: Keys,
    sid: String,
    owner: Client,
    redis: redis::aio::MultiplexedConnection,
}

impl Stack {
    async fn start() -> Option<Self> {
        let database = owner_url();
        let url = redis_url()?;
        let mut redis = admin(&url).await;
        let password = load_acl(&mut redis).await;
        let sid = Uuid::new_v4().to_string();
        let _: () = redis::cmd("SET")
            .arg(format!("pub:auth:v1:live:{sid}"))
            .arg("1")
            .arg("EX")
            .arg(300)
            .query_async(&mut redis)
            .await
            .unwrap();
        let keys = Keys::make();
        let config = KitConfig {
            issuer: ISSUER.to_owned(),
            jwks_path: keys.dir.join("jwks.json"),
            redis_state_url: url,
            redis_password: Some(password),
            rate_limit_enabled: false,
            rate_limit_requests: 120,
            rate_limit_window_seconds: 60,
            database_url: Some(service_url(&database)),
            database_pool_timeout: Duration::from_secs(2),
            database_connect_timeout: Duration::from_secs(2),
        };
        let (app, _) = segments::router(config, "contracts", Reader::new(None)).unwrap();
        let owner = connect(&database).await;
        Some(Self {
            app,
            keys,
            sid,
            owner,
            redis,
        })
    }

    async fn get(
        &self,
        path: &str,
        sub: &str,
        role: &str,
        accept: &str,
    ) -> (StatusCode, String, String) {
        let correlation = format!("test-{}", Uuid::new_v4().simple());
        let request = Request::builder()
            .uri(path)
            .header("Accept", accept)
            .header("X-Correlation-ID", &correlation)
            .header(
                "Authorization",
                format!("Bearer {}", self.keys.token(sub, role, &self.sid)),
            )
            .body(Body::empty())
            .unwrap();
        let response = tower::ServiceExt::oneshot(self.app.clone(), request)
            .await
            .unwrap();
        let status = response.status();
        let body = response.into_body().collect().await.unwrap().to_bytes();
        (
            status,
            String::from_utf8(body.to_vec()).unwrap(),
            correlation,
        )
    }

    /// The events a request recorded: (kind, outcome, actor, role, source,
    /// subject_type, subject_id, details, and every non-null column name).
    async fn events(&self, correlation: &str) -> Vec<Value> {
        self.owner
            .query(
                "SELECT to_jsonb(d)::text FROM audit_events.domain_event AS d \
                 WHERE correlation_id = $1 ORDER BY seq",
                &[&correlation],
            )
            .await
            .unwrap()
            .iter()
            .map(|row| serde_json::from_str(row.get::<_, &str>(0)).unwrap())
            .collect()
    }

    async fn finish(mut self) {
        drop_acl(&mut self.redis).await;
    }
}

#[tokio::test]
async fn a_customer_history_read_records_who_what_and_when_but_not_the_data() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let path = format!("/v1/segment-assignments?customer_id={CUSTOMER}");
    for accept in ["application/json", "application/xml"] {
        let (status, body, correlation) = stack.get(&path, ANALYST, "ANALYST", accept).await;
        assert_eq!(status, 200, "{body}");
        assert!(
            body.contains("run_id"),
            "the customer has history to withhold"
        );
        let events = stack.events(&correlation).await;
        assert_eq!(events.len(), 1, "{events:?}");
        let event = &events[0];
        assert_eq!(event["kind"], "HISTORY_READ");
        assert_eq!(event["outcome"], "SUCCESS");
        assert_eq!(event["actor_type"], "USER");
        assert_eq!(event["actor_user_id"], ANALYST);
        assert_eq!(event["actor_role"], "ANALYST");
        assert_eq!(event["source"], "svc_segments");
        assert_eq!(event["subject_type"], "customer");
        assert_eq!(event["subject_id"], CUSTOMER);
        assert_eq!(event["details"], json!({"history": "SEGMENT"}));
        // Who, which customer, when, and the chain: nothing that was read.
        let present: Vec<&str> = event
            .as_object()
            .unwrap()
            .iter()
            .filter(|(_, value)| !value.is_null())
            .map(|(key, _)| key.as_str())
            .collect();
        assert_eq!(
            present,
            [
                "seq",
                "kind",
                "source",
                "details",
                "outcome",
                "event_id",
                "prev_hash",
                "actor_role",
                "actor_type",
                "entry_hash",
                "subject_id",
                "occurred_at",
                "recorded_at",
                "subject_type",
                "actor_user_id",
                "correlation_id",
            ]
        );
    }
    stack.finish().await;
}

#[tokio::test]
async fn a_read_by_label_records_nothing_and_a_refusal_is_recorded() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let (status, _, correlation) = stack
        .get(
            "/v1/segment-assignments?label_code=CHAMPION&open=true",
            ANALYST,
            "ANALYST",
            "application/json",
        )
        .await;
    assert_eq!(status, 200);
    assert!(stack.events(&correlation).await.is_empty());

    let path = format!("/v1/segment-assignments?customer_id={CUSTOMER}");
    for accept in ["application/json", "application/xml"] {
        let (status, body, correlation) =
            stack.get(&path, PLANNER, "INVENTORY_PLANNER", accept).await;
        assert_eq!(status, 403, "{body}");
        assert!(body.contains("forbidden_role"), "{body}");
        let events = stack.events(&correlation).await;
        assert_eq!(events.len(), 1, "{events:?}");
        assert_eq!(events[0]["outcome"], "REFUSED");
        assert_eq!(events[0]["actor_user_id"], PLANNER);
        assert_eq!(events[0]["actor_role"], "INVENTORY_PLANNER");
        assert_eq!(events[0]["subject_id"], CUSTOMER);
    }
    // A refusal by label names no customer, so it records nothing.
    let (status, _, correlation) = stack
        .get(
            "/v1/segment-assignments?label_code=CHAMPION",
            PLANNER,
            "INVENTORY_PLANNER",
            "application/json",
        )
        .await;
    assert_eq!(status, 403);
    assert!(stack.events(&correlation).await.is_empty());
    stack.finish().await;
}

#[tokio::test]
async fn the_read_fails_closed_when_its_event_cannot_be_recorded() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let path = format!("/v1/segment-assignments?customer_id={CUSTOMER}");
    stack.owner.batch_execute(REVOKE).await.unwrap();
    let json = stack
        .get(&path, ANALYST, "ANALYST", "application/json")
        .await;
    let xml = stack
        .get(&path, ANALYST, "ANALYST", "application/xml")
        .await;
    let refused = stack
        .get(&path, PLANNER, "INVENTORY_PLANNER", "application/json")
        .await;
    stack.owner.batch_execute(GRANT).await.unwrap();

    for (status, body, correlation) in [&json, &xml, &refused] {
        assert_eq!(*status, 503, "{body}");
        assert!(body.contains("audit_unavailable"), "{body}");
        assert!(
            !body.contains("run_id") && !body.contains("label_code"),
            "{body}"
        );
        assert!(stack.events(correlation).await.is_empty());
    }
    assert!(xml.1.starts_with("<?xml"), "{}", xml.1);
    stack.finish().await;
}
