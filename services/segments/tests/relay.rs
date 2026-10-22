//! The outbox relay of `stream:segments.v1.run_recorded` against a real Redis
//! (TEST_REDIS_URL) and PostgreSQL (TEST_DATABASE_URL), with the committed ACL
//! line loaded.

mod support;

use std::collections::HashMap;
use std::time::{Duration, Instant};

use mosaiq_kit::OutboxRelay;
use redis::aio::MultiplexedConnection;
use segments::{run_consumer, run_relay, STREAM, SUFFIX};
use support::{
    admin, connect, drop_acl, load_acl, owner_url, redis_url, service_redis, RecordedRun,
};
use uuid::Uuid;

/// Both tests load and delete the same ACL user and stream.
static REDIS_USER: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

type Entry = (String, Vec<(String, String)>);

async fn recent_entries(admin: &mut MultiplexedConnection) -> Vec<Entry> {
    redis::cmd("XREVRANGE")
        .arg(STREAM)
        .arg("+")
        .arg("-")
        .arg("COUNT")
        .arg(200)
        .query_async(admin)
        .await
        .unwrap()
}

fn entry_for<'a>(entries: &'a [Entry], entity: &str) -> Option<&'a Entry> {
    entries
        .iter()
        .find(|(_, fields)| fields.contains(&("entity_id".to_owned(), entity.to_owned())))
}

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

#[tokio::test]
async fn a_recorded_run_reaches_the_stream_within_five_seconds_and_old_rows_are_purged() {
    let _turn = REDIS_USER.lock().await;
    let Some(redis_url) = redis_url() else {
        return;
    };
    let db_url = owner_url();
    let mut owner = connect(&db_url).await;
    let mut admin = admin(&redis_url).await;
    let password = load_acl(&mut admin).await;
    let mut relay = OutboxRelay::new(
        &support::service_url(&db_url),
        SUFFIX,
        [STREAM],
        service_redis(&redis_url, &password).await,
    )
    .unwrap();
    while relay.run_once(Duration::ZERO).await.unwrap() > 0 {}

    // A row relayed eight days ago, purged by the relay's first pass.
    let stale = Uuid::new_v4().to_string();
    owner
        .execute(
            "INSERT INTO segments_events.outbox \
             (stream, event_type, entity_id, occurred_at, relayed_at) \
             VALUES ($1, 'INSERT', $2, now() - interval '9 days', now() - interval '8 days')",
            &[&STREAM, &stale],
        )
        .await
        .unwrap();
    let fresh = Uuid::new_v4().to_string();
    owner
        .execute(
            "INSERT INTO segments_events.outbox \
             (stream, event_type, entity_id, occurred_at, relayed_at) \
             VALUES ($1, 'INSERT', $2, now() - interval '2 days', now() - interval '1 day')",
            &[&STREAM, &fresh],
        )
        .await
        .unwrap();
    // The purge runs once an hour per relay, so a new relay runs it now.
    let mut relay = OutboxRelay::new(
        &support::service_url(&db_url),
        SUFFIX,
        [STREAM],
        service_redis(&redis_url, &password).await,
    )
    .unwrap();
    while relay.run_once(Duration::ZERO).await.unwrap() > 0 {}
    let remaining: Vec<String> = owner
        .query(
            "SELECT entity_id FROM segments_events.outbox WHERE entity_id = ANY($1)",
            &[&vec![stale.clone(), fresh.clone()]],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get(0))
        .collect();
    assert_eq!(
        remaining,
        std::slice::from_ref(&fresh),
        "only the row older than 7 days is purged"
    );

    // The web records a run; the trigger writes the event; the relay delivers.
    let run = RecordedRun::record(&mut owner).await;
    let committed = Instant::now();
    let entity = run.run_id.to_string();
    let mut found = None;
    while found.is_none() && committed.elapsed() < Duration::from_secs(5) {
        relay.run_once(Duration::from_secs(1)).await.unwrap();
        found = entry_for(&recent_entries(&mut admin).await, &entity).cloned();
    }
    let (_, fields) = found.unwrap_or_else(|| panic!("run {entity} did not reach {STREAM} in 5 s"));
    let field = |name: &str| {
        fields
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.clone())
    };
    assert_eq!(field("event_type").as_deref(), Some("INSERT"));
    assert_eq!(field("v").as_deref(), Some("1"));
    assert_eq!(field("entity_id"), Some(entity.clone()));
    let event_id = field("event_id").expect("an event_id");
    let (row_event, relayed): (Uuid, bool) = {
        let row = owner
            .query_one(
                "SELECT event_id, relayed_at IS NOT NULL FROM segments_events.outbox \
                 WHERE entity_id = $1",
                &[&entity],
            )
            .await
            .unwrap();
        (row.get(0), row.get(1))
    };
    assert_eq!(row_event.to_string(), event_id);
    assert!(relayed, "the delivered row is marked relayed");

    owner
        .execute(
            "DELETE FROM segments_events.outbox WHERE entity_id = $1",
            &[&fresh],
        )
        .await
        .unwrap();
    let _: i64 = redis::cmd("DEL")
        .arg(STREAM)
        .query_async(&mut admin)
        .await
        .unwrap();
    drop_acl(&mut admin).await;
}

/// The service's own relay and consumer rebuild their Redis connections after
/// the server drops them, as a Redis restart does, and a run recorded
/// afterwards is still relayed and consumed.
#[tokio::test]
async fn relay_and_consumer_reconnect_after_redis_kills_their_connections() {
    let _turn = REDIS_USER.lock().await;
    let Some(redis_url) = redis_url() else {
        return;
    };
    let db_url = owner_url();
    let mut owner = connect(&db_url).await;
    let mut admin = admin(&redis_url).await;
    let password = load_acl(&mut admin).await;

    let mut relay = OutboxRelay::new(
        &support::service_url(&db_url),
        SUFFIX,
        [STREAM],
        service_redis(&redis_url, &password).await,
    )
    .unwrap();
    while relay.run_once(Duration::ZERO).await.unwrap() > 0 {}
    drop(relay);
    let _: i64 = redis::cmd("DEL")
        .arg(STREAM)
        .query_async(&mut admin)
        .await
        .unwrap();

    std::env::set_var("MOSAIQ_SERVICE_NAME", "segments-reconnect-test");
    let (sender, mut receiver) = tokio::sync::mpsc::unbounded_channel();
    let handler = move |event: HashMap<String, String>| {
        let sender = sender.clone();
        async move { sender.send(event).map_err(|_| "test receiver closed") }
    };
    let relay_task = tokio::spawn(run_relay(
        support::service_url(&db_url),
        redis_url.clone(),
        Some(password.clone()),
    ));
    let consumer_task = tokio::spawn(run_consumer(
        redis_url.clone(),
        Some(password.clone()),
        handler,
    ));
    let connected = Instant::now() + Duration::from_secs(10);
    while clients_of(&mut admin, "svc_segments").await < 2
        || !group_exists(&mut admin, "segments-reconnect-test").await
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
        .arg("svc_segments")
        .query_async(&mut admin)
        .await
        .unwrap();
    assert!(killed >= 2, "Redis did not kill both stream connections");

    let run = RecordedRun::record(&mut owner).await;
    let entity = run.run_id.to_string();
    let deadline = Instant::now() + Duration::from_secs(20);
    let mut consumed = false;
    let mut delivered = false;
    while !consumed || !delivered {
        while let Ok(fields) = receiver.try_recv() {
            consumed |= fields.get("entity_id") == Some(&entity);
        }
        delivered = entry_for(&recent_entries(&mut admin).await, &entity).is_some();
        assert!(
            Instant::now() < deadline,
            "run {entity} was not relayed and consumed after reconnecting"
        );
        if !consumed || !delivered {
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    }
    let unrelayed_since = Instant::now() + Duration::from_secs(10);
    loop {
        let unrelayed: i64 = owner
            .query_one(
                "SELECT count(*) FROM segments_events.outbox \
                 WHERE entity_id = $1 AND relayed_at IS NULL",
                &[&entity],
            )
            .await
            .unwrap()
            .get(0);
        if unrelayed == 0 {
            break;
        }
        assert!(
            Instant::now() < unrelayed_since,
            "the delivered outbox row stayed unrelayed"
        );
        tokio::time::sleep(Duration::from_millis(50)).await;
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
    drop_acl(&mut admin).await;
}
