//! Every audited table's entries are listed and read, and each answer fits the contract (#1023).
//!
//! The database is the one loaded from empty by the three scripts. The test writes to each audited
//! table as its owner (an update that changes no value still fires `fn_audit`), then pages through the
//! whole trail and reads entries of every entity through the router, in JSON and in XML. The XML is
//! validated against `audit-trail.xsd`; the JSON entries are checked against the members, types and
//! enumerations of `AuditEntry` and `AuditedEntity` in the staged OpenAPI document.

mod support;

use std::collections::BTreeSet;

use audit_trail::{ACTIONS, ENTITIES};
use serde_json::Value;
use support::connect;
use support::http::{assert_valid_xml, send, Stack, TURN};

const AUDITED: &str = "SELECT n.nspname, c.relname \
     FROM pg_trigger t \
     JOIN pg_proc p ON p.oid = t.tgfoid \
     JOIN pg_class c ON c.oid = t.tgrelid \
     JOIN pg_namespace n ON n.oid = c.relnamespace \
     WHERE p.proname = 'fn_audit' AND NOT t.tgisinternal \
     ORDER BY 1, 2";

/// The tables the issue names: the four product tables and the users and stores tables.
const MUST_BE_WRITTEN: [&str; 9] = [
    "product_attribute",
    "product_barcode",
    "product_relation",
    "product_status",
    "store_assignment",
    "store_location",
    "store_status",
    "store_hours",
    "store_channel",
];

/// The names under `AuditedEntity: ... enum:` in the staged OpenAPI document.
fn contract_entities() -> BTreeSet<String> {
    let text = std::fs::read_to_string("contracts/audit-trail/openapi.yaml").unwrap();
    let mut names = BTreeSet::new();
    let mut in_schema = false;
    let mut in_enum = false;
    for line in text.lines() {
        if line == "    AuditedEntity:" {
            in_schema = true;
        } else if in_schema && line.trim() == "enum:" {
            in_enum = true;
        } else if in_enum {
            match line.trim().strip_prefix("- ") {
                Some(name) => {
                    names.insert(name.to_owned());
                }
                None => break,
            }
        }
    }
    names
}

fn assert_entry_fits(entry: &Value, entities: &BTreeSet<String>) {
    let object = entry.as_object().expect("an entry is an object");
    for member in ["audit_id", "entity", "entity_pk", "action", "executed_at"] {
        assert!(object.contains_key(member), "{member} is missing: {entry}");
    }
    assert!(object["audit_id"].as_i64().unwrap() >= 1, "{entry}");
    let entity = object["entity"].as_str().expect("entity is a string");
    assert!(
        entities.contains(entity),
        "entity {entity} is not in the contract's AuditedEntity: {entry}"
    );
    let entity_pk = object["entity_pk"].as_str().expect("entity_pk is a string");
    assert!((1..=80).contains(&entity_pk.chars().count()), "{entry}");
    assert!(
        ACTIONS.contains(&object["action"].as_str().unwrap()),
        "{entry}"
    );
    assert!(
        object["executed_at"].as_str().unwrap().contains('T'),
        "{entry}"
    );
    for payload in ["data_before", "data_after"] {
        if let Some(value) = object.get(payload) {
            assert!(value.is_object() || value.is_null(), "{payload}: {entry}");
        }
    }
    if let Some(user) = object.get("user_id") {
        assert!(user.is_null() || user.is_string(), "{entry}");
    }
    let allowed = [
        "audit_id",
        "user_id",
        "entity",
        "entity_pk",
        "action",
        "data_before",
        "data_after",
        "executed_at",
    ];
    for key in object.keys() {
        assert!(
            allowed.contains(&key.as_str()),
            "unexpected member {key}: {entry}"
        );
    }
}

#[tokio::test]
async fn every_audited_table_is_listed_and_read_in_json_and_xml() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;

    // The tables that carry the trigger, from the database itself.
    let audited: Vec<(String, String)> = owner
        .query(AUDITED, &[])
        .await
        .unwrap()
        .iter()
        .map(|row| (row.get(0), row.get(1)))
        .collect();
    let names: BTreeSet<String> = audited.iter().map(|(_, name)| name.clone()).collect();
    assert_eq!(
        names.len(),
        audited.len(),
        "an audited name is used twice: {audited:?}"
    );
    let contract = contract_entities();
    assert_eq!(names, contract, "the contract names the audited tables");
    assert_eq!(
        names,
        ENTITIES.iter().map(|name| (*name).to_owned()).collect(),
        "ENTITIES names the audited tables"
    );

    // A write by each table's owner: an update that changes nothing still fires the trigger.
    let before: i64 = owner
        .query_one(
            "SELECT coalesce(max(audit_id), 0) FROM public.audit_log",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    let mut written = BTreeSet::new();
    for (schema, table) in &audited {
        let column: String = owner
            .query_one(
                "SELECT a.attname::text FROM pg_attribute a \
                 WHERE a.attrelid = format('%I.%I', $1::text, $2::text)::regclass \
                   AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum LIMIT 1",
                &[schema, table],
            )
            .await
            .unwrap()
            .get(0);
        let statement = format!(
            "UPDATE {schema}.{table} SET {column} = {column} \
             WHERE ctid = (SELECT ctid FROM {schema}.{table} LIMIT 1)"
        );
        if let Ok(1) = owner.execute(statement.as_str(), &[]).await {
            written.insert(table.clone());
        }
    }
    for table in MUST_BE_WRITTEN {
        assert!(
            written.contains(table),
            "no write could be made to {table}: {written:?}"
        );
    }

    let token = stack.token("AUDITOR");
    let app = &stack.app;

    // The whole trail, page by page, in both representations.
    let mut seen = BTreeSet::new();
    let mut page = 1;
    loop {
        let path = format!("/v1/audit-entries?size=100&page={page}");
        let (status, body) = send(app, "GET", &path, Some(&token), "application/json").await;
        assert_eq!(status, 200, "{path}: {body}");
        let parsed: Value = serde_json::from_str(&body).unwrap();
        let entries = parsed["audit_entries"].as_array().unwrap();
        for entry in entries {
            assert_entry_fits(entry, &contract);
            seen.insert(entry["entity"].as_str().unwrap().to_owned());
        }
        let (status, xml) = send(app, "GET", &path, Some(&token), "application/xml").await;
        assert_eq!(status, 200, "{path}: {xml}");
        assert_valid_xml(&xml);
        let total = parsed["page_info"]["total"].as_i64().unwrap();
        if i64::from(page) * 100 >= total || entries.is_empty() {
            break;
        }
        page += 1;
    }
    assert_eq!(
        seen, names,
        "the trail holds an entry for every audited table"
    );

    // Each entity filter answers, and an entry of it reads on its own.
    for entity in &names {
        let path = format!("/v1/audit-entries?entity={entity}&size=1");
        let (status, body) = send(app, "GET", &path, Some(&token), "application/json").await;
        assert_eq!(status, 200, "{path}: {body}");
        let parsed: Value = serde_json::from_str(&body).unwrap();
        let entry = &parsed["audit_entries"][0];
        assert_entry_fits(entry, &contract);
        assert_eq!(entry["entity"], entity.as_str());
        let (status, xml) = send(app, "GET", &path, Some(&token), "application/xml").await;
        assert_eq!(status, 200, "{path}: {xml}");
        assert_valid_xml(&xml);

        let one = format!("/v1/audit-entries/{}", entry["audit_id"]);
        let (status, body) = send(app, "GET", &one, Some(&token), "application/json").await;
        assert_eq!(status, 200, "{one}: {body}");
        assert_entry_fits(&serde_json::from_str(&body).unwrap(), &contract);
        let (status, xml) = send(app, "GET", &one, Some(&token), "application/xml").await;
        assert_eq!(status, 200, "{one}: {xml}");
        assert_valid_xml(&xml);
    }

    // What the test wrote is removed, so the trail is as the seed left it.
    owner
        .execute(
            "DELETE FROM public.audit_log WHERE audit_id > $1",
            &[&before],
        )
        .await
        .unwrap();
    stack.finish().await;
}
