//! The floor view of a customer's current segment (F21-26, ADR-0060), through
//! the service's whole router: `SALES_ASSOCIATE` and `LOYALTY_OFFICER` read the
//! label and `since` in JSON and XML, never a score or `run_id`, in each of the
//! three states; every other role is refused, and the floor roles still cannot
//! read `getCurrentAssignment`, which carries the scores. Needs
//! TEST_DATABASE_URL, TEST_REDIS_URL and `openssl`.

mod support;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use axum::Router;
use http_body_util::BodyExt;
use segments::Reader;
use serde_json::Value;
use support::{
    admin, assert_valid_xml, connect, drop_acl, kit_config, load_acl, owner_url, redis_url, Keys,
    TestCustomer,
};
use tokio_postgres::Client;
use uuid::Uuid;

const SELLER: &str = "11111111-1111-1111-1111-000000000031";
const OFFICER: &str = "11111111-1111-1111-1111-000000000032";
const MANAGER: &str = "11111111-1111-1111-1111-000000000003";
const ANALYST: &str = "11111111-1111-1111-1111-000000000002";
const FLOOR: [(&str, &str); 2] = [(SELLER, "SALES_ASSOCIATE"), (OFFICER, "LOYALTY_OFFICER")];
const WITHHELD: [&str; 4] = ["r_score", "f_score", "m_score", "run_id"];

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

    async fn finish(mut self) {
        drop_acl(&mut self.redis).await;
    }
}

fn floor_path(customer: Uuid) -> String {
    format!("/v1/segment-assignments/floor-view?customer_id={customer}")
}

/// A 200 floor answer in both formats: exactly the four elements, the XML
/// valid against the XSD, and no withheld element anywhere.
async fn read_floor(stack: &Stack, customer: Uuid, sub: &str, role: &str) -> Value {
    let (status, body) = stack
        .get(&floor_path(customer), sub, role, "application/json")
        .await;
    assert_eq!(status, 200, "{role}: {body}");
    let json: Value = serde_json::from_str(&body).unwrap();
    let mut keys: Vec<&str> = json
        .as_object()
        .unwrap()
        .keys()
        .map(String::as_str)
        .collect();
    keys.sort_unstable();
    assert_eq!(
        keys,
        ["customer_id", "label_code", "since", "state"],
        "{role}"
    );
    assert_eq!(json["customer_id"], customer.to_string());

    let (status, xml) = stack
        .get(&floor_path(customer), sub, role, "application/xml")
        .await;
    assert_eq!(status, 200, "{role}: {xml}");
    assert!(xml.contains("<FloorCurrentAssignment"), "{xml}");
    assert_valid_xml(&xml);
    for field in WITHHELD {
        assert!(
            !body.contains(field) && !xml.contains(field),
            "{role} got {field}"
        );
    }
    json
}

#[tokio::test]
async fn both_floor_roles_read_the_three_states_without_scores_or_run() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
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
    let unassigned = TestCustomer::create_unassigned(&stack.owner).await;
    let bare = TestCustomer::create(&stack.owner).await;

    for (sub, role) in FLOOR {
        let floor = read_floor(&stack, assigned, sub, role).await;
        assert_eq!(floor["state"], "assigned");
        assert!(floor["label_code"].is_string() && floor["since"].is_string());

        let floor = read_floor(&stack, unassigned.id, sub, role).await;
        assert_eq!(floor["state"], "unassigned_result");
        assert!(floor["label_code"].is_null() && floor["since"].is_string());

        let floor = read_floor(&stack, bare.id, sub, role).await;
        assert_eq!(floor["state"], "no_open_assignment");
        assert!(floor["label_code"].is_null() && floor["since"].is_null());
    }

    // The floor view is the full answer narrowed, never another source.
    let (_, full) = stack
        .get(
            &format!("/v1/segment-assignments/current?customer_id={assigned}"),
            ANALYST,
            "ANALYST",
            "application/json",
        )
        .await;
    let full: Value = serde_json::from_str(&full).unwrap();
    let floor = read_floor(&stack, assigned, SELLER, "SALES_ASSOCIATE").await;
    for field in ["customer_id", "state", "label_code", "since"] {
        assert_eq!(floor[field], full[field], "{field}");
    }

    unassigned.cleanup(&stack.owner).await;
    bare.cleanup(&stack.owner).await;
    stack.finish().await;
}

#[tokio::test]
async fn other_roles_are_refused_and_floor_roles_never_read_the_scores() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let customer = Uuid::parse_str("00000000-0000-0000-0000-000000000007").unwrap();
    for accept in ["application/json", "application/xml"] {
        for (sub, role) in [(MANAGER, "STORE_MANAGER"), (ANALYST, "ANALYST")] {
            let (status, body) = stack.get(&floor_path(customer), sub, role, accept).await;
            assert_eq!(status, 403, "{role}: {body}");
            assert!(body.contains("forbidden_role"), "{body}");
        }
        for (sub, role) in FLOOR {
            let path = format!("/v1/segment-assignments/current?customer_id={customer}");
            let (status, body) = stack.get(&path, sub, role, accept).await;
            assert_eq!(status, 403, "{role}: {body}");
            assert!(body.contains("forbidden_role"), "{body}");
        }
    }
    stack.finish().await;
}

#[tokio::test]
async fn an_unknown_or_malformed_customer_is_refused() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    for (sub, role) in FLOOR {
        let (status, body) = stack
            .get(&floor_path(Uuid::new_v4()), sub, role, "application/json")
            .await;
        assert_eq!(status, 404, "{body}");
        assert!(body.contains("customer_not_found"), "{body}");
        for path in [
            "/v1/segment-assignments/floor-view",
            "/v1/segment-assignments/floor-view?customer_id=abc",
        ] {
            let (status, body) = stack.get(path, sub, role, "application/json").await;
            assert_eq!(status, 422, "{path}: {body}");
            assert!(body.contains("invalid_query"), "{body}");
        }
    }
    stack.finish().await;
}
