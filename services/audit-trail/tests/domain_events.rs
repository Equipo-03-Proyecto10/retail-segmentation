//! `domain-events` through the whole router (F21-25, ADR-0072): only ADMIN
//! and AUDITOR read it, in JSON and in XML that validates against the XSD; the
//! seed covers every kind; every read of either log records one `AUDIT_READ`
//! naming the filters used, never their values; a read whose event cannot be
//! recorded answers 503 with no rows; and verification names a corrupted
//! event.

mod support;

use serde_json::Value;
use support::connect;
use support::http::{assert_valid_xml, send, send_with, Stack, AUDITOR, ROLES, TURN};
use tokio_postgres::Client;
use uuid::Uuid;

const JSON: &str = "application/json";
const XML: &str = "application/xml";
const KINDS: [&str; 7] = [
    "CALCULATION_RUN",
    "MODEL_PUBLISHED",
    "MODEL_ROLLED_BACK",
    "PERMISSION_CHANGED",
    "CRITICAL_ACTION",
    "HISTORY_READ",
    "AUDIT_READ",
];
/// The seed's events are `7a0c5e1d-0000-4000-8000-<n>`; 17 assigns stores.
fn seeded(n: u32) -> String {
    format!("7a0c5e1d-0000-4000-8000-{n:012}")
}
const REVOKE: &str = "REVOKE EXECUTE ON FUNCTION api_audit.record_event_v1(uuid, text, text, \
     timestamptz, text, uuid, text, smallint, text, text, bigint, bigint, text, jsonb) \
     FROM svc_audit_trail";
const GRANT: &str = "GRANT EXECUTE ON FUNCTION api_audit.record_event_v1(uuid, text, text, \
     timestamptz, text, uuid, text, smallint, text, text, bigint, bigint, text, jsonb) \
     TO svc_audit_trail";

fn correlation() -> String {
    format!("test-{}", Uuid::new_v4().simple())
}

/// The AUDIT_READ events a request recorded, by its correlation id.
async fn audit_reads(
    owner: &Client,
    correlation: &str,
) -> Vec<(String, String, Option<String>, Value)> {
    owner
        .query(
            "SELECT actor_user_id::text, actor_role, subject_id, details::text \
             FROM audit_events.domain_event \
             WHERE kind = 'AUDIT_READ' AND correlation_id = $1 ORDER BY seq",
            &[&correlation],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| {
            (
                row.get(0),
                row.get(1),
                row.get(2),
                serde_json::from_str(row.get::<_, &str>(3)).unwrap(),
            )
        })
        .collect()
}

async fn events_with(owner: &Client, correlation: &str) -> i64 {
    owner
        .query_one(
            "SELECT count(*) FROM audit_events.domain_event WHERE correlation_id = $1",
            &[&correlation],
        )
        .await
        .unwrap()
        .get(0)
}

#[tokio::test]
async fn only_admin_and_auditor_read_domain_events_in_json_and_xml() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let one = format!("/v1/domain-events/{}", seeded(17));
    let paths = [
        "/v1/domain-events",
        one.as_str(),
        "/v1/domain-events/verification",
    ];
    for role in ROLES {
        let token = stack.token(role);
        for path in paths {
            for accept in [JSON, XML] {
                let (status, body) = send(&stack.app, "GET", path, Some(&token), accept).await;
                if matches!(role, "ADMIN" | "AUDITOR") {
                    assert_eq!(status, 200, "{role} {path} {accept}: {body}");
                    if accept == XML {
                        assert_valid_xml(&body);
                    } else {
                        serde_json::from_str::<Value>(&body).expect("JSON");
                    }
                } else {
                    assert_eq!(status, 403, "{role} {path} {accept}");
                    assert!(body.contains("forbidden_role"), "{body}");
                    assert!(!body.contains("event_id"), "{body}");
                }
            }
        }
    }
    // The resource has no write.
    let token = stack.token("ADMIN");
    for method in ["POST", "PUT", "PATCH", "DELETE"] {
        for path in paths {
            let (status, _) = send(&stack.app, method, path, Some(&token), JSON).await;
            assert_eq!(status, 405, "{method} {path}");
        }
    }
    stack.finish().await;
}

#[tokio::test]
async fn every_seeded_kind_reads_back_and_the_filters_narrow() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let token = stack.token("AUDITOR");
    for kind in KINDS {
        let path = format!("/v1/domain-events?kind={kind}&size=100");
        let (status, body) = send(&stack.app, "GET", &path, Some(&token), JSON).await;
        assert_eq!(status, 200, "{body}");
        let page: Value = serde_json::from_str(&body).unwrap();
        let events = page["domain_events"].as_array().unwrap();
        assert!(!events.is_empty(), "the seed has a {kind}");
        assert!(events.iter().all(|event| event["kind"] == kind), "{body}");
        let (status, xml) = send(&stack.app, "GET", &path, Some(&token), XML).await;
        assert_eq!(status, 200);
        assert_valid_xml(&xml);
    }

    // One event: the JSON details are the stored object, and the XML holds
    // them under the kind's element with one store_id per store.
    let path = format!("/v1/domain-events/{}", seeded(17));
    let (_, body) = send(&stack.app, "GET", &path, Some(&token), JSON).await;
    let event: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(
        event["details"],
        serde_json::json!({"change": "STORES_ASSIGNED", "store_ids": [3, 9]})
    );
    assert_eq!(event["source"], "svc_users");
    assert_eq!(event["subject_type"], "user");
    assert!(event["store_id"].is_null() && event["model_version_id"].is_null());
    let (_, xml) = send(&stack.app, "GET", &path, Some(&token), XML).await;
    assert_valid_xml(&xml);
    assert!(
        xml.contains(
            "<details><PermissionChange><change>STORES_ASSIGNED</change>\
                      <store_id>3</store_id><store_id>9</store_id></PermissionChange></details>"
        ),
        "{xml}"
    );
    assert!(
        !xml.contains("<store_id/>") && !xml.contains("<model_version_id"),
        "{xml}"
    );

    // The filters combine with AND.
    for (query, check) in [
        ("model_version_id=1", "model_version_id"),
        ("actor_role=INVENTORY_PLANNER&outcome=REFUSED", "outcome"),
        ("source=svc_models&kind=MODEL_ROLLED_BACK", "kind"),
    ] {
        let path = format!("/v1/domain-events?{query}&size=100");
        let (status, body) = send(&stack.app, "GET", &path, Some(&token), JSON).await;
        assert_eq!(status, 200, "{body}");
        let page: Value = serde_json::from_str(&body).unwrap();
        let events = page["domain_events"].as_array().unwrap();
        assert!(!events.is_empty(), "{query}");
        for event in events {
            match check {
                "model_version_id" => assert_eq!(event["model_version_id"], 1),
                "outcome" => {
                    assert_eq!(event["outcome"], "REFUSED");
                    assert_eq!(event["actor_role"], "INVENTORY_PLANNER");
                }
                _ => assert_eq!(event["kind"], "MODEL_ROLLED_BACK"),
            }
        }
    }
    let customer = "00000000-0000-0000-0000-000000000007";
    let path =
        format!("/v1/domain-events?kind=HISTORY_READ&subject_type=customer&subject_id={customer}");
    let (_, body) = send(&stack.app, "GET", &path, Some(&token), JSON).await;
    let page: Value = serde_json::from_str(&body).unwrap();
    let events = page["domain_events"].as_array().unwrap();
    assert!(events.len() >= 3, "{body}");
    assert!(events.iter().all(|event| event["subject_id"] == customer));

    // A refused filter names itself, and an unknown id is 404.
    for (query, field) in [
        ("kind=PAGE_VIEW", "kind"),
        ("subject_id=7", "subject_id"),
        ("actor_user_id=nope", "actor_user_id"),
        ("from=2026-11-02T00:00:00Z&to=2026-11-01T00:00:00Z", "to"),
    ] {
        let path = format!("/v1/domain-events?{query}");
        let (status, body) = send(&stack.app, "GET", &path, Some(&token), JSON).await;
        assert_eq!(status, 422, "{query}: {body}");
        assert!(
            body.contains("invalid_query") && body.contains(&format!("\"field\":\"{field}\"")),
            "{body}"
        );
    }
    let path = format!("/v1/domain-events/{}", Uuid::new_v4());
    let (status, body) = send(&stack.app, "GET", &path, Some(&token), JSON).await;
    assert_eq!(
        (status.as_u16(), body.contains("domain_event_not_found")),
        (404, true)
    );
    stack.finish().await;
}

#[tokio::test]
async fn every_read_of_either_log_records_who_and_which_filters_but_no_values() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    let token = stack.keys.token_for(AUDITOR, "AUDITOR", &stack.sid);
    let customer = "00000000-0000-0000-0000-000000000007";
    let one = seeded(25);
    let reads = [
        (
            format!(
                "/v1/domain-events?kind=HISTORY_READ&subject_type=customer&subject_id={customer}"
            ),
            serde_json::json!({"resource": "DOMAIN_EVENTS", "filters": ["kind", "subject_type", "subject_id"]}),
            None,
        ),
        (
            format!("/v1/domain-events/{one}"),
            serde_json::json!({"resource": "DOMAIN_EVENTS", "filters": []}),
            Some(one.clone()),
        ),
        (
            "/v1/domain-events/verification?from=2020-01-01T00:00:00Z".to_owned(),
            serde_json::json!({"resource": "DOMAIN_EVENT_VERIFICATION", "filters": ["from"]}),
            None,
        ),
        (
            "/v1/audit-entries?entity=app_user&page=1".to_owned(),
            serde_json::json!({"resource": "AUDIT_ENTRIES", "filters": ["entity", "page"]}),
            None,
        ),
        (
            "/v1/audit-entries/1".to_owned(),
            serde_json::json!({"resource": "AUDIT_ENTRIES", "filters": []}),
            Some("1".to_owned()),
        ),
    ];
    for (path, details, subject) in reads {
        let id = correlation();
        let (status, body) =
            send_with(&stack.app, "GET", &path, Some(&token), JSON, Some(&id)).await;
        assert_eq!(status, 200, "{path}: {body}");
        let recorded = audit_reads(&owner, &id).await;
        assert_eq!(
            recorded,
            [(AUDITOR.to_owned(), "AUDITOR".to_owned(), subject, details)],
            "{path}"
        );
        // Never a filter's value.
        let text = format!("{recorded:?}");
        assert!(
            !text.contains(customer) && !text.contains("app_user"),
            "{text}"
        );
    }

    // A refused query or role reads nothing, so it records nothing.
    for (path, role) in [
        ("/v1/domain-events?kind=PAGE_VIEW", "AUDITOR"),
        ("/v1/domain-events", "ANALYST"),
    ] {
        let id = correlation();
        let token = stack.token(role);
        let (status, _) = send_with(&stack.app, "GET", path, Some(&token), JSON, Some(&id)).await;
        assert!(matches!(status.as_u16(), 403 | 422), "{path}");
        assert_eq!(events_with(&owner, &id).await, 0, "{path}");
    }
    stack.finish().await;
}

#[tokio::test]
async fn a_read_whose_audit_read_cannot_be_recorded_returns_nothing() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    let token = stack.token("ADMIN");
    let one = format!("/v1/domain-events/{}", seeded(17));
    let paths = [
        "/v1/domain-events",
        one.as_str(),
        "/v1/domain-events/verification",
        "/v1/audit-entries",
        "/v1/audit-entries/1",
    ];
    owner.batch_execute(REVOKE).await.unwrap();
    let mut answers = Vec::new();
    for path in paths {
        for accept in [JSON, XML] {
            let id = correlation();
            let (status, body) =
                send_with(&stack.app, "GET", path, Some(&token), accept, Some(&id)).await;
            answers.push((path, accept, status, body, id));
        }
    }
    owner.batch_execute(GRANT).await.unwrap();
    for (path, accept, status, body, id) in answers {
        assert_eq!(status, 503, "{path} {accept}: {body}");
        assert!(body.contains("audit_unavailable"), "{body}");
        for leaked in [
            "event_id",
            "audit_id",
            "entry_hash",
            "head_hash",
            "domain_events",
        ] {
            assert!(
                !body.contains(leaked),
                "{path} {accept} returned {leaked}: {body}"
            );
        }
        assert_eq!(events_with(&owner, &id).await, 0);
    }
    stack.finish().await;
}

/// Change one stored event as the owner, past the trigger, and back.
async fn tamper(owner: &Client, statement: &str, event_id: &str) {
    owner
        .batch_execute(
            "BEGIN; ALTER TABLE audit_events.domain_event \
             DISABLE TRIGGER domain_event_append_only",
        )
        .await
        .unwrap();
    let id = Uuid::parse_str(event_id).unwrap();
    owner.execute(statement, &[&id]).await.unwrap();
    owner
        .batch_execute(
            "ALTER TABLE audit_events.domain_event \
             ENABLE TRIGGER domain_event_append_only; COMMIT",
        )
        .await
        .unwrap();
}

async fn verification(stack: &Stack, query: &str) -> Value {
    let token = stack.token("AUDITOR");
    let path = format!("/v1/domain-events/verification{query}");
    let (status, body) = send(&stack.app, "GET", &path, Some(&token), JSON).await;
    assert_eq!(status, 200, "{body}");
    let (status, xml) = send(&stack.app, "GET", &path, Some(&token), XML).await;
    assert_eq!(status, 200);
    assert_valid_xml(&xml);
    serde_json::from_str(&body).unwrap()
}

#[tokio::test]
async fn verification_names_a_corrupted_or_missing_event_and_an_intact_chain_is_ok() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    let head: (i64, String) = {
        let row = owner
            .query_one(
                "SELECT count(*), (array_agg(entry_hash ORDER BY seq DESC))[1] \
                 FROM audit_events.domain_event",
                &[],
            )
            .await
            .unwrap();
        (row.get(0), row.get(1))
    };
    let intact = verification(&stack, "").await;
    assert_eq!(intact["status"], "ok", "{intact}");
    assert_eq!(intact["checked"], head.0);
    assert_eq!(intact["head_hash"], head.1);
    assert!(intact["first_broken_event_id"].is_null() && intact["reason"].is_null());

    // An edit of any column breaks that event's own hash.
    let edited = seeded(10);
    tamper(
        &owner,
        "UPDATE audit_events.domain_event SET outcome = 'REFUSED' WHERE event_id = $1",
        &edited,
    )
    .await;
    let broken = verification(&stack, "").await;
    tamper(
        &owner,
        "UPDATE audit_events.domain_event SET outcome = 'SUCCESS' WHERE event_id = $1",
        &edited,
    )
    .await;
    assert_eq!(broken["status"], "broken", "{broken}");
    assert_eq!(broken["first_broken_event_id"], edited.as_str());
    assert_eq!(broken["reason"], "entry_hash_mismatch");

    // A removed event breaks the link of the one after it.
    let removed = seeded(20);
    let next = seeded(21);
    owner
        .batch_execute(
            "CREATE TEMP TABLE kept AS SELECT * FROM audit_events.domain_event \
             WHERE event_id = '7a0c5e1d-0000-4000-8000-000000000020'",
        )
        .await
        .unwrap();
    tamper(
        &owner,
        "DELETE FROM audit_events.domain_event WHERE event_id = $1",
        &removed,
    )
    .await;
    let gap = verification(&stack, "").await;
    owner
        .batch_execute("INSERT INTO audit_events.domain_event SELECT * FROM kept; DROP TABLE kept")
        .await
        .unwrap();
    assert_eq!(gap["status"], "broken", "{gap}");
    assert_eq!(gap["first_broken_event_id"], next.as_str());
    assert_eq!(gap["reason"], "prev_hash_mismatch");

    // Restored, the chain verifies again, up to the head it had before this
    // read (each verification appends its own AUDIT_READ after reading).
    let now: String = owner
        .query_one(
            "SELECT entry_hash FROM audit_events.domain_event ORDER BY seq DESC LIMIT 1",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    let restored = verification(&stack, "").await;
    assert_eq!(restored["status"], "ok", "{restored}");
    assert_eq!(restored["head_hash"], now.as_str());
    let empty = verification(&stack, "?from=1999-01-01T00:00:00Z&to=1999-01-02T00:00:00Z").await;
    assert_eq!(
        (empty["checked"].as_i64(), empty["head_hash"].is_null()),
        (Some(0), true)
    );
    stack.finish().await;
}
