//! The committed ACL line and the kit-rs relay against a real Redis
//! (TEST_REDIS_URL, the admin user) and PostgreSQL (TEST_DATABASE_URL).

mod support;

use std::time::{Duration, Instant};

use customer_preferences::{replace, run_relay, PreferenceSet, STREAM, SUFFIX};
use mosaiq_kit::{service_redis_info, OutboxRelay};
use redis::aio::MultiplexedConnection;
use support::{channel_ids, connect, owner_url, principal, service_database, TestCustomer};
use uuid::Uuid;

const ACL: &str = "redis/customer-preferences.acl";

/// Both Redis tests load and delete the same ACL user, so they take turns.
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

async fn admin(url: &str) -> MultiplexedConnection {
    redis::Client::open(url)
        .unwrap()
        .get_multiplexed_async_connection()
        .await
        .unwrap()
}

/// Load the committed user line under a throwaway password.
async fn load_acl(admin: &mut MultiplexedConnection) -> String {
    let password = Uuid::new_v4().simple().to_string();
    let line = std::fs::read_to_string(ACL).unwrap();
    let tokens = acl_tokens(line.trim());
    assert_eq!(&tokens[..3], ["user", "svc_preferences", "on"]);
    let _: () = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_preferences")
        .query_async(admin)
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
    let _: () = setuser.query_async(admin).await.unwrap();
    password
}

async fn dry_run(admin: &mut MultiplexedConnection, command: &[&str]) -> String {
    let mut dry = redis::cmd("ACL");
    dry.arg("DRYRUN").arg("svc_preferences");
    for part in command {
        dry.arg(*part);
    }
    dry.query_async(admin).await.unwrap()
}

#[tokio::test]
async fn the_acl_allows_its_own_keys_and_denies_the_rest() {
    let Some(url) = redis_url() else { return };
    let _turn = REDIS_USER.lock().await;
    let mut admin = admin(&url).await;
    load_acl(&mut admin).await;
    for allowed in [
        vec!["XADD", STREAM, "*", "a", "b"],
        vec!["EXISTS", "pub:auth:v1:live:x"],
        vec!["INCR", "preferences:rl:x"],
        vec!["WAITAOF", "1", "0", "2000"],
    ] {
        assert_eq!(dry_run(&mut admin, &allowed).await, "OK", "{allowed:?}");
    }
    for denied in [
        vec!["XADD", "stream:stock.v1.changed", "*", "a", "b"],
        vec!["GET", "auth:session:x"],
        vec!["GET", "pub:auth:v1:live:x"],
        vec!["SCAN", "0"],
        vec!["KEYS", "*"],
        vec!["RANDOMKEY"],
        vec!["FLUSHALL"],
    ] {
        assert_ne!(dry_run(&mut admin, &denied).await, "OK", "{denied:?}");
    }
    let _: () = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_preferences")
        .query_async(&mut admin)
        .await
        .unwrap();
}

#[tokio::test]
async fn a_changing_replace_reaches_the_stream_within_five_seconds() {
    let (Some(db_url), Some(url)) = (owner_url(), redis_url()) else {
        return;
    };
    let _turn = REDIS_USER.lock().await;
    let owner = connect(&db_url).await;
    let db = service_database(&db_url);
    let mut admin = admin(&url).await;
    let password = load_acl(&mut admin).await;
    let info = service_redis_info(&url, SUFFIX, Some(password)).unwrap();
    let redis = redis::Client::open(info)
        .unwrap()
        .get_multiplexed_async_connection()
        .await
        .unwrap();

    // A row relayed eight days ago, purged by the relay's first pass that
    // completes. A pass whose delivery fails returns 0 without purging, so run
    // passes until the row is gone, a bounded number of times (#705).
    let stale = Uuid::new_v4().to_string();
    owner
        .execute(
            "INSERT INTO preferences_events.outbox \
             (stream, event_type, entity_id, occurred_at, relayed_at) \
             VALUES ($1, 'INSERT', $2, now() - interval '9 days', now() - interval '8 days')",
            &[&STREAM, &stale],
        )
        .await
        .unwrap();
    let mut relay =
        OutboxRelay::new(&support::service_url(&db_url), SUFFIX, [STREAM], redis).unwrap();
    let mut left: i64 = 1;
    for _ in 0..20 {
        while relay.run_once(Duration::ZERO).await.unwrap() > 0 {}
        left = owner
            .query_one(
                "SELECT count(*) FROM preferences_events.outbox WHERE entity_id = $1",
                &[&stale],
            )
            .await
            .unwrap()
            .get(0);
        if left == 0 {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert_eq!(left, 0, "a relayed row older than 7 days is purged");

    let customer = TestCustomer::create(&owner).await;
    let channels = channel_ids(&owner).await;
    replace(
        &db,
        &principal(Uuid::new_v4(), "STORE_MANAGER"),
        customer.id,
        PreferenceSet::PreferredChannels,
        &[Some(channels[0])],
    )
    .await
    .unwrap();
    let committed = Instant::now();
    let entity = customer.id.to_string();
    let mut seen = false;
    while !seen && committed.elapsed() < Duration::from_secs(5) {
        relay.run_once(Duration::from_secs(1)).await.unwrap();
        let entries: Vec<(String, Vec<(String, String)>)> = redis::cmd("XREVRANGE")
            .arg(STREAM)
            .arg("+")
            .arg("-")
            .arg("COUNT")
            .arg(50)
            .query_async(&mut admin)
            .await
            .unwrap();
        seen = entries.iter().any(|(_, fields)| {
            fields.contains(&("entity_id".to_owned(), entity.clone()))
                && fields.contains(&("event_type".to_owned(), "INSERT".to_owned()))
        });
    }
    assert!(seen, "the replace did not reach {STREAM} within 5 s");
    let unrelayed: i64 = owner
        .query_one(
            "SELECT count(*) FROM preferences_events.outbox \
             WHERE entity_id = $1 AND relayed_at IS NULL",
            &[&entity],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(unrelayed, 0);

    let _: () = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_preferences")
        .query_async(&mut admin)
        .await
        .unwrap();
    customer.cleanup(&owner).await;
}

/// Entries in the stream for one customer.
async fn entries_for(admin: &mut MultiplexedConnection, entity: &str) -> usize {
    let entries: Vec<(String, Vec<(String, String)>)> = redis::cmd("XREVRANGE")
        .arg(STREAM)
        .arg("+")
        .arg("-")
        .arg("COUNT")
        .arg(200)
        .query_async(admin)
        .await
        .unwrap();
    let wanted = ("entity_id".to_owned(), entity.to_owned());
    entries
        .iter()
        .filter(|(_, fields)| fields.contains(&wanted))
        .count()
}

/// Poll until the customer has more than `seen` entries, or `within` passes.
async fn entries_after(
    admin: &mut MultiplexedConnection,
    entity: &str,
    seen: usize,
    within: Duration,
) -> usize {
    let started = Instant::now();
    loop {
        let count = entries_for(admin, entity).await;
        if count > seen || started.elapsed() >= within {
            return count;
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
}

/// #579: the service's own relay rebuilds its Redis connection after a real
/// outage. The server drops the connection, as a Redis restart does, and a
/// replace committed afterwards still reaches the stream.
#[tokio::test]
async fn the_service_relay_delivers_again_after_its_connection_is_killed() {
    let (Some(db_url), Some(url)) = (owner_url(), redis_url()) else {
        return;
    };
    let _turn = REDIS_USER.lock().await;
    let owner = connect(&db_url).await;
    let db = service_database(&db_url);
    let mut admin = admin(&url).await;
    let password = load_acl(&mut admin).await;
    let relay = tokio::spawn({
        let (service_url, url) = (support::service_url(&db_url), url.clone());
        async move { run_relay(&service_url, &url, Some(password)).await }
    });

    let customer = TestCustomer::create(&owner).await;
    let channels = channel_ids(&owner).await;
    let entity = customer.id.to_string();
    let manager = principal(Uuid::new_v4(), "STORE_MANAGER");
    replace(
        &db,
        &manager,
        customer.id,
        PreferenceSet::PreferredChannels,
        &[Some(channels[0])],
    )
    .await
    .unwrap();
    let before = entries_after(&mut admin, &entity, 0, Duration::from_secs(10)).await;
    assert!(before > 0, "the first replace did not reach {STREAM}");

    let killed: i64 = redis::cmd("CLIENT")
        .arg("KILL")
        .arg("USER")
        .arg("svc_preferences")
        .query_async(&mut admin)
        .await
        .unwrap();
    assert!(killed >= 1, "the relay held no Redis connection to drop");

    replace(
        &db,
        &manager,
        customer.id,
        PreferenceSet::PreferredChannels,
        &[Some(channels[1])],
    )
    .await
    .unwrap();
    let after = entries_after(&mut admin, &entity, before, Duration::from_secs(20)).await;
    relay.abort();
    assert!(
        after > before,
        "a replace after the outage did not reach {STREAM} within 20 s"
    );

    let _: () = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_preferences")
        .query_async(&mut admin)
        .await
        .unwrap();
    customer.cleanup(&owner).await;
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
fn the_acl_is_one_user_line_with_the_documented_selectors() {
    let line = std::fs::read_to_string(ACL).unwrap();
    assert_eq!(line.trim().lines().count(), 1);
    assert!(line.starts_with("user svc_preferences "));
    for part in [
        "-@all +@connection +@read +@write -@dangerous -scan -randomkey",
        "~preferences:rl:*",
        "+waitaof",
        "(%R~pub:auth:v1:live:* +exists)",
        "(~stream:preferences.v1.changed +xadd)",
    ] {
        assert!(line.contains(part), "{part}");
    }
}
