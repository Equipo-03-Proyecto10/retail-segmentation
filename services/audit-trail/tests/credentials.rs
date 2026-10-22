//! RN-29 as the service holds it: no entry carries `password_hash`, at any
//! depth, in either format.

use audit_trail::{clean_payload, expand_payloads, strip_credentials, AuditEntry};
use chrono::{TimeZone, Utc};
use mosaiq_kit::rendering::{render_json, render_xml};
use mosaiq_kit::{Page, Reply};
use serde_json::json;

const NAMESPACE: &str = "https://mosaiq.maxthecoder.online/xml/audit-trail/v1";

fn entry_with_credentials() -> AuditEntry {
    let payload = json!({
        "user_id": "4f6c1d52-8a3e-4b17-9c20-5d1e7a9b3c64",
        "password_hash": "$argon2id$top-level",
        "nested": {"password_hash": "$argon2id$nested", "kept": 1,
                   "deeper": [{"password_hash": "$argon2id$in-array", "ok": true}]},
        "name": "Sam"
    })
    .to_string();
    AuditEntry {
        audit_id: 7,
        user_id: None,
        entity: "app_user".to_owned(),
        entity_pk: "4f6c1d52-8a3e-4b17-9c20-5d1e7a9b3c64".to_owned(),
        action: "INSERT".to_owned(),
        data_before: None,
        data_after: clean_payload(Some(payload)).unwrap(),
        executed_at: Utc.with_ymd_and_hms(2026, 10, 16, 14, 36, 5).unwrap(),
    }
}

fn page_of(entry: &AuditEntry) -> Reply {
    Page::new(
        "AuditEntries",
        "AuditEntry",
        "audit_entries",
        vec![entry.wire()],
        1,
        1,
    )
    .size(20)
    .into()
}

#[test]
fn a_credential_is_stripped_at_the_top_and_nested() {
    let mut value = json!({"password_hash": "x", "a": {"PASSWORD_HASH": "y", "b": [{"password_hash": "z"}]}, "keep": 1});
    strip_credentials(&mut value);
    assert_eq!(value, json!({"a": {"b": [{}]}, "keep": 1}));
}

#[test]
fn a_null_payload_stays_null() {
    assert_eq!(clean_payload(None).unwrap(), None);
}

#[test]
fn neither_representation_carries_the_key() {
    let reply = page_of(&entry_with_credentials());
    let xml = render_xml(&reply, NAMESPACE, 20).unwrap();
    assert!(!xml.contains("password_hash"), "{xml}");
    assert!(!xml.contains("argon2id"), "{xml}");
    let json = render_json(&reply, 20).unwrap().to_string();
    assert!(!json.contains("password_hash"), "{json}");
    assert!(!json.contains("argon2id"), "{json}");
}

#[test]
fn xml_holds_the_payload_as_json_text_and_omits_what_is_null() {
    let xml = render_xml(&page_of(&entry_with_credentials()), NAMESPACE, 20).unwrap();
    assert!(xml.contains("<data_after>{"), "{xml}");
    assert!(
        xml.contains("&quot;nested&quot;") || xml.contains("\"nested\""),
        "{xml}"
    );
    assert!(!xml.contains("<data_before"), "{xml}");
    assert!(!xml.contains("<user_id"), "{xml}");
}

#[test]
fn json_holds_the_payload_as_an_object_and_null_as_null() {
    let rendered = render_json(&page_of(&entry_with_credentials()), 20).unwrap();
    let body = serde_json::to_vec(&rendered).unwrap();
    let rewritten: serde_json::Value =
        serde_json::from_slice(&expand_payloads(&body).unwrap()).unwrap();
    let entry = &rewritten["audit_entries"][0];
    assert!(entry["data_before"].is_null());
    assert!(entry["user_id"].is_null());
    assert_eq!(entry["data_after"]["nested"]["kept"], 1);
    assert_eq!(entry["data_after"]["name"], "Sam");
    assert!(entry["data_after"].get("password_hash").is_none());
    assert_eq!(rewritten["page_info"]["total"], 1);
}

#[test]
fn a_single_entry_is_expanded_too() {
    let body = serde_json::to_vec(&json!({
        "audit_id": 1, "data_before": "{\"a\":1}", "data_after": null
    }))
    .unwrap();
    let rewritten: serde_json::Value =
        serde_json::from_slice(&expand_payloads(&body).unwrap()).unwrap();
    assert_eq!(rewritten["data_before"], json!({"a": 1}));
    assert!(rewritten["data_after"].is_null());
}
