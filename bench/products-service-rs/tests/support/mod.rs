//! The seeded PostgreSQL of TEST_DATABASE_URL, as `svc_products_bench`.
#![allow(dead_code)]

use std::time::Duration;

use mosaiq_kit::Database;

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

/// The same database as `svc_products_bench` (its password is its name).
pub fn bench_database(owner: &str) -> Database {
    let (scheme, rest) = owner.split_once("://").expect("a database URL");
    let after_user = rest.split_once('@').map_or(rest, |(_, host)| host);
    let url = format!("{scheme}://svc_products_bench:svc_products_bench@{after_user}");
    Database::new(Some(&url), Duration::from_secs(2), Duration::from_secs(2))
}
