//! Structured request context and secret redaction for service logs
//! (kit-py `logging.py`).
//!
//! Every line carries `correlation=<id> actor=<sub> client=<address>`, and
//! no bearer token, JWT or `password=` value survives into it, whatever the
//! caller logged.

use std::cell::RefCell;
use std::fmt;
use std::sync::LazyLock;

use regex::Regex;
use tracing::{Event, Subscriber};
use tracing_subscriber::fmt::format::Writer;
use tracing_subscriber::fmt::{FmtContext, FormatEvent, FormatFields};
use tracing_subscriber::registry::LookupSpan;

static BEARER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\bBearer\s+[^\s,;]+").expect("valid pattern"));
static TOKEN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b").expect("valid pattern")
});
static PASSWORD: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\bpassword\s*=\s*[^\s,;]+").expect("valid pattern"));

/// Remove credentials from a rendered log message.
pub fn redact(value: &str) -> String {
    let value = BEARER.replace_all(value, "Bearer [redacted]");
    let value = TOKEN.replace_all(&value, "[token]");
    PASSWORD
        .replace_all(&value, "password=[redacted]")
        .into_owned()
}

/// The fields attached to every record logged while a request is handled.
#[derive(Clone, Debug)]
pub(crate) struct LogContext {
    pub(crate) correlation_id: String,
    pub(crate) actor: String,
    pub(crate) client: String,
}

tokio::task_local! {
    pub(crate) static CONTEXT: RefCell<LogContext>;
}

fn current() -> LogContext {
    CONTEXT
        .try_with(|context| context.borrow().clone())
        .unwrap_or_else(|_| LogContext {
            correlation_id: "-".into(),
            actor: "-".into(),
            client: "-".into(),
        })
}

/// `<time> <LEVEL> correlation=… actor=… client=… <target> <message>`, the
/// line kit-py writes, redacted after formatting.
pub struct RedactingFormat;

impl<S, N> FormatEvent<S, N> for RedactingFormat
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
        writeln!(
            writer,
            "{} {} correlation={} actor={} client={} {} {}",
            chrono::Utc::now().format("%Y-%m-%d %H:%M:%S,%3f"),
            event.metadata().level(),
            request.correlation_id,
            request.actor,
            request.client,
            event.metadata().target(),
            redact(&message),
        )
    }
}

/// Install the kit's single stderr subscriber, as kit-py's
/// `configure_logging()` installs its handler. `level` is a tracing filter
/// level such as `INFO`.
pub fn configure_logging(level: &str) {
    let level = level.parse().unwrap_or(tracing::Level::INFO);
    let _ = tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .with_max_level(level)
        .event_format(RedactingFormat)
        .try_init();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn credentials_never_survive() {
        let line = redact(
            "values='visitor' Bearer secret-value eyJaaa.bbb.ccc password=hidden-value \
             PASSWORD = trace-value",
        );
        for secret in [
            "secret-value",
            "eyJaaa.bbb.ccc",
            "hidden-value",
            "trace-value",
        ] {
            assert!(!line.contains(secret), "{secret} in {line}");
        }
        assert!(line.contains("visitor"));
        assert!(line.contains("Bearer [redacted]"));
    }
}
