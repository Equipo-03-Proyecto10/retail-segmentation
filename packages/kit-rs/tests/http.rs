//! The request pipeline, mirroring kit-py `tests/test_http.py` for the
//! mechanisms this crate has so far.

mod common;

use common::{app, app_with, get, post, send};
use http::Method;

const COMMON_XML: &str = "<Error xmlns=\"https://mosaiq.maxthecoder.online/xml/common/v1\" \
                          xmlns:common=\"https://mosaiq.maxthecoder.online/xml/common/v1\">";

fn assert_common_xml(answer: &common::Answer, code: &str) {
    assert_eq!(answer.content_type(), Some("application/xml"));
    let text = answer.text();
    assert!(
        text.starts_with("<?xml version='1.0' encoding='UTF-8'?>\n"),
        "{text}"
    );
    assert!(text.contains(COMMON_XML), "{text}");
    assert!(text.contains(&format!("<code>{code}</code>")), "{text}");
}

#[tokio::test]
async fn oversize_is_refused_before_anything_else() {
    let app = app();
    let answer = send(
        &app.router,
        post(
            "/v1/notes",
            &[("content-type", "application/json")],
            vec![b'x'; 65_537],
        ),
    )
    .await;
    assert_eq!(answer.status, 413);
    assert_eq!(answer.json()["code"], "payload_too_large");

    // A body without a Content-Length is counted as it is read.
    let request = http::Request::post("/v1/notes")
        .header("content-type", "application/json")
        .body(axum::body::Body::from_stream(futures::stream::iter(
            (0..17).map(|_| Ok::<_, std::io::Error>(vec![b'x'; 4096])),
        )))
        .unwrap();
    assert_eq!(send(&app.router, request).await.status, 413);
}

#[tokio::test]
async fn xml_client_gets_xml_for_early_errors() {
    let app = app();
    let answer = send(
        &app.router,
        post(
            "/v1/notes",
            &[
                ("accept", "application/xml"),
                ("content-type", "application/json"),
            ],
            vec![b'x'; 65_537],
        ),
    )
    .await;
    assert_eq!(answer.status, 413);
    assert_common_xml(&answer, "payload_too_large");

    let answer = send(
        &app.router,
        get("/missing", &[("accept", "application/xml")]),
    )
    .await;
    assert_eq!(answer.status, 404);
    assert_common_xml(&answer, "not_found");
}

#[tokio::test]
async fn json_is_the_default_and_xml_is_negotiated() {
    let app = app();
    for accept in [None, Some("*/*"), Some("application/json")] {
        let headers: Vec<(&str, &str)> = accept.into_iter().map(|a| ("accept", a)).collect();
        let answer = send(&app.router, get("/v1/notes", &headers)).await;
        assert_eq!(answer.status, 200);
        assert_eq!(answer.content_type(), Some("application/json"));
    }
    let answer = send(
        &app.router,
        get("/v1/notes", &[("accept", "application/xml")]),
    )
    .await;
    assert_eq!(answer.status, 200);
    assert_eq!(answer.content_type(), Some("application/xml"));
    assert!(
        answer
            .text()
            .contains(&format!("<Notes xmlns=\"{}\"", common::NAMESPACE))
    );
}

#[tokio::test]
async fn unacceptable_response_is_a_json_406() {
    let answer = send(&app().router, get("/v1/notes", &[("accept", "text/plain")])).await;
    assert_eq!(answer.status, 406);
    assert_eq!(answer.content_type(), Some("application/json"));
    assert_eq!(answer.json()["code"], "not_acceptable");
}

#[tokio::test]
async fn unsupported_body_uses_the_negotiated_format() {
    let answer = send(
        &app().router,
        post(
            "/v1/notes",
            &[
                ("accept", "application/xml"),
                ("content-type", "text/plain"),
            ],
            b"title".to_vec(),
        ),
    )
    .await;
    assert_eq!(answer.status, 415);
    assert_common_xml(&answer, "unsupported_media_type");
}

#[tokio::test]
async fn invalid_pagination_is_a_422() {
    let app = app();
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
        let answer = send(&app.router, get(&format!("/v1/notes{query}"), &[])).await;
        assert_eq!(answer.status, 422, "{query}");
        assert_eq!(answer.json()["code"], "invalid_pagination", "{query}");
    }
}

#[tokio::test]
async fn valid_pagination_reaches_page_info() {
    let answer = send(&app().router, get("/v1/notes?page=2&size=7", &[])).await;
    assert_eq!(
        answer.json()["page_info"],
        serde_json::json!({"page": 2, "size": 7, "total": 1})
    );
}

#[tokio::test]
async fn correlation_id_is_validated_and_echoed() {
    let app = app();
    for supplied in ["corr-123", "bad value", "", &"x".repeat(65)] {
        let answer = send(
            &app.router,
            get("/v1/notes", &[("x-correlation-id", supplied)]),
        )
        .await;
        let echoed = answer.header("x-correlation-id").unwrap();
        assert_eq!(echoed == supplied, supplied == "corr-123", "{supplied:?}");
        assert!((1..=64).contains(&echoed.len()));
    }
    let answer = send(
        &app.router,
        get("/v1/notes?page=0", &[("x-correlation-id", "corr-error")]),
    )
    .await;
    assert_eq!(answer.json()["correlation_id"], "corr-error");
    assert_eq!(answer.header("x-correlation-id"), Some("corr-error"));
}

#[tokio::test]
async fn meta_routes_are_public_and_byte_exact() {
    let app = app();
    assert_eq!(
        send(&app.router, get("/health/live", &[])).await.status,
        204
    );
    assert_eq!(
        send(&app.router, get("/health/ready", &[])).await.status,
        204
    );
    let staged = app.contracts.path().join("kit-example");
    let answer = send(&app.router, get("/openapi.yaml", &[])).await;
    assert_eq!(
        answer.body,
        std::fs::read(staged.join("openapi.yaml")).unwrap()
    );
    assert_eq!(answer.content_type(), Some("application/yaml"));
    let answer = send(&app.router, get("/schema.xsd", &[])).await;
    assert_eq!(
        answer.body,
        std::fs::read(staged.join("kit-example.xsd")).unwrap()
    );
    assert_eq!(answer.content_type(), Some("application/xml"));
}

#[tokio::test]
async fn failed_readiness_has_its_own_error() {
    let answer = send(&app_with(false).router, get("/health/ready", &[])).await;
    assert_eq!(answer.status, 503);
    assert_eq!(answer.json()["code"], "schema_not_ready");
}

#[tokio::test]
async fn undeclared_methods_are_routing_errors() {
    let app = app();
    let request = http::Request::builder()
        .method(Method::OPTIONS)
        .uri("/v1/notes")
        .body(axum::body::Body::empty())
        .unwrap();
    let answer = send(&app.router, request).await;
    assert_eq!(answer.status, 405);
    assert_eq!(answer.json()["code"], "method_not_allowed");
    assert!(answer.header("allow").unwrap_or_default().contains("GET"));
    assert_eq!(send(&app.router, get("/v1/notes/", &[])).await.status, 404);
}

#[tokio::test]
async fn service_errors_and_panics_use_the_envelope() {
    let app = app();
    let answer = send(&app.router, get("/v1/notes/2", &[])).await;
    assert_eq!(answer.status, 404);
    assert_eq!(answer.json()["code"], "note_not_found");
    let answer = send(&app.router, get("/v1/notes/13", &[])).await;
    assert_eq!(answer.status, 500);
    assert_eq!(answer.json()["code"], "internal_error");
    assert!(!answer.text().contains("trace-value"));
    let answer = send(&app.router, get("/v1/notes/1", &[])).await;
    assert_eq!(answer.text(), "{\"note_id\":1}");
}

#[tokio::test]
async fn created_status_is_kept() {
    let answer = send(
        &app().router,
        post(
            "/v1/notes",
            &[("content-type", "application/json")],
            b"{}".to_vec(),
        ),
    )
    .await;
    assert_eq!(answer.status, 201);
}
