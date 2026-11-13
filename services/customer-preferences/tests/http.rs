//! Consent, restrictions and the preference history through the whole router
//! (F21-17): the role policy of the contract, every example in JSON and in
//! XML, the errors a client receives, and the `HISTORY_READ` that a read of
//! one customer's log records and fails closed on (ADR-0066, ADR-0072).
//!
//! Needs TEST_DATABASE_URL (the seeded database, as its owner), TEST_REDIS_URL
//! (an administrator's URL to a disposable Redis), the staged `contracts/`
//! directory and the `openssl` command.

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
use serde_json::{json, Value};
use support::{connect, owner_url, service_url, TestCustomer};
use tokio_postgres::Client;
use tower::ServiceExt;
use uuid::Uuid;

const ISSUER: &str = "https://auth.test/";
const KID: &str = "preferences-test";
const ADMIN: &str = "11111111-1111-1111-1111-000000000001";
const OFFICER: &str = "11111111-1111-1111-1111-000000000002";
const ROLES: [&str; 10] = [
    "ADMIN",
    "ANALYST",
    "STORE_MANAGER",
    "MARKETING",
    "INVENTORY_PLANNER",
    "AUDITOR",
    "CUSTOMER",
    "SALES_ASSOCIATE",
    "LOYALTY_OFFICER",
    "COMMERCIAL_SUPERVISOR",
];
const HISTORY_READERS: [&str; 4] = ["ADMIN", "AUDITOR", "STORE_MANAGER", "LOYALTY_OFFICER"];
const WRITERS: [&str; 2] = ["ADMIN", "LOYALTY_OFFICER"];
const PROFILE_READERS: [&str; 7] = [
    "ADMIN",
    "ANALYST",
    "STORE_MANAGER",
    "MARKETING",
    "AUDITOR",
    "SALES_ASSOCIATE",
    "LOYALTY_OFFICER",
];
const JSON: &str = "application/json";
const XML: &str = "application/xml";
const REVOKE: &str = "REVOKE EXECUTE ON FUNCTION api_audit.record_event_v1(uuid, text, text, \
     timestamptz, text, uuid, text, smallint, text, text, bigint, bigint, text, jsonb) \
     FROM svc_preferences";
const GRANT: &str = "GRANT EXECUTE ON FUNCTION api_audit.record_event_v1(uuid, text, text, \
     timestamptz, text, uuid, text, smallint, text, text, bigint, bigint, text, jsonb) \
     TO svc_preferences";

/// The tests share the ACL user and one revokes a grant; they take turns.
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
        let dir = std::env::temp_dir().join(format!("preferences-test-{}", Uuid::new_v4()));
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

/// ACL tokens, keeping a parenthesised selector as one token.
fn acl_tokens(line: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut current = String::new();
    let mut depth = 0;
    for character in line.chars() {
        match character {
            '(' => {
                depth += 1;
                current.push(character);
            }
            ')' => {
                depth -= 1;
                current.push(character);
            }
            ' ' if depth == 0 => {
                if !current.is_empty() {
                    tokens.push(std::mem::take(&mut current));
                }
            }
            other => current.push(other),
        }
    }
    if !current.is_empty() {
        tokens.push(current);
    }
    tokens
}

struct Reply {
    status: StatusCode,
    body: String,
    correlation: String,
}

impl Reply {
    fn json(&self) -> Value {
        serde_json::from_str(&self.body).unwrap_or_else(|_| panic!("not JSON: {}", self.body))
    }

    fn details(&self) -> Vec<(String, String)> {
        self.json()["details"]
            .as_array()
            .unwrap()
            .iter()
            .map(|d| {
                (
                    d["field"].as_str().unwrap().to_owned(),
                    d["code"].as_str().unwrap().to_owned(),
                )
            })
            .collect()
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
        let database = owner_url()?;
        let redis_url = match std::env::var("TEST_REDIS_URL") {
            Ok(url) if !url.is_empty() => url,
            _ => {
                if std::env::var("MOSAIQ_REQUIRE_REDIS").as_deref() == Ok("1") {
                    panic!("TEST_REDIS_URL is missing, and MOSAIQ_REQUIRE_REDIS forbids skipping");
                }
                eprintln!("skipped: TEST_REDIS_URL is not set");
                return None;
            }
        };
        let mut redis = redis::Client::open(redis_url.as_str())
            .unwrap()
            .get_multiplexed_async_connection()
            .await
            .unwrap();
        let password = Uuid::new_v4().simple().to_string();
        let line = std::fs::read_to_string("redis/customer-preferences.acl").unwrap();
        let tokens = acl_tokens(line.trim());
        let _: i64 = redis::cmd("ACL")
            .arg("DELUSER")
            .arg("svc_preferences")
            .query_async(&mut redis)
            .await
            .unwrap();
        let mut setuser = redis::cmd("ACL");
        setuser.arg("SETUSER").arg("svc_preferences").arg("on");
        for token in &tokens[3..] {
            if token == "{{password}}" {
                setuser.arg(format!(">{password}"));
            } else {
                setuser.arg(token);
            }
        }
        let _: String = setuser.query_async(&mut redis).await.unwrap();
        let sid = Uuid::new_v4().to_string();
        let _: String = redis::cmd("SET")
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
            redis_state_url: redis_url,
            redis_password: Some(password),
            rate_limit_enabled: false,
            rate_limit_requests: 120,
            rate_limit_window_seconds: 60,
            database_url: Some(service_url(&database)),
            database_pool_timeout: Duration::from_secs(2),
            database_connect_timeout: Duration::from_secs(2),
        };
        let app = customer_preferences::router(config, "contracts").unwrap();
        let owner = connect(&database).await;
        Some(Self {
            app,
            keys,
            sid,
            owner,
            redis,
        })
    }

    async fn call(
        &self,
        method: &str,
        path: &str,
        role: &str,
        accept: &str,
        body: Option<(&str, &str)>,
    ) -> Reply {
        let correlation = format!("test-{}", Uuid::new_v4().simple());
        let sub = if role == "ADMIN" { ADMIN } else { OFFICER };
        let mut request = Request::builder()
            .method(method)
            .uri(path)
            .header("Accept", accept)
            .header("X-Correlation-ID", &correlation)
            .header(
                "Authorization",
                format!("Bearer {}", self.keys.token(sub, role, &self.sid)),
            );
        let payload = match body {
            Some((kind, text)) => {
                request = request.header("Content-Type", kind);
                text.to_owned()
            }
            None => String::new(),
        };
        let response = self
            .app
            .clone()
            .oneshot(request.body(Body::from(payload)).unwrap())
            .await
            .unwrap();
        let status = response.status();
        let body = response.into_body().collect().await.unwrap().to_bytes();
        Reply {
            status,
            body: String::from_utf8(body.to_vec()).unwrap(),
            correlation,
        }
    }

    async fn get(&self, path: &str, role: &str, accept: &str) -> Reply {
        self.call("GET", path, role, accept, None).await
    }

    async fn post(&self, path: &str, role: &str, accept: &str, kind: &str, body: &str) -> Reply {
        self.call("POST", path, role, accept, Some((kind, body)))
            .await
    }

    /// The domain events a request recorded.
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

    async fn count(&self, table: &str, customer: Uuid) -> i64 {
        let statement = match table {
            "consent_decision" => {
                "SELECT count(*) FROM preferences.consent_decision WHERE customer_id = $1"
            }
            "restriction_decision" => {
                "SELECT count(*) FROM preferences.restriction_decision WHERE customer_id = $1"
            }
            other => panic!("no statement for {other}"),
        };
        self.owner
            .query_one(statement, &[&customer])
            .await
            .unwrap()
            .get(0)
    }

    async fn finish(mut self) {
        let _: i64 = redis::cmd("ACL")
            .arg("DELUSER")
            .arg("svc_preferences")
            .query_async(&mut self.redis)
            .await
            .unwrap();
    }
}

fn example(name: &str) -> String {
    std::fs::read_to_string(format!("contracts/customer-preferences/examples/{name}"))
        .unwrap_or_else(|error| panic!("{name}: {error}; stage the contracts first"))
}

#[tokio::test]
async fn consent_and_restrictions_in_json_and_xml_by_the_roles_of_the_contract() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let customer = TestCustomer::create(&stack.owner).await;
    let base = format!("/v1/preference-profiles/{}", customer.id);
    let consent = format!("{base}/consent");
    let consent_log = format!("{base}/consent-decisions");
    let restrictions = format!("{base}/restrictions");
    let restriction_log = format!("{base}/restriction-decisions");
    let changes = format!("{base}/preference-changes");
    let register = example("record-consent.request.json");
    let restrict = example("record-restrictions.request.json");

    // Only ADMIN and LOYALTY_OFFICER write, and every other role is refused
    // before anything is appended, in both formats.
    for role in ROLES.iter().filter(|role| !WRITERS.contains(role)) {
        for (path, body) in [(&consent_log, &register), (&restriction_log, &restrict)] {
            for accept in [JSON, XML] {
                let reply = stack.post(path, role, accept, JSON, body).await;
                assert_eq!(reply.status, 403, "{role} {path}: {}", reply.body);
                assert!(reply.body.contains("forbidden_role"), "{}", reply.body);
            }
        }
    }
    assert_eq!(stack.count("consent_decision", customer.id).await, 0);
    assert_eq!(stack.count("restriction_decision", customer.id).await, 0);

    // The reads, by role. The two logs record each call, refusals included.
    for role in ROLES {
        for path in [&consent, &changes] {
            let reply = stack.get(path, role, JSON).await;
            if HISTORY_READERS.contains(&role) {
                assert_eq!(reply.status, 200, "{role} {path}: {}", reply.body);
            } else {
                assert_eq!(reply.status, 403, "{role} {path}");
                assert_eq!(reply.json()["code"], "forbidden_role");
            }
            assert!(
                stack.events(&reply.correlation).await.is_empty(),
                "{path} is the current state or unlogged, not a logged history"
            );
        }
        let reply = stack.get(&restrictions, role, JSON).await;
        assert_eq!(
            reply.status,
            if PROFILE_READERS.contains(&role) {
                200
            } else {
                403
            },
            "{role} {restrictions}"
        );
        for path in [&consent_log, &restriction_log] {
            let reply = stack.get(path, role, JSON).await;
            let events = stack.events(&reply.correlation).await;
            assert_eq!(events.len(), 1, "{role} {path}: {events:?}");
            assert_eq!(events[0]["kind"], "HISTORY_READ");
            assert_eq!(events[0]["details"], json!({"history": "CONSENT"}));
            assert_eq!(events[0]["subject_id"], customer.id.to_string());
            assert_eq!(events[0]["actor_role"], role);
            if HISTORY_READERS.contains(&role) {
                assert_eq!(reply.status, 200, "{role} {path}: {}", reply.body);
                assert_eq!(events[0]["outcome"], "SUCCESS");
            } else {
                assert_eq!(reply.status, 403, "{role} {path}");
                assert_eq!(reply.json()["code"], "forbidden_role");
                assert_eq!(events[0]["outcome"], "REFUSED");
            }
        }
    }

    // Registration: the first record, in JSON.
    let reply = stack
        .post(&consent_log, "LOYALTY_OFFICER", JSON, JSON, &register)
        .await;
    assert_eq!(reply.status, 201, "{}", reply.body);
    let state = reply.json();
    assert_eq!(state["customer_id"], customer.id.to_string());
    let purposes = state["purposes"].as_array().unwrap();
    assert_eq!(
        purposes
            .iter()
            .map(|p| p["purpose"].as_str().unwrap())
            .collect::<Vec<_>>(),
        ["PERSONALIZATION", "MARKETING_CONTACT", "PROFILING"]
    );
    assert_eq!(
        purposes
            .iter()
            .map(|p| p["state"].as_str().unwrap())
            .collect::<Vec<_>>(),
        ["GRANTED", "WITHDRAWN", "GRANTED"]
    );
    for purpose in purposes {
        assert_eq!(purpose["recorded_by"], OFFICER);
        assert_eq!(purpose["source"], "STAFF_ON_BEHALF");
        assert_eq!(purpose["basis"], "EXPLICIT");
        assert!(purpose["decision_id"].is_i64() && purpose["recorded_at"].is_string());
    }

    // The objection, in XML, and the current state after it.
    let reply = stack
        .post(
            &consent_log,
            "ADMIN",
            XML,
            XML,
            &example("withdraw-personalization.request.xml"),
        )
        .await;
    assert_eq!(reply.status, 201, "{}", reply.body);
    assert!(reply.body.starts_with("<?xml"), "{}", reply.body);
    assert!(
        reply.body.contains("<state>WITHDRAWN</state>"),
        "{}",
        reply.body
    );
    let now = stack.get(&consent, "AUDITOR", JSON).await.json();
    assert_eq!(now["purposes"][0]["state"], "WITHDRAWN");
    assert_eq!(now["purposes"][0]["recorded_by"], ADMIN);
    // The published view and the stream have it in the same commit.
    let view: String = stack
        .owner
        .query_one(
            "SELECT decision FROM api_preferences.consent_state_v1 \
             WHERE customer_id = $1 AND purpose = 'PERSONALIZATION'",
            &[&customer.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(view, "WITHDRAWN");
    let events: i64 = stack
        .owner
        .query_one(
            "SELECT count(*) FROM preferences_events.outbox WHERE entity_id = $1 \
             AND stream = 'stream:preferences.v1.consent_changed'",
            &[&customer.id.to_string()],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(events, 4, "three at registration and one objection");

    // Any past instant: a minute ago nothing was recorded, in both formats.
    let minute_ago = (chrono::Utc::now() - chrono::Duration::minutes(1))
        .to_rfc3339_opts(chrono::SecondsFormat::Secs, true);
    let past = stack
        .get(&format!("{consent}?as_of={minute_ago}"), "AUDITOR", JSON)
        .await;
    assert_eq!(past.status, 200, "{}", past.body);
    assert_eq!(past.json()["purposes"][0]["state"], "NOT_RECORDED");
    assert_eq!(past.json()["purposes"][0]["decision_id"], Value::Null);
    let xml = stack
        .get(&format!("{consent}?as_of={minute_ago}"), "AUDITOR", XML)
        .await;
    assert!(
        xml.body.contains("<state>NOT_RECORDED</state>"),
        "{}",
        xml.body
    );
    assert!(!xml.body.contains("<decision_id>"), "{}", xml.body);
    for (query, code) in [
        ("as_of=yesterday", "invalid_timestamp"),
        ("as_of=2999-01-01T00:00:00Z", "in_the_future"),
    ] {
        let reply = stack
            .get(&format!("{consent}?{query}"), "AUDITOR", JSON)
            .await;
        assert_eq!(reply.status, 422, "{query}: {}", reply.body);
        assert_eq!(reply.json()["code"], "invalid_query");
        assert_eq!(reply.details(), [("as_of".to_owned(), code.to_owned())]);
    }

    // The log: newest first, filtered, paged, and the previous grant stays.
    let log = stack.get(&consent_log, "AUDITOR", JSON).await.json();
    let rows = log["consent_decisions"].as_array().unwrap();
    assert_eq!(rows.len(), 4);
    assert_eq!(rows[0]["purpose"], "PERSONALIZATION");
    assert_eq!(rows[0]["decision"], "WITHDRAWN");
    assert_eq!(rows.last().unwrap()["decision"], "GRANTED");
    assert_eq!(log["page_info"], json!({"page": 1, "size": 20, "total": 4}));
    let ids: Vec<i64> = rows
        .iter()
        .map(|r| r["decision_id"].as_i64().unwrap())
        .collect();
    assert!(ids.windows(2).all(|pair| pair[0] > pair[1]));
    let filtered = stack
        .get(
            &format!("{consent_log}?purpose=PERSONALIZATION"),
            "AUDITOR",
            JSON,
        )
        .await
        .json();
    assert_eq!(filtered["page_info"]["total"], 2);
    let paged = stack
        .get(&format!("{consent_log}?size=2&page=2"), "AUDITOR", JSON)
        .await
        .json();
    assert_eq!(paged["consent_decisions"].as_array().unwrap().len(), 2);
    let xml = stack.get(&consent_log, "AUDITOR", XML).await;
    assert_eq!(
        xml.body.matches("<ConsentDecision>").count(),
        4,
        "{}",
        xml.body
    );
    let reply = stack
        .get(
            &format!("{consent_log}?purpose=ADVERTISING"),
            "AUDITOR",
            JSON,
        )
        .await;
    assert_eq!(reply.status, 422);
    assert_eq!(
        reply.details(),
        [("purpose".to_owned(), "not_allowed".to_owned())]
    );
    assert!(
        stack.events(&reply.correlation).await.is_empty(),
        "a malformed query reads nothing, so it records no read"
    );

    // What the body may not carry or repeat.
    let reply = stack
        .post(
            &consent_log,
            "ADMIN",
            JSON,
            JSON,
            r#"{"decisions":[{"purpose":"PROFILING","decision":"GRANTED"},{"purpose":"PROFILING","decision":"WITHDRAWN"}]}"#,
        )
        .await;
    assert_eq!(reply.status, 422);
    assert_eq!(reply.json()["code"], "invalid_body");
    assert_eq!(
        reply.details(),
        [("decisions[1].purpose".to_owned(), "duplicate".to_owned())]
    );
    let reply = stack
        .post(
            &consent_log,
            "ADMIN",
            JSON,
            JSON,
            &format!(r#"{{"decisions":[{{"purpose":"PROFILING","decision":"GRANTED","recorded_by":"{OFFICER}"}}]}}"#),
        )
        .await;
    assert_eq!(
        (reply.status.as_u16(), reply.details()),
        (422, vec![("recorded_by".into(), "unexpected_field".into())])
    );
    for file in [
        "record-consent-with-recorded-by.invalid.xml",
        "record-consent-with-unknown-purpose.invalid.xml",
    ] {
        let reply = stack
            .post(&consent_log, "ADMIN", XML, XML, &example(file))
            .await;
        assert_eq!(reply.status, 422, "{file}: {}", reply.body);
        assert!(reply.body.contains("<code>invalid_body</code>"), "{file}");
    }
    let four = r#"{"decisions":[{"purpose":"PROFILING","decision":"GRANTED"},{"purpose":"PERSONALIZATION","decision":"GRANTED"},{"purpose":"MARKETING_CONTACT","decision":"GRANTED"},{"purpose":"PROFILING","decision":"GRANTED"}]}"#;
    assert_eq!(
        stack
            .post(&consent_log, "ADMIN", JSON, JSON, four)
            .await
            .status,
        422
    );
    assert_eq!(
        stack
            .post(&consent_log, "ADMIN", JSON, JSON, "{nope")
            .await
            .status,
        400
    );
    assert_eq!(
        stack.count("consent_decision", customer.id).await,
        4,
        "refused calls appended nothing"
    );

    // Restrictions: JSON, then XML; lifted ones leave the current profile.
    let reply = stack
        .post(&restriction_log, "LOYALTY_OFFICER", JSON, JSON, &restrict)
        .await;
    assert_eq!(reply.status, 201, "{}", reply.body);
    assert_eq!(
        reply.json(),
        json!({
            "customer_id": customer.id.to_string(),
            "personalization_allowed": false,
            "restricted_channel_ids": [5],
            "restricted_category_ids": [9]
        })
    );
    let reply = stack
        .post(
            &restriction_log,
            "ADMIN",
            XML,
            XML,
            &example("record-restrictions.request.xml"),
        )
        .await;
    assert_eq!(reply.status, 201, "{}", reply.body);
    assert!(reply
        .body
        .contains("<restricted_channel_ids>5</restricted_channel_ids>"));
    assert!(reply
        .body
        .contains("<restricted_category_ids>9</restricted_category_ids>"));
    let profile = stack.get(&restrictions, "SALES_ASSOCIATE", JSON).await;
    assert_eq!(profile.status, 200);
    assert_eq!(profile.json()["restricted_category_ids"], json!([9]));
    assert_eq!(stack.count("restriction_decision", customer.id).await, 6);
    let log = stack
        .get(
            &format!("{restriction_log}?kind=INTEREST_CATEGORY"),
            "AUDITOR",
            JSON,
        )
        .await
        .json();
    assert_eq!(log["page_info"]["total"], 4);
    assert_eq!(log["restriction_decisions"][0]["decision"], "LIFTED");
    let reply = stack.get(&restriction_log, "AUDITOR", XML).await;
    assert_eq!(reply.body.matches("<RestrictionDecision>").count(), 6);
    for (body, field, code) in [
        (
            r#"{"decisions":[{"kind":"CHANNEL","target_id":9999,"decision":"IMPOSED"}]}"#,
            "decisions[0].target_id",
            "unknown_channel",
        ),
        (
            r#"{"decisions":[{"kind":"CHANNEL","target_id":1,"decision":"IMPOSED"},{"kind":"INTEREST_CATEGORY","target_id":9999,"decision":"IMPOSED"}]}"#,
            "decisions[1].target_id",
            "unknown_category",
        ),
    ] {
        let reply = stack
            .post(&restriction_log, "ADMIN", JSON, JSON, body)
            .await;
        assert_eq!(reply.status, 422, "{}", reply.body);
        assert_eq!(reply.json()["code"], code);
        assert_eq!(reply.details(), [(field.to_owned(), "unknown".to_owned())]);
    }
    let reply = stack
        .post(
            &restriction_log,
            "ADMIN",
            JSON,
            JSON,
            r#"{"decisions":[{"kind":"CHANNEL","target_id":1,"decision":"IMPOSED"},{"kind":"CHANNEL","target_id":1,"decision":"LIFTED"}]}"#,
        )
        .await;
    assert_eq!(reply.status, 422);
    assert_eq!(
        reply.details(),
        [("decisions[1]".to_owned(), "duplicate".to_owned())]
    );
    let reply = stack
        .post(
            &restriction_log,
            "ADMIN",
            XML,
            XML,
            &example("record-restrictions-with-unknown-kind.invalid.xml"),
        )
        .await;
    assert_eq!(reply.status, 422);
    assert_eq!(stack.count("restriction_decision", customer.id).await, 6);

    // The preference history, written by the whole-set replace.
    let put = |ids: &str| format!(r#"{{"channel_ids":{ids}}}"#);
    for ids in ["[1,3]", "[3,4]", "[3,4]"] {
        let reply = stack
            .call(
                "PUT",
                &format!("{base}/preferred-channels"),
                "LOYALTY_OFFICER",
                JSON,
                Some((JSON, &put(ids))),
            )
            .await;
        assert_eq!(reply.status, 200, "{}", reply.body);
    }
    let history = stack.get(&changes, "STORE_MANAGER", JSON).await.json();
    assert_eq!(
        history["page_info"]["total"], 2,
        "the repeat changed nothing"
    );
    assert_eq!(
        history["preference_changes"][0]["before_ids"],
        json!([1, 3])
    );
    assert_eq!(history["preference_changes"][0]["after_ids"], json!([3, 4]));
    assert_eq!(history["preference_changes"][1]["before_ids"], json!([]));
    assert_eq!(history["preference_changes"][1]["changed_by"], OFFICER);
    let xml = stack.get(&changes, "AUDITOR", XML).await;
    assert_eq!(
        xml.body.matches("<PreferenceChange>").count(),
        2,
        "{}",
        xml.body
    );
    let only = stack
        .get(
            &format!("{changes}?preference_set=INTEREST_CATEGORIES"),
            "AUDITOR",
            JSON,
        )
        .await
        .json();
    assert_eq!(only["page_info"]["total"], 0);
    let reply = stack
        .get(
            &format!("{changes}?preference_set=EVERYTHING"),
            "AUDITOR",
            JSON,
        )
        .await;
    assert_eq!(reply.status, 422);
    assert_eq!(reply.json()["code"], "invalid_query");

    // An unknown customer is 404 everywhere, and records no read.
    let nobody = Uuid::new_v4();
    for suffix in [
        "consent",
        "consent-decisions",
        "restrictions",
        "restriction-decisions",
        "preference-changes",
    ] {
        let reply = stack
            .get(
                &format!("/v1/preference-profiles/{nobody}/{suffix}"),
                "ADMIN",
                JSON,
            )
            .await;
        assert_eq!(reply.status, 404, "{suffix}: {}", reply.body);
        assert_eq!(reply.json()["code"], "customer_not_found");
        assert!(
            stack.events(&reply.correlation).await.is_empty(),
            "{suffix}: an unknown customer's read is rolled back with its event"
        );
    }
    for suffix in ["consent-decisions", "restriction-decisions"] {
        let body = if suffix == "consent-decisions" {
            &register
        } else {
            &restrict
        };
        let reply = stack
            .post(
                &format!("/v1/preference-profiles/{nobody}/{suffix}"),
                "ADMIN",
                JSON,
                JSON,
                body,
            )
            .await;
        assert_eq!(reply.status, 404, "{suffix}: {}", reply.body);
    }

    customer.cleanup(&stack.owner).await;
    stack.finish().await;
}

#[tokio::test]
async fn a_read_of_the_log_fails_closed_when_its_event_cannot_be_recorded() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let customer = TestCustomer::create(&stack.owner).await;
    let base = format!("/v1/preference-profiles/{}", customer.id);
    let seeded = stack
        .post(
            &format!("{base}/consent-decisions"),
            "ADMIN",
            JSON,
            JSON,
            &example("record-consent.request.json"),
        )
        .await;
    assert_eq!(seeded.status, 201);

    stack.owner.batch_execute(REVOKE).await.unwrap();
    let mut unavailable = Vec::new();
    for suffix in ["consent-decisions", "restriction-decisions"] {
        let path = format!("{base}/{suffix}");
        unavailable.push(stack.get(&path, "AUDITOR", JSON).await);
        unavailable.push(stack.get(&path, "AUDITOR", XML).await);
        // The refusal is a logged read too, so it fails closed as well.
        unavailable.push(stack.get(&path, "ANALYST", JSON).await);
    }
    // The reads that are not logged do not depend on the audit function.
    let current = stack.get(&format!("{base}/consent"), "AUDITOR", JSON).await;
    let restrictions = stack
        .get(&format!("{base}/restrictions"), "AUDITOR", JSON)
        .await;
    stack.owner.batch_execute(GRANT).await.unwrap();

    for reply in &unavailable {
        assert_eq!(reply.status, 503, "{}", reply.body);
        assert!(reply.body.contains("audit_unavailable"), "{}", reply.body);
        assert!(
            !reply.body.contains("PERSONALIZATION") && !reply.body.contains("decision_id"),
            "no row left with the failure: {}",
            reply.body
        );
        assert!(stack.events(&reply.correlation).await.is_empty());
    }
    assert!(
        unavailable[1].body.starts_with("<?xml"),
        "{}",
        unavailable[1].body
    );
    assert_eq!(current.status, 200);
    assert_eq!(restrictions.status, 200);

    let recovered = stack
        .get(&format!("{base}/consent-decisions"), "AUDITOR", JSON)
        .await;
    assert_eq!(recovered.status, 200, "{}", recovered.body);
    customer.cleanup(&stack.owner).await;
    stack.finish().await;
}
