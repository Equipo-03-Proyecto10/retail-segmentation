//! The read cache and the `segments` consumer group against a real Redis
//! (TEST_REDIS_URL), with the committed ACL line loaded. That ACL denies SCAN
//! and KEYS, so passing under it shows reads and the run switch use neither.

mod support;

use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use mosaiq_kit::{OutboxRelay, StreamConsumer};
use redis::aio::MultiplexedConnection;
use segments::{
    current_key, list_key, read_current, read_list, Cache, Reader, SegmentList, STREAM, SUFFIX,
};
use support::{
    admin, connect, drop_acl, expected_sizes, load_acl, owner_url, redis_url, service_cache,
    service_database, service_redis, RecordedRun,
};
use uuid::Uuid;

/// Every Redis test loads and deletes the same ACL user and stream.
static REDIS_USER: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

async fn dry_run(admin: &mut MultiplexedConnection, command: &[&str]) -> String {
    let mut dry = redis::cmd("ACL");
    dry.arg("DRYRUN").arg("svc_segments");
    for part in command {
        dry.arg(*part);
    }
    dry.query_async(admin).await.unwrap()
}

/// Cache keys, found as the operator would: the admin may SCAN, the service may not.
async fn cache_keys(admin: &mut MultiplexedConnection) -> Vec<String> {
    let mut keys = Vec::new();
    let mut cursor = 0_u64;
    loop {
        let (next, found): (u64, Vec<String>) = redis::cmd("SCAN")
            .arg(cursor)
            .arg("MATCH")
            .arg("segments:cache:*")
            .arg("COUNT")
            .arg(1000)
            .query_async(admin)
            .await
            .unwrap();
        keys.extend(found);
        if next == 0 {
            return keys;
        }
        cursor = next;
    }
}

async fn clear_cache(admin: &mut MultiplexedConnection) {
    for key in cache_keys(admin).await {
        let _: i64 = redis::cmd("DEL").arg(key).query_async(admin).await.unwrap();
    }
}

async fn ttl(admin: &mut MultiplexedConnection, key: &str) -> i64 {
    redis::cmd("TTL").arg(key).query_async(admin).await.unwrap()
}

async fn exists(admin: &mut MultiplexedConnection, key: &str) -> bool {
    redis::cmd("EXISTS")
        .arg(key)
        .query_async::<i64>(admin)
        .await
        .unwrap()
        == 1
}

async fn plant(admin: &mut MultiplexedConnection, key: &str, list: &SegmentList) {
    let _: () = redis::cmd("SET")
        .arg(key)
        .arg(serde_json::to_string(list).unwrap())
        .arg("EX")
        .arg(900)
        .query_async(admin)
        .await
        .unwrap();
}

#[tokio::test]
async fn a_run_the_web_records_is_served_within_ten_seconds_and_the_old_keys_are_left() {
    let _turn = REDIS_USER.lock().await;
    let (db_url, redis_url) = (owner_url(), redis_url());
    let mut owner = connect(&db_url).await;
    let db = service_database(&db_url);
    let mut admin = admin(&redis_url).await;
    let password = load_acl(&mut admin).await;
    clear_cache(&mut admin).await;
    let _: i64 = redis::cmd("DEL")
        .arg(STREAM)
        .query_async(&mut admin)
        .await
        .unwrap();

    for denied in [
        vec!["XADD", "stream:sales.v1.recorded", "*", "a", "b"],
        vec![
            "XREADGROUP",
            "GROUP",
            "g",
            "c",
            "STREAMS",
            "stream:products.v1.changed",
            ">",
        ],
        vec!["GET", "auth:session:x"],
        vec!["SCAN", "0"],
        vec!["KEYS", "*"],
    ] {
        assert_ne!(dry_run(&mut admin, &denied).await, "OK", "{denied:?}");
    }
    for allowed in [
        vec!["SET", "segments:cache:1:list", "x", "EX", "900"],
        vec!["XADD", STREAM, "*", "a", "b"],
        vec![
            "XREADGROUP",
            "GROUP",
            "segments",
            "c",
            "STREAMS",
            STREAM,
            ">",
        ],
    ] {
        assert_eq!(dry_run(&mut admin, &allowed).await, "OK", "{allowed:?}");
    }

    std::env::set_var("MOSAIQ_SERVICE_NAME", "segments");
    let reader = Reader::new(Some(service_cache(&redis_url, &password)));
    let mut relay = OutboxRelay::new(
        &support::service_url(&db_url),
        SUFFIX,
        [STREAM],
        service_redis(&redis_url, &password).await,
    )
    .unwrap();
    let mut consumer = {
        let (reader, db_url) = (reader.clone(), db_url.clone());
        StreamConsumer::new(
            service_redis(&redis_url, &password).await,
            STREAM,
            move |_event: HashMap<String, String>| {
                let (reader, db) = (reader.clone(), service_database(&db_url));
                async move { reader.refresh_run(&db).await.map(|_| ()) }
            },
        )
        .unwrap()
    };
    consumer.run_once(Some(100)).await.unwrap(); // creates the group first
    while relay.run_once(Duration::ZERO).await.unwrap() > 0 {}
    while consumer.run_once(Some(100)).await.unwrap() > 0 {}

    // The first read asks PostgreSQL for the run and caches under it.
    let before = reader.list(&db).await.unwrap();
    let old_run = before.run_id.expect("the seed has runs");
    assert_eq!(reader.tracked_run(), Some(old_run));
    let old_list_key = list_key(old_run);
    let seen = ttl(&mut admin, &old_list_key).await;
    assert!(seen > 800 && seen <= 900, "ttl {seen}");
    let customer: Uuid = owner
        .query_one(
            "SELECT customer_id FROM api_segments.current_label_v1 \
             WHERE state = 'assigned' ORDER BY customer_id LIMIT 1",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    let current_before = reader.current(&db, customer).await.unwrap();
    assert_eq!(current_before, read_current(&db, customer).await.unwrap());
    let old_current_key = current_key(old_run, &customer.to_string());
    let seen = ttl(&mut admin, &old_current_key).await;
    assert!(seen > 800 && seen <= 900, "ttl {seen}");

    // A cached copy is what a read returns: plant a marker under the old key.
    let mut marker = before.clone();
    marker.unassigned_count = 9_999;
    plant(&mut admin, &old_list_key, &marker).await;
    assert_eq!(reader.list(&db).await.unwrap().unassigned_count, 9_999);

    // The web records a run. Until the event arrives the cache still answers.
    let run = RecordedRun::record(&mut owner).await;
    assert!(run.run_id > old_run);
    assert_eq!(reader.list(&db).await.unwrap().unassigned_count, 9_999);

    // Relay, then consumer: the tracker moves, within ten seconds.
    let started = Instant::now();
    while reader.tracked_run() != Some(run.run_id) {
        assert!(
            started.elapsed() < Duration::from_secs(10),
            "the new run was not picked up within 10 s"
        );
        relay.run_once(Duration::ZERO).await.unwrap();
        consumer.run_once(Some(200)).await.unwrap();
    }
    let after = reader.list(&db).await.unwrap();
    assert_eq!(after.run_id, Some(run.run_id));
    let expected: HashMap<String, i64> = expected_sizes(&owner).await.into_iter().collect();
    let served: HashMap<String, i64> = after
        .segments
        .iter()
        .filter(|segment| segment.size > 0)
        .map(|segment| (segment.label_code.clone(), segment.size))
        .collect();
    assert_eq!(served, expected);
    assert!(after.unassigned_count > 0 && after.unassigned_count != 9_999);
    assert_ne!(after, before);
    assert!(exists(&mut admin, &list_key(run.run_id)).await);

    // The customer's current moves too, and is read from the new run's keys.
    let current_after = reader.current(&db, customer).await.unwrap();
    assert_eq!(current_after, read_current(&db, customer).await.unwrap());
    assert_eq!(current_after.run_id, Some(run.run_id));
    assert_ne!(current_after.run_id, current_before.run_id);
    assert!(exists(&mut admin, &current_key(run.run_id, &customer.to_string())).await);

    // The old run's keys are not read again: change them and read once more.
    marker.unassigned_count = 9_998;
    plant(&mut admin, &old_list_key, &marker).await;
    let _: () = redis::cmd("SET")
        .arg(&old_current_key)
        .arg("{\"not\":\"a record\"}")
        .arg("EX")
        .arg(900)
        .query_async(&mut admin)
        .await
        .unwrap();
    assert_eq!(reader.list(&db).await.unwrap(), after);
    assert_eq!(reader.current(&db, customer).await.unwrap(), current_after);

    // Every key the service wrote expires.
    let keys = cache_keys(&mut admin).await;
    assert!(keys.len() >= 4, "{keys:?}");
    for key in &keys {
        let seconds = ttl(&mut admin, key).await;
        assert!(seconds > 0 && seconds <= 900, "{key} ttl {seconds}");
    }

    clear_cache(&mut admin).await;
    let _: i64 = redis::cmd("DEL")
        .arg(STREAM)
        .query_async(&mut admin)
        .await
        .unwrap();
    drop_acl(&mut admin).await;
}

#[tokio::test]
async fn a_repeated_event_moves_reads_once() {
    let _turn = REDIS_USER.lock().await;
    let (db_url, redis_url) = (owner_url(), redis_url());
    let mut admin = admin(&redis_url).await;
    let password = load_acl(&mut admin).await;
    let _: i64 = redis::cmd("DEL")
        .arg(STREAM)
        .query_async(&mut admin)
        .await
        .unwrap();
    std::env::set_var("MOSAIQ_SERVICE_NAME", "segments");

    let seen = Arc::new(AtomicUsize::new(0));
    let reader = Reader::new(None);
    let mut consumer = {
        let (seen, reader, db_url) = (Arc::clone(&seen), reader.clone(), db_url.clone());
        StreamConsumer::new(
            service_redis(&redis_url, &password).await,
            STREAM,
            move |_event: HashMap<String, String>| {
                let (seen, reader, db) =
                    (Arc::clone(&seen), reader.clone(), service_database(&db_url));
                async move {
                    seen.fetch_add(1, Ordering::SeqCst);
                    reader.refresh_run(&db).await.map(|_| ())
                }
            },
        )
        .unwrap()
    };
    consumer.run_once(Some(100)).await.unwrap(); // creates the group
    let event_id = Uuid::new_v4().to_string();
    for _ in 0..2 {
        let _: String = redis::cmd("XADD")
            .arg(STREAM)
            .arg("*")
            .arg("event_id")
            .arg(&event_id)
            .arg("v")
            .arg("1")
            .arg("event_type")
            .arg("INSERT")
            .arg("entity_id")
            .arg("1")
            .query_async(&mut admin)
            .await
            .unwrap();
    }
    while consumer.run_once(Some(100)).await.unwrap() > 0 {}
    assert_eq!(seen.load(Ordering::SeqCst), 1, "handled once per event_id");
    assert!(reader.tracked_run().is_some());

    let _: i64 = redis::cmd("DEL")
        .arg(STREAM)
        .query_async(&mut admin)
        .await
        .unwrap();
    drop_acl(&mut admin).await;
}

#[tokio::test]
async fn reads_answer_the_same_from_postgres_when_redis_cache_is_out() {
    let _turn = REDIS_USER.lock().await;
    let (db_url, redis_url) = (owner_url(), redis_url());
    let owner = connect(&db_url).await;
    let db = service_database(&db_url);
    let mut admin = admin(&redis_url).await;
    let password = load_acl(&mut admin).await;

    let customer: Uuid = owner
        .query_one(
            "SELECT customer_id FROM api_segments.current_label_v1 \
             WHERE state = 'assigned' ORDER BY customer_id LIMIT 1",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    let plain = Reader::new(None);
    let list = plain.list(&db).await.unwrap();
    let current = plain.current(&db, customer).await.unwrap();
    assert_eq!(list, read_list(&db).await.unwrap());

    let refused = redis::Client::open("redis://127.0.0.1:1/0").unwrap();
    let wrong_password = service_cache(&redis_url, "not-the-password");
    for cache in [Cache::new(refused), wrong_password] {
        let reader = Reader::new(Some(cache));
        let started = Instant::now();
        for _ in 0..3 {
            assert_eq!(reader.list(&db).await.unwrap(), list);
            assert_eq!(
                reader
                    .segment(&db, &list.segments[0].label_code)
                    .await
                    .unwrap(),
                list.segments[0]
            );
            assert_eq!(reader.current(&db, customer).await.unwrap(), current);
        }
        assert!(
            started.elapsed() < Duration::from_secs(10),
            "reads stayed fast with the cache out"
        );
        let error = reader.current(&db, Uuid::new_v4()).await.unwrap_err();
        assert_eq!(error.code, "customer_not_found");
    }

    // A cache that works and then loses its connections falls back too.
    let reader = Reader::new(Some(service_cache(&redis_url, &password)));
    assert_eq!(reader.list(&db).await.unwrap(), list);
    assert!(exists(&mut admin, &list_key(list.run_id.unwrap())).await);
    let killed: i64 = redis::cmd("CLIENT")
        .arg("KILL")
        .arg("USER")
        .arg("svc_segments")
        .query_async(&mut admin)
        .await
        .unwrap();
    assert!(killed >= 0);
    drop_acl(&mut admin).await; // the user is gone: every command is refused
    assert_eq!(reader.list(&db).await.unwrap(), list);
    assert_eq!(reader.current(&db, customer).await.unwrap(), current);

    clear_cache(&mut admin).await;
}
