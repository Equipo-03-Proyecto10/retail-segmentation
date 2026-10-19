//! Pool size, audit actor and 503 `database_unavailable`, as kit-py's test_database.py.

mod common;

use std::sync::Arc;
use std::time::Duration;

use common::*;
use mosaiq_kit::{ApiError, Ctx, Database, Reply, Representation, Value};

async fn actor(ctx: Ctx) -> Result<Reply, ApiError> {
    let mut connection = ctx.db().connection().await?;
    let transaction = connection.transaction(ctx.principal()).await?;
    let row = transaction
        .query_one("SELECT current_setting($1, true)", &[&"mosaiq.user_id"])
        .await?;
    let actor: Option<String> = row.try_get(0)?;
    transaction.commit().await?;
    Ok(Representation::new("Note", [("actor", Value::from(actor))]).into())
}

#[tokio::test]
async fn pool_is_capped_at_three() {
    if redis_url().is_none() || database_url().is_none() {
        return;
    }
    let kit = kit(config().await);
    assert_eq!(kit.database().max_size(), 3);
}

#[tokio::test]
async fn transaction_sets_the_bound_audit_actor() {
    if redis_url().is_none() || database_url().is_none() {
        return;
    }
    let app = kit(config().await)
        .operation("createNote", actor)
        .unwrap()
        .into_router();
    let answer = call("POST", "/v1/notes")
        .bearer("valid_writer")
        .json(serde_json::json!({"title": "Audit", "amount": "1.00"}))
        .send(&app)
        .await;
    assert_eq!(answer.status, 200, "{}", answer.text());
    assert_eq!(answer.json()["actor"], WRITER_SUB);
}

async fn select_note(ctx: Ctx) -> Result<Reply, ApiError> {
    let note_id = ctx.path_i64("note_id").unwrap() as i32;
    let connection = ctx.db().connection().await?;
    connection
        .query_one("SELECT $1::integer", &[&note_id])
        .await?;
    Ok(Representation::new("Note", [("note_id", Value::from(note_id))]).into())
}

#[tokio::test]
async fn unreachable_database_uses_the_public_vector_expectation() {
    if redis_url().is_none() {
        return;
    }
    let mut config = config().await;
    config.database_url =
        Some("postgresql://service@127.0.0.1:1/retail?connect_timeout=1".to_owned());
    config.database_pool_timeout = Duration::from_millis(300);
    let app = kit(config)
        .operation("getNote", select_note)
        .unwrap()
        .into_router();
    let answer = call("GET", "/v1/notes/1")
        .bearer("valid_reader")
        .send(&app)
        .await;
    let wanted = &expected()["database_unavailable"];
    assert_eq!(answer.status.as_u16() as u64, wanted["status"]);
    assert_eq!(answer.code(), wanted["code"]);
}

#[tokio::test]
async fn no_database_url_is_unavailable_too() {
    if redis_url().is_none() {
        return;
    }
    let mut config = config().await;
    config.database_url = None;
    let app = kit(config)
        .operation("getNote", select_note)
        .unwrap()
        .into_router();
    let answer = call("GET", "/v1/notes/1")
        .bearer("valid_reader")
        .send(&app)
        .await;
    assert_eq!(answer.status, 503);
    assert_eq!(answer.code(), "database_unavailable");
}

async fn terminated(ctx: Ctx) -> Result<Reply, ApiError> {
    let note_id = ctx.path_i64("note_id").unwrap() as i32;
    let connection = ctx.db().connection().await?;
    let backend: i32 = connection
        .query_one("SELECT pg_backend_pid(), $1::integer", &[&note_id])
        .await?
        .get(0);
    let (killer, driver) = tokio_postgres::connect(
        &std::env::var("KIT_TEST_DATABASE_URL").unwrap(),
        tokio_postgres::NoTls,
    )
    .await
    .unwrap();
    tokio::spawn(driver);
    killer
        .execute("SELECT pg_terminate_backend($1)", &[&backend])
        .await
        .unwrap();
    connection
        .query_one("SELECT $1::integer", &[&note_id])
        .await?;
    Ok(Representation::new("Note", [("note_id", Value::from(note_id))]).into())
}

#[tokio::test]
async fn mid_request_connection_failure_is_not_a_500() {
    if redis_url().is_none() || database_url().is_none() {
        return;
    }
    let app = kit(config().await)
        .operation("getNote", terminated)
        .unwrap()
        .into_router();
    let answer = call("GET", "/v1/notes/1")
        .bearer("valid_reader")
        .send(&app)
        .await;
    assert_eq!(answer.status, 503, "{}", answer.text());
    assert_eq!(answer.code(), "database_unavailable");
    // The broken connection is discarded; the next request gets a fresh one.
    let app = kit(config().await)
        .operation("getNote", select_note)
        .unwrap()
        .into_router();
    assert_eq!(
        call("GET", "/v1/notes/1")
            .bearer("valid_reader")
            .send(&app)
            .await
            .status,
        200
    );
}

#[tokio::test]
async fn readiness_keeps_schema_not_ready_when_database_check_fails() {
    if redis_url().is_none() {
        return;
    }
    let mut config = config().await;
    config.database_url =
        Some("postgresql://service@127.0.0.1:1/retail?connect_timeout=1".to_owned());
    config.database_pool_timeout = Duration::from_millis(300);
    let app = kit(config)
        .ready_check(|db: Arc<Database>| async move {
            match db.connection().await {
                Ok(connection) => connection
                    .query_one("SELECT $1::integer", &[&1i32])
                    .await
                    .is_ok(),
                Err(_) => false,
            }
        })
        .into_router();
    let answer = call("GET", "/health/ready").send(&app).await;
    assert_eq!(answer.status, 503);
    assert_eq!(answer.code(), "schema_not_ready");
}
