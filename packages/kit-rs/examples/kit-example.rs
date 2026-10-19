//! Minimal contract-driven service used by kit smoke and conformance tests.

use std::net::SocketAddr;
use std::sync::Arc;

use chrono::{DateTime, Utc};
use indexmap::IndexMap;
use mosaiq_kit::{
    configure_logging, money, ApiError, Ctx, Database, Kit, KitConfig, Page, Reply, Representation,
    Value,
};
use rust_decimal::Decimal;
use tokio_postgres::Row;

const SERVICE: &str = "kit-example";
const SUFFIX: &str = "kit_example";
const TOTAL: i32 = 45;

fn note(row: &Row) -> Result<IndexMap<String, Value>, ApiError> {
    let note_id: i32 = row.try_get(0)?;
    let title: String = row.try_get(1)?;
    let amount: Decimal = row.try_get(2)?;
    let created_by: Option<String> = row.try_get(3)?;
    let created_at: DateTime<Utc> = row.try_get(4)?;
    Ok(IndexMap::from([
        ("note_id".to_owned(), Value::from(note_id)),
        ("title".to_owned(), Value::from(title)),
        ("amount".to_owned(), money(amount)),
        ("created_by".to_owned(), Value::from(created_by)),
        ("created_at".to_owned(), Value::from(created_at)),
    ]))
}

async fn list_notes(ctx: Ctx) -> Result<Reply, ApiError> {
    let offset = i64::from(ctx.page - 1) * i64::from(ctx.size);
    let connection = ctx.db().connection().await?;
    let rows = connection
        .query(
            "SELECT value, $1 || value::text, (value::numeric / $2::integer), \
             NULL::text, now() FROM generate_series($3::integer, $4::integer) AS value \
             ORDER BY value LIMIT $5 OFFSET $6",
            &[
                &"Note ",
                &100i32,
                &1i32,
                &TOTAL,
                &i64::from(ctx.size),
                &offset,
            ],
        )
        .await?;
    let notes = rows.iter().map(note).collect::<Result<Vec<_>, _>>()?;
    Ok(
        Page::new("Notes", "Note", "notes", notes, ctx.page, TOTAL as u64)
            .size(ctx.size)
            .into(),
    )
}

async fn get_note(ctx: Ctx) -> Result<Reply, ApiError> {
    let note_id = ctx
        .path_i64("note_id")
        .and_then(|value| i32::try_from(value).ok())
        .ok_or_else(|| ApiError::new(404, "note_not_found", "The note does not exist."))?;
    let connection = ctx.db().connection().await?;
    let row = connection
        .query_opt(
            "SELECT $1::integer, $2 || $3::integer::text, ($4::integer::numeric / $5::integer), \
             NULL::text, now() WHERE $6::integer BETWEEN $7::integer AND $8::integer",
            &[
                &note_id, &"Note ", &note_id, &note_id, &100i32, &note_id, &1i32, &TOTAL,
            ],
        )
        .await?
        .ok_or_else(|| ApiError::new(404, "note_not_found", "The note does not exist."))?;
    Ok(Representation::new("Note", note(&row)?).into())
}

async fn create_note(ctx: Ctx) -> Result<Reply, ApiError> {
    let body = ctx.body.clone().unwrap_or_default();
    let mut connection = ctx.db().connection().await?;
    let transaction = connection.transaction(ctx.principal()).await?;
    let row = transaction
        .query_one(
            "SELECT current_setting($1, true), now()",
            &[&"mosaiq.user_id"],
        )
        .await?;
    let created_by: Option<String> = row.try_get(0)?;
    let created_at: DateTime<Utc> = row.try_get(1)?;
    transaction.commit().await?;
    Ok(Representation::new(
        "Note",
        [
            ("note_id", Value::from(46)),
            ("title", Value::from(&body["title"])),
            ("amount", Value::from(&body["amount"])),
            ("created_by", Value::from(created_by)),
            ("created_at", Value::from(created_at)),
        ],
    )
    .status(201)
    .header("Location", "/v1/notes/46")
    .into())
}

async fn database_ready(db: Arc<Database>) -> bool {
    let Ok(connection) = db.connection().await else {
        return false;
    };
    connection
        .query_one("SELECT $1::integer", &[&1i32])
        .await
        .is_ok_and(|row| row.try_get::<_, i32>(0).is_ok_and(|value| value == 1))
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    configure_logging(&std::env::var("LOG_LEVEL").unwrap_or_else(|_| "info".to_owned()));
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let router = Kit::new(SERVICE, SUFFIX, "contracts", KitConfig::from_env(SUFFIX))?
            .operation("listNotes", list_notes)?
            .operation("getNote", get_note)?
            .operation("createNote", create_note)?
            .ready_check(database_ready)
            .into_router();
        let listener = tokio::net::TcpListener::bind("0.0.0.0:8000").await?;
        axum::serve(
            listener,
            router.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await?;
        Ok(())
    })
}
