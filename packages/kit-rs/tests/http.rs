//! HTTP behaviour, mirroring kit-py's tests/test_http.py.

mod common;

use common::*;
use mosaiq_kit::{ApiError, Ctx, Reply, Representation};
use serde_json::json;

const AUTH_CASES: [&str; 9] = [
    "no_token",
    "alg_none",
    "hmac_with_public_key",
    "wrong_aud",
    "wrong_iss",
    "expired",
    "tampered_payload",
    "unknown_kid",
    "revoked_session",
];

#[tokio::test]
async fn public_vectors_produce_the_expected_authentication_error() {
    let Some(app) = app().await else { return };
    let expected = expected();
    for case in AUTH_CASES {
        let answer = call("GET", "/v1/notes").bearer(case).send(&app).await;
        assert_eq!(
            answer.status.as_u16() as u64,
            expected[case]["status"].as_u64().unwrap(),
            "{case}"
        );
        assert_eq!(
            answer.code(),
            expected[case]["code"].as_str().unwrap(),
            "{case}"
        );
    }
}

#[tokio::test]
async fn valid_tokens_reach_the_handler() {
    let Some(app) = app().await else { return };
    let expected = expected();
    for case in ["valid_reader", "valid_writer"] {
        let answer = call("GET", "/v1/notes").bearer(case).send(&app).await;
        assert_eq!(
            answer.status.as_u16() as u64,
            expected[case]["status"].as_u64().unwrap()
        );
        assert_eq!(
            answer.json()["page_info"],
            json!({"page": 1, "size": 20, "total": 1})
        );
    }
}

#[tokio::test]
async fn operation_roles_come_from_the_contract() {
    let Some(app) = app().await else { return };
    let answer = call("POST", "/v1/notes")
        .bearer("valid_reader")
        .json(json!({"title": "New", "amount": "1.00"}))
        .send(&app)
        .await;
    assert_eq!(answer.status, 403);
    assert_eq!(answer.code(), expected()["forbidden_role"]["code"]);
}

#[tokio::test]
async fn authentication_precedes_negotiation() {
    let Some(app) = app().await else { return };
    let answer = call("GET", "/v1/notes")
        .header("Accept", "text/plain")
        .send(&app)
        .await;
    assert_eq!(answer.status, 401);
    assert_eq!(answer.code(), "invalid_token");
}

#[tokio::test]
async fn oversize_precedes_authentication() {
    let Some(app) = app().await else { return };
    let answer = call("POST", "/v1/notes")
        .body("application/json", vec![b'x'; 65_537])
        .send(&app)
        .await;
    assert_eq!(answer.status, 413);
    assert_eq!(answer.code(), "payload_too_large");
}

#[tokio::test]
async fn a_body_of_exactly_64_kib_is_not_too_large() {
    let Some(app) = app().await else { return };
    let answer = call("POST", "/v1/notes")
        .bearer("valid_writer")
        .body("application/json", vec![b' '; 65_536])
        .send(&app)
        .await;
    assert_eq!(answer.status, 400);
    assert_eq!(answer.code(), "malformed_body");
}

#[tokio::test]
async fn xml_client_gets_xml_for_early_errors() {
    let Some(app) = app().await else { return };
    let answer = call("GET", "/v1/notes")
        .header("Accept", "application/xml")
        .send(&app)
        .await;
    assert_eq!(answer.status, 401);
    assert_common_xml(&answer, "invalid_token");

    let answer = call("POST", "/v1/notes")
        .header("Accept", "application/xml")
        .body("application/json", vec![b'x'; 65_537])
        .send(&app)
        .await;
    assert_eq!(answer.status, 413);
    assert_common_xml(&answer, "payload_too_large");

    let answer = call("GET", "/missing")
        .header("Accept", "application/xml")
        .send(&app)
        .await;
    assert_eq!(answer.status, 404);
    assert_common_xml(&answer, "not_found");
}

#[tokio::test]
async fn json_negotiation_defaults() {
    let Some(app) = app().await else { return };
    for accept in [None, Some("*/*"), Some("application/json")] {
        let mut request = call("GET", "/v1/notes").bearer("valid_reader");
        if let Some(accept) = accept {
            request = request.header("Accept", accept);
        }
        let answer = request.send(&app).await;
        assert_eq!(answer.status, 200);
        assert_eq!(answer.content_type(), "application/json");
    }
}

#[tokio::test]
async fn xml_negotiation_validates_against_the_service_xsd() {
    let Some(app) = app().await else { return };
    let answer = call("GET", "/v1/notes")
        .bearer("valid_reader")
        .header("Accept", "application/xml")
        .send(&app)
        .await;
    assert_eq!(answer.status, 200);
    assert_eq!(answer.content_type(), "application/xml");
    xsd_valid(
        &contracts_dir().join("kit-example/kit-example.xsd"),
        &answer.body,
    )
    .unwrap();
    assert!(answer.text().contains("<Notes "));
}

#[tokio::test]
async fn unacceptable_response_is_json() {
    let Some(app) = app().await else { return };
    let answer = call("GET", "/v1/notes")
        .bearer("valid_reader")
        .header("Accept", "text/plain")
        .send(&app)
        .await;
    assert_eq!(answer.status, 406);
    assert_eq!(answer.content_type(), "application/json");
    assert_eq!(answer.code(), "not_acceptable");
}

#[tokio::test]
async fn unsupported_body_uses_negotiated_error() {
    let Some(app) = app().await else { return };
    let answer = call("POST", "/v1/notes")
        .bearer("valid_writer")
        .header("Accept", "application/xml")
        .body("application/x-www-form-urlencoded", "title=wrong")
        .send(&app)
        .await;
    assert_eq!(answer.status, 415);
    assert_common_xml(&answer, "unsupported_media_type");
}

#[tokio::test]
async fn malformed_bodies_are_refused() {
    let Some(app) = app().await else { return };
    let cases: [(&[u8], &str); 7] = [
        (b"{", "application/json"),
        (br#"{"title":NaN,"amount":"1.00"}"#, "application/json"),
        (b"<CreateNoteRequest>", "application/xml"),
        (
            br#"<!DOCTYPE x [<!ENTITY a "ha">]><CreateNoteRequest>&a;</CreateNoteRequest>"#,
            "application/xml",
        ),
        (
            br#"<!DOCTYPE x [<!ENTITY a "x"><!ENTITY b "&a;&a;">]><CreateNoteRequest>&b;</CreateNoteRequest>"#,
            "application/xml",
        ),
        (
            br#"<!DOCTYPE x [<!ENTITY ext SYSTEM "file:///etc/passwd">]><CreateNoteRequest>&ext;</CreateNoteRequest>"#,
            "application/xml",
        ),
        (b"<CreateNoteRequest>\x00</CreateNoteRequest>", "application/xml"),
    ];
    for (payload, content_type) in cases {
        let answer = call("POST", "/v1/notes")
            .bearer("valid_writer")
            .body(content_type, payload)
            .send(&app)
            .await;
        assert_eq!(answer.status, 400, "{}", String::from_utf8_lossy(payload));
        assert_eq!(answer.code(), "malformed_body");
    }
}

#[tokio::test]
async fn an_undeclared_entity_without_a_doctype_is_refused() {
    let Some(app) = app().await else { return };
    let body = format!(
        r#"<CreateNoteRequest xmlns="{EXAMPLE_NAMESPACE}"><title>&a;</title><amount>1.00</amount></CreateNoteRequest>"#
    );
    let answer = call("POST", "/v1/notes")
        .bearer("valid_writer")
        .body("application/xml", body)
        .send(&app)
        .await;
    assert_eq!(answer.status, 400);
    assert_eq!(answer.code(), "malformed_body");
}

#[tokio::test]
async fn schema_errors_have_stable_details() {
    let Some(app) = app().await else { return };
    let cases = [
        (json!({"amount": "1.00"}), "title", "required"),
        (
            json!({"title": 3, "amount": "1.00"}),
            "title",
            "invalid_type",
        ),
        (
            json!({"title": "New", "amount": "1"}),
            "amount",
            "invalid_format",
        ),
        (
            json!({"title": "New", "amount": "1.00", "extra": true}),
            "extra",
            "unexpected_field",
        ),
    ];
    for (body, field, code) in cases {
        let answer = call("POST", "/v1/notes")
            .bearer("valid_writer")
            .json(body.clone())
            .send(&app)
            .await;
        assert_eq!(answer.status, 422, "{body}");
        let json = answer.json();
        assert_eq!(json["code"], "invalid_body");
        assert!(
            json["details"]
                .as_array()
                .unwrap()
                .contains(&json!({"field": field, "code": code})),
            "{body}: {json}"
        );
    }
}

#[tokio::test]
async fn duplicate_json_member_is_malformed() {
    let Some(app) = app().await else { return };
    let answer = call("POST", "/v1/notes")
        .bearer("valid_writer")
        .body(
            "application/json",
            r#"{"title":"First","title":"Second","amount":"1.00"}"#,
        )
        .send(&app)
        .await;
    assert_eq!(answer.status, 400);
    assert_eq!(answer.code(), "malformed_body");
}

#[tokio::test]
async fn valid_xml_and_json_reach_the_same_handler() {
    let Some(app) = app().await else { return };
    let xml = format!(
        r#"<CreateNoteRequest xmlns="{EXAMPLE_NAMESPACE}"><title>New</title><amount>1.25</amount></CreateNoteRequest>"#
    );
    let xml_answer = call("POST", "/v1/notes")
        .bearer("valid_writer")
        .header("Accept", "application/xml")
        .body("application/xml", xml)
        .send(&app)
        .await;
    let json_answer = call("POST", "/v1/notes")
        .bearer("valid_writer")
        .json(json!({"title": "New", "amount": "1.25"}))
        .send(&app)
        .await;
    assert_eq!(xml_answer.status, 201, "{}", xml_answer.text());
    assert_eq!(json_answer.status, 201);
    assert_eq!(xml_answer.header("Location"), Some("/v1/notes/46"));
    xsd_valid(
        &contracts_dir().join("kit-example/kit-example.xsd"),
        &xml_answer.body,
    )
    .unwrap();
    let json = json_answer.json();
    assert_eq!(json["title"], "New");
    assert_eq!(json["amount"], "1.25");
    assert_eq!(json["created_by"], WRITER_SUB);
    let text = xml_answer.text();
    assert!(text.contains("<title>New</title>") && text.contains("<amount>1.25</amount>"));
}

#[tokio::test]
async fn wrong_xml_root_is_an_invalid_body() {
    let Some(app) = app().await else { return };
    let xml = format!(r#"<Note xmlns="{EXAMPLE_NAMESPACE}"><title>New</title></Note>"#);
    let answer = call("POST", "/v1/notes")
        .bearer("valid_writer")
        .body("application/xml", xml)
        .send(&app)
        .await;
    assert_eq!(answer.status, 422);
    assert_eq!(
        answer.json()["details"],
        json!([{"field": "$", "code": "invalid"}])
    );
}

#[tokio::test]
async fn invalid_pagination() {
    let Some(app) = app().await else { return };
    for query in [
        "?page=0",
        "?page=no",
        "?page=1_0",
        "?page=%2B5",
        "?page=%205",
        "?size=0",
        "?size=101",
        "?size=no",
    ] {
        let answer = call("GET", &format!("/v1/notes{query}"))
            .bearer("valid_reader")
            .send(&app)
            .await;
        assert_eq!(answer.status, 422, "{query}");
        assert_eq!(answer.code(), "invalid_pagination");
    }
}

#[tokio::test]
async fn valid_pagination_is_passed_to_page_info() {
    let Some(app) = app().await else { return };
    let answer = call("GET", "/v1/notes?page=2&size=7")
        .bearer("valid_reader")
        .send(&app)
        .await;
    assert_eq!(
        answer.json()["page_info"],
        json!({"page": 2, "size": 7, "total": 1})
    );
}

#[tokio::test]
async fn correlation_id_is_validated_and_echoed() {
    let Some(app) = app().await else { return };
    let long = "x".repeat(65);
    for supplied in ["corr-123", "bad value", "", long.as_str()] {
        let answer = call("GET", "/v1/notes")
            .bearer("valid_reader")
            .header("X-Correlation-ID", supplied)
            .send(&app)
            .await;
        let echoed = answer.header("X-Correlation-ID").unwrap().to_owned();
        if supplied == "corr-123" {
            assert_eq!(echoed, supplied);
        } else {
            assert_ne!(echoed, supplied);
        }
        assert!((1..=64).contains(&echoed.len()));
    }
    let answer = call("GET", "/v1/notes")
        .header("X-Correlation-ID", "corr-error")
        .send(&app)
        .await;
    assert_eq!(answer.json()["correlation_id"], "corr-error");
    assert_eq!(answer.header("X-Correlation-ID"), Some("corr-error"));
}

#[tokio::test]
async fn meta_routes_are_public_and_byte_exact() {
    let Some(app) = app().await else { return };
    assert_eq!(call("GET", "/health/live").send(&app).await.status, 204);
    assert_eq!(call("GET", "/health/ready").send(&app).await.status, 204);
    let openapi = call("GET", "/openapi.yaml").send(&app).await;
    assert_eq!(
        openapi.body,
        std::fs::read(contracts_dir().join("kit-example/openapi.yaml")).unwrap()
    );
    assert_eq!(openapi.content_type(), "application/yaml");
    let xsd = call("GET", "/schema.xsd").send(&app).await;
    assert_eq!(
        xsd.body,
        std::fs::read(contracts_dir().join("kit-example/kit-example.xsd")).unwrap()
    );
    assert_eq!(xsd.content_type(), "application/xml");
}

#[tokio::test]
async fn failed_readiness_has_its_own_error() {
    if redis_url().is_none() {
        return;
    }
    let app = kit(config().await)
        .ready_check(|_db| async { false })
        .into_router();
    let answer = call("GET", "/health/ready").send(&app).await;
    assert_eq!(answer.status, 503);
    assert_eq!(answer.code(), "schema_not_ready");
}

#[tokio::test]
async fn path_constraints_and_undeclared_methods_are_routing_errors() {
    let Some(app) = app().await else { return };
    let answer = call("GET", "/v1/notes/0")
        .bearer("valid_reader")
        .send(&app)
        .await;
    assert_eq!(answer.status, 404);
    assert_eq!(answer.code(), "not_found");
    let answer = call("GET", "/v1/notes/abc")
        .bearer("valid_reader")
        .send(&app)
        .await;
    assert_eq!(answer.status, 404);
    let answer = call("OPTIONS", "/v1/notes")
        .bearer("valid_reader")
        .send(&app)
        .await;
    assert_eq!(answer.status, 405);
    assert_eq!(answer.code(), "method_not_allowed");
    assert!(answer.header("Allow").unwrap().contains("GET"));
    let answer = call("DELETE", "/health/live").send(&app).await;
    assert_eq!(answer.status, 405);
}

async fn list_nothing(_ctx: Ctx) -> Result<Reply, ApiError> {
    Ok(Representation::new("Notes", Vec::<(String, mosaiq_kit::Value)>::new()).into())
}

#[tokio::test]
async fn session_store_unreachable_fails_closed() {
    if redis_url().is_none() {
        return;
    }
    let mut config = config().await;
    config.redis_state_url = "redis://127.0.0.1:1/0".to_owned();
    let app = kit(config)
        .operation("listNotes", list_nothing)
        .unwrap()
        .into_router();
    let answer = call("GET", "/v1/notes")
        .bearer("valid_reader")
        .send(&app)
        .await;
    let expected = expected();
    assert_eq!(
        answer.status.as_u16() as u64,
        expected["auth_unavailable"]["status"]
    );
    assert_eq!(answer.code(), expected["auth_unavailable"]["code"]);
}

#[tokio::test]
async fn rate_limit_precedes_negotiation() {
    if redis_url().is_none() {
        return;
    }
    let mut admin = admin().await;
    let clear = |mut admin: redis::aio::MultiplexedConnection| async move {
        let keys: Vec<String> = redis::cmd("KEYS")
            .arg(format!("{}:rl:*", suffix()))
            .query_async(&mut admin)
            .await
            .unwrap();
        if !keys.is_empty() {
            let _: i64 = redis::cmd("DEL")
                .arg(&keys)
                .query_async(&mut admin)
                .await
                .unwrap();
        }
    };
    clear(admin.clone()).await;
    let mut config = config().await;
    config.rate_limit_enabled = true;
    config.rate_limit_requests = 1;
    config.rate_limit_window_seconds = 60;
    let app = kit(config)
        .operation("listNotes", list_nothing)
        .unwrap()
        .into_router();
    let first = call("GET", "/v1/notes")
        .bearer("valid_reader")
        .header("Accept", "application/xml")
        .send(&app)
        .await;
    assert_eq!(first.status, 200);
    let limited = call("GET", "/v1/notes")
        .bearer("valid_reader")
        .header("Accept", "application/xml")
        .send(&app)
        .await;
    assert_eq!(limited.status, 429);
    assert_common_xml(&limited, "rate_limited");
    assert!(
        limited
            .header("Retry-After")
            .unwrap()
            .parse::<u64>()
            .unwrap()
            >= 1
    );

    clear(admin.clone()).await;
    let unacceptable = call("GET", "/v1/notes")
        .bearer("valid_writer")
        .header("Accept", "text/plain")
        .send(&app)
        .await;
    assert_eq!(unacceptable.status, 406);
    let limited = call("GET", "/v1/notes")
        .bearer("valid_writer")
        .header("Accept", "text/plain")
        .send(&app)
        .await;
    assert_eq!(limited.status, 429);
    assert_eq!(limited.code(), "rate_limited");
    let ttl: i64 = redis::cmd("TTL")
        .arg(
            redis::cmd("KEYS")
                .arg(format!("{}:rl:*", suffix()))
                .query_async::<Vec<String>>(&mut admin)
                .await
                .unwrap()[0]
                .clone(),
        )
        .query_async(&mut admin)
        .await
        .unwrap();
    assert!(ttl > 60 && ttl <= 120, "{ttl}");
    clear(admin).await;
}

#[test]
fn vector_files_have_public_material_only() {
    let jwks: serde_json::Value =
        serde_json::from_slice(&std::fs::read(vectors().join("jwks.json")).unwrap()).unwrap();
    let mut members: Vec<&str> = jwks["keys"][0]
        .as_object()
        .unwrap()
        .keys()
        .map(String::as_str)
        .collect();
    members.sort_unstable();
    assert_eq!(members, ["alg", "e", "kid", "kty", "n", "use"]);
    assert!(!std::fs::read_dir(vectors()).unwrap().any(|entry| entry
        .unwrap()
        .path()
        .extension()
        .is_some_and(|ext| ext == "pem")));
}
