//! The movement operations through the whole router: roles, the
//! `Idempotency-Key`, and what a client receives in JSON and in XML (F21-06).
//!
//! Needs TEST_DATABASE_URL (the seeded database, as its owner), TEST_REDIS_URL
//! (an administrator's URL to a disposable Redis) and the `openssl` command,
//! which makes a throwaway signing key. Without them it skips, or fails under
//! MOSAIQ_REQUIRE_POSTGRES / MOSAIQ_REQUIRE_REDIS.

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
use tokio_postgres::{Client, NoTls};
use tower::ServiceExt;
use uuid::Uuid;

const ISSUER: &str = "https://auth.test/";
const KID: &str = "stock-test";
const ADMIN: &str = "11111111-1111-1111-1111-000000000001";
const ROLES: [&str; 7] = [
    "ADMIN",
    "ANALYST",
    "STORE_MANAGER",
    "MARKETING",
    "INVENTORY_PLANNER",
    "AUDITOR",
    "CUSTOMER",
];
const PRODUCT: i32 = 8;

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
        let dir = std::env::temp_dir().join(format!("stock-test-{}", Uuid::new_v4()));
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
    replayed: Option<String>,
    body: String,
}

impl Reply {
    fn json(&self) -> Value {
        serde_json::from_str(&self.body).unwrap_or_else(|_| panic!("not JSON: {}", self.body))
    }
}

struct Call<'a> {
    method: &'a str,
    path: &'a str,
    token: &'a str,
    accept: &'a str,
    content_type: Option<&'a str>,
    key: Option<&'a str>,
    body: String,
}

fn post<'a>(token: &'a str, key: Option<&'a str>, body: &Value) -> Call<'a> {
    Call {
        method: "POST",
        path: "/v1/stock-movements",
        token,
        accept: "application/json",
        content_type: Some("application/json"),
        key,
        body: body.to_string(),
    }
}

fn get<'a>(token: &'a str, path: &'a str) -> Call<'a> {
    Call {
        method: "GET",
        path,
        token,
        accept: "application/json",
        content_type: None,
        key: None,
        body: String::new(),
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
    if let Some(key) = call.key {
        request = request.header("Idempotency-Key", key);
    }
    let response = app
        .clone()
        .oneshot(request.body(Body::from(call.body)).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let replayed = response
        .headers()
        .get("Idempotent-Replayed")
        .map(|value| value.to_str().unwrap().to_owned());
    let body = response.into_body().collect().await.unwrap().to_bytes();
    Reply {
        status,
        replayed,
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

fn service_url(owner: &str) -> String {
    let (scheme, rest) = owner.split_once("://").expect("a database URL");
    let after_user = rest.split_once('@').map_or(rest, |(_, host)| host);
    format!("{scheme}://svc_stock:svc_stock@{after_user}")
}

async fn connect(url: &str) -> Client {
    let (client, connection) = tokio_postgres::connect(url, NoTls).await.unwrap();
    tokio::spawn(connection);
    client
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

async fn cleanup(owner: &Client) {
    let keys: Vec<Uuid> = owner
        .query(
            "SELECT DISTINCT idempotency_key FROM stock.stock_movement \
             WHERE product_id = $1 AND idempotency_key IS NOT NULL",
            &[&PRODUCT],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get(0))
        .collect();
    owner
        .execute(
            "DELETE FROM stock.stock_movement WHERE product_id = $1",
            &[&PRODUCT],
        )
        .await
        .unwrap();
    owner
        .execute(
            "DELETE FROM stock.movement_request WHERE idempotency_key = ANY($1)",
            &[&keys],
        )
        .await
        .unwrap();
    owner
        .execute(
            "DELETE FROM public.inventory WHERE product_id = $1",
            &[&PRODUCT],
        )
        .await
        .unwrap();
}

async fn on_hand(owner: &Client, store_id: i16) -> Option<i32> {
    owner
        .query_opt(
            "SELECT quantity_on_hand FROM public.inventory \
             WHERE store_id = $1 AND product_id = $2",
            &[&store_id, &PRODUCT],
        )
        .await
        .unwrap()
        .map(|row| row.get(0))
}

#[tokio::test]
async fn roles_the_key_and_both_representations() {
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
    let line = std::fs::read_to_string("redis/stock.acl").unwrap();
    let tokens = acl_tokens(line.trim());
    let _: i64 = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_stock")
        .query_async(&mut admin)
        .await
        .unwrap();
    let mut setuser = redis::cmd("ACL");
    setuser.arg("SETUSER").arg("svc_stock").arg("on");
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
        database_url: Some(service_url(&database)),
        database_pool_timeout: Duration::from_secs(2),
        database_connect_timeout: Duration::from_secs(2),
    };
    let app = stock::router(config, "contracts").unwrap();
    let owner = connect(&database).await;
    cleanup(&owner).await;
    owner
        .execute(
            "INSERT INTO public.inventory (store_id, product_id, quantity_on_hand) \
             VALUES (1, $1, 10)",
            &[&PRODUCT],
        )
        .await
        .unwrap();

    let receipt = json!({
        "movement_type": "RECEIPT", "product_id": PRODUCT, "store_id": 1,
        "quantity": 5, "reference": "DN-HTTP-1", "note": "http test"
    });

    // Only ADMIN and INVENTORY_PLANNER write; every other role is refused
    // before anything is changed. The reads add AUDITOR.
    for role in ROLES {
        let token = keys.token(role, &sid);
        let key = Uuid::new_v4().to_string();
        let reply = send(&app, post(&token, Some(&key), &receipt)).await;
        if matches!(role, "ADMIN" | "INVENTORY_PLANNER") {
            assert_eq!(reply.status, 201, "{role}: {}", reply.body);
        } else {
            assert_eq!(reply.status, 403, "{role}: {}", reply.body);
            assert!(reply.body.contains("\"forbidden_role\""), "{}", reply.body);
        }
        for path in ["/v1/stock-movements", "/v1/stock-movements/1"] {
            let reply = send(&app, get(&token, path)).await;
            if matches!(role, "ADMIN" | "INVENTORY_PLANNER" | "AUDITOR") {
                assert!(matches!(reply.status.as_u16(), 200 | 404), "{role} {path}");
            } else {
                assert_eq!(reply.status, 403, "{role} {path}");
                assert!(reply.body.contains("\"forbidden_role\""));
            }
        }
    }
    // The planner and the administrator each added 5, nobody else did.
    assert_eq!(on_hand(&owner, 1).await, Some(20));

    let planner = keys.token("INVENTORY_PLANNER", &sid);

    // The key is required and is a UUID.
    for key in [None, Some("not-a-uuid"), Some("")] {
        let reply = send(&app, post(&planner, key, &receipt)).await;
        assert_eq!(reply.status, 400, "{key:?}: {}", reply.body);
        assert_eq!(reply.json()["code"], "idempotency_key_required");
    }
    assert_eq!(on_hand(&owner, 1).await, Some(20));

    // A first post is 201, and a retry is 200 with the replay header and the
    // same movement: nothing changes.
    let key = Uuid::new_v4().to_string();
    let first = send(&app, post(&planner, Some(&key), &receipt)).await;
    assert_eq!(first.status, 201, "{}", first.body);
    assert!(first.replayed.is_none());
    let created = first.json();
    let movement = &created["movements"][0];
    assert_eq!(movement["movement_type"], "RECEIPT");
    assert_eq!(movement["quantity_before"], 20);
    assert_eq!(movement["quantity_after"], 25);
    assert_eq!(movement["reference"], "DN-HTTP-1");
    assert_eq!(movement["recorded_by"], ADMIN);
    assert!(movement["transfer_id"].is_null());
    let retry = send(&app, post(&planner, Some(&key), &receipt)).await;
    assert_eq!(retry.status, 200, "{}", retry.body);
    assert_eq!(retry.replayed.as_deref(), Some("true"));
    assert_eq!(retry.json(), created);
    assert_eq!(on_hand(&owner, 1).await, Some(25));

    // An XML retry of the same movement is the same request, and answers in XML.
    let xml_body = format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\
         <StockMovementRequest xmlns=\"https://mosaiq.maxthecoder.online/xml/stock/v1\">\
         <movement_type>RECEIPT</movement_type><product_id>{PRODUCT}</product_id>\
         <store_id>1</store_id><quantity>5</quantity><reference>DN-HTTP-1</reference>\
         <note>http test</note></StockMovementRequest>"
    );
    let mut call = post(&planner, Some(&key), &json!(null));
    call.body = xml_body.clone();
    call.content_type = Some("application/xml");
    call.accept = "application/xml";
    let xml_retry = send(&app, call).await;
    assert_eq!(xml_retry.status, 200, "{}", xml_retry.body);
    assert_eq!(xml_retry.replayed.as_deref(), Some("true"));
    assert!(
        xml_retry.body.contains("<StockMovementRecord"),
        "{}",
        xml_retry.body
    );
    assert!(xml_retry
        .body
        .contains("<quantity_after>25</quantity_after>"));
    assert_eq!(on_hand(&owner, 1).await, Some(25));

    // The same key with another body is 409 and changes nothing.
    let other = json!({
        "movement_type": "RECEIPT", "product_id": PRODUCT, "store_id": 1,
        "quantity": 6, "reference": "DN-HTTP-1"
    });
    let reused = send(&app, post(&planner, Some(&key), &other)).await;
    assert_eq!(reused.status, 409);
    assert_eq!(reused.json()["code"], "idempotency_key_reused");

    // A transfer answers two rows, origin first, in both formats.
    let transfer = json!({
        "movement_type": "TRANSFER", "product_id": PRODUCT,
        "origin_store_id": 1, "destination_store_id": 2, "quantity": 9
    });
    let moved = send(
        &app,
        post(&planner, Some(&Uuid::new_v4().to_string()), &transfer),
    )
    .await;
    assert_eq!(moved.status, 201, "{}", moved.body);
    let rows = moved.json()["movements"].as_array().unwrap().clone();
    assert_eq!(rows.len(), 2);
    assert_eq!(
        (rows[0]["store_id"].as_i64(), rows[1]["store_id"].as_i64()),
        (Some(1), Some(2))
    );
    assert_eq!(rows[0]["transfer_id"], rows[1]["transfer_id"]);
    assert_eq!(on_hand(&owner, 1).await, Some(16));
    assert_eq!(on_hand(&owner, 2).await, Some(9));

    // The contract's refusals.
    let refusal = |reply: &Reply, status: u16, code: &str| {
        assert_eq!(reply.status, status, "{}", reply.body);
        assert_eq!(reply.json()["code"], code, "{}", reply.body);
    };
    let too_much = json!({
        "movement_type": "ADJUSTMENT", "product_id": PRODUCT, "store_id": 1,
        "quantity_delta": -100, "reason": "LOSS"
    });
    let reply = send(
        &app,
        post(&planner, Some(&Uuid::new_v4().to_string()), &too_much),
    )
    .await;
    refusal(&reply, 409, "stock_unavailable");
    let reply = send(
        &app,
        post(
            &planner,
            Some(&Uuid::new_v4().to_string()),
            &json!({"movement_type": "ADJUSTMENT", "product_id": PRODUCT, "store_id": 3,
                    "quantity_delta": -1, "reason": "LOSS"}),
        ),
    )
    .await;
    refusal(&reply, 422, "no_stock_record");
    let reply = send(
        &app,
        post(
            &planner,
            Some(&Uuid::new_v4().to_string()),
            &json!({"movement_type": "RECEIPT", "product_id": PRODUCT, "store_id": 9999,
                    "quantity": 1, "reference": "x"}),
        ),
    )
    .await;
    refusal(&reply, 422, "unknown_store");
    let reply = send(
        &app,
        post(
            &planner,
            Some(&Uuid::new_v4().to_string()),
            &json!({"movement_type": "RECEIPT", "product_id": 99999, "store_id": 1,
                    "quantity": 1, "reference": "x"}),
        ),
    )
    .await;
    refusal(&reply, 422, "unknown_product");
    let reply = send(
        &app,
        post(
            &planner,
            Some(&Uuid::new_v4().to_string()),
            &json!({"movement_type": "RESTOCK", "product_id": PRODUCT, "store_id": 1, "quantity": 1}),
        ),
    )
    .await;
    refusal(&reply, 422, "invalid_body");
    let reply = send(
        &app,
        post(
            &planner,
            Some(&Uuid::new_v4().to_string()),
            &json!({"movement_type": "RECEIPT", "product_id": PRODUCT, "store_id": 1, "quantity": 1}),
        ),
    )
    .await;
    refusal(&reply, 422, "reference_required");
    assert_eq!(on_hand(&owner, 1).await, Some(16));

    // The reads: a page, a filter, one movement, and the refusals.
    let page = send(
        &app,
        get(
            &planner,
            &format!("/v1/stock-movements?product_id={PRODUCT}&movement_type=TRANSFER"),
        ),
    )
    .await;
    assert_eq!(page.status, 200, "{}", page.body);
    let page = page.json();
    assert_eq!(page["stock_movements"].as_array().unwrap().len(), 2);
    let id = page["stock_movements"][0]["movement_id"].as_i64().unwrap();
    let one = send(&app, get(&planner, &format!("/v1/stock-movements/{id}"))).await;
    assert_eq!(one.status, 200, "{}", one.body);
    assert_eq!(one.json()["movement_id"], id);
    let mut xml_get = get(&planner, "/v1/stock-movements?movement_type=RECEIPT&size=2");
    xml_get.accept = "application/xml";
    let xml_page = send(&app, xml_get).await;
    assert_eq!(xml_page.status, 200, "{}", xml_page.body);
    assert!(
        xml_page.body.contains("<StockMovements"),
        "{}",
        xml_page.body
    );
    let missing = send(&app, get(&planner, "/v1/stock-movements/999999999")).await;
    refusal(&missing, 404, "stock_movement_not_found");
    for query in [
        "movement_type=SALE",
        "transfer_id=nope",
        "from=yesterday",
        "store_id=0",
    ] {
        let reply = send(&app, get(&planner, &format!("/v1/stock-movements?{query}"))).await;
        refusal(&reply, 422, "invalid_query");
    }

    cleanup(&owner).await;
    let _: i64 = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_stock")
        .query_async(&mut admin)
        .await
        .unwrap();
}
