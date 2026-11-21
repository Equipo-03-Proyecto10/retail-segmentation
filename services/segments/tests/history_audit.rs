//! A read of one customer's segment history is logged, and fails closed
//! (F21-25, ADR-0072 section 4), through the service's whole router.
//!
//! `GET /v1/segment-assignments?customer_id=` records one `HISTORY_READ`
//! naming who, which customer and when, never what was returned; a 403 on it
//! records one with outcome `REFUSED`; a read by label records nothing; and
//! when the event cannot be recorded the read answers 503 `audit_unavailable`
//! with no rows. Needs TEST_DATABASE_URL, TEST_REDIS_URL and `openssl`.

mod support;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use axum::Router;
use chrono::{Duration as ChronoDuration, SecondsFormat};
use http_body_util::BodyExt;
use segments::Reader;
use serde_json::{json, Value};
use support::{admin, connect, drop_acl, kit_config, load_acl, owner_url, redis_url, Keys};
use tokio_postgres::Client;
use uuid::Uuid;

const CUSTOMER: &str = "00000000-0000-0000-0000-000000000007";
const ANALYST: &str = "11111111-1111-1111-1111-000000000002";
const PLANNER: &str = "11111111-1111-1111-1111-000000000005";
const REVOKE: &str = "REVOKE EXECUTE ON FUNCTION api_audit.record_event_v1(uuid, text, text, \
     timestamptz, text, uuid, text, smallint, text, text, bigint, bigint, text, jsonb) \
     FROM svc_segments";
const GRANT: &str = "GRANT EXECUTE ON FUNCTION api_audit.record_event_v1(uuid, text, text, \
     timestamptz, text, uuid, text, smallint, text, text, bigint, bigint, text, jsonb) \
     TO svc_segments";

/// The tests share the ACL user and revoke a grant; they take turns.
static TURN: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

struct Stack {
    app: Router,
    keys: Keys,
    sid: String,
    owner: Client,
    redis: redis::aio::MultiplexedConnection,
}

impl Stack {
    async fn start() -> Option<Self> {
        let database = owner_url();
        let url = redis_url()?;
        let mut redis = admin(&url).await;
        let password = load_acl(&mut redis).await;
        let sid = Uuid::new_v4().to_string();
        let _: () = redis::cmd("SET")
            .arg(format!("pub:auth:v1:live:{sid}"))
            .arg("1")
            .arg("EX")
            .arg(300)
            .query_async(&mut redis)
            .await
            .unwrap();
        let keys = Keys::make();
        let config = kit_config(&keys, url, password, &database);
        let (app, _) = segments::router(config, "contracts", Reader::new(None)).unwrap();
        let owner = connect(&database).await;
        Some(Self {
            app,
            keys,
            sid,
            owner,
            redis,
        })
    }

    async fn get(
        &self,
        path: &str,
        sub: &str,
        role: &str,
        accept: &str,
    ) -> (StatusCode, String, String) {
        let correlation = format!("test-{}", Uuid::new_v4().simple());
        let request = Request::builder()
            .uri(path)
            .header("Accept", accept)
            .header("X-Correlation-ID", &correlation)
            .header(
                "Authorization",
                format!("Bearer {}", self.keys.token(sub, role, &self.sid)),
            )
            .body(Body::empty())
            .unwrap();
        let response = tower::ServiceExt::oneshot(self.app.clone(), request)
            .await
            .unwrap();
        let status = response.status();
        let body = response.into_body().collect().await.unwrap().to_bytes();
        (
            status,
            String::from_utf8(body.to_vec()).unwrap(),
            correlation,
        )
    }

    /// The events a request recorded: (kind, outcome, actor, role, source,
    /// subject_type, subject_id, details, and every non-null column name).
    async fn events(&self, correlation: &str) -> Vec<Value> {
        self.owner
            .query(
                "SELECT to_jsonb(d)::text FROM audit_events.domain_event AS d \
                 WHERE correlation_id = $1 ORDER BY seq",
                &[&correlation],
            )
            .await
            .unwrap()
            .iter()
            .map(|row| serde_json::from_str(row.get::<_, &str>(0)).unwrap())
            .collect()
    }

    async fn finish(mut self) {
        drop_acl(&mut self.redis).await;
    }
}

#[tokio::test]
async fn a_customer_history_read_records_who_what_and_when_but_not_the_data() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let path = format!("/v1/segment-assignments?customer_id={CUSTOMER}");
    for accept in ["application/json", "application/xml"] {
        let (status, body, correlation) = stack.get(&path, ANALYST, "ANALYST", accept).await;
        assert_eq!(status, 200, "{body}");
        assert!(
            body.contains("run_id"),
            "the customer has history to withhold"
        );
        let events = stack.events(&correlation).await;
        assert_eq!(events.len(), 1, "{events:?}");
        let event = &events[0];
        assert_eq!(event["kind"], "HISTORY_READ");
        assert_eq!(event["outcome"], "SUCCESS");
        assert_eq!(event["actor_type"], "USER");
        assert_eq!(event["actor_user_id"], ANALYST);
        assert_eq!(event["actor_role"], "ANALYST");
        assert_eq!(event["source"], "svc_segments");
        assert_eq!(event["subject_type"], "customer");
        assert_eq!(event["subject_id"], CUSTOMER);
        assert_eq!(event["details"], json!({"history": "SEGMENT"}));
        // Who, which customer, when, and the chain: nothing that was read.
        let present: Vec<&str> = event
            .as_object()
            .unwrap()
            .iter()
            .filter(|(_, value)| !value.is_null())
            .map(|(key, _)| key.as_str())
            .collect();
        assert_eq!(
            present,
            [
                "seq",
                "kind",
                "source",
                "details",
                "outcome",
                "event_id",
                "prev_hash",
                "actor_role",
                "actor_type",
                "entry_hash",
                "subject_id",
                "occurred_at",
                "recorded_at",
                "subject_type",
                "actor_user_id",
                "correlation_id",
            ]
        );
    }
    stack.finish().await;
}

#[tokio::test]
async fn a_read_by_label_records_nothing_and_a_refusal_is_recorded() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let (status, _, correlation) = stack
        .get(
            "/v1/segment-assignments?label_code=CHAMPION&open=true",
            ANALYST,
            "ANALYST",
            "application/json",
        )
        .await;
    assert_eq!(status, 200);
    assert!(stack.events(&correlation).await.is_empty());

    let path = format!("/v1/segment-assignments?customer_id={CUSTOMER}");
    for accept in ["application/json", "application/xml"] {
        let (status, body, correlation) =
            stack.get(&path, PLANNER, "INVENTORY_PLANNER", accept).await;
        assert_eq!(status, 403, "{body}");
        assert!(body.contains("forbidden_role"), "{body}");
        let events = stack.events(&correlation).await;
        assert_eq!(events.len(), 1, "{events:?}");
        assert_eq!(events[0]["outcome"], "REFUSED");
        assert_eq!(events[0]["actor_user_id"], PLANNER);
        assert_eq!(events[0]["actor_role"], "INVENTORY_PLANNER");
        assert_eq!(events[0]["subject_id"], CUSTOMER);
    }
    // A refusal by label names no customer, so it records nothing.
    let (status, _, correlation) = stack
        .get(
            "/v1/segment-assignments?label_code=CHAMPION",
            PLANNER,
            "INVENTORY_PLANNER",
            "application/json",
        )
        .await;
    assert_eq!(status, 403);
    assert!(stack.events(&correlation).await.is_empty());
    stack.finish().await;
}

#[tokio::test]
async fn migration_routes_enforce_roles_periods_and_both_representations() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let rows = stack
        .owner
        .query(
            "SELECT run_at FROM public.segmentation_run WHERE run_id IN (28, 29) ORDER BY run_id",
            &[],
        )
        .await
        .unwrap();
    let from = (rows[0].get::<_, chrono::DateTime<chrono::Utc>>(0) + ChronoDuration::minutes(1))
        .to_rfc3339_opts(SecondsFormat::Secs, true);
    let to = (rows[1].get::<_, chrono::DateTime<chrono::Utc>>(0) + ChronoDuration::minutes(1))
        .to_rfc3339_opts(SecondsFormat::Secs, true);
    let path = format!("/v1/segment-migrations/matrix?from={from}&to={to}");
    for (accept, marker) in [
        ("application/json", "\"customer_count\""),
        ("application/xml", "<SegmentMigrationMatrix"),
    ] {
        let (status, body, _) = stack.get(&path, ANALYST, "ANALYST", accept).await;
        assert_eq!(status, 200, "{body}");
        assert!(body.contains(marker), "{body}");
    }
    for accept in ["application/json", "application/xml"] {
        let (status, body, _) = stack.get(&path, PLANNER, "INVENTORY_PLANNER", accept).await;
        assert_eq!(status, 403, "{body}");
        assert!(body.contains("forbidden_role"), "{body}");
    }
    let bad = format!("/v1/segment-migrations/matrix?from={to}&to={from}");
    let (status, body, _) = stack
        .get(&bad, ANALYST, "ANALYST", "application/json")
        .await;
    assert_eq!(status, 422, "{body}");
    assert!(body.contains("invalid_query"), "{body}");

    // The list and the explanation: their refusals, and both representations.
    let window = format!("from={from}&to={to}");
    let ghost = "00000000-0000-0000-0000-00000000ffff";
    for (path, expected, marker) in [
        (
            format!("/v1/segment-migrations?{window}&direction=entries"),
            422,
            "invalid_query",
        ),
        (
            format!("/v1/segment-migrations?{window}&label_code=NOT_A_LABEL"),
            404,
            "segment_not_found",
        ),
        (
            format!("/v1/segment-migrations?{window}&customer_id={ghost}"),
            404,
            "customer_not_found",
        ),
        (
            format!("/v1/segment-migrations/explanation?{window}&customer_id={ghost}"),
            404,
            "customer_not_found",
        ),
        (
            format!("/v1/segment-migrations/explanation?{window}"),
            422,
            "invalid_query",
        ),
    ] {
        for accept in ["application/json", "application/xml"] {
            let (status, body, _) = stack.get(&path, ANALYST, "ANALYST", accept).await;
            assert_eq!(status, expected, "{path} {accept}: {body}");
            assert!(body.contains(marker), "{path} {accept}: {body}");
        }
    }
    let customer = "00000000-0000-0000-0000-000000000006";
    for (path, marker) in [
        (
            format!("/v1/segment-migrations?{window}&size=5"),
            ("\"migrations\"", "<SegmentMigrations"),
        ),
        (
            format!("/v1/segment-migrations/explanation?{window}&customer_id={customer}"),
            ("\"measures\"", "<SegmentMigrationExplanation"),
        ),
    ] {
        for (accept, expected) in [
            ("application/json", marker.0),
            ("application/xml", marker.1),
        ] {
            let (status, body, _) = stack.get(&path, ANALYST, "ANALYST", accept).await;
            assert_eq!(status, 200, "{path} {accept}: {body}");
            assert!(body.contains(expected), "{path} {accept}: {body}");
        }
        let (status, body, _) = stack
            .get(&path, PLANNER, "INVENTORY_PLANNER", "application/json")
            .await;
        assert_eq!(status, 403, "{path}: {body}");
        assert!(body.contains("forbidden_role"), "{path}: {body}");
    }
    stack.finish().await;
}

#[tokio::test]
async fn the_read_fails_closed_when_its_event_cannot_be_recorded() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let path = format!("/v1/segment-assignments?customer_id={CUSTOMER}");
    stack.owner.batch_execute(REVOKE).await.unwrap();
    let json = stack
        .get(&path, ANALYST, "ANALYST", "application/json")
        .await;
    let xml = stack
        .get(&path, ANALYST, "ANALYST", "application/xml")
        .await;
    let refused = stack
        .get(&path, PLANNER, "INVENTORY_PLANNER", "application/json")
        .await;
    stack.owner.batch_execute(GRANT).await.unwrap();

    for (status, body, correlation) in [&json, &xml, &refused] {
        assert_eq!(*status, 503, "{body}");
        assert!(body.contains("audit_unavailable"), "{body}");
        assert!(
            !body.contains("run_id") && !body.contains("label_code"),
            "{body}"
        );
        assert!(stack.events(correlation).await.is_empty());
    }
    assert!(xml.1.starts_with("<?xml"), "{}", xml.1);
    stack.finish().await;
}
