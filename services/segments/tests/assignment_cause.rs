//! Why each assignment exists and why a customer has no label (F22-18, ADR-0066,
//! ADR-0087), against the real seeded PostgreSQL: the migrations carry the history
//! row's own `cause`, and `getCurrentAssignmentState` says `PROFILING_WITHDRAWN`
//! or `NO_PURCHASES` for an unassigned customer, in JSON and in XML valid against
//! the XSD. Needs TEST_DATABASE_URL, TEST_REDIS_URL and `openssl`.

mod support;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use axum::Router;
use chrono::{DateTime, Duration, Utc};
use http_body_util::BodyExt;
use segments::{read_migrations, MigrationFilter, MigrationQuery, MigrationWindow, Reader};
use serde_json::Value;
use support::{
    admin, assert_valid_xml, connect, drop_acl, kit_config, load_acl, owner_url, redis_url,
    service_database, Keys, TestCustomer,
};
use tokio_postgres::Client;
use uuid::Uuid;

const ANALYST: &str = "11111111-1111-1111-1111-000000000002";
const MANAGER: &str = "11111111-1111-1111-1111-000000000003";
const SELLER: &str = "11111111-1111-1111-1111-000000000031";

/// The tests share the ACL user and add customers; they take turns.
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

    async fn get(&self, path: &str, sub: &str, role: &str, accept: &str) -> (StatusCode, String) {
        let request = Request::builder()
            .uri(path)
            .header("Accept", accept)
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
        (status, String::from_utf8(body.to_vec()).unwrap())
    }

    async fn get_without_token(&self, path: &str, accept: &str) -> (StatusCode, String) {
        let request = Request::builder()
            .uri(path)
            .header("Accept", accept)
            .body(Body::empty())
            .unwrap();
        let response = tower::ServiceExt::oneshot(self.app.clone(), request)
            .await
            .unwrap();
        let status = response.status();
        let body = response.into_body().collect().await.unwrap().to_bytes();
        (status, String::from_utf8(body.to_vec()).unwrap())
    }

    async fn finish(mut self) {
        drop_acl(&mut self.redis).await;
    }
}

fn state_path(customer: Uuid) -> String {
    format!("/v1/segment-assignments/current-state?customer_id={customer}")
}

/// A customer whose open row says they withdrew `PROFILING` (ADR-0066): no label,
/// cause `CONSENT_WITHDRAWN`.
async fn create_withdrawn(owner: &Client) -> TestCustomer {
    let customer = TestCustomer::create(owner).await;
    owner
        .execute(
            "INSERT INTO public.customer_segment_history (customer_id, run_id, cause) \
             SELECT $1, max(run_id), 'CONSENT_WITHDRAWN' FROM public.segmentation_run \
             WHERE scope = 'FULL'",
            &[&customer.id],
        )
        .await
        .unwrap();
    customer
}

async fn read_state(stack: &Stack, customer: Uuid) -> (Value, String) {
    let (status, body) = stack
        .get(
            &state_path(customer),
            ANALYST,
            "ANALYST",
            "application/json",
        )
        .await;
    assert_eq!(status, 200, "{body}");
    let json: Value = serde_json::from_str(&body).unwrap();
    let (status, xml) = stack
        .get(&state_path(customer), ANALYST, "ANALYST", "application/xml")
        .await;
    assert_eq!(status, 200, "{xml}");
    assert!(xml.contains("<CurrentAssignmentState"), "{xml}");
    assert_valid_xml(&xml);
    (json, xml)
}

#[tokio::test]
async fn a_customer_who_withdrew_profiling_is_not_one_with_no_purchases() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let withdrawn = create_withdrawn(&stack.owner).await;
    let no_purchases = TestCustomer::create_unassigned(&stack.owner).await;
    let bare = TestCustomer::create(&stack.owner).await;
    let assigned: Uuid = stack
        .owner
        .query_one(
            "SELECT customer_id FROM api_segments.current_label_v1 \
             WHERE state = 'assigned' ORDER BY customer_id LIMIT 1",
            &[],
        )
        .await
        .unwrap()
        .get(0);

    let (json, xml) = read_state(&stack, withdrawn.id).await;
    assert_eq!(json["state"], "unassigned_result");
    assert_eq!(json["unassigned_reason"], "PROFILING_WITHDRAWN");
    assert!(json["label_code"].is_null() && json["r_score"].is_null());
    assert!(xml.contains("<unassigned_reason>PROFILING_WITHDRAWN</unassigned_reason>"));

    let (json, xml) = read_state(&stack, no_purchases.id).await;
    assert_eq!(json["state"], "unassigned_result");
    assert_eq!(json["unassigned_reason"], "NO_PURCHASES");
    assert!(xml.contains("<unassigned_reason>NO_PURCHASES</unassigned_reason>"));

    // Only an unassigned result has a reason; every other field is the current
    // assignment's own.
    let (json, xml) = read_state(&stack, assigned).await;
    assert_eq!(json["state"], "assigned");
    assert!(json["unassigned_reason"].is_null());
    assert!(!xml.contains("unassigned_reason"), "{xml}");
    let (_, plain) = stack
        .get(
            &format!("/v1/segment-assignments/current?customer_id={assigned}"),
            ANALYST,
            "ANALYST",
            "application/json",
        )
        .await;
    let plain: Value = serde_json::from_str(&plain).unwrap();
    for field in [
        "state",
        "label_code",
        "r_score",
        "f_score",
        "m_score",
        "since",
        "run_id",
    ] {
        assert_eq!(json[field], plain[field], "{field}");
    }
    assert!(
        plain.get("unassigned_reason").is_none(),
        "current is unchanged"
    );

    let (json, xml) = read_state(&stack, bare.id).await;
    assert_eq!(json["state"], "no_open_assignment");
    assert!(json["unassigned_reason"].is_null());
    assert!(!xml.contains("unassigned_reason"), "{xml}");

    // The withdrawn customer comes back: a granted row with a label is assigned.
    stack
        .owner
        .execute(
            "UPDATE public.customer_segment_history SET valid_to = now() \
             WHERE customer_id = $1 AND valid_to IS NULL",
            &[&withdrawn.id],
        )
        .await
        .unwrap();
    stack
        .owner
        .execute(
            "INSERT INTO public.customer_segment_history \
                 (customer_id, run_id, label_code, r_score, f_score, m_score, cause) \
             SELECT $1, min(run_id), 'LOYAL', 4, 4, 4, 'CONSENT_GRANTED' \
             FROM public.segmentation_run WHERE scope = 'FULL'",
            &[&withdrawn.id],
        )
        .await
        .unwrap();
    let (json, _) = read_state(&stack, withdrawn.id).await;
    assert_eq!(json["state"], "assigned");
    assert!(json["unassigned_reason"].is_null());

    withdrawn.cleanup(&stack.owner).await;
    no_purchases.cleanup(&stack.owner).await;
    bare.cleanup(&stack.owner).await;
    stack.finish().await;
}

#[tokio::test]
async fn the_contract_roles_read_the_reason_and_other_roles_are_refused() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let customer = Uuid::parse_str("00000000-0000-0000-0000-000000000007").unwrap();
    for accept in ["application/json", "application/xml"] {
        let (status, body) = stack.get_without_token(&state_path(customer), accept).await;
        assert_eq!(status, 401, "{body}");
        assert!(body.contains("invalid_token"), "{body}");
        let (status, body) = stack
            .get(&state_path(customer), MANAGER, "STORE_MANAGER", accept)
            .await;
        assert_eq!(status, 200, "{body}");
        for role in ["SALES_ASSOCIATE", "LOYALTY_OFFICER"] {
            let (status, body) = stack.get(&state_path(customer), SELLER, role, accept).await;
            assert_eq!(status, 403, "{role}: {body}");
            assert!(body.contains("forbidden_role"), "{body}");
        }
        let (status, body) = stack
            .get(&state_path(Uuid::new_v4()), ANALYST, "ANALYST", accept)
            .await;
        assert_eq!(status, 404, "{body}");
        assert!(body.contains("customer_not_found"), "{body}");
        for path in [
            "/v1/segment-assignments/current-state",
            "/v1/segment-assignments/current-state?customer_id=abc",
        ] {
            let (status, body) = stack.get(path, ANALYST, "ANALYST", accept).await;
            assert_eq!(status, 422, "{path}: {body}");
            assert!(body.contains("invalid_query"), "{body}");
        }
    }
    stack.finish().await;
}

#[tokio::test]
async fn a_migration_carries_the_history_rows_own_cause() {
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    let customer = TestCustomer::create(&owner).await;
    let started: DateTime<Utc> = Utc::now() - Duration::days(3);
    let withdrew = started + Duration::days(2);
    owner
        .execute(
            "INSERT INTO public.customer_segment_history \
                 (customer_id, run_id, label_code, r_score, f_score, m_score, cause, \
                  valid_from, valid_to) \
             SELECT $1, min(run_id), 'LOYAL', 4, 4, 4, 'SALE', $2, $3 \
             FROM public.segmentation_run WHERE scope = 'FULL'",
            &[&customer.id, &started, &withdrew],
        )
        .await
        .unwrap();
    owner
        .execute(
            "INSERT INTO public.customer_segment_history \
                 (customer_id, run_id, cause, valid_from) \
             SELECT $1, max(run_id), 'CONSENT_WITHDRAWN', $2 \
             FROM public.segmentation_run WHERE scope = 'FULL'",
            &[&customer.id, &withdrew],
        )
        .await
        .unwrap();

    let query = MigrationQuery {
        window: MigrationWindow {
            from: started + Duration::hours(1),
            to: Utc::now() + Duration::minutes(1),
        },
        filter: MigrationFilter {
            customer_id: Some(customer.id),
            ..MigrationFilter::default()
        },
    };
    let migrations = read_migrations(&db, &query).await.unwrap();
    assert_eq!(migrations.len(), 1);
    let migration = &migrations[0];
    assert_eq!(
        migration.previous.state.label_code.as_deref(),
        Some("LOYAL")
    );
    assert_eq!(migration.previous.cause.as_deref(), Some("SALE"));
    assert_eq!(migration.new.state.state, "unassigned");
    assert_eq!(migration.new.cause.as_deref(), Some("CONSENT_WITHDRAWN"));

    let reply = segments::migrations_reply(&query.window, &migrations, 1, 20, 1, false);
    let json = mosaiq_kit::rendering::render_json(&reply, 20).unwrap();
    assert_eq!(json["migrations"][0]["cause"], "CONSENT_WITHDRAWN");
    let reply = segments::migrations_reply(&query.window, &migrations, 1, 20, 1, true);
    let xml = mosaiq_kit::rendering::render_xml(
        &reply,
        "https://mosaiq.maxthecoder.online/xml/segments/v1",
        20,
    )
    .unwrap();
    assert!(xml.contains("<cause>CONSENT_WITHDRAWN</cause>"), "{xml}");
    assert_valid_xml(&xml);

    customer.cleanup(&owner).await;
}

#[tokio::test]
async fn every_seeded_row_reports_a_cause_not_null() {
    let url = owner_url();
    let owner = connect(&url).await;
    let db = service_database(&url);
    let from: DateTime<Utc> = owner
        .query_one(
            "SELECT min(valid_from) FROM public.customer_segment_history",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    let query = MigrationQuery {
        window: MigrationWindow {
            from,
            to: Utc::now() + Duration::minutes(1),
        },
        filter: MigrationFilter::default(),
    };
    let migrations = read_migrations(&db, &query).await.unwrap();
    assert!(!migrations.is_empty());
    for migration in migrations.iter().filter(|m| m.new.run_id.is_some()) {
        assert!(migration.new.cause.is_some(), "{:?}", migration.customer_id);
    }
}
