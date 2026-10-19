//! Log lines carry the request fields and never a credential.

mod common;

use std::io::Write;
use std::sync::{Arc, Mutex};

use mosaiq_kit::logging::{scope, KitFormat, LogContext};

#[derive(Clone, Default)]
struct Capture(Arc<Mutex<Vec<u8>>>);

impl Write for Capture {
    fn write(&mut self, buffer: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(buffer);
        Ok(buffer.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

#[tokio::test]
async fn logging_redacts_credentials_and_adds_request_fields() {
    let capture = Capture::default();
    let writer = capture.clone();
    let subscriber = tracing_subscriber::fmt()
        .event_format(KitFormat)
        .with_writer(move || writer.clone())
        .finish();
    let _guard = tracing::subscriber::set_default(subscriber);
    let context = LogContext {
        correlation_id: "corr-log".to_owned(),
        actor: "actor-1".to_owned(),
        client: "198.51.100.8".to_owned(),
    };
    scope(context, async {
        tracing::warn!(
            "values={:?} {} {} {}",
            "visitor",
            "Bearer secret-value",
            "eyJaaa.bbb.ccc",
            "password=hidden-value"
        );
        let failure = std::io::Error::other("password=trace-value");
        tracing::error!(error = %failure, "A visitor-triggered failure");
    })
    .await;
    let lines = String::from_utf8(capture.0.lock().unwrap().clone()).unwrap();
    assert!(
        lines.contains("correlation=corr-log actor=actor-1 client=198.51.100.8"),
        "{lines}"
    );
    for secret in [
        "secret-value",
        "eyJaaa.bbb.ccc",
        "hidden-value",
        "trace-value",
    ] {
        assert!(!lines.contains(secret), "{lines}");
    }
}
