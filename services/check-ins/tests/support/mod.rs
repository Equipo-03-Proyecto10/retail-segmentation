//! Shared fixtures: the seeded PostgreSQL of TEST_DATABASE_URL, as its owner
//! and as `svc_checkins`.
#![allow(dead_code)]

use std::time::Duration;

use chrono::{DateTime, Utc};
use mosaiq_kit::{Database, Principal};
use rust_decimal::Decimal;
use serde_json::Map;
use tokio_postgres::{Client, NoTls};
use uuid::Uuid;

/// The seed's one administrator and one of its five store managers.
pub const ADMIN: &str = "11111111-1111-1111-1111-000000000001";
pub const STORE_MANAGER: &str = "11111111-1111-1111-1111-000000000003";

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

/// The same database as `svc_checkins` (its password is its name).
pub fn service_url(owner: &str) -> String {
    let (scheme, rest) = owner.split_once("://").expect("a database URL");
    let after_user = rest.split_once('@').map_or(rest, |(_, host)| host);
    format!("{scheme}://svc_checkins:svc_checkins@{after_user}")
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

pub fn principal(sub: &str, role: &str) -> Principal {
    Principal {
        sub: sub.to_owned(),
        sid: Uuid::new_v4().to_string(),
        role: role.to_owned(),
        claims: Map::new(),
    }
}

pub fn dec(text: &str) -> Decimal {
    text.parse().unwrap()
}

/// A moment safely in the past, to the second, so replays compare equal.
pub fn earlier() -> DateTime<Utc> {
    DateTime::from_timestamp(Utc::now().timestamp() - 600, 0).unwrap()
}

/// A throwaway store at (25.6866, -100.3161) with a 150 m geofence, or with
/// no location; removed with its check-ins and reviews on `cleanup`.
pub struct TestStore {
    pub id: i16,
}

impl TestStore {
    pub async fn create(owner: &Client, located: bool) -> Self {
        let id: i16 = 28_000 + (Uuid::new_v4().as_u128() % 2_000) as i16;
        owner
            .execute(
                "INSERT INTO public.store (store_id, name, city, state) \
                 VALUES ($1, $2, 'Testville', 'TS')",
                &[&id, &format!("check-in-test-{id}")],
            )
            .await
            .unwrap();
        if located {
            owner
                .execute(
                    "INSERT INTO stores.store_location \
                     (store_id, latitude, longitude, geofence_radius_m, set_by) \
                     VALUES ($1, 25.6866, -100.3161, 150, $2::text::uuid)",
                    &[&id, &ADMIN],
                )
                .await
                .unwrap();
        }
        Self { id }
    }

    pub async fn deactivate(&self, owner: &Client) {
        owner
            .execute(
                "UPDATE public.store SET is_active = FALSE WHERE store_id = $1",
                &[&self.id],
            )
            .await
            .unwrap();
    }

    pub async fn cleanup(self, owner: &Client) {
        owner
            .execute(
                "DELETE FROM checkins.check_in_review WHERE check_in_id IN \
                 (SELECT check_in_id FROM checkins.store_check_in WHERE store_id = $1)",
                &[&self.id],
            )
            .await
            .unwrap();
        owner
            .execute(
                "DELETE FROM checkins.store_check_in WHERE store_id = $1",
                &[&self.id],
            )
            .await
            .unwrap();
        owner
            .execute("DELETE FROM public.store WHERE store_id = $1", &[&self.id])
            .await
            .unwrap();
    }
}
