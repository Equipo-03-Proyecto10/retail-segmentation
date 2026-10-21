//! RN-54 against PostgreSQL, through the service's own read and replace path,
//! connected as `svc_preferences`.

mod support;

use std::sync::Arc;
use std::time::Duration;

use customer_preferences::{read_profile, replace, PreferenceSet, LOCK_CLASS};
use support::{
    category_ids, channel_ids, connect, missing_category, missing_channel, owner_url, principal,
    service_database, service_url, TestCustomer,
};
use uuid::Uuid;

const CHANNELS: PreferenceSet = PreferenceSet::PreferredChannels;
const CATEGORIES: PreferenceSet = PreferenceSet::InterestCategories;

fn some(ids: &[i16]) -> Vec<Option<i16>> {
    ids.iter().copied().map(Some).collect()
}

#[tokio::test]
async fn a_put_makes_the_set_exactly_the_list_sorted_and_leaves_the_other_alone() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let actor = principal(Uuid::new_v4(), "STORE_MANAGER");
    let customer = TestCustomer::create(&owner).await;
    let channels = channel_ids(&owner).await;
    let categories = category_ids(&owner).await;

    let first = [categories[2], categories[0], categories[5]];
    let replaced = replace(&db, &actor, customer.id, CATEGORIES, &some(&first))
        .await
        .unwrap();
    let mut sorted = first.to_vec();
    sorted.sort_unstable();
    assert_eq!(replaced.profile.interest_category_ids, sorted);
    assert!(replaced.profile.preferred_channel_ids.is_empty());

    let wanted = [channels[2], channels[0]];
    replace(&db, &actor, customer.id, CHANNELS, &some(&wanted))
        .await
        .unwrap();
    // Overlapping replace: one id kept, one removed, one added.
    let second = [categories[5], categories[7]];
    let replaced = replace(&db, &actor, customer.id, CATEGORIES, &some(&second))
        .await
        .unwrap();
    assert_eq!(replaced.profile.interest_category_ids, second.to_vec());
    assert_eq!(customer.categories(&owner).await, second.to_vec());
    // Replacing the categories never touched the channels.
    assert_eq!(
        customer.channels(&owner).await,
        vec![channels[0], channels[2]]
    );
    let read = read_profile(&db, customer.id).await.unwrap().unwrap();
    assert_eq!(read, replaced.profile);
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn an_empty_list_clears_one_set_only() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let actor = principal(Uuid::new_v4(), "ADMIN");
    let customer = TestCustomer::create(&owner).await;
    let channels = channel_ids(&owner).await;
    let categories = category_ids(&owner).await;
    replace(&db, &actor, customer.id, CHANNELS, &some(&channels[..2]))
        .await
        .unwrap();
    replace(
        &db,
        &actor,
        customer.id,
        CATEGORIES,
        &some(&categories[..3]),
    )
    .await
    .unwrap();

    let cleared = replace(&db, &actor, customer.id, CHANNELS, &[])
        .await
        .unwrap();
    assert!(cleared.profile.preferred_channel_ids.is_empty());
    assert_eq!(
        cleared.profile.interest_category_ids,
        categories[..3].to_vec()
    );
    assert!(customer.channels(&owner).await.is_empty());
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn an_unknown_reference_is_422_naming_its_position_and_changes_nothing() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let actor = principal(Uuid::new_v4(), "STORE_MANAGER");
    let customer = TestCustomer::create(&owner).await;
    let channels = channel_ids(&owner).await;
    let categories = category_ids(&owner).await;
    replace(&db, &actor, customer.id, CHANNELS, &some(&channels[..2]))
        .await
        .unwrap();
    replace(
        &db,
        &actor,
        customer.id,
        CATEGORIES,
        &some(&categories[..2]),
    )
    .await
    .unwrap();
    let events_before = customer.events(&owner).await.len();

    // The unknown id sits after a valid new one, so the transaction has
    // already deleted and inserted when the foreign key refuses it.
    let unknown = missing_channel(&owner).await;
    let error = replace(
        &db,
        &actor,
        customer.id,
        CHANNELS,
        &some(&[channels[3], unknown, channels[0]]),
    )
    .await
    .unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (422, "unknown_channel")
    );
    assert_eq!(
        (
            error.details[0].field.as_str(),
            error.details[0].code.as_str()
        ),
        ("channel_ids[1]", "unknown")
    );
    assert_eq!(customer.channels(&owner).await, channels[..2].to_vec());

    let unknown = missing_category(&owner).await;
    let error = replace(
        &db,
        &actor,
        customer.id,
        CATEGORIES,
        &some(&[unknown, categories[4]]),
    )
    .await
    .unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (422, "unknown_category")
    );
    assert_eq!(error.details[0].field, "category_ids[0]");
    assert_eq!(customer.categories(&owner).await, categories[..2].to_vec());

    // An id outside the smallint key range can name nothing either.
    let error = replace(
        &db,
        &actor,
        customer.id,
        CATEGORIES,
        &[Some(categories[4]), None],
    )
    .await
    .unwrap_err();
    assert_eq!(error.code, "unknown_category");
    assert_eq!(error.details[0].field, "category_ids[1]");
    assert_eq!(customer.categories(&owner).await, categories[..2].to_vec());

    // A refused replace captured no event: its rows were rolled back.
    assert_eq!(customer.events(&owner).await.len(), events_before);
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn an_unknown_customer_is_404_customer_not_found() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let actor = principal(Uuid::new_v4(), "ADMIN");
    let channels = channel_ids(&owner).await;
    let nobody = Uuid::new_v4();
    assert_eq!(read_profile(&db, nobody).await.unwrap(), None);
    for (set, ids) in [(CHANNELS, some(&channels[..1])), (CATEGORIES, vec![])] {
        let error = replace(&db, &actor, nobody, set, &ids).await.unwrap_err();
        assert_eq!(
            (error.status, error.code.as_str()),
            (404, "customer_not_found")
        );
    }
}

#[tokio::test]
async fn a_customer_with_no_preferences_reads_as_two_empty_lists() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let customer = TestCustomer::create(&owner).await;
    let profile = read_profile(&db, customer.id).await.unwrap().unwrap();
    assert!(profile.preferred_channel_ids.is_empty());
    assert!(profile.interest_category_ids.is_empty());
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn the_transaction_carries_the_caller_as_the_audit_actor() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let customer = TestCustomer::create(&owner).await;
    let channels = channel_ids(&owner).await;
    let caller = Uuid::new_v4();
    let replaced = replace(
        &db,
        &principal(caller, "STORE_MANAGER"),
        customer.id,
        CHANNELS,
        &some(&channels[..1]),
    )
    .await
    .unwrap();
    // Read with current_setting('mosaiq.user_id', true) inside the replacing
    // transaction, before its commit.
    assert_eq!(replaced.audit_actor, Some(caller.to_string()));
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn a_changing_replace_captures_one_event_per_row_and_a_no_op_captures_none() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let actor = principal(Uuid::new_v4(), "ADMIN");
    let customer = TestCustomer::create(&owner).await;
    let channels = channel_ids(&owner).await;
    let stream = "stream:preferences.v1.changed".to_owned();

    replace(&db, &actor, customer.id, CHANNELS, &some(&channels[..2]))
        .await
        .unwrap();
    assert_eq!(
        customer.events(&owner).await,
        vec![
            ("INSERT".to_owned(), stream.clone()),
            ("INSERT".to_owned(), stream.clone())
        ]
    );
    // The same set again, in another order, changes nothing.
    replace(
        &db,
        &actor,
        customer.id,
        CHANNELS,
        &some(&[channels[1], channels[0]]),
    )
    .await
    .unwrap();
    assert_eq!(customer.events(&owner).await.len(), 2);
    // One removed and one added: a DELETE and an INSERT.
    replace(
        &db,
        &actor,
        customer.id,
        CHANNELS,
        &some(&[channels[0], channels[2]]),
    )
    .await
    .unwrap();
    let mut kinds: Vec<String> = customer
        .events(&owner)
        .await
        .into_iter()
        .map(|(kind, _)| kind)
        .collect();
    kinds.sort();
    assert_eq!(kinds, ["DELETE", "INSERT", "INSERT", "INSERT"]);
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn a_replace_waits_for_the_customer_advisory_lock() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let holder = connect(&url).await;
    let db = Arc::new(service_database(&url));
    let customer = TestCustomer::create(&owner).await;
    let channels = channel_ids(&owner).await;

    holder
        .query_one(
            "SELECT pg_advisory_lock($1, hashtext($2::uuid::text))",
            &[&LOCK_CLASS, &customer.id],
        )
        .await
        .unwrap();
    let id = customer.id;
    let wanted = some(&channels[..1]);
    let task = {
        let db = Arc::clone(&db);
        tokio::spawn(async move {
            replace(
                &db,
                &principal(Uuid::new_v4(), "ADMIN"),
                id,
                CHANNELS,
                &wanted,
            )
            .await
        })
    };
    tokio::time::sleep(Duration::from_millis(500)).await;
    assert!(!task.is_finished(), "the replace ran without the lock");
    assert!(customer.channels(&owner).await.is_empty());
    holder
        .query_one(
            "SELECT pg_advisory_unlock($1, hashtext($2::uuid::text))",
            &[&LOCK_CLASS, &customer.id],
        )
        .await
        .unwrap();
    task.await.unwrap().unwrap();
    assert_eq!(customer.channels(&owner).await, channels[..1].to_vec());
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn two_concurrent_replaces_leave_one_submitted_set_never_a_union() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = Arc::new(service_database(&url));
    let customer = TestCustomer::create(&owner).await;
    let categories = category_ids(&owner).await;
    let left: Vec<i16> = categories[..4].to_vec();
    let right: Vec<i16> = categories[3..8].to_vec();

    for round in 0..50 {
        // Start each round from a third set, so either order changes rows.
        let start = if round % 2 == 0 {
            vec![categories[9]]
        } else {
            vec![]
        };
        replace(
            &db,
            &principal(Uuid::new_v4(), "ADMIN"),
            customer.id,
            CATEGORIES,
            &some(&start),
        )
        .await
        .unwrap();
        let spawn = |ids: Vec<i16>| {
            let db = Arc::clone(&db);
            let id = customer.id;
            tokio::spawn(async move {
                replace(
                    &db,
                    &principal(Uuid::new_v4(), "STORE_MANAGER"),
                    id,
                    CATEGORIES,
                    &some(&ids),
                )
                .await
            })
        };
        let (a, b) = tokio::join!(spawn(left.clone()), spawn(right.clone()));
        a.unwrap().unwrap();
        b.unwrap().unwrap();
        let after = customer.categories(&owner).await;
        assert!(
            after == left || after == right,
            "round {round}: {after:?} is neither submitted set"
        );
    }
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn the_role_replaces_but_never_updates_and_reads_no_master_table() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let service = connect(&service_url(&url)).await;
    let customer = TestCustomer::create(&owner).await;
    for statement in [
        "UPDATE public.customer_preferred_channel SET channel_id = channel_id \
         WHERE customer_id = $1",
        "UPDATE public.customer_interest_category SET category_id = category_id \
         WHERE customer_id = $1",
        "SELECT name FROM public.customer WHERE customer_id = $1",
    ] {
        let denied = service
            .execute(statement, &[&customer.id])
            .await
            .unwrap_err();
        assert_eq!(
            denied.code().map(|c| c.code()),
            Some("42501"),
            "{statement}"
        );
    }
    for statement in [
        "SELECT count(*) FROM public.channel",
        "SELECT count(*) FROM public.category",
        "DELETE FROM preferences_events.outbox WHERE false",
    ] {
        let outcome = service.query(statement, &[]).await;
        if statement.starts_with("DELETE") {
            outcome.unwrap();
        } else {
            let denied = outcome.unwrap_err();
            assert_eq!(
                denied.code().map(|c| c.code()),
                Some("42501"),
                "{statement}"
            );
        }
    }
    // The published view is readable, as recommendations reads it.
    service
        .query(
            "SELECT customer_id, category_id FROM api_preferences.interest_category_v1 \
             WHERE customer_id = $1",
            &[&customer.id],
        )
        .await
        .unwrap();
    customer.cleanup(&owner).await;
}
