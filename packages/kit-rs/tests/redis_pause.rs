//! A slow `redis-state` fails closed; alone in its binary because
//! `CLIENT PAUSE` stalls every other client too.

mod common;

use std::time::{Duration, Instant};

use common::*;

#[tokio::test]
async fn slow_session_store_fails_closed() {
    let Some(app) = app().await else { return };
    // Warm the connection so the pause, not the handshake, is measured.
    assert_eq!(
        call("GET", "/v1/notes")
            .bearer("valid_reader")
            .send(&app)
            .await
            .status,
        200
    );
    let mut admin = admin().await;
    let _: String = redis::cmd("CLIENT")
        .arg("PAUSE")
        .arg(350)
        .arg("ALL")
        .query_async(&mut admin)
        .await
        .unwrap();
    let started = Instant::now();
    let answer = call("GET", "/v1/notes")
        .bearer("valid_reader")
        .send(&app)
        .await;
    let elapsed = started.elapsed();
    assert_eq!(answer.status, 503);
    assert_eq!(answer.code(), "auth_unavailable");
    assert!(elapsed < Duration::from_secs(1), "{elapsed:?}");
    tokio::time::sleep(Duration::from_millis(400)).await;
    assert_eq!(
        call("GET", "/v1/notes")
            .bearer("valid_reader")
            .send(&app)
            .await
            .status,
        200
    );
}
