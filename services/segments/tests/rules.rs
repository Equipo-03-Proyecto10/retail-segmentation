//! The segments rule against the seeded PostgreSQL, connected as
//! `svc_segments`: the label list and its sizes, a customer's current
//! assignment in its three states, the history, and the refusals.

mod support;

use std::collections::{BTreeMap, BTreeSet};

use mosaiq_kit::rendering::{render_json, render_xml};
use mosaiq_kit::Reply;
use segments::{
    assignment_filter, assignments_reply, current_reply, list_reply, read_assignments,
    read_current, read_list, segment_reply, Filter, Reader,
};
use support::{connect, expected_sizes, owner_url, service_database, TestCustomer};
use uuid::Uuid;

const NAMESPACE: &str = "https://mosaiq.maxthecoder.online/xml/segments/v1";

/// Fixtures add customers and rows; the tests that count take turns.
static TURN: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

fn json(reply: &Reply) -> serde_json::Value {
    render_json(reply, 20).expect("a body")
}

fn xml(reply: &Reply) -> String {
    render_xml(reply, NAMESPACE, 20).expect("a body")
}

#[tokio::test]
async fn every_seeded_customers_current_matches_the_view_and_the_open_row() {
    let _turn = TURN.lock().await;
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    let rows = owner
        .query(
            "SELECT v.customer_id, v.state, v.label_code, v.since, v.run_id, \
                    h.r_score, h.f_score, h.m_score \
             FROM api_segments.current_label_v1 AS v \
             LEFT JOIN public.customer_segment_history AS h \
                    ON h.customer_id = v.customer_id AND h.valid_to IS NULL",
            &[],
        )
        .await
        .unwrap();
    assert!(rows.len() >= 30, "the seed has at least 30 customers");
    let mut assigned = 0;
    for row in &rows {
        let id: Uuid = row.get(0);
        let state: String = row.get(1);
        let current = read_current(&db, id).await.unwrap();
        assert_eq!(current.customer_id, id.to_string());
        assert_eq!(current.state, state, "{id}");
        assert_eq!(current.label_code, row.get::<_, Option<String>>(2), "{id}");
        assert_eq!(current.since, row.get(3), "{id}");
        assert_eq!(current.run_id, row.get::<_, Option<i64>>(4), "{id}");
        if state == "assigned" {
            assigned += 1;
            assert_eq!(current.r_score, row.get::<_, Option<i16>>(5), "{id}");
            assert_eq!(current.f_score, row.get::<_, Option<i16>>(6), "{id}");
            assert_eq!(current.m_score, row.get::<_, Option<i16>>(7), "{id}");
            assert!(current.r_score.is_some() && current.label_code.is_some());
        } else {
            assert!(current.label_code.is_none() && current.r_score.is_none());
        }
        // The open row is the only one the answer rests on.
        let (open, _) = read_assignments(&db, &Filter::Customer { id, open: true }, 1, 100)
            .await
            .unwrap();
        assert_eq!(
            open.len(),
            usize::from(state != "no_open_assignment"),
            "{id}"
        );
    }
    assert!(assigned > 0, "the seed assigns somebody");
}

#[tokio::test]
async fn the_three_states_are_answers_and_never_filled_from_another_source() {
    let _turn = TURN.lock().await;
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    let bare = TestCustomer::create(&owner).await;
    let unassigned = TestCustomer::create_unassigned(&owner).await;

    let none = read_current(&db, bare.id).await.unwrap();
    assert_eq!(none.state, "no_open_assignment");
    assert!(none.label_code.is_none() && none.since.is_none() && none.run_id.is_none());
    assert!(none.r_score.is_none() && none.f_score.is_none() && none.m_score.is_none());
    let body = json(&current_reply(&none));
    assert_eq!(body["state"], "no_open_assignment");
    assert!(body["label_code"].is_null() && body["since"].is_null());
    let text = xml(&current_reply(&none));
    assert!(text.contains("<state>no_open_assignment</state>"));
    assert!(!text.contains("label_code") && !text.contains("<since>"));

    let result = read_current(&db, unassigned.id).await.unwrap();
    assert_eq!(result.state, "unassigned_result");
    assert!(result.label_code.is_none() && result.r_score.is_none());
    assert!(result.since.is_some() && result.run_id.is_some());
    let latest: i64 = owner
        .query_one("SELECT max(run_id) FROM public.segmentation_run", &[])
        .await
        .unwrap()
        .get(0);
    assert_eq!(result.run_id, Some(latest));
    let body = json(&current_reply(&result));
    assert_eq!(body["state"], "unassigned_result");
    assert!(body["label_code"].is_null() && body["r_score"].is_null());
    assert_eq!(body["run_id"], latest);
    assert!(!xml(&current_reply(&result)).contains("label_code"));

    // The history reports the same row as recorded: no label, no scores.
    let (rows, total) = read_assignments(
        &db,
        &Filter::Customer {
            id: unassigned.id,
            open: false,
        },
        1,
        20,
    )
    .await
    .unwrap();
    assert_eq!(total, 1);
    assert!(rows[0].label_code.is_none() && rows[0].valid_to.is_none());

    // The seeded assigned customer is the third state.
    let assigned = owner
        .query_one(
            "SELECT customer_id FROM api_segments.current_label_v1 \
             WHERE state = 'assigned' LIMIT 1",
            &[],
        )
        .await
        .unwrap()
        .get::<_, Uuid>(0);
    let current = read_current(&db, assigned).await.unwrap();
    assert_eq!(current.state, "assigned");
    assert!(current.label_code.is_some() && current.since.is_some());

    bare.cleanup(&owner).await;
    unassigned.cleanup(&owner).await;
}

#[tokio::test]
async fn unknown_and_malformed_requests_are_refused() {
    let _turn = TURN.lock().await;
    let url = owner_url();
    let db = service_database(&url);
    let reader = Reader::new(None);

    let error = read_current(&db, Uuid::new_v4()).await.unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (404, "customer_not_found")
    );
    let error = read_assignments(
        &db,
        &Filter::Customer {
            id: Uuid::new_v4(),
            open: false,
        },
        1,
        20,
    )
    .await
    .unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (404, "customer_not_found")
    );
    let error = read_assignments(
        &db,
        &Filter::Label {
            code: "NO_SUCH_LABEL".to_owned(),
            open: true,
        },
        1,
        20,
    )
    .await
    .unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (404, "segment_not_found")
    );
    let error = reader.segment(&db, "NO_SUCH_LABEL").await.unwrap_err();
    assert_eq!(
        (error.status, error.code.as_str()),
        (404, "segment_not_found")
    );

    let id = Uuid::new_v4();
    for query in [
        String::new(),
        "open=true".to_owned(),
        format!("customer_id={id}&label_code=LOYAL"),
        format!("customer_id={id}&label_code=LOYAL&open=true"),
        "customer_id=not-a-uuid".to_owned(),
        "label_code=LOYAL&open=sometimes".to_owned(),
    ] {
        let error = assignment_filter(&query).unwrap_err();
        assert_eq!(
            (error.status, error.code.as_str()),
            (422, "invalid_query"),
            "{query}"
        );
        assert!(!error.details.is_empty(), "{query}");
    }
    let error = segments::current_customer("customer_id=nope").unwrap_err();
    assert_eq!((error.status, error.code.as_str()), (422, "invalid_query"));
}

#[tokio::test]
async fn sizes_come_from_the_latest_run_in_business_order() {
    let _turn = TURN.lock().await;
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    let list = read_list(&db).await.unwrap();

    let latest: i64 = owner
        .query_one("SELECT max(run_id) FROM public.segmentation_run", &[])
        .await
        .unwrap()
        .get(0);
    let run_at: chrono::DateTime<chrono::Utc> = owner
        .query_one(
            "SELECT run_at FROM public.segmentation_run WHERE run_id = $1",
            &[&latest],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(list.run_id, Some(latest));
    assert_eq!(list.run_at, Some(run_at));

    let expected: BTreeMap<String, i64> = expected_sizes(&owner).await.into_iter().collect();
    let served: BTreeMap<String, i64> = list
        .segments
        .iter()
        .filter(|segment| segment.size > 0)
        .map(|segment| (segment.label_code.clone(), segment.size))
        .collect();
    assert_eq!(served, expected);
    assert!(!expected.is_empty());

    let vocabulary: Vec<String> = owner
        .query(
            "SELECT label_code FROM public.segment_label ORDER BY ordinal_position",
            &[],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get(0))
        .collect();
    let order: Vec<String> = list.segments.iter().map(|s| s.label_code.clone()).collect();
    assert_eq!(order, vocabulary, "every label, in ordinal_position order");
    let positions: Vec<i16> = list.segments.iter().map(|s| s.ordinal_position).collect();
    assert!(positions.windows(2).all(|pair| pair[0] < pair[1]));

    let unassigned: i64 = owner
        .query_one(
            "SELECT count(*) FROM public.customer_segment_history \
             WHERE run_id = $1 AND label_code IS NULL",
            &[&latest],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(list.unassigned_count, unassigned);
    let customers: i64 = owner
        .query_one(
            "SELECT count(*) FROM public.customer_segment_history WHERE run_id = $1",
            &[&latest],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(
        list.segments.iter().map(|s| s.size).sum::<i64>() + unassigned,
        customers
    );

    // One label by its code, with the same size.
    let reader = Reader::new(None);
    let first = &list.segments[0];
    assert_eq!(
        &reader.segment(&db, &first.label_code).await.unwrap(),
        first
    );
    assert_eq!(json(&segment_reply(first))["size"], first.size);

    // JSON names the array `segments`; XML repeats `Segment`.
    let body = json(&list_reply(&list, false));
    assert_eq!(body["run_id"], latest);
    assert_eq!(body["segments"].as_array().unwrap().len(), vocabulary.len());
    let text = xml(&list_reply(&list, true));
    assert_eq!(text.matches("<Segment>").count(), vocabulary.len());
    assert!(text.starts_with("<?xml") && text.contains("<run_id>"));
}

#[tokio::test]
async fn history_is_newest_first_and_pages() {
    let _turn = TURN.lock().await;
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    let id: Uuid = owner
        .query_one(
            "SELECT customer_id FROM public.customer_segment_history \
             GROUP BY customer_id ORDER BY count(*) DESC, customer_id LIMIT 1",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    let total_rows: i64 = owner
        .query_one(
            "SELECT count(*) FROM public.customer_segment_history WHERE customer_id = $1",
            &[&id],
        )
        .await
        .unwrap()
        .get(0);
    assert!(
        total_rows > 20,
        "the seed gives a customer more than one page"
    );

    let filter = Filter::Customer { id, open: false };
    let (first, total) = read_assignments(&db, &filter, 1, 20).await.unwrap();
    let (second, again) = read_assignments(&db, &filter, 2, 20).await.unwrap();
    assert_eq!((total, again), (total_rows as u64, total_rows as u64));
    assert_eq!(first.len(), 20);
    assert_eq!(second.len(), (total_rows - 20).min(20) as usize);
    let all: Vec<_> = first.iter().chain(second.iter()).collect();
    assert!(all
        .windows(2)
        .all(|pair| pair[0].valid_from >= pair[1].valid_from));
    assert!(all.iter().all(|row| row.customer_id == id));
    assert!(all[0].valid_to.is_none(), "the open row is the newest");
    let runs: BTreeSet<i64> = all.iter().map(|row| row.run_id).collect();
    assert_eq!(runs.len(), all.len(), "one row per run");

    let (open, open_total) = read_assignments(&db, &Filter::Customer { id, open: true }, 1, 20)
        .await
        .unwrap();
    assert_eq!((open.len(), open_total), (1, 1));
    assert_eq!(open[0], *all[0]);

    let body = json(&assignments_reply(&first, 1, 20, total));
    assert_eq!(body["page_info"]["total"], total_rows);
    assert_eq!(body["assignments"].as_array().unwrap().len(), 20);
    assert_eq!(body["assignments"][0]["valid_to"], serde_json::Value::Null);
    let text = xml(&assignments_reply(&first, 1, 20, total));
    assert_eq!(text.matches("<SegmentAssignment>").count(), 20);
    assert!(text.contains("<common:total>"));
}

#[tokio::test]
async fn a_label_with_open_true_lists_the_customers_whose_open_row_carries_it() {
    let _turn = TURN.lock().await;
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    let labels = read_list(&db).await.unwrap().segments;
    let mut checked = 0;
    for label in &labels {
        let expected: BTreeSet<Uuid> = owner
            .query(
                "SELECT customer_id FROM public.customer_segment_history \
                 WHERE label_code = $1 AND valid_to IS NULL",
                &[&label.label_code],
            )
            .await
            .unwrap()
            .iter()
            .map(|row| row.get(0))
            .collect();
        let filter = Filter::Label {
            code: label.label_code.clone(),
            open: true,
        };
        let (rows, total) = read_assignments(&db, &filter, 1, 100).await.unwrap();
        assert_eq!(total as usize, expected.len(), "{}", label.label_code);
        let served: BTreeSet<Uuid> = rows.iter().map(|row| row.customer_id).collect();
        assert_eq!(served, expected, "{}", label.label_code);
        assert!(rows.iter().all(|row| {
            row.valid_to.is_none() && row.label_code.as_deref() == Some(&label.label_code)
        }));
        checked += expected.len();
    }
    assert!(checked > 0, "some label has open members in the seed");
}

#[tokio::test]
async fn no_response_carries_segment_id() {
    let _turn = TURN.lock().await;
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    let list = read_list(&db).await.unwrap();
    let id: Uuid = owner
        .query_one(
            "SELECT customer_id FROM api_segments.current_label_v1 \
             WHERE state = 'assigned' LIMIT 1",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    let current = read_current(&db, id).await.unwrap();
    let (history, total) = read_assignments(&db, &Filter::Customer { id, open: false }, 1, 20)
        .await
        .unwrap();
    let label = Filter::Label {
        code: list.segments[0].label_code.clone(),
        open: false,
    };
    let (members, members_total) = read_assignments(&db, &label, 1, 20).await.unwrap();
    let replies = [
        list_reply(&list, false),
        list_reply(&list, true),
        segment_reply(&list.segments[0]),
        current_reply(&current),
        assignments_reply(&history, 1, 20, total),
        assignments_reply(&members, 1, 20, members_total),
    ];
    for reply in &replies {
        assert!(!json(reply).to_string().contains("segment_id"));
        assert!(!xml(reply).contains("segment_id"));
    }
}
