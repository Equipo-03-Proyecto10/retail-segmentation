//! Shared fixtures: the seeded PostgreSQL of TEST_DATABASE_URL, as its owner
//! and as `svc_audit_trail`.
#![allow(dead_code)]

use std::time::Duration;

use mosaiq_kit::Database;
use tokio_postgres::{Client, NoTls};

/// The seed's one administrator.
pub const ADMIN: &str = "11111111-1111-1111-1111-000000000001";

/// The owner URL, or None (the test then skips) when none is configured.
pub fn owner_url() -> Option<String> {
    match std::env::var("TEST_DATABASE_URL") {
        Ok(url) if !url.is_empty() => Some(url),
        _ => {
            if std::env::var("MOSAIQ_REQUIRE_POSTGRES").as_deref() == Ok("1") {
                panic!(
                    "TEST_DATABASE_URL is missing, and MOSAIQ_REQUIRE_POSTGRES forbids skipping"
                );
            }
            eprintln!("skipped: TEST_DATABASE_URL is not set");
            None
        }
    }
}

/// The same database as `svc_audit_trail` (its password is its name).
pub fn service_url(owner: &str) -> String {
    let (scheme, rest) = owner.split_once("://").expect("a database URL");
    let after_user = rest.split_once('@').map_or(rest, |(_, host)| host);
    format!("{scheme}://svc_audit_trail:svc_audit_trail@{after_user}")
}

pub async fn connect(url: &str) -> Client {
    let (client, connection) = tokio_postgres::connect(url, NoTls)
        .await
        .expect("PostgreSQL is reachable");
    tokio::spawn(connection);
    client
}

pub fn service_database(owner: &str) -> Database {
    Database::new(
        Some(&service_url(owner)),
        Duration::from_secs(2),
        Duration::from_secs(2),
    )
}

/// Rows a test adds to `audit_log` carry this entity key, so they can be found
/// and removed without touching the seed's.
pub async fn remove_test_rows(owner: &Client, key: &str) {
    owner
        .execute("DELETE FROM public.audit_log WHERE entity_pk = $1", &[&key])
        .await
        .unwrap();
}
