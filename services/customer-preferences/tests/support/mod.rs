//! Shared fixtures: the seeded PostgreSQL of TEST_DATABASE_URL, as its owner
//! and as `svc_preferences`.
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

/// The same database as `svc_preferences` (its local password is its name).
pub fn service_url(owner: &str) -> String {
    let (scheme, rest) = owner.split_once("://").expect("a database URL");
    let after_user = rest.split_once('@').map_or(rest, |(_, host)| host);
    format!("{scheme}://svc_preferences:svc_preferences@{after_user}")
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
        Duration::from_secs(5),
        Duration::from_secs(2),
    )
}

pub fn principal(sub: Uuid, role: &str) -> Principal {
    Principal {
        sub: sub.to_string(),
        sid: Uuid::new_v4().to_string(),
        role: role.to_owned(),
        claims: Map::new(),
    }
}

/// Every seeded id of a small catalog, ascending, read as the owner.
pub async fn ids(owner: &Client, statement: &str) -> Vec<i16> {
    owner
        .query(statement, &[])
        .await
        .unwrap()
        .iter()
        .map(|row| row.get(0))
        .collect()
}

pub async fn channel_ids(owner: &Client) -> Vec<i16> {
    ids(
        owner,
        "SELECT channel_id FROM public.channel ORDER BY channel_id",
    )
    .await
}

pub async fn category_ids(owner: &Client) -> Vec<i16> {
    ids(
        owner,
        "SELECT category_id FROM public.category ORDER BY category_id",
    )
    .await
}

/// A smallint id no row of `public.<table>` has.
pub async fn missing_id(owner: &Client, statement: &str) -> i16 {
    owner.query_one(statement, &[]).await.unwrap().get(0)
}

pub async fn missing_channel(owner: &Client) -> i16 {
    missing_id(
        owner,
        "SELECT (COALESCE(max(channel_id), 0) + 100)::smallint FROM public.channel",
    )
    .await
}

pub async fn missing_category(owner: &Client) -> i16 {
    missing_id(
        owner,
        "SELECT (COALESCE(max(category_id), 0) + 100)::smallint FROM public.category",
    )
    .await
}

/// A throwaway customer with no preferences, removed with them on `cleanup`.
pub struct TestCustomer {
    pub id: Uuid,
}

impl TestCustomer {
    pub async fn create(owner: &Client) -> Self {
        let id = Uuid::new_v4();
        owner
            .execute(
                "INSERT INTO public.customer (customer_id, name, registration_channel_id) \
                 SELECT $1, $2, min(channel_id) FROM public.channel",
                &[&id, &format!("prefs-test-{id}")],
            )
            .await
            .unwrap();
        Self { id }
    }

    pub async fn channels(&self, owner: &Client) -> Vec<i16> {
        ids_of(
            owner,
            "SELECT channel_id FROM public.customer_preferred_channel \
             WHERE customer_id = $1 ORDER BY channel_id",
            self.id,
        )
        .await
    }

    pub async fn categories(&self, owner: &Client) -> Vec<i16> {
        ids_of(
            owner,
            "SELECT category_id FROM public.customer_interest_category \
             WHERE customer_id = $1 ORDER BY category_id",
            self.id,
        )
        .await
    }

    /// Outbox rows captured for this customer, as (event_type, stream).
    pub async fn events(&self, owner: &Client) -> Vec<(String, String)> {
        owner
            .query(
                "SELECT event_type, stream FROM preferences_events.outbox \
                 WHERE entity_id = $1 ORDER BY occurred_at, event_type",
                &[&self.id.to_string()],
            )
            .await
            .unwrap()
            .iter()
            .map(|row| (row.get(0), row.get(1)))
            .collect()
    }

    pub async fn cleanup(self, owner: &Client) {
        owner
            .execute(
                "DELETE FROM public.customer WHERE customer_id = $1",
                &[&self.id],
            )
            .await
            .unwrap();
        owner
            .execute(
                "DELETE FROM preferences_events.outbox WHERE entity_id = $1",
                &[&self.id.to_string()],
            )
            .await
            .unwrap();
    }
}

async fn ids_of(owner: &Client, statement: &str, customer: Uuid) -> Vec<i16> {
    owner
        .query(statement, &[&customer])
        .await
        .unwrap()
        .iter()
        .map(|row| row.get(0))
        .collect()
}
