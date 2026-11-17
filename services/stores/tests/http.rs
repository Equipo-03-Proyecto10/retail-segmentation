//! The eight operations of F21-12 through the whole router: the role policy of
//! the contract, the request and response of every example in JSON and in XML,
//! and the errors a client receives (F21-12, RN-53).
//!
//! Needs TEST_DATABASE_URL (the seeded database, as its owner), TEST_REDIS_URL
//! (an administrator's URL to a disposable Redis), the staged `contracts/`
//! directory and the `openssl` command, which makes a throwaway signing key.
//! Without them it skips, or fails under MOSAIQ_REQUIRE_POSTGRES /
//! MOSAIQ_REQUIRE_REDIS.

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
use tower::ServiceExt;
use uuid::Uuid;

const ISSUER: &str = "https://auth.test/";
const KID: &str = "stores-test";
const ADMIN: &str = "11111111-1111-1111-1111-000000000001";
const READERS: [&str; 9] = [
    "ADMIN",
    "ANALYST",
    "STORE_MANAGER",
    "MARKETING",
    "INVENTORY_PLANNER",
    "AUDITOR",
    "SALES_ASSOCIATE",
    "LOYALTY_OFFICER",
    "COMMERCIAL_SUPERVISOR",
];
const NOT_READERS: [&str; 1] = ["CUSTOMER"];
const RESOURCES: [&str; 4] = ["status", "hours", "channel-enablement", "commercial-rules"];

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
        let dir = std::env::temp_dir().join(format!("stores-test-{}", Uuid::new_v4()));
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

struct Reply {
    status: StatusCode,
    content_type: String,
    body: String,
}

impl Reply {
    fn json(&self) -> Value {
        serde_json::from_str(&self.body).unwrap_or_else(|_| panic!("not JSON: {}", self.body))
    }
}

struct Call<'a> {
    method: &'a str,
    path: String,
    token: &'a str,
    accept: &'a str,
    content_type: Option<&'a str>,
    body: String,
}

fn get<'a>(token: &'a str, path: &str, accept: &'a str) -> Call<'a> {
    Call {
        method: "GET",
        path: path.to_owned(),
        token,
        accept,
        content_type: None,
        body: String::new(),
    }
}

fn put<'a>(token: &'a str, path: &str, accept: &'a str, kind: &'a str, body: &str) -> Call<'a> {
    Call {
        method: "PUT",
        path: path.to_owned(),
        token,
        accept,
        content_type: Some(kind),
        body: body.to_owned(),
    }
}

async fn send(app: &Router, call: Call<'_>) -> Reply {
    let mut request = Request::builder()
        .method(call.method)
        .uri(call.path)
        .header("Accept", call.accept)
        .header("Authorization", format!("Bearer {}", call.token));
    if let Some(content_type) = call.content_type {
        request = request.header("Content-Type", content_type);
    }
    let response = app
        .clone()
        .oneshot(request.body(Body::from(call.body)).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let content_type = response
        .headers()
        .get("Content-Type")
        .map(|value| value.to_str().unwrap().to_owned())
        .unwrap_or_default();
    let body = response.into_body().collect().await.unwrap().to_bytes();
    Reply {
        status,
        content_type,
        body: String::from_utf8(body.to_vec()).unwrap(),
    }
}

fn required(name: &str, requirement: &str) -> Option<String> {
    match std::env::var(name) {
        Ok(url) if !url.is_empty() => Some(url),
        _ => {
            if std::env::var(requirement).as_deref() == Ok("1") {
                panic!("{name} is missing, and {requirement} forbids skipping");
            }
            eprintln!("skipped: {name} is not set");
            None
        }
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

fn example(name: &str) -> String {
    std::fs::read_to_string(format!("contracts/stores/examples/{name}"))
        .unwrap_or_else(|error| panic!("{name}: {error}; stage the contracts first"))
}

const JSON: &str = "application/json";
const XML: &str = "application/xml";

fn detail(reply: &Reply) -> Vec<(String, String)> {
    reply.json()["details"]
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

#[tokio::test]
async fn roles_examples_and_errors_in_json_and_xml() {
    let Some(database) = required("TEST_DATABASE_URL", "MOSAIQ_REQUIRE_POSTGRES") else {
        return;
    };
    let Some(redis_url) = required("TEST_REDIS_URL", "MOSAIQ_REQUIRE_REDIS") else {
        return;
    };
    let Some(keys) = Keys::make() else {
        if std::env::var("MOSAIQ_REQUIRE_REDIS").as_deref() == Ok("1") {
            panic!("openssl is required");
        }
        eprintln!("skipped: openssl is not available");
        return;
    };
    let mut admin = redis::Client::open(redis_url.as_str())
        .unwrap()
        .get_multiplexed_async_connection()
        .await
        .unwrap();

    // The committed ACL line, loaded for the user the kit signs in as.
    let password = Uuid::new_v4().simple().to_string();
    let line = std::fs::read_to_string("redis/stores.acl").unwrap();
    let tokens = acl_tokens(line.trim());
    let _: i64 = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_stores")
        .query_async(&mut admin)
        .await
        .unwrap();
    let mut setuser = redis::cmd("ACL");
    setuser.arg("SETUSER").arg("svc_stores").arg("on");
    for token in &tokens[3..] {
        if token == "{{password}}" {
            setuser.arg(format!(">{password}"));
        } else {
            setuser.arg(token);
        }
    }
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
        redis_state_url: redis_url.clone(),
        redis_password: Some(password),
        rate_limit_enabled: false,
        rate_limit_requests: 120,
        rate_limit_window_seconds: 60,
        database_url: Some(support::service_url(&database)),
        database_pool_timeout: Duration::from_secs(2),
        database_connect_timeout: Duration::from_secs(2),
    };
    let app = stores::router(config, "contracts").unwrap();
    let owner = support::connect(&database).await;
    let store = support::TestStore::create(&owner).await;
    let base = format!("/v1/stores/{}", store.id);
    let admin_token = keys.token("ADMIN", &sid);

    // Only ADMIN writes: every other role is refused before anything changes,
    // with the contract's `forbidden_role` in both formats.
    let bodies = [
        example("replace-store-status.request.json"),
        example("replace-store-hours.request.json"),
        example("replace-store-channels.request.json"),
        example("replace-store-commercial-rules.request.json"),
    ];
    for role in READERS.iter().chain(NOT_READERS.iter()) {
        let token = keys.token(role, &sid);
        for (resource, body) in RESOURCES.iter().zip(&bodies) {
            let path = format!("{base}/{resource}");
            if *role == "ADMIN" {
                continue;
            }
            for (accept, kind) in [(JSON, JSON), (XML, JSON)] {
                let reply = send(&app, put(&token, &path, accept, kind, body)).await;
                assert_eq!(reply.status, 403, "{role} PUT {resource}: {}", reply.body);
                if accept == JSON {
                    assert_eq!(reply.json()["code"], "forbidden_role");
                } else {
                    assert!(reply.body.contains("<code>forbidden_role</code>"));
                }
            }
        }
        for resource in RESOURCES {
            let reply = send(&app, get(&token, &format!("{base}/{resource}"), JSON)).await;
            if READERS.contains(role) {
                assert_eq!(reply.status, 200, "{role} GET {resource}: {}", reply.body);
            } else {
                assert_eq!(reply.status, 403, "{role} GET {resource}");
                assert_eq!(reply.json()["code"], "forbidden_role");
            }
        }
    }
    let rows: i64 = owner
        .query_one(
            "SELECT (SELECT count(*) FROM stores.store_status WHERE store_id = $1) \
                  + (SELECT count(*) FROM stores.store_hours WHERE store_id = $1) \
                  + (SELECT count(*) FROM stores.store_channel_set WHERE store_id = $1) \
                  + (SELECT count(*) FROM stores.store_commercial_rule WHERE store_id = $1)",
            &[&store.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(rows, 0, "no refused role wrote anything");

    // Never written: the contract's defaults, null `updated_at` included.
    let reply = send(&app, get(&admin_token, &format!("{base}/status"), JSON)).await;
    assert_eq!(reply.json(), json!({"status": "OPEN", "updated_at": null}));
    let reply = send(&app, get(&admin_token, &format!("{base}/hours"), JSON)).await;
    assert_eq!(
        reply.json(),
        json!({"time_zone": null, "intervals": [], "updated_at": null})
    );
    let reply = send(
        &app,
        get(&admin_token, &format!("{base}/channel-enablement"), JSON),
    )
    .await;
    assert_eq!(reply.json(), json!({"channel_ids": [], "updated_at": null}));
    let reply = send(
        &app,
        get(&admin_token, &format!("{base}/commercial-rules"), JSON),
    )
    .await;
    assert_eq!(
        reply.json(),
        json!({"max_discount_percent": null, "updated_at": null})
    );
    let reply = send(&app, get(&admin_token, &format!("{base}/hours"), XML)).await;
    assert!(reply.content_type.starts_with(XML));
    assert!(reply.body.contains("<StoreHours"));
    assert!(!reply.body.contains("<time_zone>") && !reply.body.contains("<updated_at>"));

    // JSON: each example request is accepted and answered as the state stored.
    let reply = send(
        &app,
        put(
            &admin_token,
            &format!("{base}/status"),
            JSON,
            JSON,
            &bodies[0],
        ),
    )
    .await;
    assert_eq!(reply.status, 200, "{}", reply.body);
    let status = reply.json();
    assert_eq!(status["status"], "TEMPORARILY_CLOSED");
    assert!(status["updated_at"].is_string());
    assert!(reply.content_type.starts_with(JSON));

    let reply = send(
        &app,
        put(
            &admin_token,
            &format!("{base}/hours"),
            JSON,
            JSON,
            &bodies[1],
        ),
    )
    .await;
    assert_eq!(reply.status, 200, "{}", reply.body);
    let hours = reply.json();
    assert_eq!(hours["time_zone"], "America/Mexico_City");
    assert_eq!(
        hours["intervals"],
        serde_json::from_str::<Value>(&bodies[1]).unwrap()["intervals"]
    );

    let reply = send(
        &app,
        put(
            &admin_token,
            &format!("{base}/channel-enablement"),
            JSON,
            JSON,
            &bodies[2],
        ),
    )
    .await;
    assert_eq!(reply.status, 200, "{}", reply.body);
    assert_eq!(reply.json()["channel_ids"], json!([1, 3, 4]));

    let reply = send(
        &app,
        put(
            &admin_token,
            &format!("{base}/commercial-rules"),
            JSON,
            JSON,
            &bodies[3],
        ),
    )
    .await;
    assert_eq!(reply.status, 200, "{}", reply.body);
    assert_eq!(reply.json()["max_discount_percent"], "15.00");

    // A read returns what the write answered, in JSON and in XML.
    for (resource, needle) in [
        ("status", "<status>TEMPORARILY_CLOSED</status>"),
        ("hours", "<time_zone>America/Mexico_City</time_zone>"),
        ("channel-enablement", "<channel_ids>3</channel_ids>"),
        (
            "commercial-rules",
            "<max_discount_percent>15.00</max_discount_percent>",
        ),
    ] {
        let path = format!("{base}/{resource}");
        let as_json = send(&app, get(&admin_token, &path, JSON)).await;
        assert_eq!(as_json.status, 200);
        assert!(as_json.json()["updated_at"].is_string(), "{resource}");
        let as_xml = send(&app, get(&admin_token, &path, XML)).await;
        assert_eq!(as_xml.status, 200, "{}", as_xml.body);
        assert!(as_xml.content_type.starts_with(XML));
        assert!(as_xml.body.contains(needle), "{resource}: {}", as_xml.body);
        assert!(as_xml.body.contains("<updated_at>"), "{resource}");
    }

    // XML: each example request is accepted and answered in XML.
    let xml_requests = [
        (
            "status",
            "replace-store-status.request.xml",
            "<status>TEMPORARILY_CLOSED</status>",
        ),
        (
            "hours",
            "replace-store-hours.request.xml",
            "<day_of_week>6</day_of_week>",
        ),
        (
            "channel-enablement",
            "replace-store-channels.request.xml",
            "<channel_ids>4</channel_ids>",
        ),
        (
            "commercial-rules",
            "replace-store-commercial-rules.request.xml",
            "<max_discount_percent>15.00</max_discount_percent>",
        ),
    ];
    for (resource, file, needle) in xml_requests {
        let reply = send(
            &app,
            put(
                &admin_token,
                &format!("{base}/{resource}"),
                XML,
                XML,
                &example(file),
            ),
        )
        .await;
        assert_eq!(reply.status, 200, "{file}: {}", reply.body);
        assert!(reply.body.contains(needle), "{file}: {}", reply.body);
    }

    // Clearing, in JSON and in XML: the cleared state still has `updated_at`.
    let cleared_json = [
        ("hours", "clear-store-hours.request.json"),
        ("channel-enablement", "clear-store-channels.request.json"),
        (
            "commercial-rules",
            "clear-store-commercial-rules.request.json",
        ),
    ];
    for (resource, file) in cleared_json {
        let reply = send(
            &app,
            put(
                &admin_token,
                &format!("{base}/{resource}"),
                JSON,
                JSON,
                &example(file),
            ),
        )
        .await;
        assert_eq!(reply.status, 200, "{file}: {}", reply.body);
        let body = reply.json();
        assert!(body["updated_at"].is_string(), "{file}");
        match resource {
            "hours" => assert_eq!(
                (&body["time_zone"], &body["intervals"]),
                (&Value::Null, &json!([]))
            ),
            "channel-enablement" => assert_eq!(body["channel_ids"], json!([])),
            _ => assert_eq!(body["max_discount_percent"], Value::Null),
        }
    }
    // Set again, then clear in XML.
    for (resource, set, clear) in [
        (
            "hours",
            "replace-store-hours.request.xml",
            "clear-store-hours.request.xml",
        ),
        (
            "channel-enablement",
            "replace-store-channels.request.xml",
            "clear-store-channels.request.xml",
        ),
        (
            "commercial-rules",
            "replace-store-commercial-rules.request.xml",
            "clear-store-commercial-rules.request.xml",
        ),
    ] {
        let path = format!("{base}/{resource}");
        let reply = send(&app, put(&admin_token, &path, XML, XML, &example(set))).await;
        assert_eq!(reply.status, 200, "{set}: {}", reply.body);
        let reply = send(&app, put(&admin_token, &path, XML, XML, &example(clear))).await;
        assert_eq!(reply.status, 200, "{clear}: {}", reply.body);
        assert!(reply.body.contains("<updated_at>"), "{clear}");
        for omitted in [
            "<time_zone>",
            "<channel_ids>",
            "<max_discount_percent>",
            "<intervals>",
        ] {
            assert!(!reply.body.contains(omitted), "{clear}: {}", reply.body);
        }
    }

    // Every write above is audited under the administrator who made it.
    let audited: i64 = owner
        .query_one(
            "SELECT count(*) FROM audit_log WHERE entity_pk = $1 AND user_id = $2 \
             AND entity IN ('store_status', 'store_hours', 'store_hours_interval', \
                            'store_channel_set', 'store_channel', 'store_commercial_rule')",
            &[&store.id.to_string(), &Uuid::parse_str(ADMIN).unwrap()],
        )
        .await
        .unwrap()
        .get(0);
    assert!(audited >= 8, "{audited}");

    // Refusals: the contract's codes, fields and details.
    let refuse = |path: String, body: String, kind: &'static str| {
        let app = app.clone();
        let token = admin_token.clone();
        async move {
            let reply = send(&app, put(&token, &path, JSON, kind, &body)).await;
            assert_eq!(reply.status, 422, "{path} {body}: {}", reply.body);
            assert_eq!(reply.json()["code"], "invalid_body");
            detail(&reply)
        }
    };
    let status = format!("{base}/status");
    let hours = format!("{base}/hours");
    let channels = format!("{base}/channel-enablement");
    let rules = format!("{base}/commercial-rules");
    assert_eq!(
        refuse(status.clone(), r#"{"status":"ARCHIVED"}"#.into(), JSON).await,
        [("status".to_owned(), "not_allowed".to_owned())]
    );
    let interval = |day: i64, opens: &str, closes: &str| json!({"day_of_week": day, "opens_at": opens, "closes_at": closes});
    assert_eq!(
        refuse(
            hours.clone(),
            json!({"time_zone": "UTC", "intervals": [interval(1, "09:00", "17:00"), interval(1, "10:00", "12:00")]}).to_string(),
            JSON,
        )
        .await,
        [("intervals.1.day_of_week".to_owned(), "duplicate_day".to_owned())]
    );
    assert_eq!(
        refuse(
            hours.clone(),
            json!({"time_zone": "UTC", "intervals": [interval(1, "17:00", "09:00")]}).to_string(),
            JSON,
        )
        .await,
        [(
            "intervals.0.closes_at".to_owned(),
            "closes_before_opens".to_owned()
        )]
    );
    assert_eq!(
        refuse(
            hours.clone(),
            json!({"time_zone": "UTC", "intervals": [interval(8, "09:00", "17:00")]}).to_string(),
            JSON,
        )
        .await,
        [(
            "intervals.0.day_of_week".to_owned(),
            "out_of_range".to_owned()
        )]
    );
    assert_eq!(
        refuse(
            hours.clone(),
            json!({"time_zone": null, "intervals": [interval(1, "09:00", "17:00")]}).to_string(),
            JSON,
        )
        .await,
        [("time_zone".to_owned(), "time_zone_required".to_owned())]
    );
    assert_eq!(
        refuse(
            hours.clone(),
            json!({"time_zone": "UTC", "intervals": []}).to_string(),
            JSON,
        )
        .await,
        [("time_zone".to_owned(), "time_zone_without_hours".to_owned())]
    );
    assert_eq!(
        refuse(
            hours.clone(),
            json!({"time_zone": "Mars/Base", "intervals": [interval(1, "09:00", "17:00")]})
                .to_string(),
            JSON,
        )
        .await,
        [("time_zone".to_owned(), "invalid_format".to_owned())]
    );
    assert_eq!(
        refuse(channels.clone(), r#"{"channel_ids":[1,1]}"#.into(), JSON).await,
        [("channel_ids".to_owned(), "duplicate_channel".to_owned())]
    );
    assert_eq!(
        refuse(channels.clone(), r#"{"channel_ids":[1,99]}"#.into(), JSON).await,
        [("channel_ids".to_owned(), "unknown_channel".to_owned())]
    );
    // The schema's anyOf (a two-decimal string or null) reports the kit's
    // generic `invalid`; the XSD names the facet.
    assert_eq!(
        refuse(
            rules.clone(),
            r#"{"max_discount_percent":"100.01"}"#.into(),
            JSON
        )
        .await,
        [("max_discount_percent".to_owned(), "invalid".to_owned())]
    );
    assert_eq!(
        refuse(rules.clone(), r#"{"max_discount_percent":15}"#.into(), JSON).await,
        [("max_discount_percent".to_owned(), "invalid".to_owned())]
    );
    // The XML twins refuse by the XSD, or by the service where XSD cannot.
    for (path, file, expected) in [
        (
            &status,
            "replace-store-status-unknown.invalid.xml",
            "not_allowed",
        ),
        (
            &hours,
            "replace-store-hours-bad-time.invalid.xml",
            "invalid_format",
        ),
        (
            &channels,
            "replace-store-channels-text.invalid.xml",
            "invalid_type",
        ),
        (
            &rules,
            "replace-store-commercial-rules-over-100.invalid.xml",
            "invalid_format",
        ),
    ] {
        let reply = send(&app, put(&admin_token, path, XML, XML, &example(file))).await;
        assert_eq!(reply.status, 422, "{file}: {}", reply.body);
        assert!(reply.body.contains("<code>invalid_body</code>"), "{file}");
        assert!(
            reply.body.contains(&format!("<code>{expected}</code>")),
            "{file}: {}",
            reply.body
        );
    }
    let twice = r#"<?xml version="1.0"?><StoreChannelsUpdate xmlns="https://mosaiq.maxthecoder.online/xml/stores/v1"><channel_ids>2</channel_ids><channel_ids>2</channel_ids></StoreChannelsUpdate>"#;
    let reply = send(&app, put(&admin_token, &channels, XML, XML, twice)).await;
    assert_eq!(reply.status, 422, "{}", reply.body);
    assert!(reply.body.contains("<code>duplicate_channel</code>"));
    let unknown = twice.replace(
        "<channel_ids>2</channel_ids><channel_ids>2</channel_ids>",
        "<channel_ids>99</channel_ids>",
    );
    let reply = send(&app, put(&admin_token, &channels, XML, XML, &unknown)).await;
    assert_eq!(reply.status, 422, "{}", reply.body);
    assert!(reply.body.contains("<code>unknown_channel</code>"));
    let reply = send(&app, put(&admin_token, &status, JSON, JSON, "{not json")).await;
    assert_eq!(reply.status, 400);
    assert_eq!(reply.json()["code"], "malformed_body");

    // Nothing the refusals touched changed: the last cleared state stands.
    let reply = send(&app, get(&admin_token, &channels, JSON)).await;
    assert_eq!(reply.json()["channel_ids"], json!([]));

    // An unknown store is 404 on every operation, as the contract says.
    for resource in RESOURCES {
        let path = format!("/v1/stores/29999/{resource}");
        let reply = send(&app, get(&admin_token, &path, JSON)).await;
        assert_eq!(reply.status, 404, "GET {resource}");
        assert_eq!(reply.json()["code"], "store_not_found");
    }
    for (resource, body) in RESOURCES.iter().zip(&bodies) {
        let path = format!("/v1/stores/29999/{resource}");
        let reply = send(&app, put(&admin_token, &path, JSON, JSON, body)).await;
        assert_eq!(reply.status, 404, "PUT {resource}");
        assert_eq!(reply.json()["code"], "store_not_found");
    }

    // The directory's own shape is unchanged: is_active stays the web's.
    let reply = send(&app, get(&admin_token, &base, JSON)).await;
    assert_eq!(reply.status, 200);
    let store_body = reply.json();
    assert_eq!(store_body["is_active"], true);
    let mut members: Vec<_> = store_body.as_object().unwrap().keys().cloned().collect();
    members.sort();
    assert_eq!(
        members,
        ["city", "is_active", "location", "name", "state", "store_id"]
    );

    owner
        .execute(
            "DELETE FROM stores_events.outbox WHERE entity_id = $1",
            &[&store.id.to_string()],
        )
        .await
        .unwrap();
    store.cleanup(&owner).await;
}
