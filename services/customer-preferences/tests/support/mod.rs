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

/// The seeded sole administrator: a real `app_user`, so a decision or a change
/// recorded as them satisfies the foreign key to the user table.
pub fn admin() -> Uuid {
    Uuid::parse_str("11111111-1111-1111-1111-000000000001").unwrap()
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

    /// Record a sale of one unit of each product, `days_ago` days back, at the
    /// price of 10.00, and return its id. The sale's total must equal its lines
    /// at commit, so both are written in one transaction.
    pub async fn sale(&self, owner: &Client, days_ago: i32, products: &[i32]) -> i64 {
        owner.batch_execute("BEGIN").await.unwrap();
        let total = rust_decimal_text(products.len());
        let id: i64 = owner
            .query_one(
                "INSERT INTO public.transaction (source_transaction_id, customer_id, store_id, \
                     channel_id, occurred_at, total, data_origin) \
                 SELECT $1, $2, (SELECT min(store_id) FROM public.store), \
                        (SELECT min(channel_id) FROM public.channel), \
                        now() - make_interval(days => $3::int), $4::text::numeric, 'OBSERVED' \
                 RETURNING transaction_id",
                &[
                    &format!("infer-test-{}", Uuid::new_v4()),
                    &self.id,
                    &days_ago,
                    &total,
                ],
            )
            .await
            .unwrap()
            .get(0);
        for product in products {
            owner
                .execute(
                    "INSERT INTO public.transaction_line (transaction_id, product_id, quantity, \
                         unit_price) VALUES ($1, $2, 1, 10.00)",
                    &[&id, product],
                )
                .await
                .unwrap();
        }
        owner.batch_execute("COMMIT").await.unwrap();
        id
    }

    /// Return every unit of a sale, as the reversal contract leaves it. The
    /// reversal rows that normally accompany it are not needed by the view, so
    /// the guard triggers are switched off for this one fixture transaction.
    pub async fn return_all(&self, owner: &Client, sale: i64) {
        owner.batch_execute("BEGIN").await.unwrap();
        owner
            .batch_execute("SET LOCAL session_replication_role = replica")
            .await
            .unwrap();
        owner
            .execute(
                "UPDATE public.transaction_line SET returned_quantity = quantity \
                 WHERE transaction_id = $1",
                &[&sale],
            )
            .await
            .unwrap();
        owner.batch_execute("COMMIT").await.unwrap();
    }

    /// The inferred interests stored for this customer, as the owner sees them:
    /// (category, strength, window), strongest first.
    pub async fn stored(&self, owner: &Client) -> Vec<(i16, i16, i16)> {
        owner
            .query(
                "SELECT category_id, strength, window_days FROM preferences.inferred_interest \
                 WHERE customer_id = $1 ORDER BY strength DESC, category_id",
                &[&self.id],
            )
            .await
            .unwrap()
            .iter()
            .map(|row| (row.get(0), row.get(1), row.get(2)))
            .collect()
    }

    /// Remove the customer and everything recorded about it. The logs are
    /// append-only for every role and a customer is never deleted, so this
    /// runs as a superuser under `session_replication_role = replica`, which
    /// switches the guard triggers and the foreign keys off for the one
    /// transaction. Nothing but a throwaway fixture is ever removed this way.
    pub async fn cleanup(self, owner: &Client) {
        let id = self.id;
        owner.batch_execute("BEGIN").await.unwrap();
        owner
            .batch_execute("SET LOCAL session_replication_role = replica")
            .await
            .unwrap();
        for statement in [
            "DELETE FROM preferences.inferred_interest WHERE customer_id = $1",
            "DELETE FROM sales_events.outbox WHERE entity_id IN \
             (SELECT transaction_id::text FROM public.transaction WHERE customer_id = $1)",
            "DELETE FROM public.transaction_line WHERE transaction_id IN \
             (SELECT transaction_id FROM public.transaction WHERE customer_id = $1)",
            "DELETE FROM public.transaction WHERE customer_id = $1",
            "DELETE FROM preferences.preference_change_member WHERE change_id IN \
             (SELECT change_id FROM preferences.preference_change WHERE customer_id = $1)",
            "DELETE FROM preferences.preference_change WHERE customer_id = $1",
            "DELETE FROM preferences.consent_decision WHERE customer_id = $1",
            "DELETE FROM preferences.restriction_decision WHERE customer_id = $1",
            "DELETE FROM public.customer WHERE customer_id = $1",
        ] {
            owner.execute(statement, &[&id]).await.unwrap();
        }
        owner
            .execute(
                "DELETE FROM preferences_events.outbox WHERE entity_id = $1",
                &[&id.to_string()],
            )
            .await
            .unwrap();
        owner.batch_execute("COMMIT").await.unwrap();
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

/// The total of `lines` one-unit lines at 10.00, as the decimal text a
/// `numeric` parameter takes.
fn rust_decimal_text(lines: usize) -> String {
    format!("{}.00", 10 * lines)
}

/// The product ids of a category, ascending.
pub async fn products_of(owner: &Client, category: i16) -> Vec<i32> {
    owner
        .query(
            "SELECT product_id FROM public.product WHERE category_id = $1 ORDER BY product_id",
            &[&category],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get(0))
        .collect()
}

/// The first three categories that hold at least two products each.
pub async fn stocked_categories(owner: &Client) -> [i16; 3] {
    let ids: Vec<i16> = owner
        .query(
            "SELECT category_id FROM public.product GROUP BY category_id \
             HAVING count(*) >= 2 ORDER BY category_id LIMIT 3",
            &[],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get(0))
        .collect();
    ids.try_into().expect("three categories with two products")
}
