//! The migration matrix adds up, on a case small enough to check by hand (F29-02, #887).
//!
//! Six customers are written to the throwaway database, five with an assignment from a
//! full run and one with none, and then moved the way rfm-engine moves them: through
//! `api_segments.apply_assignment_v1`, one batch per cause (ADR-0063, ADR-0087). The
//! matrix between the instant before and the instant after is then compared with a table
//! worked out on paper, not with a second computation. The rows are taken away again.
//!
//!   customer  before        after         why
//!   1         LOYAL         CHAMPION      a sale (improved)
//!   2         AT_RISK       LOST          the clock (declined)
//!   3         POTENTIAL     POTENTIAL     a recomputation with the same result
//!   4         (no row)      LOYAL         a sale: the first assignment (an entry)
//!   5         HIBERNATING   unassigned    the clock: the last purchase left the window (an exit)
//!   6         LOYAL         LOYAL         a recomputation with the same result
//!
//! Needs TEST_DATABASE_URL, as its owner, to write rows and call the function.

mod support;

use chrono::{DateTime, Utc};
use segments::{
    read_migration_explanation, read_migration_matrix, read_migrations, tally, Migration,
    MigrationFilter, MigrationQuery, MigrationState, MigrationWindow,
};
use support::{connect, owner_url, service_database};
use tokio_postgres::Client;
use uuid::Uuid;

const APPLY: &str = "SELECT api_segments.apply_assignment_v1($1::uuid, $2::uuid, $3::varchar, \
     $4::smallint, $5::smallint, $6::smallint, now(), $7::int, $8::text::numeric, $9::text, \
     1::bigint, 'RFM_RULES', 180, 1::bigint)";

/// Only one of these tests writes at a time: they share the six customers.
static TURN: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

fn customer(n: u32) -> Uuid {
    Uuid::parse_str(&format!("00000000-0000-0000-0000-f29000000{n:03}")).unwrap()
}

fn mine() -> Vec<Uuid> {
    (1..=6).map(customer).collect()
}

fn label(code: &str) -> MigrationState {
    MigrationState {
        state: "label".to_owned(),
        label_code: Some(code.to_owned()),
    }
}

fn state(name: &str) -> MigrationState {
    MigrationState {
        state: name.to_owned(),
        label_code: None,
    }
}

async fn clock(owner: &Client) -> DateTime<Utc> {
    owner
        .query_one("SELECT clock_timestamp()", &[])
        .await
        .unwrap()
        .get(0)
}

/// Take away what a test wrote: the customers and their history, the runs only they were in
/// and the events those wrote. Safe to run when there is nothing to take away.
async fn clean(owner: &Client) {
    let ids = mine();
    let runs: Vec<i64> = owner
        .query(
            "SELECT DISTINCT run_id FROM public.customer_segment_history \
              WHERE customer_id = ANY($1)",
            &[&ids],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get(0))
        .collect();
    let texts: Vec<String> = ids.iter().map(Uuid::to_string).collect();
    owner
        .execute(
            "DELETE FROM segments_events.outbox WHERE entity_id = ANY($1)",
            &[&texts],
        )
        .await
        .unwrap();
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
    let run_texts: Vec<String> = runs.iter().map(i64::to_string).collect();
    owner
        .execute(
            "DELETE FROM segments_events.outbox WHERE entity_id = ANY($1)",
            &[&run_texts],
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

/// The full run that came first and the open assignments it left, two days ago.
async fn before(owner: &Client) -> i64 {
    for n in 1..=6 {
        owner
            .execute(
                "INSERT INTO public.customer (customer_id, name, registration_channel_id) \
                 VALUES ($1, $2, 1)",
                &[&customer(n), &format!("F29-02 customer {n}")],
            )
            .await
            .unwrap();
    }
    let run: i64 = owner
        .query_one(
            "INSERT INTO public.segmentation_run \
                 (method, window_days, parameters, customer_count, run_at, data_origin) \
             VALUES ('RFM_RULES', 180, '{}'::jsonb, 5, now() - interval '2 days', \
                     public.fn_segmentation_window_origin(180)) RETURNING run_id",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    for (n, code) in [
        (1, "LOYAL"),
        (2, "AT_RISK"),
        (3, "POTENTIAL"),
        (5, "HIBERNATING"),
        (6, "LOYAL"),
    ] {
        owner
            .execute(
                "INSERT INTO public.customer_segment_history \
                     (customer_id, run_id, label_code, r_score, f_score, m_score, valid_from) \
                 VALUES ($1, $2, $3, 3, 3, 3, now() - interval '2 days')",
                &[&customer(n), &run, &code],
            )
            .await
            .unwrap();
    }
    run
}

/// One call of the writer rfm-engine uses; returns its outcome.
async fn apply(owner: &Client, batch: Uuid, n: u32, code: Option<&str>, cause: &str) -> String {
    let (score, frequency, total): (Option<i16>, i32, &str) = match code {
        Some(_) => (Some(3), 3, "120.00"),
        None => (None, 0, "0"),
    };
    owner
        .query_one(
            APPLY,
            &[
                &batch,
                &customer(n),
                &code,
                &score,
                &score,
                &score,
                &frequency,
                &total,
                &cause,
            ],
        )
        .await
        .unwrap()
        .get(0)
}

/// What rfm-engine does after a sale and after the clock: one batch per cause.
async fn move_them(owner: &Client) {
    let (sale, ageing) = (Uuid::new_v4(), Uuid::new_v4());
    assert_eq!(
        apply(owner, sale, 1, Some("CHAMPION"), "SALE").await,
        "changed"
    );
    assert_eq!(apply(owner, sale, 4, Some("LOYAL"), "SALE").await, "opened");
    assert_eq!(
        apply(owner, ageing, 2, Some("LOST"), "AGEING").await,
        "changed"
    );
    assert_eq!(apply(owner, ageing, 5, None, "AGEING").await, "changed");
    assert_eq!(
        apply(owner, sale, 3, Some("POTENTIAL"), "SALE").await,
        "unchanged"
    );
    assert_eq!(
        apply(owner, sale, 6, Some("LOYAL"), "SALE").await,
        "unchanged"
    );
}

async fn mine_between(db: &mosaiq_kit::Database, window: &MigrationWindow) -> Vec<Migration> {
    let ids = mine();
    read_migrations(
        db,
        &MigrationQuery {
            window: window.clone(),
            filter: MigrationFilter::default(),
        },
    )
    .await
    .unwrap()
    .into_iter()
    .filter(|migration| ids.contains(&migration.customer_id))
    .collect()
}

async fn ordinals(owner: &Client) -> std::collections::HashMap<String, i16> {
    owner
        .query(
            "SELECT label_code, ordinal_position FROM public.segment_label",
            &[],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| (row.get(0), row.get(1)))
        .collect()
}

#[tokio::test]
async fn entries_exits_and_moves_match_the_table_worked_out_by_hand() {
    let _turn = TURN.lock().await;
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    clean(&owner).await;
    before(&owner).await;
    let from: DateTime<Utc> = owner
        .query_one("SELECT now() - interval '1 day'", &[])
        .await
        .unwrap()
        .get(0);
    move_them(&owner).await;
    let to = clock(&owner).await;
    let window = MigrationWindow { from, to };

    let migrations = mine_between(&db, &window).await;
    let (totals, cells) = tally(&migrations, &ordinals(&owner).await);

    // state, before, after, entries, exits
    let by_hand = [
        (label("CHAMPION"), 0, 1, 1, 0),
        (label("LOYAL"), 2, 2, 1, 1),
        (label("POTENTIAL"), 1, 1, 0, 0),
        (label("AT_RISK"), 1, 0, 0, 1),
        (label("HIBERNATING"), 1, 0, 0, 1),
        (label("LOST"), 0, 1, 1, 0),
        (state("unassigned"), 0, 1, 1, 0),
        (state("new"), 1, 0, 0, 1),
    ];
    for (wanted, before, after, entries, exits) in &by_hand {
        let total = totals
            .iter()
            .find(|total| &total.state == wanted)
            .unwrap_or_else(|| panic!("no total for {wanted:?}"));
        assert_eq!(
            (
                total.before_count,
                total.after_count,
                total.entries,
                total.exits
            ),
            (*before, *after, *entries, *exits),
            "{wanted:?}"
        );
    }
    assert_eq!(
        totals.len(),
        by_hand.len(),
        "no state the table does not name"
    );

    // previous, new, movement, customers
    let moves = [
        (label("LOYAL"), label("CHAMPION"), "improved", 1),
        (label("AT_RISK"), label("LOST"), "declined", 1),
        (label("POTENTIAL"), label("POTENTIAL"), "unchanged", 1),
        (state("new"), label("LOYAL"), "not_ranked", 1),
        (label("HIBERNATING"), state("unassigned"), "not_ranked", 1),
        (label("LOYAL"), label("LOYAL"), "unchanged", 1),
    ];
    assert_eq!(cells.len(), moves.len());
    for (previous, new, movement, count) in &moves {
        let cell = cells
            .iter()
            .find(|cell| &cell.previous == previous && &cell.new == new)
            .unwrap_or_else(|| panic!("no cell {previous:?} to {new:?}"));
        assert_eq!(
            (cell.movement.as_str(), cell.customer_count),
            (*movement, *count)
        );
    }

    // The totals reconcile: six in, six out; every customer in one cell; what entered a
    // state is what left another.
    assert_eq!(totals.iter().map(|t| t.before_count).sum::<i64>(), 6);
    assert_eq!(totals.iter().map(|t| t.after_count).sum::<i64>(), 6);
    assert_eq!(cells.iter().map(|c| c.customer_count).sum::<i64>(), 6);
    assert_eq!(totals.iter().map(|t| t.entries).sum::<i64>(), 4);
    assert_eq!(totals.iter().map(|t| t.exits).sum::<i64>(), 4);

    // And the service's own matrix, over every customer there is, is those same totals:
    // its customer count is the seeded customers and these six, and the six are the ones
    // that moved.
    let matrix = read_migration_matrix(&db, window.clone()).await.unwrap();
    let everyone = read_migrations(
        &db,
        &MigrationQuery {
            window,
            filter: MigrationFilter::default(),
        },
    )
    .await
    .unwrap();
    assert_eq!(matrix.customer_count as usize, everyone.len());
    assert_eq!(
        matrix.cells.iter().map(|c| c.customer_count).sum::<i64>(),
        matrix.customer_count
    );
    assert_eq!(
        matrix.totals.iter().map(|t| t.before_count).sum::<i64>(),
        matrix.customer_count
    );
    assert_eq!(
        matrix.totals.iter().map(|t| t.after_count).sum::<i64>(),
        matrix.customer_count
    );
    assert_eq!(
        matrix.totals.iter().map(|t| t.entries).sum::<i64>(),
        matrix.totals.iter().map(|t| t.exits).sum::<i64>()
    );
    let (all_totals, all_cells) = tally(&everyone, &ordinals(&owner).await);
    assert_eq!((matrix.totals, matrix.cells), (all_totals, all_cells));
    clean(&owner).await;
}

#[tokio::test]
async fn a_change_keeps_the_previous_assignment_and_names_its_cause() {
    let _turn = TURN.lock().await;
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    clean(&owner).await;
    let full = before(&owner).await;
    let from: DateTime<Utc> = owner
        .query_one("SELECT now() - interval '1 day'", &[])
        .await
        .unwrap()
        .get(0);
    let first: i64 = owner
        .query_one(
            "SELECT history_id FROM public.customer_segment_history \
              WHERE customer_id = $1 AND valid_to IS NULL",
            &[&customer(1)],
        )
        .await
        .unwrap()
        .get(0);
    move_them(&owner).await;
    let to = clock(&owner).await;

    // The customer has two rows; the first is the one the full run wrote, closed at the
    // instant the second opened, and otherwise as it was.
    let rows = owner
        .query(
            "SELECT history_id, label_code, run_id, cause, valid_from, valid_to \
               FROM public.customer_segment_history WHERE customer_id = $1 \
              ORDER BY valid_from, history_id",
            &[&customer(1)],
        )
        .await
        .unwrap();
    assert_eq!(rows.len(), 2);
    let (kept, changed) = (&rows[0], &rows[1]);
    assert_eq!(kept.get::<_, i64>(0), first);
    assert_eq!(kept.get::<_, String>(1), "LOYAL");
    assert_eq!(kept.get::<_, i64>(2), full);
    assert_eq!(kept.get::<_, String>(3), "FULL_RUN");
    assert_eq!(
        kept.get::<_, Option<DateTime<Utc>>>(5),
        Some(changed.get::<_, DateTime<Utc>>(4)),
        "closed at the instant the next one opened"
    );
    assert_eq!(changed.get::<_, String>(1), "CHAMPION");
    assert_eq!(changed.get::<_, String>(3), "SALE");
    assert_eq!(changed.get::<_, Option<DateTime<Utc>>>(5), None);

    // The new row's run is the batch that made it, and says so.
    let run: i64 = changed.get(2);
    assert_ne!(run, full);
    let (scope, cause): (String, Option<String>) = {
        let row = owner
            .query_one(
                "SELECT scope, parameters->>'cause' FROM public.segmentation_run \
                  WHERE run_id = $1",
                &[&run],
            )
            .await
            .unwrap();
        (row.get(0), row.get(1))
    };
    assert_eq!(scope, "INCREMENTAL");
    assert_eq!(cause.as_deref(), Some("SALE"));
    let ageing: String = owner
        .query_one(
            "SELECT cause FROM public.customer_segment_history \
              WHERE customer_id = $1 AND valid_to IS NULL",
            &[&customer(2)],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(ageing, "AGEING");

    // The service reads both sides of the move, and the run each came from.
    let explanation = read_migration_explanation(&db, customer(1), MigrationWindow { from, to })
        .await
        .unwrap();
    let migration = &explanation.migration;
    assert_eq!(migration.previous.state, label("LOYAL"));
    assert_eq!(migration.new.state, label("CHAMPION"));
    assert_eq!(migration.previous.run_id, Some(full));
    assert_eq!(migration.new.run_id, Some(run));
    assert_eq!(migration.changed_at, migration.new.valid_from);
    clean(&owner).await;
}

#[tokio::test]
async fn a_recomputation_with_no_new_sale_shows_no_migration() {
    let _turn = TURN.lock().await;
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    clean(&owner).await;
    before(&owner).await;
    move_them(&owner).await;
    let settled = clock(&owner).await;
    let count = |sql: &'static str| {
        let owner = &owner;
        async move {
            owner
                .query_one(sql, &[&mine()])
                .await
                .unwrap()
                .get::<_, i64>(0)
        }
    };
    let rows =
        count("SELECT count(*) FROM public.customer_segment_history WHERE customer_id = ANY($1)")
            .await;
    let runs = owner
        .query_one("SELECT count(*) FROM public.segmentation_run", &[])
        .await
        .unwrap()
        .get::<_, i64>(0);

    // Every customer is recomputed to the result they already hold, in batches of their own.
    let (sale, ageing) = (Uuid::new_v4(), Uuid::new_v4());
    for (n, code, cause, batch) in [
        (1, Some("CHAMPION"), "SALE", sale),
        (2, Some("LOST"), "AGEING", ageing),
        (3, Some("POTENTIAL"), "AGEING", ageing),
        (4, Some("LOYAL"), "SALE", sale),
        (5, None, "AGEING", ageing),
        (6, Some("LOYAL"), "AGEING", ageing),
    ] {
        assert_eq!(
            apply(&owner, batch, n, code, cause).await,
            "unchanged",
            "customer {n}"
        );
    }
    assert_eq!(
        count("SELECT count(*) FROM public.customer_segment_history WHERE customer_id = ANY($1)")
            .await,
        rows,
        "no row is written"
    );
    assert_eq!(
        owner
            .query_one("SELECT count(*) FROM public.segmentation_run", &[])
            .await
            .unwrap()
            .get::<_, i64>(0),
        runs,
        "no run is recorded"
    );

    // So the matrix between the two readings has no entry, no exit and no move.
    let window = MigrationWindow {
        from: settled,
        to: clock(&owner).await,
    };
    let migrations = mine_between(&db, &window).await;
    assert_eq!(migrations.len(), 6);
    for migration in &migrations {
        assert_eq!(
            migration.previous.state, migration.new.state,
            "{}",
            migration.customer_id
        );
        assert_eq!(migration.changed_at, None, "{}", migration.customer_id);
    }
    let (totals, _) = tally(&migrations, &ordinals(&owner).await);
    assert!(totals
        .iter()
        .all(|total| total.entries == 0 && total.exits == 0));
    clean(&owner).await;
}
