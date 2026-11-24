//! A sale's migration is behaviour when both sides were written under the same version
//! (#1208, ADR-0064, ADR-0087).
//!
//! rfm-engine records an INCREMENTAL run's version in the run's own parameters, and
//! nothing links it in `models.run_version`, which only FULL runs have. The service reads a
//! run's version from the link, else from the parameters. Four customers hold an
//! assignment from the seeded FULL run 31 (linked to version 1 by the seed); each is then
//! moved by an INCREMENTAL run that records something different:
//!
//!   customer  incremental run records   previous  new   methodological
//!   1         version 1 (a sale)        1         1     no: behaviour
//!   2         version 2                 1         2     yes
//!   3         no version at all         1         -     yes: unknown
//!   4         something that is no id   1         -     yes: unknown, and nothing fails
//!
//! The history reads and the migration matrix are then compared with the migrations. The
//! rows are taken away again; the seeded run is never touched. Needs TEST_DATABASE_URL, as
//! its owner.

mod support;

use chrono::{DateTime, Utc};
use segments::{
    read_migration_matrix, read_migrations, read_versioned_assignments, Migration, MigrationFilter,
    MigrationQuery, MigrationWindow, VersionedAssignmentFilter,
};
use support::{connect, owner_url, service_database};
use tokio_postgres::Client;
use uuid::Uuid;

/// The writer rfm-engine uses; the version it records is the last argument.
const APPLY: &str = "SELECT api_segments.apply_assignment_v1($1::uuid, $2::uuid, $3::varchar, \
     3::smallint, 3::smallint, 3::smallint, now(), 3::int, '120.00'::numeric, 'SALE', \
     $4::bigint, 'RFM_RULES', 180, 1::bigint)";

/// The seeded FULL run, linked to version 1 in `models.run_version`.
const FULL_RUN: i64 = 31;

static TURN: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

fn customer(n: u32) -> Uuid {
    Uuid::parse_str(&format!("00000000-0000-0000-0000-f12080{n:06}")).unwrap()
}

fn mine() -> Vec<Uuid> {
    (1..=4).map(customer).collect()
}

async fn clock(owner: &Client) -> DateTime<Utc> {
    owner
        .query_one("SELECT clock_timestamp()", &[])
        .await
        .unwrap()
        .get(0)
}

/// Take away what a test wrote: the customers, their history, the runs only they were in
/// and the events those wrote. Safe to run when there is nothing to take away.
async fn clean(owner: &Client) {
    let ids = mine();
    let runs: Vec<i64> = owner
        .query(
            "SELECT DISTINCT run_id FROM public.customer_segment_history \
              WHERE customer_id = ANY($1) AND run_id <> $2",
            &[&ids, &FULL_RUN],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get(0))
        .collect();
    let texts: Vec<String> = ids.iter().map(Uuid::to_string).collect();
    let run_texts: Vec<String> = runs.iter().map(i64::to_string).collect();
    for entities in [&texts, &run_texts] {
        owner
            .execute(
                "DELETE FROM segments_events.outbox WHERE entity_id = ANY($1)",
                &[entities],
            )
            .await
            .unwrap();
    }
    owner
        .execute(
            "DELETE FROM public.customer_segment_history WHERE customer_id = ANY($1)",
            &[&ids],
        )
        .await
        .unwrap();
    owner
        .execute(
            "DELETE FROM public.customer WHERE customer_id = ANY($1)",
            &[&ids],
        )
        .await
        .unwrap();
    owner
        .execute(
            "DELETE FROM public.segmentation_run AS r WHERE r.run_id = ANY($1) \
               AND NOT EXISTS (SELECT FROM public.customer_segment_history AS h \
                                WHERE h.run_id = r.run_id)",
            &[&runs],
        )
        .await
        .unwrap();
}

/// Four customers, each LOYAL under the seeded FULL run since two days ago.
async fn before(owner: &Client) {
    for n in 1..=4 {
        owner
            .execute(
                "INSERT INTO public.customer (customer_id, name, registration_channel_id) \
                 VALUES ($1, $2, 1)",
                &[&customer(n), &format!("#1208 customer {n}")],
            )
            .await
            .unwrap();
        owner
            .execute(
                "INSERT INTO public.customer_segment_history \
                     (customer_id, run_id, label_code, r_score, f_score, m_score, valid_from) \
                 VALUES ($1, $2, 'LOYAL', 3, 3, 3, now() - interval '2 days')",
                &[&customer(n), &FULL_RUN],
            )
            .await
            .unwrap();
    }
}

/// One incremental sale through the function, recording `version`; returns its outcome.
async fn apply(owner: &Client, n: u32, version: i64) -> String {
    owner
        .query_one(
            APPLY,
            &[&Uuid::new_v4(), &customer(n), &"CHAMPION", &version],
        )
        .await
        .unwrap()
        .get(0)
}

/// An incremental run whose parameters are `parameters`, and a customer moved by it. The
/// function refuses a call that names no version, so a run that records none, or something
/// that is no id, is written the way an older writer would have.
async fn move_by_run_recording(owner: &Client, n: u32, parameters: &str) {
    let run: i64 = owner
        .query_one(
            "INSERT INTO public.segmentation_run \
                 (method, window_days, parameters, customer_count, data_origin, scope) \
             VALUES ('RFM_RULES', 180, $1::text::jsonb, 0, \
                     public.fn_segmentation_window_origin(180), 'INCREMENTAL') \
             RETURNING run_id",
            &[&parameters],
        )
        .await
        .unwrap()
        .get(0);
    owner
        .execute(
            "UPDATE public.customer_segment_history SET valid_to = clock_timestamp() \
              WHERE customer_id = $1 AND valid_to IS NULL",
            &[&customer(n)],
        )
        .await
        .unwrap();
    owner
        .execute(
            "INSERT INTO public.customer_segment_history \
                 (customer_id, run_id, label_code, r_score, f_score, m_score, valid_from, \
                  cause) \
             VALUES ($1, $2, 'CHAMPION', 3, 3, 3, clock_timestamp(), 'SALE')",
            &[&customer(n), &run],
        )
        .await
        .unwrap();
}

async fn mine_between(db: &mosaiq_kit::Database, window: MigrationWindow) -> Vec<Migration> {
    let ids = mine();
    let mut found: Vec<Migration> = read_migrations(
        db,
        &MigrationQuery {
            window,
            filter: MigrationFilter::default(),
        },
    )
    .await
    .unwrap()
    .into_iter()
    .filter(|migration| ids.contains(&migration.customer_id))
    .collect();
    found.sort_by_key(|migration| migration.customer_id);
    found
}

fn versions(migration: &Migration) -> (Option<i64>, Option<i64>) {
    (
        migration.previous.model_version_id,
        migration.new.model_version_id,
    )
}

#[tokio::test]
async fn a_sale_under_the_same_version_is_behaviour_and_the_rest_is_not() {
    let _turn = TURN.lock().await;
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    clean(&owner).await;
    before(&owner).await;
    let from = clock(&owner).await;

    // Customer 1: a sale, scored under version 1, which the run records and nothing links.
    assert_eq!(apply(&owner, 1, 1).await, "changed");
    let mid = clock(&owner).await;
    // Customer 2: scored under version 2.
    assert_eq!(apply(&owner, 2, 2).await, "changed");
    // Customers 3 and 4: runs that record no version, and one that records no id.
    move_by_run_recording(&owner, 3, r#"{"cause":"SALE"}"#).await;
    move_by_run_recording(&owner, 4, r#"{"cause":"SALE","model_version_id":"abc"}"#).await;
    let to = clock(&owner).await;

    // The increments are not linked, as on the instance: that is what #1208 is about.
    let linked: i64 = owner
        .query_one(
            "SELECT count(*) FROM models.run_version AS v \
               JOIN public.customer_segment_history AS h ON h.run_id = v.run_id \
              WHERE h.customer_id = ANY($1) AND h.run_id <> $2",
            &[&mine(), &FULL_RUN],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(linked, 0, "no incremental run of this test is linked");

    let migrations = mine_between(&db, MigrationWindow { from, to }).await;
    assert_eq!(migrations.len(), 4);
    let expected = [
        (Some(1), Some(1), false),
        (Some(1), Some(2), true),
        (Some(1), None, true),
        (Some(1), None, true),
    ];
    for (migration, (previous, new, methodological)) in migrations.iter().zip(expected) {
        assert_eq!(
            (versions(migration), migration.methodological),
            ((previous, new), methodological),
            "customer {}",
            migration.customer_id
        );
        assert_eq!(migration.new.cause.as_deref(), Some("SALE"));
    }

    // Only the sale under the same version is behaviour.
    let behaviour: Vec<Uuid> = read_migrations(
        &db,
        &MigrationQuery {
            window: MigrationWindow { from, to },
            filter: MigrationFilter {
                methodological: Some(false),
                ..MigrationFilter::default()
            },
        },
    )
    .await
    .unwrap()
    .into_iter()
    .map(|migration| migration.customer_id)
    .filter(|id| mine().contains(id))
    .collect();
    assert_eq!(behaviour, vec![customer(1)]);

    // The history says the same of each side: the version a row reports is the version the
    // migration reports, whichever way the run came by it.
    for (migration, _) in migrations.iter().zip(expected) {
        let filter = VersionedAssignmentFilter {
            model_version_id: None,
            customer_id: Some(migration.customer_id),
            label_code: None,
            open: false,
        };
        let (rows, total) = read_versioned_assignments(&db, &filter, 1, 20)
            .await
            .unwrap();
        assert_eq!(total, 2);
        let new = rows.iter().find(|row| row.valid_to.is_none()).unwrap();
        let old = rows.iter().find(|row| row.valid_to.is_some()).unwrap();
        assert_eq!(
            (old.model_version_id, new.model_version_id),
            versions(migration),
            "history of {}",
            migration.customer_id
        );
        assert_eq!(new.run_id, migration.new.run_id.unwrap());
    }

    // Filtering the history by version reaches the incremental run's row.
    let by_version = |version| VersionedAssignmentFilter {
        model_version_id: Some(version),
        customer_id: Some(customer(1)),
        label_code: None,
        open: true,
    };
    let (rows, total) = read_versioned_assignments(&db, &by_version(1), 1, 20)
        .await
        .unwrap();
    assert_eq!(
        (total, rows.len()),
        (1, 1),
        "customer 1's open row is version 1"
    );
    let (_, total) = read_versioned_assignments(&db, &by_version(2), 1, 20)
        .await
        .unwrap();
    assert_eq!(total, 0);
    let two = VersionedAssignmentFilter {
        customer_id: Some(customer(2)),
        ..by_version(2)
    };
    let (rows, total) = read_versioned_assignments(&db, &two, 1, 20).await.unwrap();
    assert_eq!((total, rows[0].model_version_id), (1, Some(2)));

    // The matrix: the version at an instant is the latest run's that has one. Before
    // customer 2's run it is version 1 at both ends, so the model did not move.
    let first = read_migration_matrix(&db, MigrationWindow { from, to: mid })
        .await
        .unwrap();
    assert_eq!(
        (first.from_model_version_id, first.to_model_version_id),
        (Some(1), Some(1))
    );
    assert!(!first.methodological);
    // Over the whole test it ends under version 2, so what moved may be the model's.
    let whole = read_migration_matrix(&db, MigrationWindow { from, to })
        .await
        .unwrap();
    assert_eq!(
        (whole.from_model_version_id, whole.to_model_version_id),
        (Some(1), Some(2))
    );
    assert!(whole.methodological);

    clean(&owner).await;
}

#[tokio::test]
async fn a_run_with_a_link_keeps_the_linked_version_over_what_it_recorded() {
    // The link is model-registry's word; the parameters are the fallback. A seeded FULL run
    // is linked and records no version, so the link answers.
    let _turn = TURN.lock().await;
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    clean(&owner).await;
    before(&owner).await;
    let (rows, total) = read_versioned_assignments(
        &db,
        &VersionedAssignmentFilter {
            model_version_id: None,
            customer_id: Some(customer(1)),
            label_code: None,
            open: true,
        },
        1,
        20,
    )
    .await
    .unwrap();
    assert_eq!((total, rows[0].model_version_id), (1, Some(1)));
    let recorded: Option<String> = owner
        .query_one(
            "SELECT parameters ->> 'model_version_id' FROM public.segmentation_run \
              WHERE run_id = $1",
            &[&FULL_RUN],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(recorded, None, "the seeded FULL run records no version");
    clean(&owner).await;
}
