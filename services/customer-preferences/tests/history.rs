//! The preference history (F21-17): each replace of a declared set that
//! changes it appends its before and after in the replacing transaction, while
//! RN-54, the whole-set replace, holds as it always did.

mod support;

use customer_preferences::{read_changes, replace, PreferenceSet};
use support::{
    admin, category_ids, channel_ids, connect, missing_channel, owner_url, principal,
    service_database, TestCustomer,
};
use uuid::Uuid;

const CHANNELS: PreferenceSet = PreferenceSet::PreferredChannels;
const CATEGORIES: PreferenceSet = PreferenceSet::InterestCategories;

fn some(ids: &[i16]) -> Vec<Option<i16>> {
    ids.iter().copied().map(Some).collect()
}

/// (set, before, after) of every change of a customer, oldest first.
async fn history(
    owner: &tokio_postgres::Client,
    customer: Uuid,
) -> Vec<(String, Vec<i16>, Vec<i16>, Uuid)> {
    owner
        .query(
            "SELECT h.preference_set, \
                    ARRAY(SELECT member_id FROM preferences.preference_change_member AS m \
                          WHERE m.change_id = h.change_id AND side = 'BEFORE' ORDER BY member_id), \
                    ARRAY(SELECT member_id FROM preferences.preference_change_member AS m \
                          WHERE m.change_id = h.change_id AND side = 'AFTER' ORDER BY member_id), \
                    h.changed_by \
             FROM preferences.preference_change AS h WHERE h.customer_id = $1 ORDER BY h.change_id",
            &[&customer],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| (row.get(0), row.get(1), row.get(2), row.get(3)))
        .collect()
}

#[tokio::test]
async fn each_changing_replace_records_its_before_and_after_and_a_repeat_records_nothing() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let actor = principal(admin(), "LOYALTY_OFFICER");
    let customer = TestCustomer::create(&owner).await;
    let channels = channel_ids(&owner).await;
    let categories = category_ids(&owner).await;

    let first = [channels[2], channels[0]];
    replace(&db, &actor, customer.id, CHANNELS, &some(&first))
        .await
        .unwrap();
    // The same set in another order is the same set: it changes nothing.
    replace(
        &db,
        &actor,
        customer.id,
        CHANNELS,
        &some(&[channels[0], channels[2]]),
    )
    .await
    .unwrap();
    let second = [channels[2], channels[4]];
    replace(&db, &actor, customer.id, CHANNELS, &some(&second))
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
    replace(&db, &actor, customer.id, CHANNELS, &[])
        .await
        .unwrap();

    let mut a = first.to_vec();
    a.sort_unstable();
    let mut b = second.to_vec();
    b.sort_unstable();
    assert_eq!(
        history(&owner, customer.id).await,
        vec![
            ("PREFERRED_CHANNELS".to_owned(), vec![], a.clone(), admin()),
            ("PREFERRED_CHANNELS".to_owned(), a, b.clone(), admin()),
            (
                "INTEREST_CATEGORIES".to_owned(),
                vec![],
                categories[..3].to_vec(),
                admin()
            ),
            ("PREFERRED_CHANNELS".to_owned(), b, vec![], admin()),
        ]
    );
    // RN-54 is untouched: the sets are exactly what was last submitted.
    assert!(customer.channels(&owner).await.is_empty());
    assert_eq!(customer.categories(&owner).await, categories[..3].to_vec());

    // The service's own reader returns the same, newest first, and filters.
    let (all, total) = read_changes(&db, customer.id, None, 1, 20).await.unwrap();
    assert_eq!(total, 4);
    assert_eq!(all.len(), 4);
    let (only, total) = read_changes(&db, customer.id, Some("INTEREST_CATEGORIES"), 1, 20)
        .await
        .unwrap();
    assert_eq!((only.len(), total), (1, 1));
    let (page, total) = read_changes(&db, customer.id, None, 2, 3).await.unwrap();
    assert_eq!((page.len(), total), (1, 4));

    let error = read_changes(&db, Uuid::new_v4(), None, 1, 20)
        .await
        .unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (404, "customer_not_found")
    );
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn a_refused_replace_leaves_neither_the_set_nor_a_history_row() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let actor = principal(admin(), "ADMIN");
    let customer = TestCustomer::create(&owner).await;
    let channels = channel_ids(&owner).await;
    let none = missing_channel(&owner).await;

    replace(&db, &actor, customer.id, CHANNELS, &some(&channels[..2]))
        .await
        .unwrap();
    let error = replace(
        &db,
        &actor,
        customer.id,
        CHANNELS,
        &some(&[channels[3], none]),
    )
    .await
    .unwrap_err();
    assert_eq!(error.code, "unknown_channel");
    assert_eq!(customer.channels(&owner).await, channels[..2].to_vec());
    assert_eq!(history(&owner, customer.id).await.len(), 1);

    // A session whose sub is no user cannot change a set at all, and writes
    // no history, since the history is attributed.
    let stranger = principal(Uuid::new_v4(), "ADMIN");
    let error = replace(
        &db,
        &stranger,
        customer.id,
        CHANNELS,
        &some(&channels[3..4]),
    )
    .await
    .unwrap_err();
    assert_eq!((error.status, error.code.as_str()), (401, "invalid_token"));
    assert_eq!(customer.channels(&owner).await, channels[..2].to_vec());
    assert_eq!(history(&owner, customer.id).await.len(), 1);
    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn concurrent_replaces_leave_a_history_that_chains_and_ends_at_the_set() {
    let Some(url) = owner_url() else { return };
    let owner = connect(&url).await;
    let db = service_database(&url);
    let actor = principal(admin(), "ADMIN");
    let customer = TestCustomer::create(&owner).await;
    let channels = channel_ids(&owner).await;

    for round in 0..6usize {
        let one = some(&channels[round % 3..round % 3 + 2]);
        let two = some(&[channels[4], channels[(round + 1) % 3]]);
        let (a, b) = tokio::join!(
            replace(&db, &actor, customer.id, CHANNELS, &one),
            replace(&db, &actor, customer.id, CHANNELS, &two),
        );
        a.unwrap();
        b.unwrap();
    }
    let changes = history(&owner, customer.id).await;
    assert!(!changes.is_empty());
    // Each change starts where the previous one ended, and the last one ends
    // at the set the customer holds: the two never disagree.
    let mut previous: Vec<i16> = Vec::new();
    for (_, before, after, _) in &changes {
        assert_eq!(*before, previous);
        previous = after.clone();
    }
    assert_eq!(previous, customer.channels(&owner).await);
    customer.cleanup(&owner).await;
}
