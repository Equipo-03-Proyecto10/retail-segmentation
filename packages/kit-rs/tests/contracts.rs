//! Contract loading and body handling, as kit-py's test_contract_compatibility.py
//! and test_contract_and_auth.py.

mod common;

use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use common::*;
use mosaiq_kit::{ApiError, Contract, Ctx, Kit, KitConfig, NoContent, Reply};
use serde_json::{json, Value as JsonValue};

const PROBE_NAMESPACE: &str = "https://mosaiq.maxthecoder.online/xml/kit-probe/v1";

/// A configuration that needs no running service: nothing here verifies a token.
fn offline_config() -> KitConfig {
    KitConfig {
        issuer: ISSUER.to_owned(),
        jwks_path: vectors().join("jwks.json"),
        redis_state_url: "redis://127.0.0.1:1/0".to_owned(),
        redis_password: None,
        rate_limit_enabled: false,
        rate_limit_requests: 120,
        rate_limit_window_seconds: 60,
        database_url: None,
        database_pool_timeout: Duration::from_millis(300),
        database_connect_timeout: Duration::from_secs(1),
    }
}

fn stage_probe(root: &Path) {
    copy_tree(
        &repository().join("contracts/common/v1"),
        &root.join("common/v1"),
    );
    let service = root.join("kit-probe");
    std::fs::create_dir_all(&service).unwrap();
    let document = json!({
        "openapi": "3.1.0",
        "info": {"title": "kit-probe", "version": "1.0.0"},
        "security": [{"bearer": []}],
        "paths": {
            "/v1/facets": {"post": {
                "operationId": "checkFacets",
                "security": [],
                "requestBody": {"content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/FacetPayload"}},
                    "application/xml": {"schema": {"$ref": "#/components/schemas/FacetPayload"}},
                }},
                "responses": {"204": {"description": "Valid."}},
            }},
            "/v1/codes/{code}": {"get": {
                "operationId": "getCode",
                "security": [],
                "parameters": [{
                    "name": "code", "in": "path", "required": true,
                    "schema": {"type": "string", "pattern": r"^[A-Z]\d{2}(-[a-z]+)?$"},
                }],
                "responses": {"204": {"description": "Matched."}},
            }},
            "/v1/unprotected-policy": {"get": {
                "operationId": "missingRoles",
                "responses": {"204": {"description": "Invalid contract."}},
            }},
            "/v1/missing-xml": {"post": {
                "operationId": "missingXml",
                "security": [],
                "requestBody": {"content": {
                    "application/json": {"schema": {"type": "object", "properties": {}}},
                }},
                "responses": {"204": {"description": "Invalid contract."}},
            }},
        },
        "components": {"schemas": {"FacetPayload": {
            "type": "object",
            "additionalProperties": false,
            "required": ["title", "code", "level", "color", "price"],
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 5},
                "code": {"type": "string", "pattern": "^[A-Z]{2}$"},
                "level": {"type": "integer", "minimum": 1, "maximum": 5},
                "color": {"type": "string", "enum": ["red", "green"]},
                "price": {"type": "number"},
            },
            "xml": {"name": "FacetRequest"},
        }}},
    });
    std::fs::write(
        service.join("openapi.yaml"),
        serde_yaml::to_string(&document).unwrap(),
    )
    .unwrap();
    std::fs::write(
        service.join("kit-probe.xsd"),
        format!(
            r#"<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns:probe="{PROBE_NAMESPACE}"
           targetNamespace="{PROBE_NAMESPACE}"
           elementFormDefault="qualified">
  <xs:simpleType name="Title">
    <xs:restriction base="xs:string">
      <xs:minLength value="1"/>
      <xs:maxLength value="5"/>
    </xs:restriction>
  </xs:simpleType>
  <xs:simpleType name="Code">
    <xs:restriction base="xs:string">
      <xs:pattern value="[A-Z]{{2}}"/>
    </xs:restriction>
  </xs:simpleType>
  <xs:simpleType name="Level">
    <xs:restriction base="xs:integer">
      <xs:minInclusive value="1"/>
      <xs:maxInclusive value="5"/>
    </xs:restriction>
  </xs:simpleType>
  <xs:simpleType name="Color">
    <xs:restriction base="xs:string">
      <xs:enumeration value="red"/>
      <xs:enumeration value="green"/>
    </xs:restriction>
  </xs:simpleType>
  <xs:complexType name="FacetRequestType">
    <xs:sequence>
      <xs:element name="title" type="probe:Title"/>
      <xs:element name="code" type="probe:Code"/>
      <xs:element name="level" type="probe:Level"/>
      <xs:element name="color" type="probe:Color"/>
      <xs:element name="price" type="xs:decimal"/>
    </xs:sequence>
  </xs:complexType>
  <xs:element name="FacetRequest" type="probe:FacetRequestType"/>
</xs:schema>
"#
        ),
    )
    .unwrap();
}

fn probe_xml(changes: &[(&str, &str)]) -> String {
    let mut values = [
        ("title", "Valid"),
        ("code", "AB"),
        ("level", "3"),
        ("color", "red"),
        ("price", "0.1"),
    ];
    for (name, value) in changes {
        values.iter_mut().find(|(key, _)| key == name).unwrap().1 = value;
    }
    let children: String = values
        .iter()
        .map(|(name, value)| format!("<{name}>{value}</{name}>"))
        .collect();
    format!(r#"<FacetRequest xmlns="{PROBE_NAMESPACE}">{children}</FacetRequest>"#)
}

type Seen = Arc<Mutex<Vec<JsonValue>>>;

fn probe_app(root: &Path, seen: Seen) -> axum::Router {
    Kit::new("kit-probe", &suffix(), root, offline_config())
        .unwrap()
        .operation("checkFacets", move |ctx: Ctx| {
            let seen = Arc::clone(&seen);
            async move {
                seen.lock()
                    .unwrap()
                    .push(ctx.body.unwrap()["price"].clone());
                Ok::<Reply, ApiError>(NoContent::default().into())
            }
        })
        .unwrap()
        .operation("getCode", |ctx: Ctx| async move {
            assert!(ctx.path_text("code").is_some());
            Ok::<Reply, ApiError>(NoContent::default().into())
        })
        .unwrap()
        .into_router()
}

#[tokio::test]
async fn xsd_errors_use_stable_details() {
    let root = TempDir::new();
    stage_probe(root.path());
    let app = probe_app(root.path(), Seen::default());
    let cases = [
        (
            format!(r#"<FacetRequest xmlns="{PROBE_NAMESPACE}"/>"#),
            "title",
            "required",
        ),
        (
            format!(
                r#"<FacetRequest xmlns="{PROBE_NAMESPACE}"><title>Valid</title><extra>x</extra><code>AB</code><level>3</level><color>red</color><price>0.1</price></FacetRequest>"#
            ),
            "extra",
            "unexpected_field",
        ),
        (probe_xml(&[("code", "bad")]), "code", "invalid_format"),
        (probe_xml(&[("title", "")]), "title", "too_short"),
        (probe_xml(&[("title", "TooLong")]), "title", "too_long"),
        (probe_xml(&[("level", "0")]), "level", "out_of_range"),
        (probe_xml(&[("color", "blue")]), "color", "not_allowed"),
        (probe_xml(&[("level", "no")]), "level", "invalid_type"),
    ];
    for (body, field, code) in cases {
        let answer = call("POST", "/v1/facets")
            .body("application/xml", body.clone())
            .send(&app)
            .await;
        assert_eq!(answer.status, 422, "{body}");
        assert!(
            answer.json()["details"]
                .as_array()
                .unwrap()
                .contains(&json!({"field": field, "code": code})),
            "{body}: {}",
            answer.text()
        );
    }
}

#[tokio::test]
async fn json_and_xml_numbers_are_parsed_exactly() {
    let root = TempDir::new();
    stage_probe(root.path());
    let seen = Seen::default();
    let app = probe_app(root.path(), Arc::clone(&seen));
    let json_answer = call("POST", "/v1/facets")
        .body(
            "application/json",
            r#"{"title":"Valid","code":"AB","level":3,"color":"red","price":0.10000000000000000001}"#,
        )
        .send(&app)
        .await;
    let xml_answer = call("POST", "/v1/facets")
        .body(
            "application/xml",
            probe_xml(&[("price", "0.10000000000000000001")]),
        )
        .send(&app)
        .await;
    assert_eq!(json_answer.status, 204, "{}", json_answer.text());
    assert_eq!(xml_answer.status, 204, "{}", xml_answer.text());
    let seen: Vec<String> = seen
        .lock()
        .unwrap()
        .iter()
        .map(ToString::to_string)
        .collect();
    assert_eq!(seen, ["0.10000000000000000001", "0.10000000000000000001"]);
}

#[tokio::test]
async fn complex_path_pattern_matches_the_whole_segment() {
    let root = TempDir::new();
    stage_probe(root.path());
    let app = probe_app(root.path(), Seen::default());
    for (path, status) in [
        ("/v1/codes/A12", 204),
        ("/v1/codes/A12-lower", 204),
        ("/v1/codes/a12", 404),
        ("/v1/codes/A12-", 404),
    ] {
        assert_eq!(call("GET", path).send(&app).await.status, status, "{path}");
    }
}

#[test]
fn registration_rejects_missing_policy_and_xml_metadata() {
    let root = TempDir::new();
    stage_probe(root.path());
    let error = Kit::new("kit-probe", &suffix(), root.path(), offline_config())
        .unwrap()
        .operation("missingRoles", |_ctx: Ctx| async {
            Ok::<Reply, ApiError>(NoContent::default().into())
        })
        .err()
        .unwrap();
    assert!(error.0.contains("x-mosaiq-roles"), "{error}");
    let error = Kit::new("kit-probe", &suffix(), root.path(), offline_config())
        .unwrap()
        .operation("missingXml", |_ctx: Ctx| async {
            Ok::<Reply, ApiError>(NoContent::default().into())
        })
        .err()
        .unwrap();
    assert!(error.0.contains("without an XML root"), "{error}");
    assert!(
        Kit::new("kit-probe", &suffix(), root.path(), offline_config())
            .unwrap()
            .operation("noSuchOperation", |_ctx: Ctx| async {
                Ok::<Reply, ApiError>(NoContent::default().into())
            })
            .is_err()
    );
}

#[tokio::test]
async fn auth_request_components_supply_default_xml_roots() {
    let staged = TempDir::new();
    copy_tree(
        &repository().join("contracts/common/v1"),
        &staged.path().join("common/v1"),
    );
    let auth = repository().join("contracts/auth");
    copy_tree(&auth, &staged.path().join("auth"));
    let reached: Arc<Mutex<Vec<&'static str>>> = Arc::default();
    let create = Arc::clone(&reached);
    let refresh = Arc::clone(&reached);
    let kit = Kit::new("auth", &suffix(), staged.path(), offline_config()).unwrap();
    for (operation, name) in [
        ("createSession", "SignInRequest"),
        ("refreshSession", "RefreshRequest"),
    ] {
        let operation = kit.contract().operation(operation).unwrap();
        assert_eq!(operation.xml_name.as_deref(), Some(name));
        assert_eq!(
            operation.xml_namespace.as_deref(),
            Some("https://mosaiq.maxthecoder.online/xml/auth/v1")
        );
    }
    let app = kit
        .operation("createSession", move |_ctx: Ctx| {
            let reached = Arc::clone(&create);
            async move {
                reached.lock().unwrap().push("create");
                Ok::<Reply, ApiError>(NoContent::default().into())
            }
        })
        .unwrap()
        .operation("refreshSession", move |_ctx: Ctx| {
            let reached = Arc::clone(&refresh);
            async move {
                reached.lock().unwrap().push("refresh");
                Ok::<Reply, ApiError>(NoContent::default().into())
            }
        })
        .unwrap()
        .into_router();
    let examples = auth.join("examples");
    for (path, file, media) in [
        ("/v1/sessions", "sign-in.request.json", "application/json"),
        ("/v1/sessions", "sign-in.request.xml", "application/xml"),
        (
            "/v1/sessions/refresh",
            "refresh.request.json",
            "application/json",
        ),
        (
            "/v1/sessions/refresh",
            "refresh.request.xml",
            "application/xml",
        ),
    ] {
        let answer = call("POST", path)
            .body(media, std::fs::read(examples.join(file)).unwrap())
            .send(&app)
            .await;
        assert_eq!(answer.status, 204, "{file}: {}", answer.text());
    }
    assert_eq!(
        *reached.lock().unwrap(),
        ["create", "create", "refresh", "refresh"]
    );
    let wrong_root = call("POST", "/v1/sessions")
        .body(
            "application/xml",
            std::fs::read(examples.join("refresh.request.xml")).unwrap(),
        )
        .send(&app)
        .await;
    assert_eq!(wrong_root.status, 422);
    assert_eq!(wrong_root.code(), "invalid_body");
    assert_eq!(reached.lock().unwrap().len(), 4);
}

#[test]
fn kit_loads_every_merged_service_contract() {
    let staged = TempDir::new();
    copy_tree(
        &repository().join("contracts/common/v1"),
        &staged.path().join("common/v1"),
    );
    let mut services: Vec<_> = std::fs::read_dir(repository().join("contracts"))
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .filter(|path| path.join("openapi.yaml").is_file())
        .collect();
    services.sort();
    // Every registered service publishes a contract, so this tracks the registry
    // rather than a count that each newly merged contract would invalidate.
    let registry: serde_yaml::Value = serde_yaml::from_str(
        &std::fs::read_to_string(repository().join("contracts/registry.yaml")).unwrap(),
    )
    .unwrap();
    let mut registered: Vec<_> = registry["services"]
        .as_sequence()
        .unwrap()
        .iter()
        .map(|service| service["name"].as_str().unwrap().to_owned())
        .collect();
    registered.sort();
    let published: Vec<_> = services
        .iter()
        .map(|path| path.file_name().unwrap().to_str().unwrap().to_owned())
        .collect();
    assert_eq!(published, registered);
    let mut incompatibilities = Vec::new();
    for source in services {
        let name = source.file_name().unwrap().to_str().unwrap().to_owned();
        copy_tree(&source, &staged.path().join(&name));
        let kit = Kit::new(&name, &suffix(), staged.path(), offline_config())
            .unwrap_or_else(|error| panic!("{name}: {error}"));
        let ids: Vec<String> = kit.contract().operations.keys().cloned().collect();
        let mut kit = Some(kit);
        for id in ids {
            let operation = kit
                .as_ref()
                .unwrap()
                .contract()
                .operation(&id)
                .unwrap()
                .clone();
            if operation.path.starts_with("/health/")
                || matches!(operation.path.as_str(), "/openapi.yaml" | "/schema.xsd")
                || operation.path.starts_with("/schema.v")
            {
                continue;
            }
            match kit.take().unwrap().operation(&id, |_ctx: Ctx| async {
                Ok::<Reply, ApiError>(NoContent::default().into())
            }) {
                Ok(next) => kit = Some(next),
                Err(error) => {
                    incompatibilities.push(format!("{name}:{id}: {error}"));
                    kit =
                        Some(Kit::new(&name, &suffix(), staged.path(), offline_config()).unwrap());
                }
            }
        }
        let _ = kit.unwrap().into_router();
    }
    assert!(
        incompatibilities.is_empty(),
        "{}",
        incompatibilities.join("\n")
    );
}

#[test]
fn polymorphic_request_uses_the_shared_xml_root_and_typed_values() {
    let contract = Contract::load(repository().join("contracts"), "engagement").unwrap();
    let operation = contract.operation("createEngagementEvent").unwrap();
    assert_eq!(
        operation.xml_name.as_deref(),
        Some("EngagementEventRequest")
    );
    let body = contract
        .parse_xml(
            &std::fs::read(repository().join("contracts/engagement/examples/qr-scan.request.xml"))
                .unwrap(),
            operation,
        )
        .unwrap();
    contract.validate(&body, operation).unwrap();
    assert!(body["store_id"].is_i64());
    assert!(body["product_id"].is_i64());
}

#[test]
fn crate_version_matches_the_documented_release() {
    let manifest = std::fs::read_to_string(kit_root().join("Cargo.toml")).unwrap();
    let readme = std::fs::read_to_string(kit_root().join("README.md")).unwrap();
    let changelog = std::fs::read_to_string(kit_root().join("CHANGELOG.md")).unwrap();
    let version = env!("CARGO_PKG_VERSION");
    assert!(manifest.contains("name = \"mosaiq-kit\""));
    assert!(!manifest.contains("[workspace]"));
    assert!(readme.contains(&format!("tag = \"kit-rs/v{version}\"")));
    assert!(changelog.contains(&format!("## {version} ")));
}

#[test]
fn verification_is_pinned_to_rs256_and_never_signs() {
    for entry in std::fs::read_dir(kit_root().join("src")).unwrap() {
        let source = std::fs::read_to_string(entry.unwrap().path()).unwrap();
        let constructor = ["Validation", "::new("].concat();
        for line in source.lines().filter(|line| line.contains(&constructor)) {
            assert!(line.contains("Algorithm::RS256"), "{line}");
        }
        assert!(!source.contains(&["EncodingKey", "::from"].concat()));
    }
}
