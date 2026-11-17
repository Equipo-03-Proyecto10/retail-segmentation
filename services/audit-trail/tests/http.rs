//! The service through its whole router: roles, methods, and what a client
//! receives in JSON and in XML. The harness is `support::http`.

mod support;

use serde_json::json;
use support::http::{send, Stack, ROLES, TURN};
use support::{connect, remove_test_rows};
use uuid::Uuid;

const PATHS: [&str; 2] = ["/v1/audit-entries", "/v1/audit-entries/1"];

#[tokio::test]
async fn roles_methods_and_both_representations() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let app = &stack.app;
    let keys = &stack.keys;
    let sid = &stack.sid;
    let database = &stack.database;

    // Only ADMIN and AUDITOR read; every other role is refused on both paths.
    for role in ROLES {
        let token = keys.token(role, sid);
        for path in PATHS {
            let (status, body) = send(app, "GET", path, Some(&token), "application/json").await;
            if matches!(role, "ADMIN" | "AUDITOR") {
                assert_eq!(status, 200, "{role} {path}: {body}");
            } else {
                assert_eq!(status, 403, "{role} {path}");
                assert!(body.contains("\"forbidden_role\""), "{body}");
            }
        }
    }

    // There is no write: every other method on either path is 405.
    let token = keys.token("ADMIN", sid);
    for method in ["POST", "PUT", "PATCH", "DELETE"] {
        for path in PATHS {
            let (status, _) = send(app, method, path, Some(&token), "application/json").await;
            assert_eq!(status, 405, "{method} {path}");
        }
    }

    // A credential written straight into the table reaches no client.
    let owner = connect(database).await;
    let key = format!("http-test-{}", Uuid::new_v4());
    let id: i64 = owner
        .query_one(
            "INSERT INTO public.audit_log (entity, entity_pk, action, data_before, data_after) \
             VALUES ('app_user', $1, 'UPDATE', \
                     '{\"password_hash\":\"$argon2id$before\",\"name\":\"A\"}'::jsonb, \
                     '{\"password_hash\":\"$argon2id$after\",\"nested\":{\"password_hash\":\"x\"}}'::jsonb) \
             RETURNING audit_id",
            &[&key],
        )
        .await
        .unwrap()
        .get(0);
    let auditor = keys.token("AUDITOR", sid);
    let list = format!("/v1/audit-entries?entity=app_user&entity_pk={key}");
    let single = format!("/v1/audit-entries/{id}");
    for path in [list.as_str(), single.as_str()] {
        let (status, json_body) = send(app, "GET", path, Some(&auditor), "application/json").await;
        assert_eq!(status, 200, "{json_body}");
        assert!(!json_body.contains("password_hash") && !json_body.contains("argon2id"));
        let parsed: serde_json::Value = serde_json::from_str(&json_body).unwrap();
        let entry = if path == list {
            &parsed["audit_entries"][0]
        } else {
            &parsed
        };
        assert_eq!(entry["data_before"]["name"], "A", "{json_body}");
        assert!(entry["data_after"]["nested"].is_object());
        assert!(entry["user_id"].is_null());

        let (status, xml) = send(app, "GET", path, Some(&auditor), "application/xml").await;
        assert_eq!(status, 200, "{xml}");
        assert!(
            !xml.contains("password_hash") && !xml.contains("argon2id"),
            "{xml}"
        );
        assert!(xml.contains("<data_after>{"), "{xml}");
        assert!(!xml.contains("<user_id"), "{xml}");
    }

    // A single object and a SQL NULL retain their JSON shapes through the router.
    owner
        .execute(
            "UPDATE public.audit_log SET data_before = $1::text::jsonb, data_after = NULL \
             WHERE audit_id = $2",
            &[&r#"{"a":1}"#, &id],
        )
        .await
        .unwrap();
    let (status, body) = send(app, "GET", &single, Some(&auditor), "application/json").await;
    assert_eq!(status, 200, "{body}");
    let entry: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(entry["data_before"], json!({"a": 1}));
    assert!(entry["data_after"].is_null());

    // The two error answers of the contract.
    let (status, body) = send(
        app,
        "GET",
        "/v1/audit-entries/999999999",
        Some(&auditor),
        "application/json",
    )
    .await;
    assert_eq!(status, 404);
    assert!(body.contains("audit_entry_not_found"), "{body}");
    let (status, body) = send(
        app,
        "GET",
        "/v1/audit-entries?entity=audit_log",
        Some(&auditor),
        "application/json",
    )
    .await;
    assert_eq!(status, 422);
    assert!(
        body.contains("invalid_query") && body.contains("\"field\":\"entity\""),
        "{body}"
    );

    remove_test_rows(&owner, &key).await;
    stack.finish().await;
}
