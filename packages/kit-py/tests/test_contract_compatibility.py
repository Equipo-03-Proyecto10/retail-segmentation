from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from conftest import COMMON_CONTRACT, REPOSITORY, TEST_SUFFIX
from flask import Flask

from mosaiq_kit import Kit, NoContent

PROBE_NAMESPACE = "https://mosaiq.maxthecoder.online/xml/kit-probe/v1"


def _stage_probe(tmp_path: Path) -> Path:
    contracts = tmp_path / "contracts"
    service = contracts / "kit-probe"
    service.mkdir(parents=True)
    shutil.copytree(COMMON_CONTRACT, contracts / "common/v1")
    document = {
        "openapi": "3.1.0",
        "info": {"title": "kit-probe", "version": "1.0.0"},
        "security": [{"bearer": []}],
        "paths": {
            "/v1/facets": {
                "post": {
                    "operationId": "checkFacets",
                    "security": [],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/FacetPayload"}
                            },
                            "application/xml": {
                                "schema": {"$ref": "#/components/schemas/FacetPayload"}
                            },
                        }
                    },
                    "responses": {"204": {"description": "Valid."}},
                }
            },
            "/v1/codes/{code}": {
                "get": {
                    "operationId": "getCode",
                    "security": [],
                    "parameters": [
                        {
                            "name": "code",
                            "in": "path",
                            "required": True,
                            "schema": {
                                "type": "string",
                                "pattern": r"^[A-Z]\d{2}(-[a-z]+)?$",
                            },
                        }
                    ],
                    "responses": {"204": {"description": "Matched."}},
                }
            },
            "/v1/unprotected-policy": {
                "get": {
                    "operationId": "missingRoles",
                    "responses": {"204": {"description": "Invalid contract."}},
                }
            },
            "/v1/missing-xml": {
                "post": {
                    "operationId": "missingXml",
                    "security": [],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {}}
                            }
                        }
                    },
                    "responses": {"204": {"description": "Invalid contract."}},
                }
            },
        },
        "components": {
            "schemas": {
                "FacetPayload": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "code", "level", "color", "price"],
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 5},
                        "code": {"type": "string", "pattern": "^[A-Z]{2}$"},
                        "level": {"type": "integer", "minimum": 1, "maximum": 5},
                        "color": {"type": "string", "enum": ["red", "green"]},
                        "price": {"type": "number"},
                    },
                    "xml": {"name": "FacetRequest"},
                }
            }
        },
    }
    (service / "openapi.yaml").write_text(yaml.safe_dump(document, sort_keys=False))
    (service / "kit-probe.xsd").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
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
""")
    return contracts


@pytest.fixture
def probe_kit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Flask, Kit, list[Decimal]]:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    app = Flask("contract-probe")
    app.testing = True
    kit = Kit(
        app,
        service="kit-probe",
        suffix=TEST_SUFFIX,
        contracts_dir=_stage_probe(tmp_path),
    )
    seen: list[Decimal] = []

    @kit.operation("checkFacets")
    def check_facets() -> NoContent:
        seen.append(kit.body["price"])
        return NoContent()

    yield app, kit, seen
    kit.db.close()


def _xml(**changes: str) -> bytes:
    values = {
        "title": "Valid",
        "code": "AB",
        "level": "3",
        "color": "red",
        "price": "0.1",
    }
    values.update(changes)
    children = "".join(f"<{name}>{value}</{name}>" for name, value in values.items())
    return f'<FacetRequest xmlns="{PROBE_NAMESPACE}">{children}</FacetRequest>'.encode()


@pytest.mark.parametrize(
    "body,field,code",
    [
        (f'<FacetRequest xmlns="{PROBE_NAMESPACE}"/>'.encode(), "title", "required"),
        (
            (
                f'<FacetRequest xmlns="{PROBE_NAMESPACE}"><title>Valid</title>'
                "<extra>x</extra><code>AB</code><level>3</level><color>red</color>"
                "<price>0.1</price></FacetRequest>"
            ).encode(),
            "extra",
            "unexpected_field",
        ),
        (_xml(code="bad"), "code", "invalid_format"),
        (_xml(title=""), "title", "too_short"),
        (_xml(title="TooLong"), "title", "too_long"),
        (_xml(level="0"), "level", "out_of_range"),
        (_xml(color="blue"), "color", "not_allowed"),
        (_xml(level="no"), "level", "invalid_type"),
    ],
)
def test_xsd_errors_use_stable_details(
    probe_kit: tuple[Flask, Kit, list[Decimal]],
    body: bytes,
    field: str,
    code: str,
) -> None:
    app, _kit, _seen = probe_kit
    response = app.test_client().post(
        "/v1/facets", data=body, content_type="application/xml"
    )
    assert response.status_code == 422
    assert {"field": field, "code": code} in response.json["details"]


def test_json_and_xml_numbers_are_parsed_exactly(
    probe_kit: tuple[Flask, Kit, list[Decimal]],
) -> None:
    app, _kit, seen = probe_kit
    json_response = app.test_client().post(
        "/v1/facets",
        data=(
            b'{"title":"Valid","code":"AB","level":3,"color":"red",'
            b'"price":0.10000000000000000001}'
        ),
        content_type="application/json",
    )
    xml_response = app.test_client().post(
        "/v1/facets",
        data=_xml(price="0.10000000000000000001"),
        content_type="application/xml",
    )
    assert json_response.status_code == xml_response.status_code == 204
    assert seen == [
        Decimal("0.10000000000000000001"),
        Decimal("0.10000000000000000001"),
    ]


def test_complex_path_pattern_uses_a_generated_converter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    app = Flask("pattern-probe")
    kit = Kit(
        app,
        service="kit-probe",
        suffix=TEST_SUFFIX,
        contracts_dir=_stage_probe(tmp_path),
    )

    @kit.operation("getCode")
    def get_code(code: str) -> NoContent:
        assert code
        return NoContent()

    client = app.test_client()
    assert client.get("/v1/codes/A12").status_code == 204
    assert client.get("/v1/codes/A12-lower").status_code == 204
    assert client.get("/v1/codes/a12").status_code == 404
    assert client.get("/v1/codes/A12-").status_code == 404
    kit.db.close()


def test_registration_rejects_missing_policy_and_xml_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    app = Flask("registration-probe")
    kit = Kit(
        app,
        service="kit-probe",
        suffix=TEST_SUFFIX,
        contracts_dir=_stage_probe(tmp_path),
    )
    with pytest.raises(ValueError, match="x-mosaiq-roles"):
        kit.operation("missingRoles")
    with pytest.raises(ValueError, match="without an XML root"):
        kit.operation("missingXml")
    kit.db.close()


def test_auth_request_components_supply_default_xml_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    staged = tmp_path / "auth-contracts"
    shutil.copytree(COMMON_CONTRACT, staged / "common/v1")
    auth_contract = REPOSITORY / "contracts/auth"
    shutil.copytree(auth_contract, staged / "auth")
    app = Flask("auth-contract-defaults")
    kit = Kit(
        app,
        service="auth",
        suffix=TEST_SUFFIX,
        contracts_dir=staged,
    )
    reached = []

    @kit.operation("createSession")
    def create_session() -> NoContent:
        reached.append(("create", kit.body))
        return NoContent()

    @kit.operation("refreshSession")
    def refresh_session() -> NoContent:
        reached.append(("refresh", kit.body))
        return NoContent()

    client = app.test_client()
    examples = auth_contract / "examples"
    requests = [
        ("/v1/sessions", "sign-in.request.json", "application/json"),
        ("/v1/sessions", "sign-in.request.xml", "application/xml"),
        ("/v1/sessions/refresh", "refresh.request.json", "application/json"),
        ("/v1/sessions/refresh", "refresh.request.xml", "application/xml"),
    ]
    responses = [
        client.post(path, data=(examples / filename).read_bytes(), content_type=media)
        for path, filename, media in requests
    ]
    assert [response.status_code for response in responses] == [204, 204, 204, 204]
    assert [name for name, _body in reached] == [
        "create",
        "create",
        "refresh",
        "refresh",
    ]
    assert kit.contract.operation("createSession").xml_name == "SignInRequest"
    assert kit.contract.operation("refreshSession").xml_name == "RefreshRequest"
    assert all(
        operation.xml_namespace == "https://mosaiq.maxthecoder.online/xml/auth/v1"
        for operation in (
            kit.contract.operation("createSession"),
            kit.contract.operation("refreshSession"),
        )
    )

    wrong_root = client.post(
        "/v1/sessions",
        data=(examples / "refresh.request.xml").read_bytes(),
        content_type="application/xml",
    )
    assert wrong_root.status_code == 422
    assert wrong_root.json["code"] == "invalid_body"
    assert len(reached) == 4
    kit.db.close()


def test_kit_loads_every_merged_service_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    staged = tmp_path / "contracts"
    shutil.copytree(COMMON_CONTRACT, staged / "common/v1")
    service_directories = sorted(
        path.parent for path in (REPOSITORY / "contracts").glob("*/openapi.yaml")
    )
    # Every registered service publishes a contract, so this tracks the registry
    # rather than a count that each newly merged contract would invalidate.
    registry = yaml.safe_load((REPOSITORY / "contracts/registry.yaml").read_text())
    assert [path.name for path in service_directories] == sorted(
        service["name"] for service in registry["services"]
    )
    incompatibilities = []
    for source in service_directories:
        shutil.copytree(source, staged / source.name)
        app = Flask("contract-" + source.name)
        kit = Kit(
            app,
            service=source.name,
            suffix=TEST_SUFFIX,
            contracts_dir=staged,
        )
        for operation in kit.contract.operations.values():
            if operation.protected and not operation.roles:
                incompatibilities.append(
                    f"{source.name}:{operation.operation_id} is protected without roles"
                )
            if operation.request_schema is not None and not (
                operation.xml_name and operation.xml_namespace
            ):
                incompatibilities.append(
                    f"{source.name}:{operation.operation_id} has no XML request root"
                )
            rule = kit._flask_rule(operation)
            app.add_url_rule(
                rule,
                endpoint="probe_" + operation.operation_id,
                view_func=lambda: None,
                methods=[operation.method],
            )
        kit.db.close()
    assert not incompatibilities, "\n".join(incompatibilities)
