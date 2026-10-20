//! The directory cache, the relay and the `stores` consumer group against a
//! real Redis (TEST_REDIS_URL), with the committed ACL line loaded. That ACL
//! denies SCAN and KEYS, so passing under it shows invalidation uses neither.

mod support;

use std::time::Duration;

use mosaiq_kit::{service_redis_info, OutboxRelay, StreamConsumer};
use redis::aio::MultiplexedConnection;
use stores::{cached_directory, Cache, DIRECTORY_CACHE_KEY, STREAM, SUFFIX};
use support::{connect, owner_url, service_database, TestStore};

fn redis_url() -> Option<String> {
    match std::env::var("TEST_REDIS_URL") {
        Ok(url) if !url.is_empty() => Some(url),
        _ => {
            eprintln!("skipped: TEST_REDIS_URL is not set");
            None
        }
    }
}

async fn service_connection(url: &str, password: &str) -> MultiplexedConnection {
    let info = service_redis_info(url, SUFFIX, Some(password.to_owned())).unwrap();
    redis::Client::open(info)
        .unwrap()
        .get_multiplexed_async_connection()
        .await
        .unwrap()
}

#[tokio::test]
async fn a_web_rename_is_served_after_the_relay_and_the_consumer_invalidate() {
    let (Some(db_url), Some(redis_url)) = (owner_url(), redis_url()) else {
        return;
    };
    let owner = connect(&db_url).await;
    let db = service_database(&db_url);
    let admin = redis::Client::open(redis_url.as_str()).unwrap();
    let mut admin = admin.get_multiplexed_async_connection().await.unwrap();

    // The user line as committed, under a throwaway password.
    let password = uuid::Uuid::new_v4().simple().to_string();
    let line = std::fs::read_to_string("redis/stores.acl").unwrap();
    let tokens = acl_tokens(&line.trim().replace("{{password}}", &format!(">{password}")));
    let _: () = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_stores")
        .query_async(&mut admin)
        .await
        .unwrap();
    let mut setuser = redis::cmd("ACL");
    setuser.arg("SETUSER").arg("svc_stores").arg("on");
    for token in &tokens[3..] {
        setuser.arg(token);
    }
    let _: () = setuser.query_async(&mut admin).await.unwrap();
    for denied in [
        vec!["XADD", "stream:users.v1.access_changed", "*", "a", "b"],
        vec!["GET", "auth:session:x"],
        vec!["SCAN", "0"],
        vec!["KEYS", "*"],
    ] {
        let mut dry = redis::cmd("ACL");
        dry.arg("DRYRUN").arg("svc_stores");
        for part in &denied {
            dry.arg(*part);
        }
        let verdict: String = dry.query_async(&mut admin).await.unwrap();
        assert_ne!(verdict, "OK", "{denied:?}");
    }
    let _: redis::RedisResult<()> = redis::cmd("CONFIG")
        .arg("SET")
        .arg("appendonly")
        .arg("yes")
        .query_async(&mut admin)
        .await;

    std::env::set_var("MOSAIQ_SERVICE_NAME", "stores");
    let cache = Cache::new(
        redis::Client::open(
            service_redis_info(&redis_url, SUFFIX, Some(password.clone())).unwrap(),
        )
        .unwrap(),
    );
    let relay_redis = service_connection(&redis_url, &password).await;
    let mut relay = OutboxRelay::new(
        &support::service_url(&db_url),
        SUFFIX,
        [STREAM],
        relay_redis,
    )
    .unwrap();
    let mut consumer = {
        let cache = cache.clone();
        StreamConsumer::new(
            service_connection(&redis_url, &password).await,
            STREAM,
            move |_event| {
                let cache = cache.clone();
                async move { cache.invalidate().await }
            },
        )
        .unwrap()
    };
    consumer.run_once(Some(100)).await.unwrap(); // creates the group first
    relay.run_once(Duration::from_millis(0)).await.unwrap(); // drain older rows

    let store = TestStore::create(&owner).await;
    relay.run_once(Duration::from_millis(0)).await.unwrap();
    consumer.run_once(Some(100)).await.unwrap();

    let name_of = |stores: &[stores::StoreRecord]| {
        stores
            .iter()
            .find(|s| s.store_id == store.id)
            .unwrap()
            .name
            .clone()
    };
    let before = cached_directory(&db, Some(&cache)).await.unwrap();
    let original = name_of(&before);
    let cached: Option<String> = redis::cmd("GET")
        .arg(DIRECTORY_CACHE_KEY)
        .query_async(&mut admin)
        .await
        .unwrap();
    assert!(cached.is_some(), "the directory read is cached");
    let ttl: i64 = redis::cmd("TTL")
        .arg(DIRECTORY_CACHE_KEY)
        .query_async(&mut admin)
        .await
        .unwrap();
    assert!(ttl > 800 && ttl <= 900, "ttl {ttl}");

    // The web renames the store; the cache still answers with the old name.
    owner
        .batch_execute(&format!(
            "BEGIN; SET LOCAL ROLE retail_app; \
             UPDATE public.store SET name = 'svc-test-renamed' WHERE store_id = {}; COMMIT;",
            store.id
        ))
        .await
        .unwrap();
    assert_eq!(
        name_of(&cached_directory(&db, Some(&cache)).await.unwrap()),
        original
    );

    // Relay to the stream, then the consumer group drops the cached copy.
    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    let mut fresh = original.clone();
    while fresh == original && std::time::Instant::now() < deadline {
        relay.run_once(Duration::from_millis(0)).await.unwrap();
        consumer.run_once(Some(200)).await.unwrap();
        fresh = name_of(&cached_directory(&db, Some(&cache)).await.unwrap());
    }
    assert_eq!(fresh, "svc-test-renamed");

    // With redis-cache unreachable, reads still answer from PostgreSQL.
    let dead = Cache::new(redis::Client::open("redis://127.0.0.1:1/0").unwrap());
    assert!(!cached_directory(&db, Some(&dead)).await.unwrap().is_empty());

    let _: redis::RedisResult<()> = redis::cmd("CONFIG")
        .arg("SET")
        .arg("appendonly")
        .arg("no")
        .query_async(&mut admin)
        .await;
    let _: redis::RedisResult<()> = redis::cmd("DEL")
        .arg(STREAM)
        .arg(DIRECTORY_CACHE_KEY)
        .query_async(&mut admin)
        .await;
    let _: () = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_stores")
        .query_async(&mut admin)
        .await
        .unwrap();
    store.cleanup(&owner).await;
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

#[test]
fn the_acl_has_the_documented_selectors_and_no_scan_or_keys() {
    let line = std::fs::read_to_string("redis/stores.acl").unwrap();
    assert_eq!(line.trim().lines().count(), 1);
    for part in [
        "~stores:rl:*",
        "~stores:cache:*",
        "+waitaof",
        "(%R~pub:auth:v1:live:* +exists)",
        "(~stream:stores.v1.changed +xadd)",
        "-scan",
        "-keys",
    ] {
        assert!(line.contains(part), "{part}");
    }
    assert!(line.contains("+xreadgroup +xack +xgroup|create +xpending +xclaim"));
}
