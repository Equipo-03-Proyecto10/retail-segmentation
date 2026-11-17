//! Shared fixtures: the seeded PostgreSQL of TEST_DATABASE_URL, as its owner
//! and as `svc_kpis`, and the sales the tests record.
#![allow(dead_code)]

pub mod http;

use tokio_postgres::{Client, NoTls};
use uuid::Uuid;

/// The seed's one administrator.
pub const ADMIN: &str = "11111111-1111-1111-1111-000000000001";
/// The seed's store manager of stores 1, 9, 17 and 25.
pub const MANAGER: &str = "11111111-1111-1111-1111-000000000003";
/// The seed's analyst: a user with no store assignment.
pub const NO_STORES: &str = "11111111-1111-1111-1111-000000000002";
pub const MANAGER_STORES: [i32; 4] = [1, 9, 17, 25];

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

/// The same database as `svc_kpis` (its password is its name).
pub fn service_url(owner: &str) -> String {
    let (scheme, rest) = owner.split_once("://").expect("a database URL");
    let after_user = rest.split_once('@').map_or(rest, |(_, host)| host);
    format!("{scheme}://svc_kpis:svc_kpis@{after_user}")
}

pub async fn connect(url: &str) -> Client {
    let (client, connection) = tokio_postgres::connect(url, NoTls)
        .await
        .expect("PostgreSQL is reachable");
    tokio::spawn(connection);
    client
}

/// An amount in cents, written as the two-place text the database takes.
pub fn decimal_text(cents: i64) -> String {
    format!("{}.{:02}", cents / 100, cents % 100)
}

/// One line of a recorded sale: product, units sold and unit price in cents.
#[derive(Clone, Copy)]
pub struct Line {
    pub product: i32,
    pub quantity: i32,
    pub cents: i64,
}

/// Everything a test records carries this tag in its `source_transaction_id`,
/// so `remove_sales` can take it out again without touching the seed.
pub fn tag() -> String {
    format!("kpi-{}", &Uuid::new_v4().simple().to_string()[..12])
}

/// A throwaway customer, with no segment assignment.
pub async fn customer(owner: &Client) -> Uuid {
    let id = Uuid::new_v4();
    owner
        .execute(
            "INSERT INTO public.customer (customer_id, name, registration_channel_id) \
             SELECT $1, $2, min(channel_id) FROM public.channel",
            &[&id, &format!("kpi-test-{id}")],
        )
        .await
        .unwrap();
    id
}

pub async fn remove_customer(owner: &Client, id: Uuid) {
    owner
        .execute("DELETE FROM public.customer WHERE customer_id = $1", &[&id])
        .await
        .unwrap();
}

/// Where, when, by whom and from what origin a sale is recorded.
pub struct Spec<'a> {
    pub customer: Uuid,
    pub store: i16,
    pub channel: i16,
    /// An RFC 3339 instant.
    pub at: &'a str,
    pub origin: &'a str,
}

impl<'a> Spec<'a> {
    pub fn new(customer: Uuid, store: i16, channel: i16, at: &'a str, origin: &'a str) -> Self {
        Self {
            customer,
            store,
            channel,
            at,
            origin,
        }
    }
}

/// Record a sale and return its id. The sale's total must equal its lines at
/// commit, so both go in one transaction.
pub async fn sale(owner: &Client, tag: &str, spec: Spec<'_>, lines: &[Line]) -> i64 {
    let total: i64 = lines
        .iter()
        .map(|line| i64::from(line.quantity) * line.cents)
        .sum();
    owner.batch_execute("BEGIN").await.unwrap();
    let id: i64 = owner
        .query_one(
            "INSERT INTO public.transaction (source_transaction_id, customer_id, store_id, \
                 channel_id, occurred_at, total, data_origin) \
             VALUES ($1, $2, $3, $4, $5::text::timestamptz, $6::text::numeric, $7) \
             RETURNING transaction_id",
            &[
                &format!("{tag}-{}", &Uuid::new_v4().simple().to_string()[..12]),
                &spec.customer,
                &spec.store,
                &spec.channel,
                &spec.at,
                &decimal_text(total),
                &spec.origin,
            ],
        )
        .await
        .unwrap()
        .get(0);
    for line in lines {
        owner
            .execute(
                "INSERT INTO public.transaction_line (transaction_id, product_id, quantity, \
                     unit_price) VALUES ($1, $2, $3, $4::text::numeric)",
                &[
                    &id,
                    &line.product,
                    &line.quantity,
                    &decimal_text(line.cents),
                ],
            )
            .await
            .unwrap();
    }
    owner.batch_execute("COMMIT").await.unwrap();
    id
}

/// Reverse `units` of the line of `product`, as the reversal contract leaves
/// it: the line's `returned_quantity` is raised. The reversal rows that
/// normally accompany it are not needed by the views, so the guard triggers
/// are switched off for this one fixture transaction.
pub async fn reverse(owner: &Client, sale: i64, product: i32, units: i32) {
    owner.batch_execute("BEGIN").await.unwrap();
    owner
        .batch_execute("SET LOCAL session_replication_role = replica")
        .await
        .unwrap();
    owner
        .execute(
            "UPDATE public.transaction_line SET returned_quantity = returned_quantity + $3 \
             WHERE transaction_id = $1 AND product_id = $2",
            &[&sale, &product, &units],
        )
        .await
        .unwrap();
    owner.batch_execute("COMMIT").await.unwrap();
}

pub async fn remove_sales(owner: &Client, tag: &str) {
    owner
        .execute(
            "DELETE FROM public.transaction WHERE source_transaction_id LIKE $1",
            &[&format!("{tag}-%")],
        )
        .await
        .unwrap();
}

/// Remove every sale and customer any test of this service recorded.
pub async fn purge(owner: &Client) {
    owner
        .execute(
            "DELETE FROM public.transaction WHERE source_transaction_id LIKE 'kpi-%'",
            &[],
        )
        .await
        .unwrap();
    owner
        .execute(
            "DELETE FROM public.customer WHERE name LIKE 'kpi-test-%'",
            &[],
        )
        .await
        .unwrap();
}
