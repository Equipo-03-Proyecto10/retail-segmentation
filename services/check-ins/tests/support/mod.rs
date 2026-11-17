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
pub const SALES_ASSOCIATE: &str = "11111111-1111-1111-1111-000000000031";

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

/// A throwaway customer with one active loyalty card and optional PROFILING
/// state. Consent rows are append-only, so the unique customer stays after a
/// test; visits are removed by the store fixture.
pub struct TestCustomer {
    pub id: Uuid,
    pub code: String,
}

impl TestCustomer {
    pub async fn create(owner: &Client, decision: Option<&str>) -> Self {
        let id = Uuid::new_v4();
        let code = id.simple().to_string()[..26].to_ascii_uppercase();
        owner
            .execute(
                "INSERT INTO public.customer \
                 (customer_id, name, email, registration_channel_id, registered_on) \
                 VALUES ($1, $2, $3, 1, CURRENT_DATE)",
                &[
                    &id,
                    &format!("visit-test-{id}"),
                    &format!("visit-{id}@test.invalid"),
                ],
            )
            .await
            .unwrap();
        owner
            .execute(
                "INSERT INTO customers.loyalty_card \
                 (customer_id, code, issued_by) VALUES ($1, $2, $3::text::uuid)",
                &[&id, &code, &ADMIN],
            )
            .await
            .unwrap();
        if let Some(decision) = decision {
            owner
                .execute(
                    "INSERT INTO preferences.consent_decision \
                     (customer_id, purpose, decision, recorded_by, source, basis) \
                     VALUES ($1, 'PROFILING', $2, $3::text::uuid, \
                             'STAFF_ON_BEHALF', 'EXPLICIT')",
                    &[&id, &decision, &ADMIN],
                )
                .await
                .unwrap();
        }
        Self { id, code }
    }

    pub async fn decide(&self, owner: &Client, decision: &str) {
        owner
            .execute(
                "INSERT INTO preferences.consent_decision \
                 (customer_id, purpose, decision, recorded_by, source, basis) \
                 VALUES ($1, 'PROFILING', $2, $3::text::uuid, \
                         'STAFF_ON_BEHALF', 'EXPLICIT')",
                &[&self.id, &decision, &ADMIN],
            )
            .await
            .unwrap();
    }

    pub async fn revoke_card(&self, owner: &Client) {
        owner
            .execute(
                "UPDATE customers.loyalty_card \
                 SET status = 'REVOKED', revoked_at = now(), \
                     revoked_by = $2::text::uuid, revocation_reason = 'OTHER' \
                 WHERE customer_id = $1 AND status = 'ACTIVE'",
                &[&self.id, &ADMIN],
            )
            .await
            .unwrap();
    }
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
        let store = Self { id };
        // The seed's manager works here, as every older case assumes (ADR-0060).
        store.assign(owner, STORE_MANAGER).await;
        store
    }

    /// `user` is assigned to this store (a no-op when already).
    pub async fn assign(&self, owner: &Client, user: &str) {
        owner
            .execute(
                "INSERT INTO users.store_assignment (user_id, store_id) \
                 VALUES ($1::text::uuid, $2) ON CONFLICT DO NOTHING",
                &[&user, &self.id],
            )
            .await
            .unwrap();
    }

    /// `user` is no longer assigned to this store.
    pub async fn unassign(&self, owner: &Client, user: &str) {
        owner
            .execute(
                "DELETE FROM users.store_assignment \
                 WHERE user_id = $1::text::uuid AND store_id = $2",
                &[&user, &self.id],
            )
            .await
            .unwrap();
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
                "DELETE FROM checkins.customer_visit WHERE store_id = $1",
                &[&self.id],
            )
            .await
            .unwrap();
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
            .execute(
                "DELETE FROM users.store_assignment WHERE store_id = $1",
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
