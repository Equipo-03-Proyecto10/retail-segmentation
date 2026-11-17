//! Consent, restrictions and the preference history (F21-17, RN-73, ADR-0066)
//! against PostgreSQL, through the service's own functions, connected as
//! `svc_preferences`: what is appended, what the published views show, what
//! the schema refuses when a writer goes around the service, and what the seed
//! decided for the customers that already exist.

mod support;

use std::time::Duration;

use chrono::{DateTime, Utc};
use customer_preferences::{
    read_changes, read_consent, read_restrictions, record_consent, record_restrictions, replace,
    ConsentState, PreferenceSet, RestrictionEntry, CONSENT_STREAM,
};
use support::{
    admin, category_ids, channel_ids, connect, missing_category, missing_channel, owner_url,
    principal, service_database, service_url, TestCustomer,
};
use tokio_postgres::Client;
use uuid::Uuid;

fn entries(list: &[(&str, &str)]) -> Vec<(String, String)> {
    list.iter()
        .map(|(purpose, decision)| ((*purpose).to_owned(), (*decision).to_owned()))
        .collect()
}

fn channel(id: i16, decision: &str) -> RestrictionEntry {
    RestrictionEntry {
        kind: "CHANNEL",
        target: Some(id),
        decision: decision.to_owned(),
    }
}

fn category(id: i16, decision: &str) -> RestrictionEntry {
    RestrictionEntry {
        kind: "INTEREST_CATEGORY",
        target: Some(id),
        decision: decision.to_owned(),
    }
}

fn state_of(state: &ConsentState, purpose: &str) -> String {
    state
        .purposes
        .iter()
        .find(|candidate| candidate.purpose == purpose)
        .and_then(|candidate| candidate.decision.as_ref())
        .map_or("NOT_RECORDED".to_owned(), |recorded| {
            recorded.decision.clone()
        })
}

async fn log_of(owner: &Client, customer: Uuid) -> Vec<(i64, String, String)> {
    owner
        .query(
            "SELECT decision_id, purpose, decision FROM preferences.consent_decision \
             WHERE customer_id = $1 ORDER BY decision_id",
            &[&customer],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| (row.get(0), row.get(1), row.get(2)))
        .collect()
}

async fn consent_events(owner: &Client, customer: Uuid) -> i64 {
    owner
        .query_one(
            "SELECT count(*) FROM preferences_events.outbox WHERE entity_id = $1 AND stream = $2",
            &[&customer.to_string(), &CONSENT_STREAM],
        )
        .await
        .unwrap()
        .get(0)
}

async fn database_clock(owner: &Client) -> DateTime<Utc> {
    owner
        .query_one("SELECT clock_timestamp()", &[])
        .await
        .unwrap()
        .get(0)
}

#[tokio::test]
async fn a_decision_is_appended_the_previous_one_stays_and_any_past_instant_is_derivable() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let actor = principal(admin(), "LOYALTY_OFFICER");
    let customer = TestCustomer::create(&owner).await;
    let before_anything = database_clock(&owner).await;

    // A registered customer with no decision reads NOT_RECORDED, which no
    // reader treats as granted.
    let connection = db.connection().await.unwrap();
    let unrecorded = read_consent(&***connection, customer.id, None)
        .await
        .unwrap();
    drop(connection);
    for purpose in ["PERSONALIZATION", "MARKETING_CONTACT", "PROFILING"] {
        assert_eq!(state_of(&unrecorded, purpose), "NOT_RECORDED");
        assert!(!unrecorded.is_granted(purpose));
    }
    assert_eq!(
        unrecorded
            .purposes
            .iter()
            .map(|p| p.purpose)
            .collect::<Vec<_>>(),
        ["PERSONALIZATION", "MARKETING_CONTACT", "PROFILING"]
    );

    let granted = record_consent(
        &db,
        &actor,
        customer.id,
        &entries(&[("PERSONALIZATION", "GRANTED"), ("PROFILING", "GRANTED")]),
    )
    .await
    .unwrap();
    assert_eq!(state_of(&granted, "PERSONALIZATION"), "GRANTED");
    assert_eq!(state_of(&granted, "MARKETING_CONTACT"), "NOT_RECORDED");
    let recorded = granted.purposes[0].decision.as_ref().unwrap();
    assert_eq!(
        (recorded.source.as_str(), recorded.basis.as_str()),
        ("STAFF_ON_BEHALF", "EXPLICIT")
    );
    assert_eq!(
        recorded.recorded_by,
        Some(admin()),
        "from the token, not a body"
    );
    let between = database_clock(&owner).await;
    tokio::time::sleep(Duration::from_millis(30)).await;

    // The objection: a new row, and the grant before it is still there.
    let objection = record_consent(
        &db,
        &actor,
        customer.id,
        &entries(&[("PERSONALIZATION", "WITHDRAWN")]),
    )
    .await
    .unwrap();
    assert_eq!(state_of(&objection, "PERSONALIZATION"), "WITHDRAWN");
    assert_eq!(state_of(&objection, "PROFILING"), "GRANTED");
    let log = log_of(&owner, customer.id).await;
    assert_eq!(
        log.iter()
            .map(|(_, purpose, decision)| (purpose.as_str(), decision.as_str()))
            .collect::<Vec<_>>(),
        [
            ("PERSONALIZATION", "GRANTED"),
            ("PROFILING", "GRANTED"),
            ("PERSONALIZATION", "WITHDRAWN")
        ],
        "a withdrawal never rewrites the grant before it"
    );
    assert!(log.windows(2).all(|pair| pair[0].0 < pair[1].0));

    // The state at any past instant: before anything, between the two
    // decisions, and now.
    let connection = db.connection().await.unwrap();
    let at = |instant| read_consent(&***connection, customer.id, Some(instant));
    assert_eq!(
        state_of(&at(before_anything).await.unwrap(), "PERSONALIZATION"),
        "NOT_RECORDED"
    );
    let then = at(between).await.unwrap();
    assert_eq!(state_of(&then, "PERSONALIZATION"), "GRANTED");
    assert_eq!(then.as_of, between);
    let now = read_consent(&***connection, customer.id, None)
        .await
        .unwrap();
    assert_eq!(state_of(&now, "PERSONALIZATION"), "WITHDRAWN");

    // A confirming decision is appended even though it equals the state.
    drop(connection);
    record_consent(
        &db,
        &actor,
        customer.id,
        &entries(&[("PERSONALIZATION", "WITHDRAWN")]),
    )
    .await
    .unwrap();
    assert_eq!(log_of(&owner, customer.id).await.len(), 4);

    // An instant after the database's clock is refused.
    let connection = db.connection().await.unwrap();
    let future = database_clock(&owner).await + chrono::Duration::hours(1);
    let error = read_consent(&***connection, customer.id, Some(future))
        .await
        .unwrap_err();
    assert_eq!((error.status, error.code.as_str()), (422, "invalid_query"));
    assert_eq!(error.details[0].field, "as_of");
    assert_eq!(error.details[0].code, "in_the_future");
    drop(connection);
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn an_objection_is_in_the_published_view_and_announced_when_it_commits() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let actor = principal(admin(), "LOYALTY_OFFICER");
    let customer = TestCustomer::create(&owner).await;

    record_consent(
        &db,
        &actor,
        customer.id,
        &entries(&[("PERSONALIZATION", "GRANTED")]),
    )
    .await
    .unwrap();
    assert_eq!(consent_events(&owner, customer.id).await, 1);
    record_consent(
        &db,
        &actor,
        customer.id,
        &entries(&[
            ("PERSONALIZATION", "WITHDRAWN"),
            ("MARKETING_CONTACT", "WITHDRAWN"),
        ]),
    )
    .await
    .unwrap();
    // One event per row, key only, in the same transaction as the row.
    assert_eq!(consent_events(&owner, customer.id).await, 3);
    let view: Vec<(String, String, String)> = owner
        .query(
            "SELECT purpose, decision, basis FROM api_preferences.consent_state_v1 \
             WHERE customer_id = $1 ORDER BY purpose",
            &[&customer.id],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| (row.get(0), row.get(1), row.get(2)))
        .collect();
    assert_eq!(
        view,
        [
            (
                "MARKETING_CONTACT".into(),
                "WITHDRAWN".into(),
                "EXPLICIT".into()
            ),
            (
                "PERSONALIZATION".into(),
                "WITHDRAWN".into(),
                "EXPLICIT".into()
            ),
        ]
    );
    // The same state, through the web's read-only view in public.
    let public: i64 = owner
        .query_one(
            "SELECT count(*) FROM public.consent_state WHERE customer_id = $1 \
             AND decision = 'WITHDRAWN'",
            &[&customer.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(public, 2);

    // A decision that fails after earlier ones in the same call announces and
    // appends nothing: a stranger as actor fails on the first row.
    let stranger = principal(Uuid::new_v4(), "ADMIN");
    let error = record_consent(
        &db,
        &stranger,
        customer.id,
        &entries(&[("PROFILING", "GRANTED")]),
    )
    .await
    .unwrap_err();
    assert_eq!((error.status, error.code.as_str()), (401, "invalid_token"));
    assert_eq!(log_of(&owner, customer.id).await.len(), 3);
    assert_eq!(consent_events(&owner, customer.id).await, 3);

    let error = record_consent(
        &db,
        &actor,
        Uuid::new_v4(),
        &entries(&[("PROFILING", "GRANTED")]),
    )
    .await
    .unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (404, "customer_not_found")
    );
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn restrictions_are_appended_lifted_and_published_only_while_imposed() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let actor = principal(admin(), "LOYALTY_OFFICER");
    let customer = TestCustomer::create(&owner).await;
    let channels = channel_ids(&owner).await;
    let categories = category_ids(&owner).await;

    let none = {
        let connection = db.connection().await.unwrap();
        read_restrictions(&***connection, customer.id)
            .await
            .unwrap()
    };
    assert!(
        !none.personalization_allowed,
        "never recorded reads as false"
    );
    assert!(none.channel_ids.is_empty() && none.category_ids.is_empty());

    let imposed = record_restrictions(
        &db,
        &actor,
        customer.id,
        &[
            channel(channels[3], "IMPOSED"),
            category(categories[8], "IMPOSED"),
            category(categories[2], "IMPOSED"),
        ],
    )
    .await
    .unwrap();
    assert_eq!(imposed.channel_ids, [channels[3]]);
    let mut wanted = vec![categories[8], categories[2]];
    wanted.sort_unstable();
    assert_eq!(imposed.category_ids, wanted);
    assert_eq!(consent_events(&owner, customer.id).await, 3);

    let lifted = record_restrictions(
        &db,
        &actor,
        customer.id,
        &[category(categories[8], "LIFTED")],
    )
    .await
    .unwrap();
    assert_eq!(lifted.category_ids, [categories[2]]);
    let rows: i64 = owner
        .query_one(
            "SELECT count(*) FROM preferences.restriction_decision WHERE customer_id = $1",
            &[&customer.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(
        rows, 4,
        "the lifted restriction's decision is still in the log"
    );
    let published: i64 = owner
        .query_one(
            "SELECT count(*) FROM api_preferences.restriction_v1 WHERE customer_id = $1",
            &[&customer.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(published, 2);

    // Personalization allowed follows the consent decision.
    record_consent(
        &db,
        &actor,
        customer.id,
        &entries(&[("PERSONALIZATION", "GRANTED")]),
    )
    .await
    .unwrap();
    let granted = {
        let connection = db.connection().await.unwrap();
        read_restrictions(&***connection, customer.id)
            .await
            .unwrap()
    };
    assert!(granted.personalization_allowed);
    record_consent(
        &db,
        &actor,
        customer.id,
        &entries(&[("PERSONALIZATION", "WITHDRAWN")]),
    )
    .await
    .unwrap();
    let withdrawn = {
        let connection = db.connection().await.unwrap();
        read_restrictions(&***connection, customer.id)
            .await
            .unwrap()
    };
    assert!(!withdrawn.personalization_allowed);
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn a_restriction_naming_nothing_is_422_by_position_and_appends_none_of_the_call() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let actor = principal(admin(), "ADMIN");
    let customer = TestCustomer::create(&owner).await;
    let channels = channel_ids(&owner).await;
    let no_channel = missing_channel(&owner).await;
    let no_category = missing_category(&owner).await;

    for (bad, code) in [
        (channel(no_channel, "IMPOSED"), "unknown_channel"),
        (category(no_category, "IMPOSED"), "unknown_category"),
        (
            RestrictionEntry {
                kind: "CHANNEL",
                target: None,
                decision: "IMPOSED".into(),
            },
            "unknown_channel",
        ),
    ] {
        let error = record_restrictions(
            &db,
            &actor,
            customer.id,
            &[channel(channels[0], "IMPOSED"), bad],
        )
        .await
        .unwrap_err();
        assert_eq!((error.status, error.code.as_str()), (422, code));
        assert_eq!(error.details[0].field, "decisions[1].target_id");
        assert_eq!(error.details[0].code, "unknown");
    }
    let rows: i64 = owner
        .query_one(
            "SELECT count(*) FROM preferences.restriction_decision WHERE customer_id = $1",
            &[&customer.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(
        rows, 0,
        "the valid first entry was rolled back with the call"
    );
    assert_eq!(consent_events(&owner, customer.id).await, 0);
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn nothing_edits_or_removes_a_row_of_the_logs_or_the_history() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let service = connect(&service_url(&url)).await;
    let db = service_database(&url);
    let actor = principal(admin(), "ADMIN");
    let customer = TestCustomer::create(&owner).await;
    let channels = channel_ids(&owner).await;
    record_consent(
        &db,
        &actor,
        customer.id,
        &entries(&[("PROFILING", "GRANTED")]),
    )
    .await
    .unwrap();
    record_restrictions(&db, &actor, customer.id, &[channel(channels[0], "IMPOSED")])
        .await
        .unwrap();
    replace(
        &db,
        &actor,
        customer.id,
        PreferenceSet::PreferredChannels,
        &[Some(channels[0])],
    )
    .await
    .unwrap();

    // (table, UPDATE, DELETE, TRUNCATE), each a constant statement.
    let tables = [
        (
            "consent_decision",
            "UPDATE preferences.consent_decision SET decision = 'WITHDRAWN'",
            "DELETE FROM preferences.consent_decision",
            "TRUNCATE preferences.consent_decision",
        ),
        (
            "restriction_decision",
            "UPDATE preferences.restriction_decision SET decision = 'LIFTED'",
            "DELETE FROM preferences.restriction_decision",
            "TRUNCATE preferences.restriction_decision",
        ),
        (
            "preference_change",
            "UPDATE preferences.preference_change SET preference_set = 'INTEREST_CATEGORIES'",
            "DELETE FROM preferences.preference_change",
            "TRUNCATE preferences.preference_change",
        ),
        (
            "preference_change_member",
            "UPDATE preferences.preference_change_member SET side = 'BEFORE'",
            "DELETE FROM preferences.preference_change_member",
            "TRUNCATE preferences.preference_change_member",
        ),
    ];
    for (table, update, delete, truncate) in tables {
        // The service role holds SELECT and INSERT and nothing else.
        for statement in [update, delete, truncate] {
            let error = service.batch_execute(statement).await.unwrap_err();
            assert_eq!(
                error.code().map(|c| c.code()),
                Some("42501"),
                "{statement}: {error}"
            );
        }
        // The owner is refused by the trigger, whatever the statement.
        for statement in [update, delete, truncate] {
            let error = owner.batch_execute(statement).await.unwrap_err();
            let database = error.as_db_error().expect(statement);
            if statement.starts_with("TRUNCATE") && table == "preference_change" {
                // PostgreSQL itself refuses to truncate a table another
                // table references, before the trigger would.
                assert_eq!(database.code().code(), "0A000", "{statement}");
                continue;
            }
            assert_eq!(database.code().code(), "23514", "{statement}");
            assert!(
                database.message().contains("append-only"),
                "{statement}: {}",
                database.message()
            );
        }
    }
    // The role may append, and a row it appended is still there afterwards.
    let still: i64 = owner
        .query_one(
            "SELECT (SELECT count(*) FROM preferences.consent_decision WHERE customer_id = $1) \
                  + (SELECT count(*) FROM preferences.restriction_decision WHERE customer_id = $1) \
                  + (SELECT count(*) FROM preferences.preference_change WHERE customer_id = $1)",
            &[&customer.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(still, 3);
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn the_schema_refuses_a_decision_that_is_not_a_decision() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let customer = TestCustomer::create(&owner).await;
    let actor = admin();
    // (statement, whether it binds the actor as $2, the constraint refusing it)
    for (statement, with_actor, constraint) in [
        (
            "INSERT INTO preferences.consent_decision \
             (customer_id, purpose, decision, recorded_by, source, basis) \
             VALUES ($1, 'ADVERTISING', 'GRANTED', $2, 'STAFF_ON_BEHALF', 'EXPLICIT')",
            true,
            "consent_decision_purpose_check",
        ),
        (
            "INSERT INTO preferences.consent_decision \
             (customer_id, purpose, decision, recorded_by, source, basis) \
             VALUES ($1, 'PROFILING', 'MAYBE', $2, 'STAFF_ON_BEHALF', 'EXPLICIT')",
            true,
            "consent_decision_decision_check",
        ),
        // An unknown actor is genuine only for the migration backfill.
        (
            "INSERT INTO preferences.consent_decision \
             (customer_id, purpose, decision, recorded_by, source, basis) \
             VALUES ($1, 'PROFILING', 'GRANTED', NULL, 'STAFF_ON_BEHALF', 'EXPLICIT')",
            false,
            "consent_decision_migration_check",
        ),
        (
            "INSERT INTO preferences.consent_decision \
             (customer_id, purpose, decision, recorded_by, source, basis) \
             VALUES ($1, 'PROFILING', 'GRANTED', $2, 'STAFF_ON_BEHALF', 'PROGRAMME_DEFAULT')",
            true,
            "consent_decision_migration_check",
        ),
        (
            "INSERT INTO preferences.restriction_decision \
             (customer_id, kind, channel_id, category_id, decision, recorded_by, source) \
             VALUES ($1, 'CHANNEL', 1, 1, 'IMPOSED', $2, 'STAFF_ON_BEHALF')",
            true,
            "restriction_decision_target_check",
        ),
        (
            "INSERT INTO preferences.restriction_decision \
             (customer_id, kind, channel_id, category_id, decision, recorded_by, source) \
             VALUES ($1, 'CHANNEL', NULL, 1, 'IMPOSED', $2, 'STAFF_ON_BEHALF')",
            true,
            "restriction_decision_target_check",
        ),
    ] {
        let error = if with_actor {
            owner.execute(statement, &[&customer.id, &actor]).await
        } else {
            owner.execute(statement, &[&customer.id]).await
        }
        .expect_err(statement);
        assert_eq!(
            error.as_db_error().and_then(|e| e.constraint()),
            Some(constraint),
            "{statement}: {error}"
        );
    }
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn the_seed_gave_each_existing_customer_the_default_adr_0066_decided() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);

    for (statement, name, minimum) in [
        (
            "SELECT count(*) FROM preferences.consent_decision",
            "consent_decision",
            30i64,
        ),
        (
            "SELECT count(*) FROM preferences.restriction_decision",
            "restriction_decision",
            30,
        ),
        (
            "SELECT count(*) FROM preferences.preference_change",
            "preference_change",
            30,
        ),
        (
            "SELECT count(*) FROM preferences.preference_change_member",
            "preference_change_member",
            30,
        ),
    ] {
        let rows: i64 = owner.query_one(statement, &[]).await.unwrap().get(0);
        assert!(rows >= minimum, "{name} holds {rows} rows");
    }

    // Every customer that existed when the log began has the three defaults,
    // as MIGRATION rows with no actor.
    let missing: i64 = owner
        .query_one(
            "SELECT count(*) FROM public.customer AS c \
             WHERE c.customer_id < '00000000-0000-0000-0000-000000000100' \
               AND (SELECT count(*) FROM preferences.consent_decision AS d \
                    WHERE d.customer_id = c.customer_id AND d.source = 'MIGRATION' \
                      AND d.recorded_by IS NULL AND d.basis = 'PROGRAMME_DEFAULT') <> 3",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(missing, 0);

    // Customer 1 has no staff decision: PERSONALIZATION and PROFILING granted
    // and MARKETING_CONTACT withdrawn, each as a programme default.
    let first = Uuid::parse_str("00000000-0000-0000-0000-000000000001").unwrap();
    let connection = db.connection().await.unwrap();
    let state = read_consent(&***connection, first, None).await.unwrap();
    assert_eq!(state_of(&state, "PERSONALIZATION"), "GRANTED");
    assert_eq!(state_of(&state, "MARKETING_CONTACT"), "WITHDRAWN");
    assert_eq!(state_of(&state, "PROFILING"), "GRANTED");
    for purpose in &state.purposes {
        let recorded = purpose.decision.as_ref().unwrap();
        assert_eq!(recorded.basis, "PROGRAMME_DEFAULT");
        assert_eq!(recorded.source, "MIGRATION");
        assert_eq!(recorded.recorded_by, None);
    }
    // Customer 20 had a staff decision on top: withdrawn personalization and
    // granted marketing contact, with the default rows still beneath.
    let twentieth = Uuid::parse_str("00000000-0000-0000-0000-000000000020").unwrap();
    let state = read_consent(&***connection, twentieth, None).await.unwrap();
    assert_eq!(state_of(&state, "PERSONALIZATION"), "WITHDRAWN");
    assert_eq!(state_of(&state, "MARKETING_CONTACT"), "GRANTED");
    let rows: i64 = owner
        .query_one(
            "SELECT count(*) FROM preferences.consent_decision WHERE customer_id = $1",
            &[&twentieth],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(rows, 5);
    drop(connection);

    // The seeded history agrees with the sets it describes.
    let disagree: i64 = owner
        .query_one(
            "SELECT count(*) FROM preferences.preference_change AS h WHERE \
               ARRAY(SELECT member_id FROM preferences.preference_change_member AS m \
                     WHERE m.change_id = h.change_id AND m.side = 'AFTER' ORDER BY member_id) \
               IS DISTINCT FROM CASE h.preference_set \
                 WHEN 'PREFERRED_CHANNELS' THEN ARRAY(SELECT channel_id \
                     FROM public.customer_preferred_channel AS c \
                     WHERE c.customer_id = h.customer_id ORDER BY channel_id) \
                 ELSE ARRAY(SELECT category_id FROM public.customer_interest_category AS i \
                     WHERE i.customer_id = h.customer_id ORDER BY category_id) END",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(disagree, 0);
    let changes = read_changes(&db, first, None, 1, 20).await.unwrap();
    assert_eq!(changes.1, 2);
}
