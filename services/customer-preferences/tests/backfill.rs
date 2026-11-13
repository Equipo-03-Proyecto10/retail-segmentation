//! ADR-0066 section 1: the default given to customers that exist when the
//! consent log is created, and to no one else. The operator re-applies this
//! boundary's section of `sql/01_schema.sql` to the live database, so the
//! test does what the operator does: it drops the log inside a transaction,
//! applies the committed section, looks, and rolls everything back.
//!
//! It is the only test that drops a table, so it has its own test binary and
//! never runs beside another test of this crate.

mod support;

use support::{connect, owner_url};
use uuid::Uuid;

fn section() -> String {
    let schema = std::fs::read_to_string("../../sql/01_schema.sql").expect("sql/01_schema.sql");
    let begin = "-- BEGIN BOUNDARY customer-preferences\n";
    let end = "-- END BOUNDARY customer-preferences\n";
    let from = schema.find(begin).expect("the boundary's first marker");
    let to = schema.find(end).expect("the boundary's last marker") + end.len();
    schema[from..to].to_owned()
}

#[tokio::test]
async fn the_backfill_runs_once_for_the_customers_that_exist_and_never_for_a_later_one() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let section = section();

    owner.batch_execute("BEGIN").await.unwrap();
    let result = async {
        // The state of a live database that has customers and no consent log.
        owner
            .batch_execute("DROP TABLE preferences.consent_decision CASCADE")
            .await?;
        owner.batch_execute(&section).await?;

        let customers: i64 = owner
            .query_one("SELECT count(*) FROM public.customer", &[])
            .await?
            .get(0);
        let rows: i64 = owner
            .query_one("SELECT count(*) FROM preferences.consent_decision", &[])
            .await?
            .get(0);
        assert!(customers >= 30);
        assert_eq!(rows, customers * 3, "three defaults for each customer");
        let wrong: i64 = owner
            .query_one(
                "SELECT count(*) FROM (SELECT customer_id, purpose, decision, source, basis, \
                        recorded_by FROM preferences.consent_decision) AS d \
                 WHERE d.source <> 'MIGRATION' OR d.basis <> 'PROGRAMME_DEFAULT' \
                    OR d.recorded_by IS NOT NULL \
                    OR (d.purpose, d.decision) NOT IN \
                       (('PERSONALIZATION', 'GRANTED'), ('MARKETING_CONTACT', 'WITHDRAWN'), \
                        ('PROFILING', 'GRANTED'))",
                &[],
            )
            .await?
            .get(0);
        assert_eq!(wrong, 0);

        // A customer registered afterwards has no default, and applying the
        // section again appends nothing.
        let later = Uuid::new_v4();
        owner
            .execute(
                "INSERT INTO public.customer (customer_id, name, registration_channel_id) \
                 SELECT $1, 'registered later', min(channel_id) FROM public.channel",
                &[&later],
            )
            .await?;
        owner.batch_execute(&section).await?;
        let for_later: i64 = owner
            .query_one(
                "SELECT count(*) FROM preferences.consent_decision WHERE customer_id = $1",
                &[&later],
            )
            .await?
            .get(0);
        assert_eq!(for_later, 0);
        let again: i64 = owner
            .query_one("SELECT count(*) FROM preferences.consent_decision", &[])
            .await?
            .get(0);
        assert_eq!(again, rows);

        Ok::<(), tokio_postgres::Error>(())
    }
    .await;
    owner.batch_execute("ROLLBACK").await.unwrap();
    result.unwrap();

    // Nothing the test did survived.
    let rows: i64 = owner
        .query_one("SELECT count(*) FROM preferences.consent_decision", &[])
        .await
        .unwrap()
        .get(0);
    assert!(rows >= 90);
}
