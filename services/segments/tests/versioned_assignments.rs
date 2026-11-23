//! Versioned assignment reads through the complete router and the real schema.

mod support;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use axum::Router;
use http_body_util::BodyExt;
use mosaiq_kit::rendering::{render_json, render_xml};
use segments::{
    read_versioned_assignments, versioned_assignment_filter, versioned_assignments_reply, Reader,
    VersionedAssignmentFilter,
};
use support::{
    admin, assert_valid_xml, connect, drop_acl, kit_config, load_acl, owner_url, redis_url,
    service_database, Keys,
};
use tokio_postgres::Client;
use uuid::Uuid;

const SUBJECT: &str = "11111111-1111-1111-1111-000000000002";

const NAMESPACE: &str = "https://mosaiq.maxthecoder.online/xml/segments/v1";
const CUSTOMER: &str = "00000000-0000-0000-0000-000000000006";
const ALLOWED: [&str; 5] = ["ADMIN", "ANALYST", "STORE_MANAGER", "MARKETING", "AUDITOR"];
const REFUSED: [&str; 2] = ["SALES_ASSOCIATE", "LOYALTY_OFFICER"];

struct Stack {
    app: Router,
    keys: Keys,
    sid: String,
    owner: Client,
    redis: redis::aio::MultiplexedConnection,
    redis_url: String,
    password: String,
}

impl Stack {
    async fn start() -> Option<Self> {
        let database = owner_url();
        let redis_url = redis_url()?;
        let mut redis = admin(&redis_url).await;
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
        let config = kit_config(&keys, redis_url.clone(), password.clone(), &database);
        let (app, _) = segments::router(config, "contracts", Reader::new(None)).unwrap();
        let owner = connect(&database).await;
        Some(Self {
            app,
            keys,
            sid,
            owner,
            redis,
            redis_url,
            password,
        })
    }

    async fn get(
        &self,
        app: &Router,
        path: &str,
        role: Option<&str>,
        accept: &str,
    ) -> (StatusCode, String, Option<String>) {
        self.get_as(app, path, SUBJECT, role, accept).await
    }

    /// The same request as another caller. The kit keys its rate limit by the
    /// caller and the window, and Redis keeps the counter between runs.
    async fn get_as(
        &self,
        app: &Router,
        path: &str,
        subject: &str,
        role: Option<&str>,
        accept: &str,
    ) -> (StatusCode, String, Option<String>) {
        let correlation = format!("versioned-{}", Uuid::new_v4().simple());
        let mut request = Request::builder()
            .uri(path)
            .header("Accept", accept)
            .header("X-Correlation-ID", &correlation);
        if let Some(role) = role {
            request = request.header(
                "Authorization",
                format!("Bearer {}", self.keys.token(subject, role, &self.sid)),
            );
        }
        let response =
            tower::ServiceExt::oneshot(app.clone(), request.body(Body::empty()).unwrap())
                .await
                .unwrap();
        let status = response.status();
        let returned_correlation = response
            .headers()
            .get("X-Correlation-ID")
            .and_then(|header| header.to_str().ok())
            .map(str::to_owned);
        let body = response.into_body().collect().await.unwrap().to_bytes();
        (
            status,
            String::from_utf8(body.to_vec()).unwrap(),
            returned_correlation,
        )
    }

    async fn finish(mut self) {
        drop_acl(&mut self.redis).await;
    }
}

#[test]
fn optional_filters_validate_the_contract_rules() {
    assert_eq!(
        versioned_assignment_filter(
            "model_version_id=4&customer_id=7b3ad781-c5de-4f40-ae50-1fa825ce2b25&open=true"
        )
        .unwrap(),
        VersionedAssignmentFilter {
            model_version_id: Some(4),
            customer_id: Some(Uuid::parse_str("7b3ad781-c5de-4f40-ae50-1fa825ce2b25").unwrap()),
            label_code: None,
            open: true,
        }
    );
    for query in [
        "model_version_id=0",
        "model_version_id=nope",
        "customer_id=nope",
        "label_code=",
        "open=yes",
        "customer_id=7b3ad781-c5de-4f40-ae50-1fa825ce2b25&label_code=LOYAL",
    ] {
        let error = versioned_assignment_filter(query).unwrap_err();
        assert_eq!(error.status, 422, "{query}");
        assert_eq!(error.code, "invalid_query", "{query}");
    }
}

#[tokio::test]
async fn filters_pagination_versions_and_representations_match_the_contract() {
    let url = owner_url();
    let db = service_database(&url);
    let (first, total) = read_versioned_assignments(
        &db,
        &VersionedAssignmentFilter {
            model_version_id: Some(1),
            ..VersionedAssignmentFilter::default()
        },
        1,
        7,
    )
    .await
    .unwrap();
    let (second, second_total) = read_versioned_assignments(
        &db,
        &VersionedAssignmentFilter {
            model_version_id: Some(1),
            ..VersionedAssignmentFilter::default()
        },
        2,
        7,
    )
    .await
    .unwrap();
    assert_eq!(first.len(), 7);
    assert_eq!(second.len(), 7);
    assert_eq!(total, second_total);
    assert!(total > 14);
    assert!(first
        .iter()
        .chain(&second)
        .all(|row| { row.model_version_id == Some(1) && row.cause.is_some() }));
    assert!(first
        .windows(2)
        .all(|rows| rows[0].valid_from >= rows[1].valid_from));
    assert_ne!(first[0].customer_id, second[0].customer_id);

    let (customer, customer_total) = read_versioned_assignments(
        &db,
        &VersionedAssignmentFilter {
            customer_id: Some(Uuid::parse_str(CUSTOMER).unwrap()),
            open: true,
            ..VersionedAssignmentFilter::default()
        },
        1,
        20,
    )
    .await
    .unwrap();
    assert_eq!(customer_total, 1);
    assert_eq!(customer[0].customer_id.to_string(), CUSTOMER);
    assert!(customer[0].valid_to.is_none());

    let (label, _) = read_versioned_assignments(
        &db,
        &VersionedAssignmentFilter {
            label_code: Some("LOYAL".to_owned()),
            ..VersionedAssignmentFilter::default()
        },
        1,
        20,
    )
    .await
    .unwrap();
    assert!(label
        .iter()
        .all(|row| row.label_code.as_deref() == Some("LOYAL")));

    let (missing_version, missing_total) = read_versioned_assignments(
        &db,
        &VersionedAssignmentFilter {
            model_version_id: Some(i64::MAX),
            ..VersionedAssignmentFilter::default()
        },
        1,
        20,
    )
    .await
    .unwrap();
    assert!(missing_version.is_empty());
    assert_eq!(missing_total, 0);

    let json = render_json(&versioned_assignments_reply(&first, 1, 7, total), 20).unwrap();
    assert_eq!(json["assignments"].as_array().unwrap().len(), 7);
    assert_eq!(json["page_info"]["size"], 7);
    let xml = render_xml(
        &versioned_assignments_reply(&first, 1, 7, total),
        NAMESPACE,
        20,
    )
    .unwrap();
    assert_eq!(xml.matches("<VersionedSegmentAssignment>").count(), 7);
    assert_valid_xml(&xml);
}

#[tokio::test]
async fn pending_version_is_null_in_json_and_omitted_in_xml() {
    let url = owner_url();
    let owner = connect(&url).await;
    let customer = Uuid::new_v4();
    owner
        .execute(
            "INSERT INTO public.customer (customer_id, name, registration_channel_id) \
             VALUES ($1, $2, (SELECT min(channel_id) FROM public.channel))",
            &[&customer, &format!("version-pending-{customer}")],
        )
        .await
        .unwrap();
    let run: i64 = owner
        .query_one(
            "INSERT INTO public.segmentation_run \
             (method, window_days, parameters, customer_count, run_at, data_origin) \
             VALUES ('RFM_RULES', 30, '{}'::jsonb, 1, now(), \
                     fn_segmentation_window_origin(30)) RETURNING run_id",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    owner
        .execute(
            "INSERT INTO public.customer_segment_history \
             (customer_id, run_id, cause, label_code, r_score, f_score, m_score) \
             VALUES ($1, $2, 'FULL_RUN', 'LOYAL', 4, 4, 4)",
            &[&customer, &run],
        )
        .await
        .unwrap();

    let db = service_database(&url);
    let (rows, total) = read_versioned_assignments(
        &db,
        &VersionedAssignmentFilter {
            customer_id: Some(customer),
            ..VersionedAssignmentFilter::default()
        },
        1,
        20,
    )
    .await
    .unwrap();
    assert_eq!(total, 1);
    assert_eq!(rows[0].model_version_id, None);
    let json = render_json(&versioned_assignments_reply(&rows, 1, 20, total), 20).unwrap();
    assert!(json["assignments"][0]["model_version_id"].is_null());
    let xml = render_xml(
        &versioned_assignments_reply(&rows, 1, 20, total),
        NAMESPACE,
        20,
    )
    .unwrap();
    assert!(!xml.contains("model_version_id"));
    assert_valid_xml(&xml);
    owner
        .execute(
            "DELETE FROM public.customer WHERE customer_id = $1",
            &[&customer],
        )
        .await
        .unwrap();
}

#[tokio::test]
async fn router_covers_roles_formats_errors_correlation_and_writes_nothing() {
    let Some(stack) = Stack::start().await else {
        return;
    };
    let path = "/v1/segment-assignments/versioned?model_version_id=1&page=1&size=2";
    let before_row = stack
        .owner
        .query_one(
            "SELECT (SELECT count(*) FROM public.customer_segment_history), \
                    (SELECT count(*) FROM public.segmentation_run), \
                    (SELECT count(*) FROM audit_events.domain_event)",
            &[],
        )
        .await
        .unwrap();
    let before: (i64, i64, i64) = (before_row.get(0), before_row.get(1), before_row.get(2));
    for role in ALLOWED {
        let (status, body, correlation) = stack
            .get(&stack.app, path, Some(role), "application/json")
            .await;
        assert_eq!(status, StatusCode::OK, "{role}: {body}");
        assert!(body.contains("\"model_version_id\":1"), "{role}: {body}");
        assert!(correlation.unwrap().starts_with("versioned-"));
    }
    let (status, xml, _) = stack
        .get(&stack.app, path, Some("ANALYST"), "application/xml")
        .await;
    assert_eq!(status, StatusCode::OK, "{xml}");
    assert_valid_xml(&xml);
    for role in REFUSED {
        let (status, body, _) = stack
            .get(&stack.app, path, Some(role), "application/json")
            .await;
        assert_eq!(status, StatusCode::FORBIDDEN, "{role}: {body}");
        assert!(body.contains("forbidden_role"), "{role}: {body}");
    }
    let (status, body, _) = stack.get(&stack.app, path, None, "application/json").await;
    assert_eq!(status, StatusCode::UNAUTHORIZED, "{body}");
    assert!(body.contains("invalid_token"), "{body}");
    let mut state = stack.redis.clone();
    let _: () = redis::cmd("DEL")
        .arg(format!("pub:auth:v1:live:{}", stack.sid))
        .query_async(&mut state)
        .await
        .unwrap();
    let (status, body, _) = stack
        .get(&stack.app, path, Some("ANALYST"), "application/json")
        .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED, "{body}");
    assert!(body.contains("session_revoked"), "{body}");
    let _: () = redis::cmd("SET")
        .arg(format!("pub:auth:v1:live:{}", stack.sid))
        .arg("1")
        .arg("EX")
        .arg(300)
        .query_async(&mut state)
        .await
        .unwrap();

    for (suffix, expected, code) in [
        (
            "&customer_id=00000000-0000-0000-0000-00000000ffff",
            404,
            "customer_not_found",
        ),
        ("&label_code=NOT_A_LABEL", 404, "segment_not_found"),
        ("&open=yes", 422, "invalid_query"),
        ("&page=0", 422, "invalid_pagination"),
    ] {
        let (status, body, _) = stack
            .get(
                &stack.app,
                &format!("/v1/segment-assignments/versioned?model_version_id=1{suffix}"),
                Some("ANALYST"),
                "application/json",
            )
            .await;
        assert_eq!(status.as_u16(), expected, "{suffix}: {body}");
        assert!(body.contains(code), "{suffix}: {body}");
    }
    let (status, body, _) = stack
        .get(&stack.app, path, Some("ANALYST"), "text/plain")
        .await;
    assert_eq!(status, StatusCode::NOT_ACCEPTABLE, "{body}");
    assert!(body.contains("not_acceptable"), "{body}");

    let after_row = stack
        .owner
        .query_one(
            "SELECT (SELECT count(*) FROM public.customer_segment_history), \
                    (SELECT count(*) FROM public.segmentation_run), \
                    (SELECT count(*) FROM audit_events.domain_event)",
            &[],
        )
        .await
        .unwrap();
    let after: (i64, i64, i64) = (after_row.get(0), after_row.get(1), after_row.get(2));
    assert_eq!(before, after, "the read and its refusals record no row");

    let mut limited = kit_config(
        &stack.keys,
        stack.redis_url.clone(),
        stack.password.clone(),
        &owner_url(),
    );
    limited.rate_limit_enabled = true;
    limited.rate_limit_requests = 1;
    let (limited_app, _) = segments::router(limited, "contracts", Reader::new(None)).unwrap();
    // A caller of its own, so a counter left in Redis by an earlier run within
    // the same window cannot answer the first request with 429.
    let caller = Uuid::new_v4().to_string();
    assert_eq!(
        stack
            .get_as(
                &limited_app,
                path,
                &caller,
                Some("ANALYST"),
                "application/json"
            )
            .await
            .0,
        StatusCode::OK
    );
    let (status, body, _) = stack
        .get_as(
            &limited_app,
            path,
            &caller,
            Some("ANALYST"),
            "application/json",
        )
        .await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS, "{body}");
    assert!(body.contains("rate_limited"), "{body}");

    let mut unavailable = kit_config(
        &stack.keys,
        stack.redis_url.clone(),
        stack.password.clone(),
        &owner_url(),
    );
    unavailable.database_url =
        Some("postgresql://svc_segments:svc_segments@127.0.0.1:1/retail".to_owned());
    let (unavailable_app, _) =
        segments::router(unavailable, "contracts", Reader::new(None)).unwrap();
    let (status, body, _) = stack
        .get(&unavailable_app, path, Some("ANALYST"), "application/json")
        .await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE, "{body}");
    assert!(body.contains("database_unavailable"), "{body}");

    let auth_unavailable = kit_config(
        &stack.keys,
        "redis://127.0.0.1:1/0".to_owned(),
        stack.password.clone(),
        &owner_url(),
    );
    let (auth_unavailable_app, _) =
        segments::router(auth_unavailable, "contracts", Reader::new(None)).unwrap();
    let (status, body, _) = stack
        .get(
            &auth_unavailable_app,
            path,
            Some("ANALYST"),
            "application/json",
        )
        .await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE, "{body}");
    assert!(body.contains("auth_unavailable"), "{body}");
    stack.finish().await;
}
