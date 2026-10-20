//! Shared fixtures: the seeded PostgreSQL of TEST_DATABASE_URL, as its owner
//! and as `svc_stores`.
#![allow(dead_code)]

use std::time::Duration;

use mosaiq_kit::{Database, Principal};
use serde_json::Map;
use tokio_postgres::{Client, NoTls};
use uuid::Uuid;

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

/// The same database as `svc_stores` (its password is its name).
pub fn service_url(owner: &str) -> String {
    let (scheme, rest) = owner.split_once("://").expect("a database URL");
    let after_user = rest.split_once('@').map_or(rest, |(_, host)| host);
    format!("{scheme}://svc_stores:svc_stores@{after_user}")
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

pub fn principal(sub: Uuid) -> Principal {
    Principal {
        sub: sub.to_string(),
        sid: Uuid::new_v4().to_string(),
        role: "ADMIN".to_owned(),
        claims: Map::new(),
    }
}

/// Two distinct seeded users, so `set_by` is visibly the caller's.
pub async fn users(owner: &Client) -> (Uuid, Uuid) {
    let rows = owner
        .query(
            "SELECT user_id FROM app_user ORDER BY user_id DESC LIMIT 2",
            &[],
        )
        .await
        .unwrap();
    (rows[0].get(0), rows[1].get(0))
}

/// A throwaway store with no location, removed (with its location) on `cleanup`.
pub struct TestStore {
    pub id: i16,
}

impl TestStore {
    pub async fn create(owner: &Client) -> Self {
        let id: i16 = 30_000 + (Uuid::new_v4().as_u128() % 2_000) as i16;
        owner
            .execute(
                "INSERT INTO public.store (store_id, name, city, state) \
                 VALUES ($1, $2, 'Testville', 'TS')",
                &[&id, &format!("svc-test-{id}")],
            )
            .await
            .unwrap();
        Self { id }
    }

    pub async fn cleanup(self, owner: &Client) {
        owner
            .execute("DELETE FROM public.store WHERE store_id = $1", &[&self.id])
            .await
            .unwrap();
    }
}
