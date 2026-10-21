//! Transactional outbox relay and Redis Stream consumer helpers (ADR-0044).

use std::collections::{HashMap, HashSet};
use std::fmt::Display;
use std::future::Future;
use std::str::FromStr;
use std::sync::LazyLock;
use std::time::{Duration, Instant};

use chrono::{DateTime, Utc};
use indexmap::IndexSet;
use redis::aio::MultiplexedConnection;
use redis::{RedisError, RedisResult, Value as RedisValue};
use regex::Regex;
use tokio::sync::mpsc;
use tokio_postgres::{AsyncMessage, Client, NoTls};

use crate::contract::KitError;

static SUFFIX: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[a-z][a-z0-9_]*$").expect("valid regex"));
const BATCH: i64 = 100;
const MAX_WAIT: Duration = Duration::from_secs(5);
const PURGE_EVERY: Duration = Duration::from_secs(3600);
const RETENTION_DAYS: i32 = 7;
const STREAM_LENGTH: usize = 10_000;
const CLAIM_IDLE_MS: u64 = 60_000;
const MAX_DELIVERIES: u64 = 5;

const BACKOFF_START: Duration = Duration::from_secs(1);
const BACKOFF_CAP: Duration = Duration::from_secs(30);

/// Tracks one Redis outage: one warning when it starts, one info line when it
/// ends, and a wait that doubles from one second up to thirty.
struct Outage {
    what: &'static str,
    down: bool,
    delay: Duration,
}

impl Outage {
    fn new(what: &'static str) -> Self {
        Self {
            what,
            down: false,
            delay: BACKOFF_START,
        }
    }

    /// Record a failure and return how long to wait before the next attempt.
    fn lost(&mut self, error: &dyn Display) -> Duration {
        if !self.down {
            tracing::warn!("{} lost Redis ({error}); retrying with backoff", self.what);
            self.down = true;
        }
        let wait = self.delay;
        self.delay = (self.delay * 2).min(BACKOFF_CAP);
        wait
    }

    fn recovered(&mut self) {
        if self.down {
            tracing::info!("{} reconnected to Redis", self.what);
            self.down = false;
        }
        self.delay = BACKOFF_START;
    }
}

/// Whether an error means the connection or the server is unavailable, as
/// opposed to a fault in the command or the handler.
fn is_outage(error: &RedisError) -> bool {
    error.is_connection_dropped()
        || error.is_io_error()
        || error.is_timeout()
        || error.is_connection_refusal()
        || matches!(
            error.code(),
            Some("NOAUTH" | "WRONGPASS" | "NOPERM" | "LOADING" | "READONLY")
        )
}

fn quote_identifier(name: &str) -> String {
    format!("\"{}\"", name.replace('"', "\"\""))
}

fn timestamp(value: &DateTime<Utc>) -> String {
    use chrono::Timelike;
    let format = if value.nanosecond() / 1_000 != 0 {
        chrono::SecondsFormat::Micros
    } else {
        chrono::SecondsFormat::Secs
    };
    value.to_rfc3339_opts(format, true)
}

/// The stream commands the relay issues; implemented for a Redis connection.
pub trait StreamStore: Send {
    fn xadd(
        &mut self,
        stream: &str,
        fields: &[(&str, String)],
    ) -> impl Future<Output = RedisResult<()>> + Send;

    /// `WAITAOF 1 0 2000`: the number of local AOF fsyncs confirmed.
    fn wait_aof(&mut self) -> impl Future<Output = RedisResult<i64>> + Send;
}

impl StreamStore for MultiplexedConnection {
    async fn xadd(&mut self, stream: &str, fields: &[(&str, String)]) -> RedisResult<()> {
        let mut command = redis::cmd("XADD");
        command
            .arg(stream)
            .arg("MAXLEN")
            .arg("~")
            .arg(STREAM_LENGTH)
            .arg("*");
        for (name, value) in fields {
            command.arg(*name).arg(value);
        }
        command.query_async::<String>(self).await.map(|_| ())
    }

    async fn wait_aof(&mut self) -> RedisResult<i64> {
        let reply: Vec<i64> = redis::cmd("WAITAOF")
            .arg(1)
            .arg(0)
            .arg(2000)
            .query_async(self)
            .await?;
        Ok(reply.first().copied().unwrap_or(0))
    }
}

struct RelayConnection {
    client: Client,
    wakeups: mpsc::UnboundedReceiver<()>,
}

/// Relay one service boundary's outbox to its configured streams.
pub struct OutboxRelay<R: StreamStore> {
    config: tokio_postgres::Config,
    suffix: String,
    streams: HashSet<String>,
    redis: R,
    connection: Option<RelayConnection>,
    last_purge: Option<Instant>,
    delivery_failure: Option<String>,
}

impl<R: StreamStore> OutboxRelay<R> {
    /// `database_url` is the service role's own URL; `streams` are the only
    /// stream names this relay may write.
    pub fn new(
        database_url: &str,
        suffix: &str,
        streams: impl IntoIterator<Item = impl Into<String>>,
        redis: R,
    ) -> Result<Self, KitError> {
        if !SUFFIX.is_match(suffix) {
            return Err(KitError(format!("Invalid service suffix {suffix:?}.")));
        }
        let config = tokio_postgres::Config::from_str(database_url)
            .map_err(|error| KitError(format!("The relay database URL is invalid: {error}")))?;
        Ok(Self {
            config,
            suffix: suffix.to_owned(),
            streams: streams.into_iter().map(Into::into).collect(),
            redis,
            connection: None,
            last_purge: None,
            delivery_failure: None,
        })
    }

    fn table(&self) -> String {
        format!(
            "{}.outbox",
            quote_identifier(&format!("{}_events", self.suffix))
        )
    }

    async fn connect(&mut self) -> Result<&mut RelayConnection, tokio_postgres::Error> {
        if self
            .connection
            .as_ref()
            .is_none_or(|connection| connection.client.is_closed())
        {
            let (client, mut connection) = self.config.connect(NoTls).await?;
            let (sender, wakeups) = mpsc::unbounded_channel();
            tokio::spawn(async move {
                while let Some(message) =
                    std::future::poll_fn(|context| connection.poll_message(context)).await
                {
                    match message {
                        Ok(AsyncMessage::Notification(_)) => {
                            let _ = sender.send(());
                        }
                        Ok(_) => {}
                        Err(error) => {
                            tracing::warn!("Outbox connection closed: {error}");
                            break;
                        }
                    }
                }
            });
            let channel = quote_identifier(&format!("{}_outbox", self.suffix));
            client.batch_execute(&format!("LISTEN {channel}")).await?;
            self.connection = Some(RelayConnection { client, wakeups });
        }
        Ok(self.connection.as_mut().expect("connected"))
    }

    /// The relay's own database connection; for tests.
    pub async fn client(&mut self) -> Result<&Client, tokio_postgres::Error> {
        Ok(&self.connect().await?.client)
    }

    /// Wait for a wake-up (at most 5 s), poll once, and return the relayed row count.
    pub async fn run_once(&mut self, wait: Duration) -> Result<usize, tokio_postgres::Error> {
        let table = self.table();
        let wait = wait.min(MAX_WAIT);
        let connection = self.connect().await?;
        if !wait.is_zero() {
            let _ = tokio::time::timeout(wait, connection.wakeups.recv()).await;
        }
        while connection.wakeups.try_recv().is_ok() {}
        let select = format!(
            "SELECT event_id, stream, event_type, entity_id, occurred_at FROM {table} \
             WHERE relayed_at IS NULL ORDER BY occurred_at LIMIT $1 FOR UPDATE SKIP LOCKED"
        );
        let update = format!("UPDATE {table} SET relayed_at = now() WHERE event_id = ANY($1)");
        let transaction = self
            .connection
            .as_mut()
            .expect("connected")
            .client
            .transaction()
            .await?;
        let rows = transaction.query(&select, &[&BATCH]).await?;
        let mut event_ids: Vec<uuid::Uuid> = Vec::new();
        let mut delivery: Result<(), RedisError> = Ok(());
        for row in &rows {
            let event_id: uuid::Uuid = row.try_get(0)?;
            let stream: String = row.try_get(1)?;
            if !self.streams.contains(&stream) {
                tracing::error!("Outbox row has unconfigured stream {stream:?}");
                continue;
            }
            let occurred_at: DateTime<Utc> = row.try_get(4)?;
            let fields = [
                ("event_id", event_id.to_string()),
                ("v", "1".to_owned()),
                ("event_type", row.try_get::<_, String>(2)?),
                ("entity_id", row.try_get::<_, String>(3)?),
                ("occurred_at", timestamp(&occurred_at)),
            ];
            if let Err(error) = self.redis.xadd(&stream, &fields).await {
                delivery = Err(error);
                break;
            }
            event_ids.push(event_id);
        }
        if delivery.is_ok() && !event_ids.is_empty() {
            delivery = match self.redis.wait_aof().await {
                Ok(local) if local >= 1 => Ok(()),
                Ok(_) => Err(RedisError::from((
                    redis::ErrorKind::ResponseError,
                    "The local AOF did not confirm the event batch.",
                ))),
                Err(error) => Err(error),
            };
        }
        if let Err(error) = delivery {
            // The caller logs it, once per outage; the rows stay unrelayed.
            self.delivery_failure = Some(error.to_string());
            transaction.rollback().await?;
            return Ok(0);
        }
        let relayed = event_ids.len();
        if relayed > 0 {
            transaction.execute(&update, &[&event_ids]).await?;
        }
        transaction.commit().await?;
        if self.last_purge.is_none_or(|at| at.elapsed() >= PURGE_EVERY) {
            let purge = format!(
                "DELETE FROM {table} WHERE relayed_at IS NOT NULL \
                 AND relayed_at < now() - ($1::integer * interval '1 day')"
            );
            let client = &self.connection.as_ref().expect("connected").client;
            client.execute(&purge, &[&RETENTION_DAYS]).await?;
            self.last_purge = Some(Instant::now());
        }
        Ok(relayed)
    }

    /// One pass of the forever loop: returns the wait owed after a Redis
    /// failure, or `None`.
    async fn pass(&mut self, outage: &mut Outage) -> Option<Duration> {
        match self.run_once(MAX_WAIT).await {
            Err(error) => {
                tracing::error!("Outbox relay pass failed: {error}");
                self.connection = None;
                tokio::time::sleep(Duration::from_secs(1)).await;
                None
            }
            Ok(relayed) => match self.delivery_failure.take() {
                Some(failure) => Some(outage.lost(&failure)),
                None => {
                    if relayed > 0 {
                        outage.recovered();
                    }
                    None
                }
            },
        }
    }

    /// Relay until the task is aborted, reconnecting PostgreSQL after a failed
    /// pass. A Redis failure is logged once per outage and retried with
    /// backoff, but `redis` is never rebuilt: use `run_with_reconnect` for a
    /// store that does not recover by itself.
    pub async fn run_forever(mut self) {
        let mut outage = Outage::new("Outbox relay");
        loop {
            if let Some(wait) = self.pass(&mut outage).await {
                tokio::time::sleep(wait).await;
            }
        }
    }

    /// Like `run_forever`, but after a Redis failure it waits (backoff from
    /// 1 s doubling to 30 s) and rebuilds the store with `connect` until that
    /// succeeds. The failed batch was rolled back, so every row is delivered
    /// again: at least once, never lost.
    pub async fn run_with_reconnect<C, Fut>(mut self, mut connect: C)
    where
        C: FnMut() -> Fut,
        Fut: Future<Output = RedisResult<R>>,
    {
        let mut outage = Outage::new("Outbox relay");
        loop {
            if let Some(wait) = self.pass(&mut outage).await {
                tokio::time::sleep(wait).await;
                loop {
                    match connect().await {
                        Ok(redis) => {
                            self.redis = redis;
                            break;
                        }
                        Err(error) => {
                            let wait = outage.lost(&error);
                            tokio::time::sleep(wait).await;
                        }
                    }
                }
            }
        }
    }
}

impl<R: StreamStore + 'static> OutboxRelay<R> {
    pub fn spawn(self) -> tokio::task::JoinHandle<()> {
        tokio::spawn(self.run_forever())
    }
}

/// One stream entry: its id, and its fields unless the entry was trimmed.
type Entry = (String, Option<HashMap<String, String>>);

fn text(value: &RedisValue) -> Option<String> {
    match value {
        RedisValue::BulkString(bytes) => String::from_utf8(bytes.clone()).ok(),
        RedisValue::SimpleString(text) => Some(text.clone()),
        RedisValue::Int(number) => Some(number.to_string()),
        _ => None,
    }
}

fn entries(value: &RedisValue) -> Vec<Entry> {
    let RedisValue::Array(items) = value else {
        return Vec::new();
    };
    items
        .iter()
        .filter_map(|item| {
            let RedisValue::Array(parts) = item else {
                return None;
            };
            let id = text(parts.first()?)?;
            let fields = match parts.get(1) {
                Some(RedisValue::Array(pairs)) => Some(
                    pairs
                        .chunks(2)
                        .filter_map(|pair| Some((text(pair.first()?)?, text(pair.get(1)?)?)))
                        .collect(),
                ),
                _ => None,
            };
            Some((id, fields))
        })
        .collect()
}

fn is_code(error: &RedisError, code: &str) -> bool {
    error.code() == Some(code)
}

fn hostname() -> String {
    std::fs::read_to_string("/proc/sys/kernel/hostname")
        .ok()
        .map(|name| name.trim().to_owned())
        .filter(|name| !name.is_empty())
        .or_else(|| std::env::var("HOSTNAME").ok())
        .unwrap_or_else(|| "consumer".to_owned())
}

/// Consume one stream as the current service's own group, `MOSAIQ_SERVICE_NAME`.
///
/// Give it a connection of its own: a blocking read holds it for up to the
/// block time, so its response timeout must exceed that.
pub struct StreamConsumer<H> {
    redis: MultiplexedConnection,
    stream: String,
    handler: H,
    group: String,
    consumer: String,
    handled: IndexSet<String>,
    handled_capacity: usize,
    group_ready: bool,
    started: bool,
}

impl<H, Fut, E> StreamConsumer<H>
where
    H: FnMut(HashMap<String, String>) -> Fut + Send,
    Fut: Future<Output = Result<(), E>> + Send,
    E: Display,
{
    pub fn new(redis: MultiplexedConnection, stream: &str, handler: H) -> Result<Self, KitError> {
        let group = std::env::var("MOSAIQ_SERVICE_NAME")
            .ok()
            .filter(|group| !group.is_empty())
            .ok_or_else(|| {
                KitError("MOSAIQ_SERVICE_NAME is required for a stream consumer.".to_owned())
            })?;
        Ok(Self {
            redis,
            stream: stream.to_owned(),
            handler,
            group,
            consumer: hostname(),
            handled: IndexSet::new(),
            handled_capacity: 10_000,
            group_ready: false,
            started: false,
        })
    }

    /// How many handled `event_id`s are remembered for deduplication.
    pub fn handled_capacity(mut self, capacity: usize) -> Self {
        self.handled_capacity = capacity;
        self
    }

    pub fn group(&self) -> &str {
        &self.group
    }

    async fn ensure_group(&mut self) -> RedisResult<()> {
        if self.group_ready {
            return Ok(());
        }
        let created: RedisResult<String> = redis::cmd("XGROUP")
            .arg("CREATE")
            .arg(&self.stream)
            .arg(&self.group)
            .arg("$")
            .arg("MKSTREAM")
            .query_async(&mut self.redis)
            .await;
        match created {
            Ok(_) => {}
            Err(error) if is_code(&error, "BUSYGROUP") => {}
            Err(error) => return Err(error),
        }
        self.group_ready = true;
        Ok(())
    }

    async fn ack(&mut self, id: &str) -> RedisResult<()> {
        redis::cmd("XACK")
            .arg(&self.stream)
            .arg(&self.group)
            .arg(id)
            .query_async::<i64>(&mut self.redis)
            .await
            .map(|_| ())
    }

    async fn delivery_count(&mut self, id: &str) -> RedisResult<u64> {
        let pending: RedisValue = redis::cmd("XPENDING")
            .arg(&self.stream)
            .arg(&self.group)
            .arg(id)
            .arg(id)
            .arg(1)
            .query_async(&mut self.redis)
            .await?;
        let count = match &pending {
            RedisValue::Array(items) => items.first().and_then(|item| match item {
                RedisValue::Array(parts) => parts.get(3).and_then(text),
                _ => None,
            }),
            _ => None,
        };
        Ok(count.and_then(|count| count.parse().ok()).unwrap_or(1))
    }

    fn remember(&mut self, event_id: String) {
        self.handled.shift_remove(&event_id);
        self.handled.insert(event_id);
        while self.handled.len() > self.handled_capacity {
            self.handled.shift_remove_index(0);
        }
    }

    async fn handle_entries(&mut self, entries: Vec<Entry>) -> RedisResult<usize> {
        let mut processed = 0;
        for (id, fields) in entries {
            let Some(fields) = fields else {
                self.ack(&id).await?;
                processed += 1;
                continue;
            };
            let event_id = fields.get("event_id").cloned().unwrap_or_default();
            if !event_id.is_empty() && self.handled.contains(&event_id) {
                self.ack(&id).await?;
                processed += 1;
                continue;
            }
            let outcome = (self.handler)(fields.clone())
                .await
                .map_err(|error| error.to_string());
            if let Err(error) = outcome {
                let deliveries = self.delivery_count(&id).await?;
                if deliveries >= MAX_DELIVERIES {
                    tracing::error!("Stream entry exhausted five deliveries: {fields:?}: {error}");
                    self.ack(&id).await?;
                    processed += 1;
                } else {
                    tracing::warn!(
                        "Stream handler failed on delivery {deliveries} for entry {id:?}: {error}"
                    );
                }
                continue;
            }
            if !event_id.is_empty() {
                self.remember(event_id);
            }
            self.ack(&id).await?;
            processed += 1;
        }
        Ok(processed)
    }

    async fn read(&mut self, marker: &str, block_ms: Option<u64>) -> RedisResult<Vec<Entry>> {
        let mut command = redis::cmd("XREADGROUP");
        command
            .arg("GROUP")
            .arg(&self.group)
            .arg(&self.consumer)
            .arg("COUNT")
            .arg(100);
        if let Some(block_ms) = block_ms {
            command.arg("BLOCK").arg(block_ms);
        }
        command.arg("STREAMS").arg(&self.stream).arg(marker);
        let reply: RedisValue = command.query_async(&mut self.redis).await?;
        let RedisValue::Array(streams) = reply else {
            return Ok(Vec::new());
        };
        Ok(streams
            .first()
            .and_then(|stream| match stream {
                RedisValue::Array(parts) => parts.get(1).map(entries),
                _ => None,
            })
            .unwrap_or_default())
    }

    async fn run_pass(&mut self, block_ms: Option<u64>) -> RedisResult<usize> {
        let mut processed = 0;
        if !self.started {
            let own = self.read("0", None).await?;
            processed += self.handle_entries(own).await?;
            self.started = true;
        }
        let pending: RedisValue = redis::cmd("XPENDING")
            .arg(&self.stream)
            .arg(&self.group)
            .arg("IDLE")
            .arg(CLAIM_IDLE_MS)
            .arg("-")
            .arg("+")
            .arg(100)
            .query_async(&mut self.redis)
            .await?;
        let ids: Vec<String> = match &pending {
            RedisValue::Array(items) => items
                .iter()
                .filter_map(|item| match item {
                    RedisValue::Array(parts) => parts.first().and_then(text),
                    _ => None,
                })
                .collect(),
            _ => Vec::new(),
        };
        if !ids.is_empty() {
            let claimed: RedisValue = redis::cmd("XCLAIM")
                .arg(&self.stream)
                .arg(&self.group)
                .arg(&self.consumer)
                .arg(CLAIM_IDLE_MS)
                .arg(&ids)
                .query_async(&mut self.redis)
                .await?;
            processed += self.handle_entries(entries(&claimed)).await?;
        }
        let fresh = self.read(">", block_ms).await?;
        processed += self.handle_entries(fresh).await?;
        Ok(processed)
    }

    /// Recover pending work in order, then read new entries.
    pub async fn run_once(&mut self, block_ms: Option<u64>) -> RedisResult<usize> {
        self.ensure_group().await?;
        match self.run_pass(block_ms).await {
            Err(error) if is_code(&error, "NOGROUP") => {
                self.group_ready = false;
                self.started = false;
                self.ensure_group().await?;
                self.run_pass(block_ms).await
            }
            outcome => outcome,
        }
    }

    /// Consume until the task is aborted. A Redis outage is logged once and
    /// retried with backoff on the same connection; use `run_with_reconnect`
    /// to rebuild it.
    pub async fn run_forever(mut self) {
        let mut outage = Outage::new("Stream consumer");
        loop {
            if let Some(wait) = self.pass(&mut outage).await {
                tokio::time::sleep(wait).await;
            }
        }
    }

    async fn pass(&mut self, outage: &mut Outage) -> Option<Duration> {
        match self.run_once(Some(5_000)).await {
            Ok(_) => {
                outage.recovered();
                None
            }
            Err(error) if is_outage(&error) => Some(outage.lost(&error)),
            Err(error) => {
                tracing::error!("Stream consumer pass failed: {error}");
                tokio::time::sleep(Duration::from_secs(1)).await;
                None
            }
        }
    }

    /// Like `run_forever`, but after an outage it waits, rebuilds the
    /// connection with `connect` until that succeeds, and starts over from the
    /// group's pending entries, so no unacknowledged entry is skipped.
    pub async fn run_with_reconnect<C, Conn>(mut self, mut connect: C)
    where
        C: FnMut() -> Conn,
        Conn: Future<Output = RedisResult<MultiplexedConnection>>,
    {
        let mut outage = Outage::new("Stream consumer");
        loop {
            if let Some(wait) = self.pass(&mut outage).await {
                tokio::time::sleep(wait).await;
                loop {
                    match connect().await {
                        Ok(redis) => {
                            self.redis = redis;
                            self.group_ready = false;
                            self.started = false;
                            break;
                        }
                        Err(error) => {
                            let wait = outage.lost(&error);
                            tokio::time::sleep(wait).await;
                        }
                    }
                }
            }
        }
    }
}

impl<H, Fut, E> StreamConsumer<H>
where
    H: FnMut(HashMap<String, String>) -> Fut + Send + 'static,
    Fut: Future<Output = Result<(), E>> + Send + 'static,
    E: Display + 'static,
{
    pub fn spawn(self) -> tokio::task::JoinHandle<()> {
        tokio::spawn(self.run_forever())
    }
}
