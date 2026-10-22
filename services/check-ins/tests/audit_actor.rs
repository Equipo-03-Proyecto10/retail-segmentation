//! Every write names its caller to PostgreSQL (ADR-0034): the transaction has
//! run `set_config('mosaiq.user_id', $1, true)` with the token's `sub` bound.

mod support;

use std::fs;

use check_ins::write_transaction;
use support::{connect, owner_url, principal, service_database, ADMIN, STORE_MANAGER};

#[tokio::test]
async fn a_write_transaction_carries_the_callers_sub() {
    let Some(url) = owner_url() else { return };
    let _owner = connect(&url).await;
    let db = service_database(&url);
    for (sub, role) in [(STORE_MANAGER, "STORE_MANAGER"), (ADMIN, "ADMIN")] {
        let mut connection = db.connection().await.unwrap();
        let transaction = write_transaction(&mut connection, &principal(sub, role))
            .await
            .unwrap();
        let actor: Option<String> = transaction
            .query_one("SELECT current_setting('mosaiq.user_id', true)", &[])
            .await
            .unwrap()
            .get(0);
        assert_eq!(actor.as_deref(), Some(sub));
        transaction.rollback().await.unwrap();
        // Local to the transaction: the pooled connection does not keep it.
        let after: Option<String> = connection
            .query_one("SELECT current_setting('mosaiq.user_id', true)", &[])
            .await
            .unwrap()
            .get(0);
        assert!(after.unwrap_or_default().is_empty());
    }
}

/// Both writes, the check-in and the review, open their transaction there.
#[test]
fn every_write_opens_the_actor_transaction() {
    let source = fs::read_to_string("src/lib.rs").expect("service source");
    assert_eq!(
        source
            .matches("write_transaction(&mut connection, actor)")
            .count(),
        2
    );
    assert_eq!(source.matches(".transaction(").count(), 1);
}
