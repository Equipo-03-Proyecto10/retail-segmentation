//! The stock outbox relay reconnects after Redis drops its ACL user's live
//! connection. Both PostgreSQL and Redis are real test servers.

use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use mosaiq_kit::{service_redis_info, OutboxRelay};
use redis::aio::MultiplexedConnection;
use stock::{STREAM, SUFFIX};
use tokio_postgres::{Client, NoTls};

const ACL: &str = "redis/stock.acl";

fn required_url(name: &str, requirement: &str) -> Option<String> {
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

async fn postgres(url: &str) -> Client {
    let (client, connection) = tokio_postgres::connect(url, NoTls).await.unwrap();
    tokio::spawn(connection);
    client
}

async fn service_connection(
    url: String,
    password: String,
) -> redis::RedisResult<MultiplexedConnection> {
    let info = service_redis_info(&url, SUFFIX, Some(password))?;
    redis::Client::open(info)?
        .get_multiplexed_async_connection()
        .await
}

async fn load_acl(admin: &mut MultiplexedConnection) -> String {
    let password = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos()
        .to_string();
    let line = std::fs::read_to_string(ACL).unwrap();
    let tokens: Vec<_> = line.split_whitespace().collect();
    assert_eq!(&tokens[..3], ["user", "svc_stock", "on"]);
    let _: () = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_stock")
        .query_async(admin)
        .await
        .unwrap();
    let mut setuser = redis::cmd("ACL");
    setuser.arg("SETUSER").arg("svc_stock").arg("on");
    for token in &tokens[3..] {
        if *token == "{{password}}" {
            setuser.arg(format!(">{password}"));
        } else {
            setuser.arg(token);
        }
    }
    let _: () = setuser.query_async(admin).await.unwrap();
    password
}

/// Connections Redis holds for one ACL user.
async fn clients_of(admin: &mut MultiplexedConnection, user: &str) -> usize {
    let list: String = redis::cmd("CLIENT")
        .arg("LIST")
        .query_async(admin)
        .await
        .unwrap();
    let wanted = format!(" user={user} ");
    list.lines().filter(|line| line.contains(&wanted)).count()
}

async fn stream_contains(
    admin: &mut MultiplexedConnection,
    event_id: &str,
) -> redis::RedisResult<bool> {
    let entries: Vec<(String, Vec<(String, String)>)> = redis::cmd("XREVRANGE")
        .arg(STREAM)
        .arg("+")
        .arg("-")
        .arg("COUNT")
        .arg(100)
        .query_async(admin)
        .await?;
    Ok(entries
        .iter()
        .any(|(_, fields)| fields.contains(&("event_id".to_owned(), event_id.to_owned()))))
}

#[tokio::test]
async fn relay_reconnects_after_redis_kills_the_service_connection() {
    let (Some(database_url), Some(redis_url)) = (
        required_url("TEST_DATABASE_URL", "MOSAIQ_REQUIRE_POSTGRES"),
        required_url("TEST_REDIS_URL", "MOSAIQ_REQUIRE_REDIS"),
    ) else {
        return;
    };
    let owner = postgres(&database_url).await;
    let mut admin = redis::Client::open(redis_url.as_str())
        .unwrap()
        .get_multiplexed_async_connection()
        .await
        .unwrap();
    let password = load_acl(&mut admin).await;
    let _: () = redis::cmd("CONFIG")
        .arg("SET")
        .arg("appendonly")
        .arg("yes")
        .query_async(&mut admin)
        .await
        .unwrap();

    let mut relay = OutboxRelay::new(
        &service_url(&database_url),
        SUFFIX,
        [STREAM],
        service_connection(redis_url.clone(), password.clone())
            .await
            .unwrap(),
    )
    .unwrap();
    while relay.run_once(Duration::ZERO).await.unwrap() > 0 {}
    drop(relay);
    let _: i64 = redis::cmd("DEL")
        .arg(STREAM)
        .query_async(&mut admin)
        .await
        .unwrap();

    // The service's own relay, as `serve` starts it.
    let task = tokio::spawn(stock::run_relay(
        service_url(&database_url),
        redis_url.clone(),
        Some(password.clone()),
    ));
    let connected = Instant::now() + Duration::from_secs(10);
    while clients_of(&mut admin, "svc_stock").await == 0 {
        assert!(Instant::now() < connected, "the relay never connected");
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    let killed: i64 = redis::cmd("CLIENT")
        .arg("KILL")
        .arg("USER")
        .arg("svc_stock")
        .query_async(&mut admin)
        .await
        .unwrap();
    assert!(killed >= 1, "Redis did not kill the relay connection");

    let row = owner
        .query_one(
            "SELECT store_id, product_id, quantity_on_hand FROM public.inventory \
             ORDER BY store_id, product_id LIMIT $1",
            &[&1i64],
        )
        .await
        .unwrap();
    let store_id: i16 = row.get(0);
    let product_id: i32 = row.get(1);
    let original: i32 = row.get(2);
    let entity_id = format!("{store_id}:{product_id}");
    owner
        .execute(
            "UPDATE public.inventory SET quantity_on_hand = $1, updated_at = now() \
             WHERE store_id = $2 AND product_id = $3",
            &[&(original + 1), &store_id, &product_id],
        )
        .await
        .unwrap();
    let event_id: String = owner
        .query_one(
            "SELECT event_id::text FROM stock_events.outbox \
             WHERE entity_id = $1 ORDER BY occurred_at DESC LIMIT $2",
            &[&entity_id, &1i64],
        )
        .await
        .unwrap()
        .get(0);

    let deadline = Instant::now() + Duration::from_secs(20);
    while !stream_contains(&mut admin, &event_id).await.unwrap() {
        assert!(
            Instant::now() < deadline,
            "the event was not relayed after reconnecting"
        );
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    loop {
        let relayed: bool = owner
            .query_one(
                "SELECT relayed_at IS NOT NULL FROM stock_events.outbox WHERE event_id::text = $1",
                &[&event_id],
            )
            .await
            .unwrap()
            .get(0);
        if relayed {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "the delivered outbox row was not marked relayed"
        );
        tokio::time::sleep(Duration::from_millis(50)).await;
    }

    owner
        .execute(
            "UPDATE public.inventory SET quantity_on_hand = $1, updated_at = now() \
             WHERE store_id = $2 AND product_id = $3",
            &[&original, &store_id, &product_id],
        )
        .await
        .unwrap();
    let cleanup_deadline = Instant::now() + Duration::from_secs(10);
    while owner
        .query_one(
            "SELECT count(*) FROM stock_events.outbox \
             WHERE entity_id = $1 AND relayed_at IS NULL",
            &[&entity_id],
        )
        .await
        .unwrap()
        .get::<_, i64>(0)
        > 0
    {
        assert!(
            Instant::now() < cleanup_deadline,
            "the cleanup event was not relayed"
        );
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    task.abort();
    let _ = task.await;
    let _: i64 = redis::cmd("DEL")
        .arg(STREAM)
        .query_async(&mut admin)
        .await
        .unwrap();
    let _: () = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_stock")
        .query_async(&mut admin)
        .await
        .unwrap();
}
