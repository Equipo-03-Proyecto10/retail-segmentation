//! Structured request context and secret redaction for service logs.

use std::cell::RefCell;
use std::fmt;
use std::future::Future;
use std::sync::LazyLock;

use regex::Regex;
use tracing::{Event, Subscriber};
use tracing_subscriber::fmt::format::Writer;
use tracing_subscriber::fmt::{FmtContext, FormatEvent, FormatFields};
use tracing_subscriber::registry::LookupSpan;

static BEARER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\bBearer\s+[^\s,;]+").expect("valid regex"));
static TOKEN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b").expect("valid regex")
});
static PASSWORD: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\bpassword\s*=\s*[^\s,;]+").expect("valid regex"));

/// Remove bearer tokens, JWT-shaped strings and `password=` values.
pub fn redact(value: &str) -> String {
    let value = BEARER.replace_all(value, "Bearer [redacted]");
    let value = TOKEN.replace_all(&value, "[token]");
    PASSWORD
        .replace_all(&value, "password=[redacted]")
        .into_owned()
}

/// Fields attached to every log line written while a request is handled.
#[derive(Clone, Debug)]
pub struct LogContext {
    pub correlation_id: String,
    pub actor: String,
    pub client: String,
}

impl Default for LogContext {
    fn default() -> Self {
        Self {
            correlation_id: "-".to_owned(),
            actor: "-".to_owned(),
            client: "-".to_owned(),
        }
    }
}

tokio::task_local! {
    static CONTEXT: RefCell<LogContext>;
}

/// Run `future` with `context` attached to its log lines.
pub async fn scope<F: Future>(context: LogContext, future: F) -> F::Output {
    CONTEXT.scope(RefCell::new(context), future).await
}

/// Set the actor for the rest of the current request.
pub fn set_actor(actor: &str) {
    let _ = CONTEXT.try_with(|context| context.borrow_mut().actor = actor.to_owned());
}

fn current() -> LogContext {
    CONTEXT
        .try_with(|context| context.borrow().clone())
        .unwrap_or_default()
}

/// One line per event: time, level, request fields, target, redacted message.
pub struct KitFormat;

impl<S, N> FormatEvent<S, N> for KitFormat
where
    S: Subscriber + for<'a> LookupSpan<'a>,
    N: for<'a> FormatFields<'a> + 'static,
{
    fn format_event(
        &self,
        context: &FmtContext<'_, S, N>,
        mut writer: Writer<'_>,
        event: &Event<'_>,
    ) -> fmt::Result {
        let mut message = String::new();
        context.format_fields(Writer::new(&mut message), event)?;
        let request = current();
        let metadata = event.metadata();
        writeln!(
            writer,
            "{} {} correlation={} actor={} client={} {} {}",
            chrono::Utc::now().format("%Y-%m-%d %H:%M:%S,%3f"),
            metadata.level(),
            request.correlation_id,
            redact(&request.actor),
            request.client,
            metadata.target(),
            redact(&message),
        )
    }
}

/// Install the kit's single stderr subscriber and route panics through it.
pub fn configure_logging(level: &str) {
    let level = level.parse().unwrap_or(tracing::Level::INFO);
    let _ = tracing_subscriber::fmt()
        .event_format(KitFormat)
        .with_writer(std::io::stderr)
        .with_max_level(level)
        .try_init();
    std::panic::set_hook(Box::new(|info| {
        tracing::error!("Unhandled panic: {info}");
    }));
}
