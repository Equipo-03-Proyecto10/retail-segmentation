//! Outbox relay and stream consumer on real PostgreSQL and Redis, as kit-py's
//! test_streams.py. The tests share one schema and two streams, so they run
//! one at a time.

mod common;

use std::collections::HashMap;
use std::sync::{Arc, LazyLock, Mutex};
use std::time::Duration;

use common::*;
use mosaiq_kit::{OutboxRelay, StreamConsumer, StreamStore};
use redis::aio::MultiplexedConnection;
use redis::RedisResult;
use tokio_postgres::{Client, NoTls};

static SERIAL: LazyLock<tokio::sync::Mutex<()>> = LazyLock::new(|| tokio::sync::Mutex::new(()));

fn template() -> String {
    let text = std::fs::read_to_string(repository().join("sql/01_schema.sql")).unwrap();
    let block = text
        .split_once("-- BEGIN OUTBOX TEMPLATE\n")
        .unwrap()
        .1
        .split_once("-- END OUTBOX TEMPLATE")
        .unwrap()
        .0;
    let lines: Vec<&str> = block.lines().collect();
    let start = lines
        .iter()
        .position(|line| line.starts_with(&["-- CREATE ", "SCHEMA"].concat()))
        .unwrap();
    lines[start..]
        .iter()
        .map(|line| line.strip_prefix("-- ").unwrap_or(""))
        .collect::<Vec<_>>()
        .join("\n")
        .replace("<suffix>", &suffix())
        .replace("<table>", &format!("{}.item", suffix()))
        .replace("<entity_column>", "item_id")
        .replace("<event>", "changed")
}

fn fixture() -> (String, String) {
    let text = std::fs::read_to_string(repository().join("tests/kit_vectors/postgres_fixture.sql"))
        .unwrap();
    let (setup, teardown) = text
        .split_once("-- SETUP\n")
        .unwrap()
        .1
        .split_once("-- TEARDOWN\n")
        .unwrap();
    (
        setup.replace("<suffix>", &suffix()),
        teardown.replace("<suffix>", &suffix()),
    )
}

async fn admin_database() -> Client {
    let (client, driver) = tokio_postgres::connect(&database_url().unwrap(), NoTls)
        .await
        .unwrap();
    tokio::spawn(driver);
    client
}

/// The relay's URL: the same database, as the boundary's own role.
fn relay_url() -> String {
    let url = database_url().unwrap();
    let separator = if url.contains('?') { '&' } else { '?' };
    format!("{url}{separator}options=-c%20role%3Dsvc_{}", suffix())
}

struct Outbox {
    admin: Client,
    teardown: String,
}

impl Outbox {
    async fn create() -> Option<Self> {
        database_url()?;
        redis_url()?;
        let admin = admin_database().await;
        let (setup, teardown) = fixture();
        admin.batch_execute(&teardown).await.unwrap();
        admin.batch_execute(&setup).await.unwrap();
        admin.batch_execute(&template()).await.unwrap();
        Some(Self { admin, teardown })
    }

    fn table(&self) -> String {
        format!("{}_events.outbox", suffix())
    }

    async fn insert_item(&self, item_id: i32) {
        self.admin
            .execute(
                &format!(
                    "INSERT INTO {}.item (item_id, label) VALUES ($1, $2)",
                    suffix()
                ),
                &[&item_id, &"relay"],
            )
            .await
            .unwrap();
    }

    async fn relayed(&self, entity_id: &str) -> Option<bool> {
        self.admin
            .query_opt(
                &format!(
                    "SELECT relayed_at IS NOT NULL FROM {} WHERE entity_id = $1",
                    self.table()
                ),
                &[&entity_id],
            )
            .await
            .unwrap()
            .map(|row| row.get(0))
    }

    async fn drop(self) {
        self.admin.batch_execute(&self.teardown).await.unwrap();
    }
}

fn stream() -> String {
    format!("stream:{}", own_stream())
}

#[tokio::test]
async fn relay_uses_the_repository_template_and_marks_after_durability() {
    let _serial = SERIAL.lock().await;
    let Some(outbox) = Outbox::create().await else {
        return;
    };
    outbox.insert_item(7).await;
    let mut relay = OutboxRelay::new(
        &relay_url(),
        &suffix(),
        [stream()],
        service_connection().await,
    )
    .unwrap();
    assert_eq!(relay.run_once(Duration::ZERO).await.unwrap(), 1);
    let user: String = relay
        .client()
        .await
        .unwrap()
        .query_one("SELECT current_user::text", &[])
        .await
        .unwrap()
        .get(0);
    assert_eq!(user, format!("svc_{}", suffix()));
    let row = outbox
        .admin
        .query_one(
            &format!(
                "SELECT stream, event_type, entity_id, relayed_at IS NOT NULL FROM {} \
                 WHERE entity_id = $1",
                outbox.table()
            ),
            &[&"7"],
        )
        .await
        .unwrap();
    assert_eq!(row.get::<_, String>(0), stream());
    assert_eq!(row.get::<_, String>(1), "INSERT");
    assert_eq!(row.get::<_, String>(2), "7");
    assert!(row.get::<_, bool>(3));
    let mut admin = admin().await;
    let entries: redis::streams::StreamRangeReply = redis::cmd("XRANGE")
        .arg(stream())
        .arg("-")
        .arg("+")
        .query_async(&mut admin)
        .await
        .unwrap();
    let last = entries.ids.last().unwrap();
    let mut fields: Vec<&str> = last.map.keys().map(String::as_str).collect();
    fields.sort_unstable();
    assert_eq!(
        fields,
        ["entity_id", "event_id", "event_type", "occurred_at", "v"]
    );
    assert_eq!(last.get::<String>("v").unwrap(), "1");
    assert_eq!(last.get::<String>("entity_id").unwrap(), "7");
    assert!(last.get::<String>("occurred_at").unwrap().ends_with('Z'));
    outbox.drop().await;
}

#[tokio::test]
async fn relay_wakes_on_its_own_channel() {
    let _serial = SERIAL.lock().await;
    let Some(outbox) = Outbox::create().await else {
        return;
    };
    let mut relay = OutboxRelay::new(
        &relay_url(),
        &suffix(),
        [stream()],
        service_connection().await,
    )
    .unwrap();
    assert_eq!(relay.run_once(Duration::ZERO).await.unwrap(), 0);
    let insert = async {
        tokio::time::sleep(Duration::from_millis(200)).await;
        outbox.insert_item(30).await;
    };
    let started = std::time::Instant::now();
    let (relayed, ()) = tokio::join!(relay.run_once(Duration::from_secs(30)), insert);
    assert_eq!(relayed.unwrap(), 1);
    assert!(started.elapsed() < Duration::from_secs(5));
    outbox.drop().await;
}

struct BrokenRedis;

impl StreamStore for BrokenRedis {
    async fn xadd(&mut self, _stream: &str, _fields: &[(&str, String)]) -> RedisResult<()> {
        Err((redis::ErrorKind::IoError, "unavailable").into())
    }

    async fn wait_aof(&mut self) -> RedisResult<i64> {
        Ok(1)
    }
}

#[tokio::test]
async fn relay_leaves_a_row_when_stream_delivery_fails() {
    let _serial = SERIAL.lock().await;
    let Some(outbox) = Outbox::create().await else {
        return;
    };
    outbox.insert_item(8).await;
    let mut relay = OutboxRelay::new(&relay_url(), &suffix(), [stream()], BrokenRedis).unwrap();
    assert_eq!(relay.run_once(Duration::ZERO).await.unwrap(), 0);
    assert_eq!(outbox.relayed("8").await, Some(false));
    outbox.drop().await;
}

/// A store that fails like a dead connection until it is replaced.
struct FlakyRedis {
    inner: Option<MultiplexedConnection>,
}

impl StreamStore for FlakyRedis {
    async fn xadd(&mut self, stream: &str, fields: &[(&str, String)]) -> RedisResult<()> {
        match self.inner.as_mut() {
            Some(inner) => inner.xadd(stream, fields).await,
            None => Err((redis::ErrorKind::IoError, "broken pipe").into()),
        }
    }

    async fn wait_aof(&mut self) -> RedisResult<i64> {
        match self.inner.as_mut() {
            Some(inner) => inner.wait_aof().await,
            None => Err((redis::ErrorKind::IoError, "broken pipe").into()),
        }
    }
}

#[tokio::test]
async fn relay_rebuilds_its_store_after_a_redis_outage_and_delivers_the_backlog() {
    let _serial = SERIAL.lock().await;
    let Some(outbox) = Outbox::create().await else {
        return;
    };
    outbox.insert_item(40).await;
    outbox.insert_item(41).await;
    let relay = OutboxRelay::new(
        &relay_url(),
        &suffix(),
        [stream()],
        FlakyRedis { inner: None },
    )
    .unwrap();
    let attempts = Arc::new(Mutex::new(0));
    let counter = Arc::clone(&attempts);
    let task = tokio::spawn(relay.run_with_reconnect(move || {
        let counter = Arc::clone(&counter);
        async move {
            let attempt = {
                let mut guard = counter.lock().unwrap();
                *guard += 1;
                *guard
            };
            if attempt < 2 {
                return Err((redis::ErrorKind::IoError, "still down").into());
            }
            Ok(FlakyRedis {
                inner: Some(admin().await),
            })
        }
    }));
    let deadline = std::time::Instant::now() + Duration::from_secs(20);
    loop {
        if outbox.relayed("40").await == Some(true) && outbox.relayed("41").await == Some(true) {
            break;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "the backlog was not delivered"
        );
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    assert!(*attempts.lock().unwrap() >= 2);
    task.abort();
    let _ = task.await;
    tokio::time::sleep(Duration::from_millis(500)).await;
    outbox.drop().await;
}

#[tokio::test]
async fn consumer_rebuilds_its_connection_after_a_redis_outage() {
    let _serial = SERIAL.lock().await;
    let Some(mut admin_connection) = fresh_stream().await else {
        return;
    };
    service_name("kit-consumer-reconnect");
    let handled = Handled::default();
    let connections = Arc::new(Mutex::new(0));
    let counter = Arc::clone(&connections);
    let consumer = StreamConsumer::new(admin().await, &consumed(), recorder(&handled)).unwrap();
    let group = consumer.group().to_owned();
    // Kill this connection's server-side session so the next command fails.
    let _: i64 = redis::cmd("CLIENT")
        .arg("KILL")
        .arg("TYPE")
        .arg("normal")
        .arg("SKIPME")
        .arg("yes")
        .query_async(&mut admin_connection)
        .await
        .unwrap_or(0);
    let task = tokio::spawn(consumer.run_with_reconnect(move || {
        *counter.lock().unwrap() += 1;
        async { Ok(admin().await) }
    }));
    tokio::time::sleep(Duration::from_secs(3)).await;
    assert!(
        *connections.lock().unwrap() >= 1,
        "the connection was not rebuilt"
    );
    let mut fresh = admin().await;
    let _ = xadd(&mut fresh, "reconnect-1").await;
    let deadline = std::time::Instant::now() + Duration::from_secs(20);
    while handled.lock().unwrap().is_empty() {
        assert!(std::time::Instant::now() < deadline, "nothing was handled");
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    assert_eq!(pending(&mut fresh, &group).await, 0);
    task.abort();
    let _ = task.await;
    tokio::time::sleep(Duration::from_millis(500)).await;
}

struct RecordingRedis {
    inner: MultiplexedConnection,
    calls: Arc<Mutex<Vec<&'static str>>>,
    confirm: bool,
}

impl StreamStore for RecordingRedis {
    async fn xadd(&mut self, stream: &str, fields: &[(&str, String)]) -> RedisResult<()> {
        self.calls.lock().unwrap().push("xadd");
        self.inner.xadd(stream, fields).await
    }

    async fn wait_aof(&mut self) -> RedisResult<i64> {
        self.calls.lock().unwrap().push("waitaof");
        if self.confirm {
            self.inner.wait_aof().await
        } else {
            Ok(0)
        }
    }
}

#[tokio::test]
async fn relay_waits_for_aof_once_after_the_whole_batch() {
    let _serial = SERIAL.lock().await;
    let Some(outbox) = Outbox::create().await else {
        return;
    };
    outbox.insert_item(20).await;
    outbox.insert_item(21).await;
    let calls = Arc::default();
    let redis = RecordingRedis {
        inner: service_connection().await,
        calls: Arc::clone(&calls),
        confirm: true,
    };
    let mut relay = OutboxRelay::new(&relay_url(), &suffix(), [stream()], redis).unwrap();
    assert_eq!(relay.run_once(Duration::ZERO).await.unwrap(), 2);
    assert_eq!(*calls.lock().unwrap(), ["xadd", "xadd", "waitaof"]);
    outbox.drop().await;
}

#[tokio::test]
async fn relay_rolls_back_when_local_aof_does_not_confirm() {
    let _serial = SERIAL.lock().await;
    let Some(outbox) = Outbox::create().await else {
        return;
    };
    outbox.insert_item(22).await;
    outbox.insert_item(23).await;
    let redis = RecordingRedis {
        inner: service_connection().await,
        calls: Arc::default(),
        confirm: false,
    };
    let mut relay = OutboxRelay::new(&relay_url(), &suffix(), [stream()], redis).unwrap();
    assert_eq!(relay.run_once(Duration::ZERO).await.unwrap(), 0);
    assert_eq!(outbox.relayed("22").await, Some(false));
    assert_eq!(outbox.relayed("23").await, Some(false));
    outbox.drop().await;
}

#[tokio::test]
async fn relay_refuses_an_unconfigured_stream() {
    let _serial = SERIAL.lock().await;
    let Some(outbox) = Outbox::create().await else {
        return;
    };
    outbox
        .admin
        .execute(
            &format!(
                "INSERT INTO {} (stream, event_type, entity_id) VALUES ($1, $2, $3)",
                outbox.table()
            ),
            &[&"stream:another.v1.changed", &"UPDATE", &"9"],
        )
        .await
        .unwrap();
    let mut relay = OutboxRelay::new(
        &relay_url(),
        &suffix(),
        [stream()],
        service_connection().await,
    )
    .unwrap();
    assert_eq!(relay.run_once(Duration::ZERO).await.unwrap(), 0);
    assert_eq!(outbox.relayed("9").await, Some(false));
    outbox.drop().await;
}

#[tokio::test]
async fn relay_purges_old_relayed_rows() {
    let _serial = SERIAL.lock().await;
    let Some(outbox) = Outbox::create().await else {
        return;
    };
    outbox
        .admin
        .execute(
            &format!(
                "INSERT INTO {} (stream, event_type, entity_id, relayed_at) \
                 VALUES ($1, $2, $3, now() - ($4::integer * interval '1 day'))",
                outbox.table()
            ),
            &[&stream(), &"UPDATE", &"10", &8i32],
        )
        .await
        .unwrap();
    let mut relay = OutboxRelay::new(
        &relay_url(),
        &suffix(),
        [stream()],
        service_connection().await,
    )
    .unwrap();
    relay.run_once(Duration::ZERO).await.unwrap();
    assert_eq!(outbox.relayed("10").await, None);
    outbox.drop().await;
}

fn consumed() -> String {
    format!("stream:{}", consumed_stream())
}

async fn fresh_stream() -> Option<MultiplexedConnection> {
    redis_url()?;
    let mut admin = admin().await;
    let _: i64 = redis::cmd("DEL")
        .arg(consumed())
        .query_async(&mut admin)
        .await
        .unwrap();
    Some(admin)
}

async fn xadd(admin: &mut MultiplexedConnection, event_id: &str) -> String {
    redis::cmd("XADD")
        .arg(consumed())
        .arg("*")
        .arg("event_id")
        .arg(event_id)
        .arg("v")
        .arg("1")
        .query_async(admin)
        .await
        .unwrap()
}

async fn pending(admin: &mut MultiplexedConnection, group: &str) -> i64 {
    let summary: redis::Value = redis::cmd("XPENDING")
        .arg(consumed())
        .arg(group)
        .query_async(admin)
        .await
        .unwrap();
    match summary {
        redis::Value::Array(parts) => redis::from_redis_value(&parts[0]).unwrap(),
        other => panic!("{other:?}"),
    }
}

type Handled = Arc<Mutex<Vec<HashMap<String, String>>>>;

fn recorder(
    handled: &Handled,
) -> impl FnMut(HashMap<String, String>) -> std::future::Ready<Result<(), String>> + Send + 'static
{
    let handled = Arc::clone(handled);
    move |entry| {
        handled.lock().unwrap().push(entry);
        std::future::ready(Ok(()))
    }
}

fn service_name(name: &str) {
    std::env::set_var("MOSAIQ_SERVICE_NAME", name);
}

#[tokio::test]
async fn consumer_creates_its_own_group_and_acks_after_success() {
    let _serial = SERIAL.lock().await;
    let Some(mut admin) = fresh_stream().await else {
        return;
    };
    service_name("kit-consumer-success");
    let handled = Handled::default();
    let mut consumer =
        StreamConsumer::new(service_connection().await, &consumed(), recorder(&handled)).unwrap();
    assert_eq!(consumer.group(), "kit-consumer-success");
    assert_eq!(consumer.run_once(Some(1)).await.unwrap(), 0);
    xadd(&mut admin, "event-1").await;
    assert_eq!(consumer.run_once(Some(1)).await.unwrap(), 1);
    assert_eq!(
        *handled.lock().unwrap(),
        [HashMap::from([
            ("event_id".to_owned(), "event-1".to_owned()),
            ("v".to_owned(), "1".to_owned()),
        ])]
    );
    assert_eq!(pending(&mut admin, "kit-consumer-success").await, 0);
}

#[tokio::test]
async fn consumer_claims_entries_idle_over_sixty_seconds() {
    let _serial = SERIAL.lock().await;
    let Some(mut admin) = fresh_stream().await else {
        return;
    };
    service_name("kit-consumer-claim");
    let mut first = StreamConsumer::new(
        service_connection().await,
        &consumed(),
        recorder(&Handled::default()),
    )
    .unwrap();
    first.run_once(Some(1)).await.unwrap();
    let message_id = xadd(&mut admin, "event-claim").await;
    let mut service = service_connection().await;
    let _: redis::Value = redis::cmd("XREADGROUP")
        .arg("GROUP")
        .arg("kit-consumer-claim")
        .arg("old-consumer")
        .arg("COUNT")
        .arg(1)
        .arg("STREAMS")
        .arg(consumed())
        .arg(">")
        .query_async(&mut service)
        .await
        .unwrap();
    let _: redis::Value = redis::cmd("XCLAIM")
        .arg(consumed())
        .arg("kit-consumer-claim")
        .arg("old-consumer")
        .arg(0)
        .arg(&message_id)
        .arg("IDLE")
        .arg(61_000)
        .query_async(&mut service)
        .await
        .unwrap();
    let handled = Handled::default();
    let mut recovering =
        StreamConsumer::new(service_connection().await, &consumed(), recorder(&handled)).unwrap();
    assert_eq!(recovering.run_once(Some(1)).await.unwrap(), 1);
    assert_eq!(handled.lock().unwrap()[0]["event_id"], "event-claim");
}

#[tokio::test]
async fn consumer_deduplicates_event_ids_in_memory() {
    let _serial = SERIAL.lock().await;
    let Some(mut admin) = fresh_stream().await else {
        return;
    };
    service_name("kit-consumer-deduplicate");
    let handled = Handled::default();
    let mut consumer =
        StreamConsumer::new(service_connection().await, &consumed(), recorder(&handled)).unwrap();
    consumer.run_once(Some(1)).await.unwrap();
    xadd(&mut admin, "same-event").await;
    xadd(&mut admin, "same-event").await;
    assert_eq!(consumer.run_once(Some(1)).await.unwrap(), 2);
    assert_eq!(handled.lock().unwrap().len(), 1);
    assert_eq!(pending(&mut admin, "kit-consumer-deduplicate").await, 0);
}

fn failing(
    attempts: &Arc<Mutex<u32>>,
) -> impl FnMut(HashMap<String, String>) -> std::future::Ready<Result<(), String>> + Send + 'static
{
    let attempts = Arc::clone(attempts);
    move |_entry| {
        *attempts.lock().unwrap() += 1;
        std::future::ready(Err("handler refusal".to_owned()))
    }
}

#[tokio::test]
async fn consumer_acks_after_the_fifth_failed_delivery() {
    let _serial = SERIAL.lock().await;
    let Some(mut admin) = fresh_stream().await else {
        return;
    };
    service_name("kit-consumer-failures");
    let attempts = Arc::default();
    let mut consumer =
        StreamConsumer::new(service_connection().await, &consumed(), failing(&attempts)).unwrap();
    consumer.run_once(Some(1)).await.unwrap();
    let message_id = xadd(&mut admin, "event-fail").await;
    consumer.run_once(Some(1)).await.unwrap();
    let mut service = service_connection().await;
    let hostname = std::fs::read_to_string("/proc/sys/kernel/hostname")
        .map(|name| name.trim().to_owned())
        .unwrap();
    for _delivery in 0..4 {
        if pending(&mut admin, "kit-consumer-failures").await == 0 {
            break;
        }
        let _: redis::Value = redis::cmd("XCLAIM")
            .arg(consumed())
            .arg("kit-consumer-failures")
            .arg(&hostname)
            .arg(0)
            .arg(&message_id)
            .arg("IDLE")
            .arg(61_000)
            .arg("JUSTID")
            .query_async(&mut service)
            .await
            .unwrap();
        consumer.run_once(Some(1)).await.unwrap();
    }
    assert_eq!(pending(&mut admin, "kit-consumer-failures").await, 0);
    assert_eq!(*attempts.lock().unwrap(), 5);
}

#[tokio::test]
async fn consumer_recreates_its_group_after_nogroup() {
    let _serial = SERIAL.lock().await;
    let Some(mut admin) = fresh_stream().await else {
        return;
    };
    service_name("kit-consumer-group");
    let handled = Handled::default();
    let mut consumer =
        StreamConsumer::new(service_connection().await, &consumed(), recorder(&handled)).unwrap();
    consumer.run_once(Some(1)).await.unwrap();
    let _: i64 = redis::cmd("XGROUP")
        .arg("DESTROY")
        .arg(consumed())
        .arg("kit-consumer-group")
        .query_async(&mut admin)
        .await
        .unwrap();
    assert_eq!(consumer.run_once(Some(1)).await.unwrap(), 0);
    xadd(&mut admin, "after-recreate").await;
    assert_eq!(consumer.run_once(Some(1)).await.unwrap(), 1);
}

#[tokio::test]
async fn consumer_acks_a_trimmed_pending_entry() {
    let _serial = SERIAL.lock().await;
    let Some(mut admin) = fresh_stream().await else {
        return;
    };
    service_name("kit-consumer-trimmed");
    let attempts = Arc::default();
    let mut consumer =
        StreamConsumer::new(service_connection().await, &consumed(), failing(&attempts)).unwrap();
    consumer.run_once(Some(1)).await.unwrap();
    let message_id = xadd(&mut admin, "event-trimmed").await;
    consumer.run_once(Some(1)).await.unwrap();
    assert_eq!(pending(&mut admin, "kit-consumer-trimmed").await, 1);
    let _: i64 = redis::cmd("XDEL")
        .arg(consumed())
        .arg(&message_id)
        .query_async(&mut admin)
        .await
        .unwrap();
    let mut restarted =
        StreamConsumer::new(service_connection().await, &consumed(), failing(&attempts)).unwrap();
    assert_eq!(restarted.run_once(Some(1)).await.unwrap(), 1);
    assert_eq!(pending(&mut admin, "kit-consumer-trimmed").await, 0);
    assert_eq!(*attempts.lock().unwrap(), 1);
}

#[test]
fn consumer_requires_its_service_name() {
    let _serial = SERIAL.blocking_lock();
    std::env::remove_var("MOSAIQ_SERVICE_NAME");
    let runtime = tokio::runtime::Runtime::new().unwrap();
    let Some(connection) = runtime.block_on(async {
        redis_url()?;
        Some(service_connection().await)
    }) else {
        return;
    };
    let error = StreamConsumer::new(connection, &consumed(), recorder(&Handled::default()))
        .err()
        .unwrap();
    assert!(error.0.contains("MOSAIQ_SERVICE_NAME"));
}
