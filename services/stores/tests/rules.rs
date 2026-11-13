//! The status, hours, channels and commercial rules (RN-53, F21-12) against
//! PostgreSQL, through the service's own write and read functions, connected as
//! `svc_stores`: what is stored, audited and announced, and what the schema
//! itself refuses when a writer goes around the service.

mod support;

use chrono::NaiveTime;
use stores::{
    parse_hours, read_channels, read_hours, read_rules, read_status, replace_channels,
    replace_hours, replace_rules, replace_status, Hours, Interval,
};
use support::{connect, owner_url, principal, service_database, users, TestStore};
use tokio_postgres::Client;

fn time(text: &str) -> NaiveTime {
    NaiveTime::parse_from_str(text, "%H:%M").unwrap()
}

fn interval(day: i16, opens: &str, closes: &str) -> Interval {
    Interval {
        day_of_week: day,
        opens_at: time(opens),
        closes_at: time(closes),
    }
}

fn week(zone: &str, intervals: Vec<Interval>) -> Hours {
    Hours {
        time_zone: Some(zone.to_owned()),
        intervals,
    }
}

fn dec(text: &str) -> rust_decimal::Decimal {
    text.parse().unwrap()
}

async fn audit(owner: &Client, entity: &str, store: i16) -> Vec<(String, Option<uuid::Uuid>)> {
    owner
        .query(
            "SELECT action, user_id FROM audit_log WHERE entity = $1 AND entity_pk = $2 \
             ORDER BY audit_id",
            &[&entity, &store.to_string()],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| (row.get(0), row.get(1)))
        .collect()
}

async fn announced(owner: &Client, store: i16) -> i64 {
    owner
        .query_one(
            "SELECT count(*) FROM stores_events.outbox \
             WHERE stream = 'stream:stores.v1.changed' AND entity_id = $1",
            &[&store.to_string()],
        )
        .await
        .unwrap()
        .get(0)
}

async fn forget(owner: &Client, store: TestStore) {
    owner
        .execute(
            "DELETE FROM stores_events.outbox WHERE entity_id = $1",
            &[&store.id.to_string()],
        )
        .await
        .unwrap();
    store.cleanup(owner).await;
}

#[tokio::test]
async fn a_store_never_written_is_open_with_no_hours_channels_or_cap() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let store = TestStore::create(&owner).await;
    let connection = db.connection().await.unwrap();

    let status = read_status(&***connection, store.id).await.unwrap();
    assert_eq!((status.status.as_str(), status.updated_at), ("OPEN", None));
    let hours = read_hours(&***connection, store.id).await.unwrap();
    assert_eq!((hours.hours.time_zone, hours.updated_at), (None, None));
    assert!(hours.hours.intervals.is_empty());
    let channels = read_channels(&***connection, store.id).await.unwrap();
    assert!(channels.channel_ids.is_empty() && channels.updated_at.is_none());
    let rules = read_rules(&***connection, store.id).await.unwrap();
    assert_eq!((rules.max_discount_percent, rules.updated_at), (None, None));

    let row = owner
        .query_one(
            "SELECT status, time_zone, opening_hours::text, channel_ids, max_discount_percent::text \
             FROM api_stores.store_directory_v2 WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap();
    assert_eq!(row.get::<_, String>(0), "OPEN");
    assert_eq!(row.get::<_, Option<String>>(1), None);
    assert_eq!(row.get::<_, String>(2), "[]");
    assert!(row.get::<_, Vec<i16>>(3).is_empty());
    assert_eq!(row.get::<_, Option<String>>(4), None);
    forget(&owner, store).await;
}

#[tokio::test]
async fn an_unknown_store_is_404_on_every_read_and_write() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let (admin, _) = users(&owner).await;
    let actor = principal(admin);
    let connection = db.connection().await.unwrap();
    let missing = 29_999;
    let hours = week("UTC", vec![interval(1, "09:00", "17:00")]);
    let errors = [
        read_status(&***connection, missing).await.unwrap_err(),
        read_hours(&***connection, missing).await.unwrap_err(),
        read_channels(&***connection, missing).await.unwrap_err(),
        read_rules(&***connection, missing).await.unwrap_err(),
        replace_status(&db, &actor, missing, "CLOSED")
            .await
            .unwrap_err(),
        replace_hours(&db, &actor, missing, &hours)
            .await
            .unwrap_err(),
        replace_channels(&db, &actor, missing, &[1])
            .await
            .unwrap_err(),
        replace_rules(&db, &actor, missing, Some(dec("5.00")))
            .await
            .unwrap_err(),
    ];
    for error in errors {
        assert_eq!(
            (error.status, error.code.as_str()),
            (404, "store_not_found")
        );
    }
}

#[tokio::test]
async fn the_status_is_replaced_audited_announced_and_a_repeat_changes_nothing() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let (first, second) = users(&owner).await;
    let store = TestStore::create(&owner).await;
    let created = announced(&owner, store.id).await;

    let closed = replace_status(&db, &principal(first), store.id, "CLOSED")
        .await
        .unwrap();
    assert_eq!(closed.status, "CLOSED");
    let stamped = closed.updated_at.expect("a write sets updated_at");
    let announced_once = announced(&owner, store.id).await;
    assert_eq!(announced_once, created + 1, "one change, one announcement");

    // The same status again: nothing is written, audited or announced, and
    // updated_at still says when it changed.
    let again = replace_status(&db, &principal(second), store.id, "CLOSED")
        .await
        .unwrap();
    assert_eq!(again.updated_at, Some(stamped));
    assert_eq!(announced(&owner, store.id).await, announced_once);
    assert_eq!(
        audit(&owner, "store_status", store.id).await,
        vec![("INSERT".to_owned(), Some(first))]
    );

    let reopened = replace_status(&db, &principal(second), store.id, "OPEN")
        .await
        .unwrap();
    assert_eq!(reopened.status, "OPEN");
    assert!(reopened.updated_at > Some(stamped));
    assert_eq!(
        audit(&owner, "store_status", store.id).await,
        vec![
            ("INSERT".to_owned(), Some(first)),
            ("UPDATE".to_owned(), Some(second))
        ]
    );
    let set_by: uuid::Uuid = owner
        .query_one(
            "SELECT set_by FROM stores.store_status WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(set_by, second);
    assert_eq!(announced(&owner, store.id).await, created + 2);

    let error = replace_status(&db, &principal(first), store.id, "ARCHIVED")
        .await
        .unwrap_err();
    assert_eq!((error.status, error.code.as_str()), (422, "invalid_body"));
    assert_eq!(
        (
            error.details[0].field.as_str(),
            error.details[0].code.as_str()
        ),
        ("status", "not_allowed")
    );
    forget(&owner, store).await;
}

#[tokio::test]
async fn a_session_whose_sub_is_not_a_user_is_401_and_writes_nothing() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let store = TestStore::create(&owner).await;
    let stranger = principal(uuid::Uuid::new_v4());

    let hours = week("UTC", vec![interval(1, "09:00", "17:00")]);
    let errors = [
        replace_status(&db, &stranger, store.id, "CLOSED")
            .await
            .unwrap_err(),
        replace_hours(&db, &stranger, store.id, &hours)
            .await
            .unwrap_err(),
        replace_channels(&db, &stranger, store.id, &[1])
            .await
            .unwrap_err(),
        replace_rules(&db, &stranger, store.id, Some(dec("5.00")))
            .await
            .unwrap_err(),
    ];
    for error in errors {
        assert_eq!((error.status, error.code.as_str()), (401, "invalid_token"));
    }
    let rows: i64 = owner
        .query_one(
            "SELECT (SELECT count(*) FROM stores.store_status WHERE store_id = $1) \
                  + (SELECT count(*) FROM stores.store_hours WHERE store_id = $1) \
                  + (SELECT count(*) FROM stores.store_channel_set WHERE store_id = $1) \
                  + (SELECT count(*) FROM stores.store_commercial_rule WHERE store_id = $1)",
            &[&store.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(rows, 0);
    forget(&owner, store).await;
}

#[tokio::test]
async fn hours_are_replaced_day_by_day_cleared_and_never_stored_with_a_bad_zone() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let (admin, _) = users(&owner).await;
    let actor = principal(admin);
    let store = TestStore::create(&owner).await;

    let monday_to_saturday = week(
        "America/Mexico_City",
        vec![
            interval(1, "09:00", "21:00"),
            interval(2, "09:00", "21:00"),
            interval(6, "10:00", "18:00"),
        ],
    );
    let stored = replace_hours(&db, &actor, store.id, &monday_to_saturday)
        .await
        .unwrap();
    assert_eq!(stored.hours, monday_to_saturday);
    let stamped = stored.updated_at.unwrap();

    // The view publishes them as the contract describes.
    let published: String = owner
        .query_one(
            "SELECT opening_hours::text FROM api_stores.store_directory_v2 WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap()
        .get(0);
    let published: serde_json::Value = serde_json::from_str(&published).unwrap();
    assert_eq!(
        published,
        serde_json::json!([
            {"day_of_week": 1, "opens_at": "09:00", "closes_at": "21:00"},
            {"day_of_week": 2, "opens_at": "09:00", "closes_at": "21:00"},
            {"day_of_week": 6, "opens_at": "10:00", "closes_at": "18:00"}
        ])
    );

    // The same hours: nothing changes. Different hours: only the days that
    // differ are written, so only they are audited.
    let same = replace_hours(&db, &actor, store.id, &monday_to_saturday)
        .await
        .unwrap();
    assert_eq!(same.updated_at, Some(stamped));
    let before = audit(&owner, "store_hours_interval", store.id).await.len();
    assert_eq!(before, 3);
    let changed = week(
        "America/Mexico_City",
        vec![
            interval(1, "09:00", "21:00"),
            interval(2, "08:00", "21:00"),
            interval(3, "09:00", "14:00"),
        ],
    );
    let after = replace_hours(&db, &actor, store.id, &changed)
        .await
        .unwrap();
    assert_eq!(after.hours, changed);
    assert!(after.updated_at > Some(stamped));
    // Saturday deleted, Tuesday updated, Wednesday inserted; Monday untouched.
    assert_eq!(
        audit(&owner, "store_hours_interval", store.id).await.len(),
        before + 3
    );

    // A new zone with the same days is a change of the header only.
    let moved = week("America/Tijuana", changed.intervals.clone());
    assert_eq!(
        replace_hours(&db, &actor, store.id, &moved)
            .await
            .unwrap()
            .hours,
        moved
    );

    // A name PostgreSQL does not know is refused and nothing is written.
    let unknown = week("Mars/Olympus_Mons", changed.intervals.clone());
    let error = replace_hours(&db, &actor, store.id, &unknown)
        .await
        .unwrap_err();
    assert_eq!((error.status, error.code.as_str()), (422, "invalid_body"));
    assert_eq!(
        (
            error.details[0].field.as_str(),
            error.details[0].code.as_str()
        ),
        ("time_zone", "invalid_format")
    );
    assert_eq!(
        read_hours(&***db.connection().await.unwrap(), store.id)
            .await
            .unwrap()
            .hours,
        moved
    );

    // Clearing is a write that keeps updated_at: no zone, no interval.
    let cleared = Hours {
        time_zone: None,
        intervals: Vec::new(),
    };
    let result = replace_hours(&db, &actor, store.id, &cleared)
        .await
        .unwrap();
    assert_eq!(result.hours, cleared);
    assert!(result.updated_at.is_some());
    let row = owner
        .query_one(
            "SELECT time_zone, opening_hours::text FROM api_stores.store_directory_v2 \
             WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap();
    assert_eq!(row.get::<_, Option<String>>(0), None);
    assert_eq!(row.get::<_, String>(1), "[]");
    forget(&owner, store).await;
}

#[tokio::test]
async fn clearing_the_hours_of_a_store_never_written_still_writes_them() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let (admin, _) = users(&owner).await;
    let store = TestStore::create(&owner).await;
    let created = announced(&owner, store.id).await;
    let cleared = Hours {
        time_zone: None,
        intervals: Vec::new(),
    };
    let result = replace_hours(&db, &principal(admin), store.id, &cleared)
        .await
        .unwrap();
    assert!(result.updated_at.is_some());
    assert_eq!(announced(&owner, store.id).await, created + 1);
    forget(&owner, store).await;
}

#[test]
fn the_hours_rules_a_schema_cannot_state_are_refused_before_the_database() {
    let body = serde_json::json!({
        "time_zone": null,
        "intervals": [{"day_of_week": 1, "opens_at": "09:00", "closes_at": "21:00"}]
    });
    assert_eq!(parse_hours(&body).unwrap_err().status, 422);
}

#[tokio::test]
async fn two_first_writes_of_the_same_hours_both_succeed_and_leave_one_schedule() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let (admin, other) = users(&owner).await;
    let store = TestStore::create(&owner).await;
    let one = week("UTC", vec![interval(1, "09:00", "17:00")]);
    let two = week(
        "UTC",
        vec![interval(2, "10:00", "12:00"), interval(3, "10:00", "12:00")],
    );
    let (first, second) = (principal(admin), principal(other));
    let (a, b) = tokio::join!(
        replace_hours(&db, &first, store.id, &one),
        replace_hours(&db, &second, store.id, &two),
    );
    a.unwrap();
    b.unwrap();
    let state = read_hours(&***db.connection().await.unwrap(), store.id)
        .await
        .unwrap();
    assert!(
        state.hours == one || state.hours == two,
        "{:?}",
        state.hours
    );
    let headers: i64 = owner
        .query_one(
            "SELECT count(*) FROM stores.store_hours WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(headers, 1);
    forget(&owner, store).await;
}

#[tokio::test]
async fn the_schema_refuses_hours_a_writer_goes_around_the_service_to_break() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let (admin, _) = users(&owner).await;
    let store = TestStore::create(&owner).await;
    let constraint_of = |error: tokio_postgres::Error| {
        error
            .as_db_error()
            .and_then(|e| e.constraint().map(str::to_owned))
    };

    // A header with no zone and no interval is the cleared state.
    owner
        .execute(
            "INSERT INTO stores.store_hours (store_id, time_zone, set_by) VALUES ($1, NULL, $2)",
            &[&store.id, &admin],
        )
        .await
        .unwrap();

    // Wrong day, overnight, empty and second-resolution intervals.
    for (day, opens, closes, constraint) in [
        (
            8i16,
            "09:00",
            "10:00",
            "store_hours_interval_day_of_week_check",
        ),
        (1, "22:00", "06:00", "store_hours_interval_order_check"),
        (1, "09:00", "09:00", "store_hours_interval_order_check"),
        (1, "09:00:30", "10:00", "store_hours_interval_minutes_check"),
    ] {
        let error = owner
            .execute(
                "INSERT INTO stores.store_hours_interval \
                 (store_id, day_of_week, opens_at, closes_at) \
                 VALUES ($1, $2, $3::text::time, $4::text::time)",
                &[&store.id, &day, &opens, &closes],
            )
            .await
            .unwrap_err();
        assert_eq!(
            constraint_of(error).as_deref(),
            Some(constraint),
            "{day} {opens} {closes}"
        );
    }

    // A zone with no interval is refused when the statement commits.
    let error = owner
        .execute(
            "UPDATE stores.store_hours SET time_zone = 'UTC' WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap_err();
    assert_eq!(
        constraint_of(error).as_deref(),
        Some("store_hours_time_zone_consistent")
    );

    // The same two changes in one transaction, in either order, are accepted:
    // the check is deferred to the commit.
    owner
        .batch_execute(&format!(
            "BEGIN; \
             INSERT INTO stores.store_hours_interval VALUES ({id}, 1, '09:00', '17:00'); \
             UPDATE stores.store_hours SET time_zone = 'UTC' WHERE store_id = {id}; \
             COMMIT;",
            id = store.id
        ))
        .await
        .unwrap();

    // An interval under no zone, and a zone left over with no interval.
    let error = owner
        .execute(
            "UPDATE stores.store_hours SET time_zone = NULL WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap_err();
    assert_eq!(
        constraint_of(error).as_deref(),
        Some("store_hours_time_zone_consistent")
    );
    let error = owner
        .execute(
            "DELETE FROM stores.store_hours_interval WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap_err();
    assert_eq!(
        constraint_of(error).as_deref(),
        Some("store_hours_time_zone_consistent")
    );
    forget(&owner, store).await;
}

#[tokio::test]
async fn channels_are_a_set_replaced_atomically_and_an_unknown_id_writes_nothing() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let (admin, _) = users(&owner).await;
    let actor = principal(admin);
    let store = TestStore::create(&owner).await;

    let stored = replace_channels(&db, &actor, store.id, &[1, 3, 4])
        .await
        .unwrap();
    assert_eq!(stored.channel_ids, [1, 3, 4]);
    let stamped = stored.updated_at.unwrap();
    let published: Vec<i16> = owner
        .query_one(
            "SELECT channel_ids FROM api_stores.store_directory_v2 WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(published, [1, 3, 4]);

    let same = replace_channels(&db, &actor, store.id, &[1, 3, 4])
        .await
        .unwrap();
    assert_eq!(same.updated_at, Some(stamped));
    let announced_before = announced(&owner, store.id).await;

    // 3 stays, 1 and 4 go, 5 arrives: the old set is never visible in part.
    let replaced = replace_channels(&db, &actor, store.id, &[3, 5])
        .await
        .unwrap();
    assert_eq!(replaced.channel_ids, [3, 5]);
    assert!(announced(&owner, store.id).await > announced_before);

    // An id that is no channel is 422 and the set is still [3, 5].
    let error = replace_channels(&db, &actor, store.id, &[3, 99])
        .await
        .unwrap_err();
    assert_eq!((error.status, error.code.as_str()), (422, "invalid_body"));
    assert_eq!(
        (
            error.details[0].field.as_str(),
            error.details[0].code.as_str()
        ),
        ("channel_ids", "unknown_channel")
    );
    let connection = db.connection().await.unwrap();
    assert_eq!(
        read_channels(&***connection, store.id)
            .await
            .unwrap()
            .channel_ids,
        [3, 5]
    );

    // Clearing: an empty set that still says when it was written.
    let cleared = replace_channels(&db, &actor, store.id, &[]).await.unwrap();
    assert!(cleared.channel_ids.is_empty() && cleared.updated_at.is_some());
    let none: i64 = owner
        .query_one(
            "SELECT count(*) FROM stores.store_channel WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(none, 0);
    forget(&owner, store).await;
}

#[tokio::test]
async fn an_unknown_channel_on_a_first_write_leaves_no_header_and_no_announcement() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let (admin, _) = users(&owner).await;
    let store = TestStore::create(&owner).await;
    let created = announced(&owner, store.id).await;
    let error = replace_channels(&db, &principal(admin), store.id, &[99])
        .await
        .unwrap_err();
    assert_eq!(error.details[0].code, "unknown_channel");
    let headers: i64 = owner
        .query_one(
            "SELECT count(*) FROM stores.store_channel_set WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(headers, 0, "a refused write is rolled back whole");
    assert_eq!(announced(&owner, store.id).await, created);
    assert!(audit(&owner, "store_channel_set", store.id)
        .await
        .is_empty());
    forget(&owner, store).await;
}

#[tokio::test]
async fn the_service_cannot_read_the_channel_table_it_references() {
    let Some(url) = owner_url() else { return };
    let service = connect(&support::service_url(&url)).await;
    let denied = service
        .query("SELECT channel_id FROM public.channel", &[])
        .await
        .unwrap_err();
    assert_eq!(denied.code().map(|c| c.code()), Some("42501"));
}

#[tokio::test]
async fn the_discount_cap_is_set_changed_removed_and_bounded_by_the_schema() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let (admin, _) = users(&owner).await;
    let actor = principal(admin);
    let store = TestStore::create(&owner).await;

    let set = replace_rules(&db, &actor, store.id, Some(dec("15.00")))
        .await
        .unwrap();
    assert_eq!(set.max_discount_percent, Some(dec("15.00")));
    let stamped = set.updated_at.unwrap();
    let same = replace_rules(&db, &actor, store.id, Some(dec("15.00")))
        .await
        .unwrap();
    assert_eq!(same.updated_at, Some(stamped));
    for edge in ["0.00", "100.00"] {
        let state = replace_rules(&db, &actor, store.id, Some(dec(edge)))
            .await
            .unwrap();
        assert_eq!(state.max_discount_percent, Some(dec(edge)));
    }
    let published: Option<String> = owner
        .query_one(
            "SELECT max_discount_percent::text FROM api_stores.store_directory_v2 \
             WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(published.as_deref(), Some("100.00"));

    // Removing the cap is a write, and the row stays to say so.
    let cleared = replace_rules(&db, &actor, store.id, None).await.unwrap();
    assert_eq!(cleared.max_discount_percent, None);
    assert!(cleared.updated_at.is_some());

    // The service's own check, and the schema's for any other writer.
    let error = replace_rules(&db, &actor, store.id, Some(dec("100.01")))
        .await
        .unwrap_err();
    assert_eq!((error.status, error.code.as_str()), (422, "invalid_body"));
    assert_eq!(error.details[0].field, "max_discount_percent");
    for bad in ["100.01", "-0.01"] {
        let refused = owner
            .execute(
                "UPDATE stores.store_commercial_rule SET max_discount_percent = $2::text::numeric \
                 WHERE store_id = $1",
                &[&store.id, &bad],
            )
            .await
            .unwrap_err();
        assert_eq!(
            refused.as_db_error().and_then(|e| e.constraint()),
            Some("store_commercial_rule_max_discount_percent_check"),
            "{bad}"
        );
    }
    forget(&owner, store).await;
}

#[tokio::test]
async fn the_web_writing_is_active_and_the_service_writing_status_are_independent_facts() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let (admin, _) = users(&owner).await;
    let store = TestStore::create(&owner).await;

    // Both write the same store, in either order. The web owns is_active and
    // the service owns the status; neither overwrites the other, and the
    // directory shows both for a consumer to combine.
    replace_status(&db, &principal(admin), store.id, "TEMPORARILY_CLOSED")
        .await
        .unwrap();
    owner
        .execute(
            "UPDATE public.store SET is_active = FALSE WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap();
    replace_status(&db, &principal(admin), store.id, "OPEN")
        .await
        .unwrap();
    let row = owner
        .query_one(
            "SELECT is_active, status FROM api_stores.store_directory_v2 WHERE store_id = $1",
            &[&store.id],
        )
        .await
        .unwrap();
    assert_eq!(
        (row.get::<_, bool>(0), row.get::<_, String>(1)),
        (false, "OPEN".to_owned())
    );
    // The web's write was announced as well: every writer is visible.
    let events: i64 = owner
        .query_one(
            "SELECT count(*) FROM stores_events.outbox WHERE entity_id = $1 \
             AND event_type = 'UPDATE'",
            &[&store.id.to_string()],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(events, 2, "the web's is_active and the service's status");
    forget(&owner, store).await;
}
