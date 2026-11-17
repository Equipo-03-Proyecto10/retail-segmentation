//! Customer visits through the complete contract-driven router in JSON/XML.

mod support;

use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use axum::Router;
use chrono::{SecondsFormat, Utc};
use http_body_util::BodyExt;
use jsonwebtoken::{encode, Algorithm, EncodingKey, Header};
use mosaiq_kit::KitConfig;
use serde_json::{json, Value};
use support::{
    connect, owner_url, service_url, TestCustomer, TestStore, ADMIN, SALES_ASSOCIATE, STORE_MANAGER,
};
use tower::ServiceExt;
use uuid::Uuid;

const ISSUER: &str = "https://auth.test/";
const KID: &str = "check-ins-test";
const JSON: &str = "application/json";
const XML: &str = "application/xml";
const FORBIDDEN: [&str; 7] = [
    "ANALYST",
    "MARKETING",
    "INVENTORY_PLANNER",
    "AUDITOR",
    "CUSTOMER",
    "LOYALTY_OFFICER",
    "COMMERCIAL_SUPERVISOR",
];

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
    let output = Command::new("openssl").args(arguments).output().unwrap();
    assert!(output.status.success(), "openssl {arguments:?}");
    output.stdout
}

struct Keys {
    dir: PathBuf,
    pem: Vec<u8>,
}

impl Keys {
    fn make() -> Self {
        let dir = std::env::temp_dir().join(format!("check-ins-test-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let key = dir.join("key.pem");
        let path = key.to_str().unwrap();
        openssl(&["genrsa", "-out", path, "2048"]);
        let modulus =
            String::from_utf8(openssl(&["rsa", "-in", path, "-noout", "-modulus"])).unwrap();
        let hex = modulus.trim().strip_prefix("Modulus=").unwrap();
        let bytes = (0..hex.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
            .collect::<Vec<_>>();
        std::fs::write(
            dir.join("jwks.json"),
            json!({"keys": [{
                "kty": "RSA", "use": "sig", "alg": "RS256", "kid": KID,
                "n": base64url(&bytes), "e": "AQAB"
            }]})
            .to_string(),
        )
        .unwrap();
        let pem = std::fs::read(&key).unwrap();
        Self { dir, pem }
    }

    fn token(&self, sub: &str, role: &str, sid: &str) -> String {
        let now = Utc::now().timestamp();
        let claims = json!({
            "iss": ISSUER, "aud": "mosaiq-api", "sub": sub, "sid": sid,
            "role": role, "iat": now, "exp": now + 300
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
}

impl Reply {
    fn json(&self) -> Value {
        serde_json::from_str(&self.body).unwrap()
    }
}

struct Stack {
    app: Router,
    keys: Keys,
    sid: String,
    redis: redis::aio::MultiplexedConnection,
}

impl Stack {
    async fn start(database: &str) -> Option<Self> {
        let redis_url = match std::env::var("TEST_REDIS_URL") {
            Ok(url) if !url.is_empty() => url,
            _ => {
                if std::env::var("MOSAIQ_REQUIRE_REDIS").as_deref() == Ok("1") {
                    panic!("TEST_REDIS_URL is required");
                }
                return None;
            }
        };
        let mut redis = redis::Client::open(redis_url.as_str())
            .unwrap()
            .get_multiplexed_async_connection()
            .await
            .unwrap();
        let password = Uuid::new_v4().simple().to_string();
        let tokens = acl_tokens(
            std::fs::read_to_string("redis/check-ins.acl")
                .unwrap()
                .trim(),
        );
        let _: i64 = redis::cmd("ACL")
            .arg("DELUSER")
            .arg("svc_checkins")
            .query_async(&mut redis)
            .await
            .unwrap();
        let mut setuser = redis::cmd("ACL");
        setuser.arg("SETUSER").arg("svc_checkins").arg("on");
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
        let app = check_ins::router(
            KitConfig {
                issuer: ISSUER.to_owned(),
                jwks_path: keys.dir.join("jwks.json"),
                redis_state_url: redis_url,
                redis_password: Some(password),
                rate_limit_enabled: false,
                rate_limit_requests: 120,
                rate_limit_window_seconds: 60,
                database_url: Some(service_url(database)),
                database_pool_timeout: Duration::from_secs(2),
                database_connect_timeout: Duration::from_secs(2),
            },
            "contracts",
        )
        .unwrap();
        Some(Self {
            app,
            keys,
            sid,
            redis,
        })
    }

    async fn call(
        &self,
        method: &str,
        path: &str,
        sub: &str,
        role: &str,
        accept: &str,
        body: Option<(&str, String)>,
    ) -> Reply {
        let mut request = Request::builder()
            .method(method)
            .uri(path)
            .header("Accept", accept)
            .header(
                "Authorization",
                format!("Bearer {}", self.keys.token(sub, role, &self.sid)),
            );
        let payload = if let Some((kind, payload)) = body {
            request = request.header("Content-Type", kind);
            payload
        } else {
            String::new()
        };
        let response = self
            .app
            .clone()
            .oneshot(request.body(Body::from(payload)).unwrap())
            .await
            .unwrap();
        let status = response.status();
        let bytes = response.into_body().collect().await.unwrap().to_bytes();
        Reply {
            status,
            body: String::from_utf8(bytes.to_vec()).unwrap(),
        }
    }

    async fn finish(mut self) {
        let _: i64 = redis::cmd("ACL")
            .arg("DELUSER")
            .arg("svc_checkins")
            .query_async(&mut self.redis)
            .await
            .unwrap();
    }
}

fn json_body(code: &str, store: i16, campaign: i32, occurred_at: &str) -> String {
    json!({
        "loyalty_code": code,
        "store_id": store,
        "occurred_at": occurred_at,
        "campaign_id": campaign
    })
    .to_string()
}

fn xml_body(code: &str, store: i16, campaign: i32, occurred_at: &str) -> String {
    format!(
        "<CustomerVisitRequest xmlns=\"https://mosaiq.maxthecoder.online/xml/check-ins/v1\">\
         <loyalty_code>{code}</loyalty_code><store_id>{store}</store_id>\
         <occurred_at>{occurred_at}</occurred_at><campaign_id>{campaign}</campaign_id>\
         </CustomerVisitRequest>"
    )
}

#[tokio::test]
async fn router_enforces_roles_refusals_and_representations() {
    let Some(database) = owner_url() else { return };
    let owner = connect(&database).await;
    let Some(stack) = Stack::start(&database).await else {
        return;
    };
    let store = TestStore::create(&owner, true).await;
    store.assign(&owner, SALES_ASSOCIATE).await;
    let other = TestStore::create(&owner, true).await;
    other.unassign(&owner, STORE_MANAGER).await;
    let granted = TestCustomer::create(&owner, Some("GRANTED")).await;
    let granted_xml = TestCustomer::create(&owner, Some("GRANTED")).await;
    let withdrawn = TestCustomer::create(&owner, Some("WITHDRAWN")).await;
    let at = (Utc::now() - chrono::Duration::hours(8)).to_rfc3339_opts(SecondsFormat::Secs, true);
    let valid = json_body(&granted.code, store.id, 1, &at);

    for role in FORBIDDEN {
        for accept in [JSON, XML] {
            let reply = stack
                .call(
                    "POST",
                    "/v1/customer-visits",
                    ADMIN,
                    role,
                    accept,
                    Some((JSON, valid.clone())),
                )
                .await;
            assert_eq!(reply.status, 403, "{role} {accept}: {}", reply.body);
            assert!(reply.body.contains("forbidden_role"));
        }
    }

    for (content_type, make_body) in [
        (JSON, json_body as fn(&str, i16, i32, &str) -> String),
        (XML, xml_body as fn(&str, i16, i32, &str) -> String),
    ] {
        let future =
            (Utc::now() + chrono::Duration::minutes(6)).to_rfc3339_opts(SecondsFormat::Secs, true);
        let cases = [
            (
                make_body(&granted.code, store.id, 1, &future),
                "occurred_in_future",
            ),
            (
                make_body(&granted.code, other.id, 1, &at),
                "forbidden_store",
            ),
            (make_body(&granted.code, 32_767, 1, &at), "unknown_store"),
            (
                make_body(&granted.code, store.id, 999_999, &at),
                "unknown_campaign",
            ),
            (
                make_body("AAAAAAAAAAAAAAAAAAAAAAAAAA", store.id, 1, &at),
                "loyalty_code_not_usable",
            ),
        ];
        for (body, code) in cases {
            let role = if code == "forbidden_store" {
                "STORE_MANAGER"
            } else {
                "ADMIN"
            };
            let sub = if role == "STORE_MANAGER" {
                STORE_MANAGER
            } else {
                ADMIN
            };
            let reply = stack
                .call(
                    "POST",
                    "/v1/customer-visits",
                    sub,
                    role,
                    content_type,
                    Some((content_type, body)),
                )
                .await;
            assert!(
                reply.body.contains(code),
                "{content_type} {code}: {}",
                reply.body
            );
        }
        let reply = stack
            .call(
                "POST",
                "/v1/customer-visits",
                ADMIN,
                "ADMIN",
                content_type,
                Some((content_type, make_body(&withdrawn.code, store.id, 1, &at))),
            )
            .await;
        assert_eq!(reply.status, 409, "{}", reply.body);
        assert!(reply.body.contains("consent_required"));
    }

    owner
        .execute(
            "REVOKE SELECT ON api_preferences.consent_state_v1 FROM svc_checkins",
            &[],
        )
        .await
        .unwrap();
    for accept in [JSON, XML] {
        let body = if accept == JSON {
            json_body(&granted.code, store.id, 1, &at)
        } else {
            xml_body(&granted.code, store.id, 1, &at)
        };
        let unavailable = stack
            .call(
                "POST",
                "/v1/customer-visits",
                ADMIN,
                "ADMIN",
                accept,
                Some((accept, body)),
            )
            .await;
        assert_eq!(unavailable.status, 503, "{}", unavailable.body);
        assert!(unavailable.body.contains("consent_unavailable"));
        assert!(!unavailable.body.contains(&granted.id.to_string()));
    }
    owner
        .execute(
            "GRANT SELECT ON api_preferences.consent_state_v1 TO svc_checkins",
            &[],
        )
        .await
        .unwrap();

    let created = stack
        .call(
            "POST",
            "/v1/customer-visits",
            ADMIN,
            "ADMIN",
            JSON,
            Some((JSON, valid)),
        )
        .await;
    assert_eq!(created.status, 201, "{}", created.body);
    let document = created.json();
    assert_eq!(document["customer_id"], granted.id.to_string());
    assert_eq!(document["campaign_id"], 1);
    assert_eq!(document["recorded_by"], ADMIN);
    assert!(!created.body.contains(&granted.code));
    let visit_id = document["visit_id"].as_str().unwrap();

    let created_xml = stack
        .call(
            "POST",
            "/v1/customer-visits",
            ADMIN,
            "ADMIN",
            XML,
            Some((XML, xml_body(&granted_xml.code, store.id, 1, &at))),
        )
        .await;
    assert_eq!(created_xml.status, 201, "{}", created_xml.body);
    assert!(created_xml.body.contains("<CustomerVisit"));
    assert!(created_xml.body.contains(&granted_xml.id.to_string()));
    assert!(!created_xml.body.contains(&granted_xml.code));

    for accept in [JSON, XML] {
        let single = stack
            .call(
                "GET",
                &format!("/v1/customer-visits/{visit_id}"),
                ADMIN,
                "ADMIN",
                accept,
                None,
            )
            .await;
        assert_eq!(single.status, 200, "{}", single.body);
        assert!(single.body.contains(visit_id));
        assert!(!single.body.contains(&granted.code));
        let listed = stack
            .call(
                "GET",
                "/v1/customer-visits?page=1&size=20",
                ADMIN,
                "ADMIN",
                accept,
                None,
            )
            .await;
        assert_eq!(listed.status, 200, "{}", listed.body);
        assert!(listed.body.contains(visit_id));
    }

    for (role, sub) in [
        ("ADMIN", ADMIN),
        ("AUDITOR", ADMIN),
        ("STORE_MANAGER", STORE_MANAGER),
        ("SALES_ASSOCIATE", SALES_ASSOCIATE),
    ] {
        let reply = stack
            .call(
                "GET",
                &format!("/v1/customer-visits/{visit_id}"),
                sub,
                role,
                JSON,
                None,
            )
            .await;
        assert_eq!(reply.status, 200, "{role}: {}", reply.body);
    }
    for role in [
        "ANALYST",
        "MARKETING",
        "INVENTORY_PLANNER",
        "CUSTOMER",
        "LOYALTY_OFFICER",
        "COMMERCIAL_SUPERVISOR",
    ] {
        for path in [
            "/v1/customer-visits".to_owned(),
            format!("/v1/customer-visits/{visit_id}"),
        ] {
            let reply = stack.call("GET", &path, ADMIN, role, JSON, None).await;
            assert_eq!(reply.status, 403, "{role} {path}: {}", reply.body);
            assert!(reply.body.contains("forbidden_role"));
        }
    }

    store.cleanup(&owner).await;
    other.cleanup(&owner).await;
    stack.finish().await;
}
