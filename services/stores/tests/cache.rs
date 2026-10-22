//! The directory cache, the relay and the `stores` consumer group against a
//! real Redis (TEST_REDIS_URL), with the committed ACL line loaded. That ACL
//! denies SCAN and KEYS, so passing under it shows invalidation uses neither.

mod support;

use std::collections::HashMap;
use std::time::{Duration, Instant};

use mosaiq_kit::{service_redis_info, OutboxRelay, StreamConsumer};
use redis::aio::MultiplexedConnection;
use stores::{cached_directory, Cache, DIRECTORY_CACHE_KEY, STREAM, SUFFIX};
use support::{connect, owner_url, service_database, TestStore};

static REDIS_USER: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

fn redis_url() -> Option<String> {
    match std::env::var("TEST_REDIS_URL") {
        Ok(url) if !url.is_empty() => Some(url),
        _ => {
            if std::env::var("MOSAIQ_REQUIRE_REDIS").as_deref() == Ok("1") {
                panic!("TEST_REDIS_URL is missing, and MOSAIQ_REQUIRE_REDIS forbids skipping");
            }
            eprintln!("skipped: TEST_REDIS_URL is not set");
            None
        }
    }
}

async fn reconnect(url: String, password: String) -> redis::RedisResult<MultiplexedConnection> {
    let info = service_redis_info(&url, SUFFIX, Some(password))?;
    redis::Client::open(info)?
        .get_multiplexed_async_connection()
        .await
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

async fn group_exists(admin: &mut MultiplexedConnection, group: &str) -> bool {
    let groups: redis::RedisResult<Vec<HashMap<String, redis::Value>>> = redis::cmd("XINFO")
        .arg("GROUPS")
        .arg(STREAM)
        .query_async(admin)
        .await;
    groups.is_ok_and(|groups| {
        groups.iter().any(|info| {
            info.get("name")
                .and_then(|name| redis::from_redis_value::<String>(name).ok())
                .as_deref()
                == Some(group)
        })
    })
}

async fn service_connection(url: &str, password: &str) -> MultiplexedConnection {
    reconnect(url.to_owned(), password.to_owned())
        .await
        .unwrap()
}

async fn load_acl(admin: &mut MultiplexedConnection) -> String {
    let password = uuid::Uuid::new_v4().simple().to_string();
    let line = std::fs::read_to_string("redis/stores.acl").unwrap();
    let tokens = acl_tokens(line.trim());
    assert_eq!(&tokens[..3], ["user", "svc_stores", "on"]);
    let _: () = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_stores")
        .query_async(admin)
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
    let _: () = setuser.query_async(admin).await.unwrap();
    password
}

#[tokio::test]
async fn a_web_rename_is_served_after_the_relay_and_the_consumer_invalidate() {
    let (Some(db_url), Some(redis_url)) = (owner_url(), redis_url()) else {
        return;
    };
    let _turn = REDIS_USER.lock().await;
    let owner = connect(&db_url).await;
    let db = service_database(&db_url);
    let admin = redis::Client::open(redis_url.as_str()).unwrap();
    let mut admin = admin.get_multiplexed_async_connection().await.unwrap();

    // The user line as committed, under a throwaway password.
    let password = load_acl(&mut admin).await;
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

#[tokio::test]
async fn relay_and_consumer_reconnect_after_redis_kills_their_connections() {
    let (Some(db_url), Some(redis_url)) = (owner_url(), redis_url()) else {
        return;
    };
    let _turn = REDIS_USER.lock().await;
    let owner = connect(&db_url).await;
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
        &support::service_url(&db_url),
        SUFFIX,
        [STREAM],
        service_connection(&redis_url, &password).await,
    )
    .unwrap();
    while relay.run_once(Duration::ZERO).await.unwrap() > 0 {}
    drop(relay);
    let _: i64 = redis::cmd("DEL")
        .arg(STREAM)
        .query_async(&mut admin)
        .await
        .unwrap();

    // The service's own relay and consumer, as `spawn_stream_tasks` starts
    // them; the handler reports each event instead of dropping the cache.
    std::env::set_var("MOSAIQ_SERVICE_NAME", "stores-reconnect-test");
    let (sender, mut receiver) = tokio::sync::mpsc::unbounded_channel();
    let handler = move |event: HashMap<String, String>| {
        let sender = sender.clone();
        async move { sender.send(event).map_err(|_| "test receiver closed") }
    };
    let relay_task = tokio::spawn(stores::run_relay(
        support::service_url(&db_url),
        redis_url.clone(),
        Some(password.clone()),
    ));
    let consumer_task = tokio::spawn(stores::run_consumer(
        redis_url.clone(),
        Some(password.clone()),
        handler,
    ));
    // Both connected, and the group exists before anything is published.
    let connected = Instant::now() + Duration::from_secs(10);
    while clients_of(&mut admin, "svc_stores").await < 2
        || !group_exists(&mut admin, "stores-reconnect-test").await
    {
        assert!(
            Instant::now() < connected,
            "the relay and consumer never connected"
        );
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    let killed: i64 = redis::cmd("CLIENT")
        .arg("KILL")
        .arg("USER")
        .arg("svc_stores")
        .query_async(&mut admin)
        .await
        .unwrap();
    assert!(
        killed >= 2,
        "Redis did not kill both service stream connections"
    );

    let store = TestStore::create(&owner).await;
    let entity_id = store.id.to_string();
    let deadline = Instant::now() + Duration::from_secs(20);
    let mut consumed = false;
    let mut delivered = false;
    while !consumed || !delivered {
        while let Ok(fields) = receiver.try_recv() {
            consumed |= fields.get("entity_id") == Some(&entity_id)
                && fields.get("event_type").map(String::as_str) == Some("INSERT");
        }
        let entries: Vec<(String, Vec<(String, String)>)> = redis::cmd("XREVRANGE")
            .arg(STREAM)
            .arg("+")
            .arg("-")
            .arg("COUNT")
            .arg(100)
            .query_async(&mut admin)
            .await
            .unwrap();
        delivered = entries.iter().any(|(_, fields)| {
            fields.contains(&("entity_id".to_owned(), entity_id.clone()))
                && fields.contains(&("event_type".to_owned(), "INSERT".to_owned()))
        });
        assert!(
            Instant::now() < deadline,
            "the event was not relayed and consumed after reconnecting"
        );
        if !consumed || !delivered {
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    }
    loop {
        let unrelayed: i64 = owner
            .query_one(
                "SELECT count(*) FROM stores_events.outbox \
                 WHERE entity_id = $1 AND relayed_at IS NULL",
                &[&entity_id],
            )
            .await
            .unwrap()
            .get(0);
        if unrelayed == 0 {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "the delivered outbox row stayed unrelayed"
        );
        tokio::time::sleep(Duration::from_millis(50)).await;
    }

    store.cleanup(&owner).await;
    let cleanup_deadline = Instant::now() + Duration::from_secs(10);
    while owner
        .query_one(
            "SELECT count(*) FROM stores_events.outbox \
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
    relay_task.abort();
    consumer_task.abort();
    let _ = relay_task.await;
    let _ = consumer_task.await;
    let _: i64 = redis::cmd("DEL")
        .arg(STREAM)
        .query_async(&mut admin)
        .await
        .unwrap();
    let _: () = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_stores")
        .query_async(&mut admin)
        .await
        .unwrap();
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
