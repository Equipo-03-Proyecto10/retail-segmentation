//! Availability by store, product and channel through the whole router
//! (F21-07, ADR-0061): one test per acceptance criterion of #787.
//!
//! Needs TEST_DATABASE_URL (the seeded database, as its owner), TEST_REDIS_URL
//! (an administrator's URL to a disposable Redis) and the `openssl` command,
//! which makes a throwaway signing key. Without them it skips, or fails under
//! MOSAIQ_REQUIRE_POSTGRES / MOSAIQ_REQUIRE_REDIS.
//!
//! The fixtures are committed, because the router reads through its own pool,
//! and each is put back at the end. Every test holds one lock, so no test
//! reads while another has a fixture in place.
//!
//! Seeded facts used here: store 3 is OPEN and enables web (2) and
//! physical_store (3) only; store 30 is CLOSED and enables physical_store;
//! stock records exist for products 1 to 5 only.

use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use axum::Router;
use chrono::{DateTime, Utc};
use http_body_util::BodyExt;
use jsonwebtoken::{encode, Algorithm, EncodingKey, Header};
use mosaiq_kit::KitConfig;
use serde_json::{json, Value};
use tokio::sync::{Mutex, MutexGuard};
use tokio_postgres::{Client, NoTls};
use tower::ServiceExt;
use uuid::Uuid;

const ISSUER: &str = "https://auth.test/";
const KID: &str = "stock-availability-test";
const ADMIN: &str = "11111111-1111-1111-1111-000000000001";
const STORE: i16 = 3;
const CLOSED_STORE: i16 = 30;
const IN_STOCK: i32 = 1;
const NOT_SELLABLE: i32 = 2;
const OUT_OF_STOCK: i32 = 4;
const NO_RECORD: i32 = 9;
const WEB: i16 = 2;
const PHYSICAL: i16 = 3;
const MOBILE: i16 = 1;

async fn test_lock() -> MutexGuard<'static, ()> {
    static LOCK: Mutex<()> = Mutex::const_new(());
    LOCK.lock().await
}

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

struct Keys {
    dir: PathBuf,
    pem: Vec<u8>,
}

impl Keys {
    fn make() -> Option<Self> {
        let dir = std::env::temp_dir().join(format!("stock-availability-{}", Uuid::new_v4()));
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
    body: String,
}

impl Reply {
    fn json(&self) -> Value {
        serde_json::from_str(&self.body).unwrap_or_else(|_| panic!("not JSON: {}", self.body))
    }
}

async fn get(app: &Router, token: &str, path: &str, accept: &str) -> Reply {
    let request = Request::builder()
        .method("GET")
        .uri(path)
        .header("Accept", accept)
        .header("Authorization", format!("Bearer {token}"))
        .body(Body::empty())
        .unwrap();
    let response = app.clone().oneshot(request).await.unwrap();
    let status = response.status();
    let body = response.into_body().collect().await.unwrap().to_bytes();
    Reply {
        status,
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

/// The router as the service runs it, a live session for `sid`, and the
/// owner's connection for fixtures.
struct Harness {
    app: Router,
    keys: Keys,
    sid: String,
    owner: Client,
    database: String,
}

async fn harness() -> Option<Harness> {
    let database = required("TEST_DATABASE_URL", "MOSAIQ_REQUIRE_POSTGRES")?;
    let redis_url = required("TEST_REDIS_URL", "MOSAIQ_REQUIRE_REDIS")?;
    let Some(keys) = Keys::make() else {
        if std::env::var("MOSAIQ_REQUIRE_REDIS").as_deref() == Ok("1") {
            panic!("openssl is required");
        }
        eprintln!("skipped: openssl is not available");
        return None;
    };
    let mut admin = redis::Client::open(redis_url.as_str())
        .unwrap()
        .get_multiplexed_async_connection()
        .await
        .unwrap();
    let password = Uuid::new_v4().simple().to_string();
    let line = std::fs::read_to_string("redis/stock.acl").unwrap();
    let tokens = acl_tokens(line.trim());
    let mut setuser = redis::cmd("ACL");
    setuser
        .arg("SETUSER")
        .arg("svc_stock")
        .arg("reset")
        .arg("on");
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
        redis_state_url: redis_url,
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
    Some(Harness {
        app,
        keys,
        sid,
        owner,
        database,
    })
}

async fn on_hand(owner: &Client, store_id: i16, product_id: i32) -> i32 {
    owner
        .query_one(
            "SELECT quantity_on_hand FROM public.inventory \
             WHERE store_id = $1 AND product_id = $2",
            &[&store_id, &product_id],
        )
        .await
        .unwrap()
        .get(0)
}

async fn set_on_hand(owner: &Client, store_id: i16, product_id: i32, quantity: i32) {
    owner
        .execute(
            "UPDATE public.inventory SET quantity_on_hand = $1, updated_at = now() \
             WHERE store_id = $2 AND product_id = $3",
            &[&quantity, &store_id, &product_id],
        )
        .await
        .unwrap();
}

async fn set_active(owner: &Client, product_id: i32, active: bool) {
    owner
        .execute(
            "UPDATE public.product SET is_active = $1 WHERE product_id = $2",
            &[&active, &product_id],
        )
        .await
        .unwrap();
}

/// The one row for a channel out of a pair's page.
fn channel_row(page: &Value, channel_id: i16) -> Value {
    page["availabilities"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["channel_id"] == i64::from(channel_id))
        .unwrap_or_else(|| panic!("no row for channel {channel_id}: {page}"))
        .clone()
}

/// Criterion 1: for a store, a channel and a product, availability follows
/// ADR-0061's rule and names the first failing test, in JSON and in XML.
#[tokio::test]
async fn availability_follows_the_rule_and_names_the_reason_in_json_and_xml() {
    let _guard = test_lock().await;
    let Some(h) = harness().await else {
        return;
    };
    let token = h.keys.token("SALES_ASSOCIATE", &h.sid);
    let original_out = on_hand(&h.owner, STORE, OUT_OF_STOCK).await;
    set_on_hand(&h.owner, STORE, OUT_OF_STOCK, 0).await;
    set_active(&h.owner, NOT_SELLABLE, false).await;

    let pair = |product: i32| format!("/v1/availabilities/{STORE}/{product}");
    let json_page = |reply: Reply| {
        assert_eq!(reply.status, 200, "{}", reply.body);
        reply.json()
    };

    // Every channel has a row, in channel order, with ids and quantities only.
    let available = json_page(get(&h.app, &token, &pair(IN_STOCK), "application/json").await);
    let channels: Vec<i64> = available["availabilities"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| row["channel_id"].as_i64().unwrap())
        .collect();
    assert_eq!(channels, [1, 2, 3, 4, 5]);
    assert_eq!(available["page_info"]["total"], 5);
    let row = channel_row(&available, PHYSICAL);
    assert_eq!(row["is_available"], true);
    assert_eq!(row["reason"], "AVAILABLE");
    assert!(row["quantity_on_hand"].as_i64().unwrap() > 0);
    assert!(row["updated_at"].is_string());

    // A product whose commercial status does not allow the sale is refused
    // first, though it is in stock in an enabled channel.
    let paused = json_page(get(&h.app, &token, &pair(NOT_SELLABLE), "application/json").await);
    for row in paused["availabilities"].as_array().unwrap() {
        assert_eq!(row["reason"], "NOT_SELLABLE", "{row}");
        assert_eq!(row["is_available"], false);
    }
    assert!(
        channel_row(&paused, PHYSICAL)["quantity_on_hand"]
            .as_i64()
            .unwrap()
            > 0
    );

    // Zero on hand is OUT_OF_STOCK; no record at all is NO_STOCK_RECORD with
    // null quantity and time, which is not the same as zero.
    let empty = json_page(get(&h.app, &token, &pair(OUT_OF_STOCK), "application/json").await);
    let row = channel_row(&empty, PHYSICAL);
    assert_eq!(
        (&row["reason"], &row["quantity_on_hand"]),
        (&json!("OUT_OF_STOCK"), &json!(0))
    );
    let none = json_page(get(&h.app, &token, &pair(NO_RECORD), "application/json").await);
    let row = channel_row(&none, PHYSICAL);
    assert_eq!(row["reason"], "NO_STOCK_RECORD");
    assert_eq!(row["is_available"], false);
    assert!(row["quantity_on_hand"].is_null() && row["updated_at"].is_null());

    // The list applies the same rule and its filters.
    let list = json_page(
        get(
            &h.app,
            &token,
            &format!("/v1/availabilities?store_id={STORE}&channel_id={PHYSICAL}&available=false"),
            "application/json",
        )
        .await,
    );
    let rows = list["availabilities"].as_array().unwrap();
    assert!(!rows.is_empty());
    assert!(rows
        .iter()
        .all(|row| row["is_available"] == false && row["store_id"] == i64::from(STORE)));
    for (product, reason) in [
        (NOT_SELLABLE, "NOT_SELLABLE"),
        (OUT_OF_STOCK, "OUT_OF_STOCK"),
        (NO_RECORD, "NO_STOCK_RECORD"),
    ] {
        assert!(
            rows.iter()
                .any(|row| row["product_id"] == product && row["reason"] == reason),
            "{product} {reason}: {list}"
        );
    }
    assert!(!rows.iter().any(|row| row["product_id"] == IN_STOCK));

    // XML carries the same rows; a missing quantity is omitted, not empty.
    let xml = get(&h.app, &token, &pair(NO_RECORD), "application/xml").await;
    assert_eq!(xml.status, 200, "{}", xml.body);
    assert!(xml.body.contains("<Availabilities"), "{}", xml.body);
    assert!(xml.body.contains("<reason>NO_STOCK_RECORD</reason>"));
    assert!(!xml.body.contains("quantity_on_hand"), "{}", xml.body);
    let xml = get(&h.app, &token, &pair(OUT_OF_STOCK), "application/xml").await;
    assert!(
        xml.body.contains("<reason>OUT_OF_STOCK</reason>"),
        "{}",
        xml.body
    );
    assert!(xml.body.contains("<quantity_on_hand>0</quantity_on_hand>"));
    let xml = get(&h.app, &token, &pair(NOT_SELLABLE), "application/xml").await;
    assert!(
        xml.body.contains("<reason>NOT_SELLABLE</reason>"),
        "{}",
        xml.body
    );

    // A store or product that does not exist is a 404; bad filters are 422.
    for path in [
        "/v1/availabilities/32767/1".to_owned(),
        format!("/v1/availabilities/{STORE}/2147483647"),
    ] {
        let reply = get(&h.app, &token, &path, "application/json").await;
        assert_eq!(reply.status, 404, "{path}: {}", reply.body);
        assert_eq!(reply.json()["code"], "availability_not_found");
    }
    for query in ["available=maybe", "channel_id=0", "store_id=x"] {
        let reply = get(
            &h.app,
            &token,
            &format!("/v1/availabilities?{query}"),
            "application/json",
        )
        .await;
        assert_eq!(reply.status, 422, "{query}: {}", reply.body);
        assert_eq!(reply.json()["code"], "invalid_query");
    }

    // A role outside the contract's list is refused.
    let customer = h.keys.token("CUSTOMER", &h.sid);
    let reply = get(&h.app, &customer, &pair(IN_STOCK), "application/json").await;
    assert_eq!(reply.status, 403, "{}", reply.body);

    set_active(&h.owner, NOT_SELLABLE, true).await;
    set_on_hand(&h.owner, STORE, OUT_OF_STOCK, original_out).await;
}

/// Criterion 2: a channel the store does not enable is unavailable, however
/// much is on hand; so is every channel of a store that is not OPEN.
#[tokio::test]
async fn a_channel_the_store_does_not_enable_is_unavailable_whatever_the_quantity() {
    let _guard = test_lock().await;
    let Some(h) = harness().await else {
        return;
    };
    let token = h.keys.token("SALES_ASSOCIATE", &h.sid);
    let original = on_hand(&h.owner, STORE, IN_STOCK).await;
    set_on_hand(&h.owner, STORE, IN_STOCK, 100_000).await;

    let reply = get(
        &h.app,
        &token,
        &format!("/v1/availabilities/{STORE}/{IN_STOCK}"),
        "application/json",
    )
    .await;
    assert_eq!(reply.status, 200, "{}", reply.body);
    let page = reply.json();
    for channel in [MOBILE, 4, 5] {
        let row = channel_row(&page, channel);
        assert_eq!(row["is_available"], false, "{row}");
        assert_eq!(row["reason"], "CHANNEL_NOT_ENABLED");
        assert_eq!(row["quantity_on_hand"], 100_000);
    }
    for channel in [WEB, PHYSICAL] {
        assert_eq!(channel_row(&page, channel)["reason"], "AVAILABLE");
    }

    // The list filtered on a disabled channel says the same.
    let reply = get(
        &h.app,
        &token,
        &format!("/v1/availabilities?store_id={STORE}&product_id={IN_STOCK}&channel_id={MOBILE}"),
        "application/xml",
    )
    .await;
    assert_eq!(reply.status, 200, "{}", reply.body);
    assert!(reply.body.contains("<reason>CHANNEL_NOT_ENABLED</reason>"));
    assert!(reply.body.contains("<is_available>false</is_available>"));

    // A CLOSED store enables physical_store, yet sells on no channel.
    let reply = get(
        &h.app,
        &token,
        &format!("/v1/availabilities/{CLOSED_STORE}/{IN_STOCK}"),
        "application/json",
    )
    .await;
    assert_eq!(reply.status, 200, "{}", reply.body);
    let page = reply.json();
    for row in page["availabilities"].as_array().unwrap() {
        assert_eq!(row["reason"], "CHANNEL_NOT_ENABLED", "{row}");
    }
    assert!(
        channel_row(&page, PHYSICAL)["quantity_on_hand"]
            .as_i64()
            .unwrap()
            > 0
    );

    set_on_hand(&h.owner, STORE, IN_STOCK, original).await;
}

type Triple = (
    i64,
    i64,
    i64,
    bool,
    String,
    Option<i64>,
    Option<DateTime<Utc>>,
);

fn view_row(row: &Value) -> Triple {
    (
        row["store_id"].as_i64().unwrap(),
        row["product_id"].as_i64().unwrap(),
        row["channel_id"].as_i64().unwrap(),
        row["is_available"].as_bool().unwrap(),
        row["reason"].as_str().unwrap().to_owned(),
        row["quantity_on_hand"].as_i64(),
        row["updated_at"].as_str().map(|text| {
            DateTime::parse_from_rfc3339(text)
                .unwrap()
                .with_timezone(&Utc)
        }),
    )
}

/// Criterion 3: recommendations, reading the published view with its own
/// grant, gets exactly what the endpoint answers, row for row, over the seed.
#[tokio::test]
async fn recommendations_reads_the_same_answer_as_the_endpoint() {
    let _guard = test_lock().await;
    let Some(h) = harness().await else {
        return;
    };
    let recommendations = connect(&h.database).await;
    recommendations
        .batch_execute("SET ROLE svc_recommendations")
        .await
        .unwrap();
    let viewed: Vec<Triple> = recommendations
        .query(
            "SELECT store_id, product_id, channel_id, is_available, reason, \
                    quantity_on_hand, updated_at \
             FROM api_stock.availability_v1 \
             ORDER BY store_id, product_id, channel_id",
            &[],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| {
            (
                i64::from(row.get::<_, i16>(0)),
                i64::from(row.get::<_, i32>(1)),
                i64::from(row.get::<_, i16>(2)),
                row.get(3),
                row.get(4),
                row.get::<_, Option<i32>>(5).map(i64::from),
                row.get(6),
            )
        })
        .collect();
    // The seed reaches every reason the seed can: in stock, a disabled
    // channel and a pair with no record.
    for reason in ["AVAILABLE", "CHANNEL_NOT_ENABLED", "NO_STOCK_RECORD"] {
        assert!(viewed.iter().any(|row| row.4 == reason), "{reason}");
    }

    let token = h.keys.token("ANALYST", &h.sid);
    let mut served: Vec<Triple> = Vec::new();
    let mut page = 1;
    loop {
        let reply = get(
            &h.app,
            &token,
            &format!("/v1/availabilities?size=100&page={page}"),
            "application/json",
        )
        .await;
        assert_eq!(reply.status, 200, "{}", reply.body);
        let body = reply.json();
        assert_eq!(body["page_info"]["total"], viewed.len() as u64);
        let rows = body["availabilities"].as_array().unwrap();
        if rows.is_empty() {
            break;
        }
        served.extend(rows.iter().map(view_row));
        page += 1;
    }
    assert_eq!(served.len(), viewed.len());
    for (endpoint, view) in served.iter().zip(&viewed) {
        assert_eq!(endpoint, view);
    }
}
