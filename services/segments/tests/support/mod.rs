//! Shared fixtures: the seeded PostgreSQL of TEST_DATABASE_URL as its owner and
//! as `svc_segments`, and the Redis of TEST_REDIS_URL as its admin. A missing
//! URL fails the test: nothing here skips.
#![allow(dead_code)]

use std::time::Duration;

use mosaiq_kit::Database;
use redis::aio::MultiplexedConnection;
use tokio_postgres::{Client, NoTls};
use uuid::Uuid;

pub const ACL: &str = "redis/segments.acl";

/// The owner URL. Absent is a failure, never a skip.
pub fn owner_url() -> String {
    match std::env::var("TEST_DATABASE_URL") {
        Ok(url) if !url.is_empty() => url,
        _ => panic!("TEST_DATABASE_URL is required; these tests never skip"),
    }
}

pub fn redis_url() -> String {
    match std::env::var("TEST_REDIS_URL") {
        Ok(url) if !url.is_empty() => url,
        _ => panic!("TEST_REDIS_URL is required; these tests never skip"),
    }
}

/// The same database as `svc_segments` (its password is its name).
pub fn service_url(owner: &str) -> String {
    let (scheme, rest) = owner.split_once("://").expect("a database URL");
    let after_user = rest.split_once('@').map_or(rest, |(_, host)| host);
    format!("{scheme}://svc_segments:svc_segments@{after_user}")
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

pub async fn admin(url: &str) -> MultiplexedConnection {
    redis::Client::open(url)
        .unwrap()
        .get_multiplexed_async_connection()
        .await
        .expect("Redis is reachable")
}

/// Load the committed `svc_segments` line under a throwaway password and give
/// the local Redis the append-only file WAITAOF needs.
pub async fn load_acl(admin: &mut MultiplexedConnection) -> String {
    let password = Uuid::new_v4().simple().to_string();
    let line = std::fs::read_to_string(ACL).unwrap();
    let tokens = acl_tokens(line.trim());
    assert_eq!(&tokens[..3], ["user", "svc_segments", "on"]);
    let _: () = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_segments")
        .query_async(admin)
        .await
        .unwrap();
    let mut setuser = redis::cmd("ACL");
    setuser.arg("SETUSER").arg("svc_segments").arg("on");
    for token in &tokens[3..] {
        if token == "{{password}}" {
            setuser.arg(format!(">{password}"));
        } else {
            setuser.arg(token);
        }
    }
    let _: () = setuser.query_async(admin).await.unwrap();
    let _: () = redis::cmd("CONFIG")
        .arg("SET")
        .arg("appendonly")
        .arg("yes")
        .query_async(admin)
        .await
        .unwrap();
    password
}

pub async fn drop_acl(admin: &mut MultiplexedConnection) {
    let _: () = redis::cmd("ACL")
        .arg("DELUSER")
        .arg("svc_segments")
        .query_async(admin)
        .await
        .unwrap();
}

/// ACL tokens, keeping a parenthesised selector as one token.
fn acl_tokens(line: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut current = String::new();
    let mut depth = 0;
    for character in line.chars() {
        match character {
            '(' => {
                depth += 1;
                current.push(character);
            }
            ')' => {
                depth -= 1;
                current.push(character);
            }
            ' ' if depth == 0 => {
                if !current.is_empty() {
                    tokens.push(std::mem::take(&mut current));
                }
            }
            other => current.push(other),
        }
    }
    if !current.is_empty() {
        tokens.push(current);
    }
    tokens
}

pub async fn service_redis(url: &str, password: &str) -> MultiplexedConnection {
    let info =
        mosaiq_kit::service_redis_info(url, segments::SUFFIX, Some(password.to_owned())).unwrap();
    redis::Client::open(info)
        .unwrap()
        .get_multiplexed_async_connection()
        .await
        .unwrap()
}

pub fn service_cache(url: &str, password: &str) -> segments::Cache {
    let info =
        mosaiq_kit::service_redis_info(url, segments::SUFFIX, Some(password.to_owned())).unwrap();
    segments::Cache::new(redis::Client::open(info).unwrap())
}

/// A throwaway customer, removed with its history on `cleanup`.
pub struct TestCustomer {
    pub id: Uuid,
}

impl TestCustomer {
    /// A customer no run has seen: `no_open_assignment`.
    pub async fn create(owner: &Client) -> Self {
        let id = Uuid::new_v4();
        owner
            .execute(
                "INSERT INTO public.customer (customer_id, name, registration_channel_id) \
                 VALUES ($1, $2, (SELECT min(channel_id) FROM public.channel))",
                &[&id, &format!("svc-test-{id}")],
            )
            .await
            .unwrap();
        Self { id }
    }

    /// A customer whose latest run left it without a label: `unassigned_result`.
    pub async fn create_unassigned(owner: &Client) -> Self {
        let customer = Self::create(owner).await;
        owner
            .execute(
                "INSERT INTO public.customer_segment_history (customer_id, run_id) \
                 SELECT $1, max(run_id) FROM public.segmentation_run",
                &[&customer.id],
            )
            .await
            .unwrap();
        customer
    }

    pub async fn cleanup(self, owner: &Client) {
        owner
            .execute(
                "DELETE FROM public.customer WHERE customer_id = $1",
                &[&self.id],
            )
            .await
            .unwrap();
    }
}

/// A run recorded the way the web records one: insert the run, close every
/// open row, open a new row per customer; all as `retail_app`, one transaction.
///
/// It is never undone. Reopening a closed row is what the close-only trigger
/// forbids, and switching that trigger off would be DDL outside
/// sql/01_schema.sql; every test reads what it expects from the database as it
/// is, so a later run left behind changes nothing they assert.
pub struct RecordedRun {
    pub run_id: i64,
}

impl RecordedRun {
    pub async fn record(owner: &mut Client) -> Self {
        let transaction = owner.transaction().await.unwrap();
        transaction
            .batch_execute("SET LOCAL ROLE retail_app")
            .await
            .unwrap();
        let run_id: i64 = transaction
            .query_one(
                "INSERT INTO public.segmentation_run \
                 (method, window_days, parameters, customer_count, run_at, data_origin) \
                 VALUES ('RFM_RULES', 30, '{}'::jsonb, 30, now(), \
                         fn_segmentation_window_origin(30)) \
                 RETURNING run_id",
                &[],
            )
            .await
            .unwrap()
            .get(0);
        transaction
            .execute(
                "UPDATE public.customer_segment_history SET valid_to = now() \
                 WHERE valid_to IS NULL",
                &[],
            )
            .await
            .unwrap();
        // Every fifth customer is left without a label; the rest cycle the
        // vocabulary, so sizes and the unassigned count differ from the old run.
        transaction
            .execute(
                "WITH l AS (SELECT array_agg(label_code ORDER BY ordinal_position) AS codes \
                            FROM public.segment_label), \
                      c AS (SELECT customer_id, row_number() OVER (ORDER BY customer_id) AS n \
                            FROM public.customer) \
                 INSERT INTO public.customer_segment_history \
                     (customer_id, run_id, label_code, r_score, f_score, m_score, valid_from) \
                 SELECT c.customer_id, $1, \
                        CASE WHEN c.n % 5 = 0 THEN NULL \
                             ELSE l.codes[1 + (c.n % cardinality(l.codes))::int] END, \
                        CASE WHEN c.n % 5 = 0 THEN NULL ELSE 3::smallint END, \
                        CASE WHEN c.n % 5 = 0 THEN NULL ELSE 4::smallint END, \
                        CASE WHEN c.n % 5 = 0 THEN NULL ELSE 5::smallint END, \
                        now() \
                 FROM c CROSS JOIN l",
                &[&run_id],
            )
            .await
            .unwrap();
        transaction.commit().await.unwrap();
        Self { run_id }
    }
}

/// Seeded label sizes of the latest run, from the owner, to compare against.
pub async fn expected_sizes(owner: &Client) -> Vec<(String, i64)> {
    owner
        .query(
            "SELECT label_code, count(*) FROM public.customer_segment_history \
             WHERE run_id = (SELECT max(run_id) FROM public.segmentation_run) \
               AND label_code IS NOT NULL \
             GROUP BY label_code ORDER BY label_code",
            &[],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| (row.get(0), row.get(1)))
        .collect()
}
