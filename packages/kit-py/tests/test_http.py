from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest
from conftest import TEST_SUFFIX
from flask import Flask
from lxml import etree

from mosaiq_kit import Kit, Representation, configure_logging
from mosaiq_kit.types import ApiError


def _assert_common_xml(response, contracts_dir: Path, code: str) -> None:
    assert response.content_type == "application/xml"
    schema = etree.XMLSchema(etree.parse(str(contracts_dir / "common/v1/common.xsd")))
    root = etree.fromstring(response.data)
    assert root.tag == "{https://mosaiq.maxthecoder.online/xml/common/v1}Error"
    assert schema.validate(root), schema.error_log
    assert (
        root.findtext("{https://mosaiq.maxthecoder.online/xml/common/v1}code") == code
    )


@pytest.mark.parametrize(
    "case",
    [
        "no_token",
        "alg_none",
        "hmac_with_public_key",
        "wrong_aud",
        "wrong_iss",
        "expired",
        "tampered_payload",
        "unknown_kid",
        "revoked_session",
    ],
)
def test_public_vectors_produce_the_expected_authentication_error(
    client,
    tokens: dict[str, str],
    expected: dict[str, dict[str, object]],
    case: str,
) -> None:
    headers = {"Authorization": "Bearer " + tokens[case]} if tokens[case] else {}
    response = client.get("/v1/notes", headers=headers)
    assert response.status_code == expected[case]["status"]
    assert response.json["code"] == expected[case]["code"]


def test_valid_token_reaches_the_handler(client, reader_headers) -> None:
    response = client.get("/v1/notes", headers=reader_headers)
    assert response.status_code == 200
    assert response.json["page_info"] == {"page": 1, "size": 20, "total": 1}


def test_operation_roles_come_from_the_contract(client, reader_headers) -> None:
    response = client.post(
        "/v1/notes",
        headers=reader_headers,
        json={"title": "New", "amount": "1.00"},
    )
    assert response.status_code == 403
    assert response.json["code"] == "forbidden_role"


def test_authentication_precedes_negotiation(client) -> None:
    response = client.get("/v1/notes", headers={"Accept": "text/plain"})
    assert response.status_code == 401
    assert response.json["code"] == "invalid_token"


def test_oversize_precedes_authentication(client) -> None:
    response = client.post(
        "/v1/notes",
        data=b"x" * 65_537,
        content_type="application/json",
    )
    assert response.status_code == 413
    assert response.json["code"] == "payload_too_large"


def test_xml_client_gets_xml_for_early_authentication_error(
    client, contracts_dir: Path
) -> None:
    response = client.get("/v1/notes", headers={"Accept": "application/xml"})
    assert response.status_code == 401
    _assert_common_xml(response, contracts_dir, "invalid_token")


def test_xml_client_gets_xml_for_early_payload_error(
    client, contracts_dir: Path
) -> None:
    response = client.post(
        "/v1/notes",
        headers={"Accept": "application/xml"},
        data=b"x" * 65_537,
        content_type="application/json",
    )
    assert response.status_code == 413
    _assert_common_xml(response, contracts_dir, "payload_too_large")


def test_xml_client_gets_xml_for_unknown_route(client, contracts_dir: Path) -> None:
    response = client.get("/missing", headers={"Accept": "application/xml"})
    assert response.status_code == 404
    _assert_common_xml(response, contracts_dir, "not_found")


@pytest.mark.parametrize("accept", [None, "*/*", "application/json"])
def test_json_negotiation_defaults(client, reader_headers, accept: str | None) -> None:
    headers = dict(reader_headers)
    if accept:
        headers["Accept"] = accept
    response = client.get("/v1/notes", headers=headers)
    assert response.status_code == 200
    assert response.content_type == "application/json"


def test_xml_negotiation(client, reader_headers) -> None:
    response = client.get(
        "/v1/notes", headers={**reader_headers, "Accept": "application/xml"}
    )
    assert response.status_code == 200
    assert response.content_type == "application/xml"
    assert etree.fromstring(response.data).tag.endswith("Notes")


def test_unacceptable_response_is_json(client, reader_headers) -> None:
    response = client.get(
        "/v1/notes", headers={**reader_headers, "Accept": "text/plain"}
    )
    assert response.status_code == 406
    assert response.content_type == "application/json"
    assert response.json["code"] == "not_acceptable"


def test_unsupported_body_uses_negotiated_error(client, writer_headers) -> None:
    response = client.post(
        "/v1/notes",
        headers={**writer_headers, "Accept": "application/xml"},
        data="title=wrong",
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 415
    assert response.content_type == "application/xml"
    assert etree.QName(etree.fromstring(response.data)).localname == "Error"


@pytest.mark.parametrize(
    "payload,content_type",
    [
        (b"{", "application/json"),
        (b'{"title":NaN,"amount":"1.00"}', "application/json"),
        (b"<CreateNoteRequest>", "application/xml"),
        (
            b'<!DOCTYPE x [<!ENTITY a "ha">]>'
            b"<CreateNoteRequest>&a;</CreateNoteRequest>",
            "application/xml",
        ),
        (
            b'<!DOCTYPE x [<!ENTITY a "x"><!ENTITY b "&a;&a;">]>'
            b"<CreateNoteRequest>&b;</CreateNoteRequest>",
            "application/xml",
        ),
        (
            b'<!DOCTYPE x [<!ENTITY ext SYSTEM "file:///etc/passwd">]>'
            b"<CreateNoteRequest>&ext;</CreateNoteRequest>",
            "application/xml",
        ),
        (b"<CreateNoteRequest>\x00</CreateNoteRequest>", "application/xml"),
    ],
)
def test_malformed_bodies_are_refused(
    client, writer_headers, payload: bytes, content_type: str
) -> None:
    response = client.post(
        "/v1/notes", headers=writer_headers, data=payload, content_type=content_type
    )
    assert response.status_code == 400
    assert response.json["code"] == "malformed_body"


@pytest.mark.parametrize(
    "body,field,code",
    [
        ({"amount": "1.00"}, "title", "required"),
        ({"title": 3, "amount": "1.00"}, "title", "invalid_type"),
        ({"title": "New", "amount": "1"}, "amount", "invalid_format"),
        (
            {"title": "New", "amount": "1.00", "extra": True},
            "extra",
            "unexpected_field",
        ),
    ],
)
def test_schema_errors_have_stable_details(
    client, writer_headers, body: dict[str, object], field: str, code: str
) -> None:
    response = client.post("/v1/notes", headers=writer_headers, json=body)
    assert response.status_code == 422
    assert response.json["code"] == "invalid_body"
    assert {"field": field, "code": code} in response.json["details"]


@pytest.mark.parametrize(
    "body,field",
    [
        ({"title": "a\x00b", "amount": "1.00"}, "title"),
        ({"title": "\x00", "amount": "1.00"}, "title"),
        ({"title": "New", "amount": "1.00", "ex\x00tra": "x"}, "$"),
    ],
)
def test_nul_in_a_json_string_is_a_schema_violation(
    client, writer_headers, body: dict[str, object], field: str
) -> None:
    # PostgreSQL text cannot hold U+0000; it must never reach the driver (#580).
    response = client.post("/v1/notes", headers=writer_headers, json=body)
    assert response.status_code == 422
    assert response.json["code"] == "invalid_body"
    assert {"field": field, "code": "invalid_format"} in response.json["details"]


def test_escaped_nul_in_raw_json_is_a_schema_violation(client, writer_headers) -> None:
    response = client.post(
        "/v1/notes",
        headers={**writer_headers, "Accept": "application/xml"},
        data=b'{"title":"a\\u0000b","amount":"1.00"}',
        content_type="application/json",
    )
    assert response.status_code == 422
    assert response.content_type == "application/xml"
    assert b"invalid_body" in response.data


@pytest.mark.parametrize(
    "inner,title",
    [
        ("<!--c--><title>New</title><amount>1.25</amount>", "New"),
        ("<?pi x?><title>New</title><amount>1.25</amount><!--end-->", "New"),
        ("<title>a<!--c-->b</title><amount>1.25</amount>", "ab"),
        ("<title>a<?pi x?>b</title><amount>1<!--c-->.25</amount>", "ab"),
        ("<title><!--only--></title><amount>1.25</amount>", None),
    ],
)
def test_xml_comments_and_processing_instructions_are_skipped(
    client, writer_headers, inner: str, title: str | None
) -> None:
    # Comments and PIs are legal XML; kit-rs skips them too (#582).
    namespace = "https://mosaiq.maxthecoder.online/xml/kit-example/v1"
    response = client.post(
        "/v1/notes",
        headers=writer_headers,
        data=(
            f'<?xml version="1.0"?><!--lead--><CreateNoteRequest xmlns="{namespace}">'
            f"{inner}</CreateNoteRequest><?trail x?>"
        ),
        content_type="application/xml",
    )
    if title is None:
        # An element holding only a comment is empty, which the XSD refuses.
        assert response.status_code == 422
        assert response.json["code"] == "invalid_body"
        return
    assert response.status_code == 201
    assert response.json["title"] == title
    assert response.json["amount"] == "1.25"


def test_duplicate_json_member_is_malformed(client, writer_headers) -> None:
    response = client.post(
        "/v1/notes",
        headers=writer_headers,
        data=b'{"title":"First","title":"Second","amount":"1.00"}',
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json["code"] == "malformed_body"


def test_valid_xml_and_json_reach_the_same_handler(client, writer_headers) -> None:
    namespace = "https://mosaiq.maxthecoder.online/xml/kit-example/v1"
    xml = (
        f'<CreateNoteRequest xmlns="{namespace}"><title>New</title>'
        "<amount>1.25</amount></CreateNoteRequest>"
    )
    xml_response = client.post(
        "/v1/notes",
        headers={**writer_headers, "Accept": "application/xml"},
        data=xml,
        content_type="application/xml",
    )
    json_response = client.post(
        "/v1/notes",
        headers=writer_headers,
        json={"title": "New", "amount": "1.25"},
    )
    assert xml_response.status_code == json_response.status_code == 201
    root = etree.fromstring(xml_response.data)
    assert root.findtext(f"{{{namespace}}}title") == json_response.json["title"]
    assert root.findtext(f"{{{namespace}}}amount") == json_response.json["amount"]


@pytest.mark.parametrize(
    "query",
    [
        "?page=0",
        "?page=no",
        "?page=1_0",
        "?page=%2B5",
        "?page=%205",
        "?size=0",
        "?size=101",
        "?size=no",
    ],
)
def test_invalid_pagination(client, reader_headers, query: str) -> None:
    response = client.get("/v1/notes" + query, headers=reader_headers)
    assert response.status_code == 422
    assert response.json["code"] == "invalid_pagination"


@pytest.mark.parametrize(
    "query,field",
    [
        ("?page=99999999999999999999", "page"),
        ("?page=4294967296", "page"),
        ("?page=" + "9" * 5000, "page"),
        ("?size=99999999999999999999", "size"),
        ("?size=" + "1" * 5000, "size"),
    ],
)
def test_out_of_range_pagination_is_refused(
    client, reader_headers, query: str, field: str
) -> None:
    # The bound keeps OFFSET inside bigint, as kit-rs's u32 does (#581).
    response = client.get("/v1/notes" + query, headers=reader_headers)
    assert response.status_code == 422
    assert response.json["code"] == "invalid_pagination"
    assert response.json["details"] == [{"field": field, "code": "out_of_range"}]


@pytest.mark.parametrize(
    "query,page,size",
    [
        ("?page=4294967295", 4_294_967_295, 20),
        ("?page=0000000000000000000002&size=007", 2, 7),
    ],
)
def test_pagination_bounds_are_inclusive(
    client, reader_headers, query: str, page: int, size: int
) -> None:
    response = client.get("/v1/notes" + query, headers=reader_headers)
    assert response.status_code == 200
    assert response.json["page_info"]["page"] == page
    assert response.json["page_info"]["size"] == size


def test_valid_pagination_is_passed_to_page_info(client, reader_headers) -> None:
    response = client.get("/v1/notes?page=2&size=7", headers=reader_headers)
    assert response.json["page_info"] == {"page": 2, "size": 7, "total": 1}


@pytest.mark.parametrize("supplied", ["corr-123", "bad value", "", "x" * 65])
def test_correlation_id_is_validated_and_echoed(
    client, reader_headers, supplied: str
) -> None:
    headers = {**reader_headers, "X-Correlation-ID": supplied}
    response = client.get("/v1/notes", headers=headers)
    correlation_id = response.headers["X-Correlation-ID"]
    if supplied == "corr-123":
        assert correlation_id == supplied
    else:
        assert correlation_id != supplied
    assert 1 <= len(correlation_id) <= 64


def test_error_contains_the_echoed_correlation_id(client) -> None:
    response = client.get("/v1/notes", headers={"X-Correlation-ID": "corr-error"})
    assert response.json["correlation_id"] == "corr-error"
    assert response.headers["X-Correlation-ID"] == "corr-error"


def test_meta_routes_are_public_and_byte_exact(client, contracts_dir: Path) -> None:
    assert client.get("/health/live").status_code == 204
    assert client.get("/health/ready").status_code == 204
    assert (
        client.get("/openapi.yaml").data
        == (contracts_dir / "kit-example/openapi.yaml").read_bytes()
    )
    assert (
        client.get("/schema.xsd").data
        == (contracts_dir / "kit-example/kit-example.xsd").read_bytes()
    )


def test_failed_readiness_has_its_own_error(
    configured_environment, contracts_dir: Path
) -> None:
    app = Flask("not-ready")
    kit = Kit(
        app,
        service="kit-example",
        suffix=TEST_SUFFIX,
        contracts_dir=contracts_dir,
        ready_checks=[lambda: False],
    )
    response = app.test_client().get("/health/ready")
    assert response.status_code == 503
    assert response.json["code"] == "schema_not_ready"
    kit.db.close()


def test_path_constraints_and_undeclared_methods_are_routing_errors(
    client, reader_headers
) -> None:
    assert client.get("/v1/notes/0", headers=reader_headers).status_code == 404
    response = client.options("/v1/notes", headers=reader_headers)
    assert response.status_code == 405
    assert "GET" in response.headers["Allow"]


def test_session_store_unreachable_fails_closed(
    monkeypatch: pytest.MonkeyPatch, contracts_dir: Path, tokens: dict[str, str]
) -> None:
    monkeypatch.setenv("AUTH_ISSUER", "https://kit-vectors.invalid/auth")
    monkeypatch.setenv(
        "AUTH_JWKS_PATH", str(Path(__file__).parents[2] / "kit-vectors/jwks.json")
    )
    monkeypatch.setenv("REDIS_STATE_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("REDIS_" + TEST_SUFFIX.upper() + "_PASSWORD", "unused")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    app = Flask("unreachable-state")
    kit = Kit(
        app,
        service="kit-example",
        suffix=TEST_SUFFIX,
        contracts_dir=contracts_dir,
    )

    @kit.operation("listNotes")
    def list_notes() -> Representation:
        return Representation("Notes", {})

    response = app.test_client().get(
        "/v1/notes",
        headers={"Authorization": "Bearer " + tokens["valid_reader"]},
    )
    assert response.status_code == 503
    assert response.json["code"] == "auth_unavailable"
    kit.db.close()


def test_slow_session_store_fails_closed(redis_admin, client, reader_headers) -> None:
    redis_admin.execute_command("CLIENT", "PAUSE", 350, "ALL")
    started = time.monotonic()
    response = client.get("/v1/notes", headers=reader_headers)
    elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.json["code"] == "auth_unavailable"
    assert elapsed < 1
    time.sleep(0.2)


def test_rate_limit_precedes_negotiation(
    monkeypatch: pytest.MonkeyPatch,
    configured_environment,
    contracts_dir: Path,
    tokens: dict[str, str],
    service_redis,
    redis_admin,
) -> None:
    keys = list(redis_admin.scan_iter(match=TEST_SUFFIX + ":rl:*"))
    if keys:
        redis_admin.delete(*keys)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    app = Flask("limited")
    kit = Kit(
        app,
        service="kit-example",
        suffix=TEST_SUFFIX,
        contracts_dir=contracts_dir,
    )

    @kit.operation("listNotes")
    def list_notes() -> Representation:
        return Representation("Notes", {})

    headers = {
        "Authorization": "Bearer " + tokens["valid_reader"],
        "Accept": "application/xml",
    }
    assert app.test_client().get("/v1/notes", headers=headers).status_code == 200
    response = app.test_client().get("/v1/notes", headers=headers)
    assert response.status_code == 429
    _assert_common_xml(response, contracts_dir, "rate_limited")
    assert int(response.headers["Retry-After"]) >= 1

    keys = list(redis_admin.scan_iter(match=TEST_SUFFIX + ":rl:*"))
    if keys:
        redis_admin.delete(*keys)
    unacceptable = {"Authorization": "Bearer " + tokens["valid_writer"]}
    assert (
        app.test_client()
        .get("/v1/notes", headers={**unacceptable, "Accept": "text/plain"})
        .status_code
        == 406
    )
    response = app.test_client().get(
        "/v1/notes", headers={**unacceptable, "Accept": "text/plain"}
    )
    assert response.status_code == 429
    assert response.json["code"] == "rate_limited"
    kit.db.close()


def test_logging_redacts_credentials_and_adds_request_fields(
    capsys: pytest.CaptureFixture[str], client
) -> None:
    handler = configure_logging()
    logger = logging.getLogger("kit-test")
    with client.application.test_request_context(
        "/",
        headers={"X-Real-IP": "198.51.100.8"},
        environ_overrides={"REMOTE_ADDR": "192.0.2.4"},
    ):
        from mosaiq_kit.logging import set_request_context

        set_request_context("corr-log", "actor-1")
        logger.warning(
            "values=%r %s %s %s",
            "visitor",
            "Bearer secret-value",
            "eyJaaa.bbb.ccc",
            "password=hidden-value",
        )
        try:
            raise ValueError("password=trace-value")
        except ValueError:
            logger.exception("A visitor-triggered exception")
    line = capsys.readouterr().err
    logging.getLogger().removeHandler(handler)
    assert "correlation=corr-log actor=actor-1 client=198.51.100.8" in line
    assert "secret-value" not in line
    assert "eyJaaa.bbb.ccc" not in line
    assert "hidden-value" not in line
    assert "trace-value" not in line


def test_vector_files_have_public_material_only() -> None:
    vector_root = Path(__file__).parents[2] / "kit-vectors"
    jwks = json.loads((vector_root / "jwks.json").read_text())
    assert set(jwks["keys"][0]) == {"kty", "use", "alg", "kid", "n", "e"}
    assert not list(vector_root.glob("*.pem"))


@pytest.mark.parametrize("path", ["/openapi.yaml", "/schema.xsd"])
@pytest.mark.parametrize("accept", ["image/png", "text/html", "application/json"])
def test_contract_documents_refuse_an_unsupported_accept(
    client, path: str, accept: str
) -> None:
    response = client.get(path, headers={"Accept": accept})
    assert response.status_code == 406
    assert response.json["code"] == "not_acceptable"


@pytest.mark.parametrize(
    "path,accept",
    [
        ("/openapi.yaml", None),
        ("/openapi.yaml", "*/*"),
        ("/openapi.yaml", "application/yaml"),
        ("/openapi.yaml", "application/*"),
        ("/schema.xsd", None),
        ("/schema.xsd", "application/xml"),
        ("/schema.xsd", "text/xml, */*;q=0.1"),
    ],
)
def test_contract_documents_serve_a_supported_accept(
    client, path: str, accept: str | None
) -> None:
    headers = {"Accept": accept} if accept else {}
    assert client.get(path, headers=headers).status_code == 200


_NIL_SCHEMA = {
    "type": "object",
    "required": ["name", "email", "phone", "note"],
    "properties": {
        "name": {"type": "string"},
        "email": {"type": ["string", "null"]},
        "phone": {"oneOf": [{"type": "string"}, {"type": "null"}]},
        "note": {"type": "string"},
        "parent": {"type": ["integer", "null"]},
    },
}


def _read_xml(contracts_dir: Path, body: str):
    from mosaiq_kit.contracts import Contract

    contract = Contract(contracts_dir, "kit-example")
    root = etree.fromstring(
        f'<R xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">{body}</R>'
    )
    return contract, contract._element_to_value(root, _NIL_SCHEMA)


def test_xml_omitted_required_nullable_members_read_as_null(
    contracts_dir: Path,
) -> None:
    contract, value = _read_xml(contracts_dir, "<name>A</name>")
    assert value == {"name": "A", "email": None, "phone": None}
    # A required member that is not nullable still fails validation.
    with pytest.raises(ApiError) as error:
        contract.validate(value, _NIL_SCHEMA)
    assert error.value.details == [{"field": "note", "code": "required"}]


def test_xml_xsi_nil_reads_as_null(contracts_dir: Path) -> None:
    _, value = _read_xml(
        contracts_dir,
        '<name>A</name><email xsi:nil="true"/><parent xsi:nil="true"/>'
        "<phone>1</phone>",
    )
    assert value["email"] is None
    assert value["parent"] is None
    assert value["phone"] == "1"


def test_xml_omitted_required_list_reads_as_empty(contracts_dir: Path) -> None:
    from mosaiq_kit.contracts import Contract

    schema = {
        "type": "object",
        "required": ["ids", "tags"],
        "properties": {
            "ids": {"type": "array", "items": {"type": "integer"}},
            "tags": {"type": ["array", "null"], "items": {"type": "string"}},
            "extra": {"type": "array", "items": {"type": "string"}},
        },
    }
    contract = Contract(contracts_dir, "kit-example")
    # `tags` is nullable, so it still reads as null; `extra` is not required
    # and stays absent. Same reading as kit-rs (#475).
    assert contract._element_to_value(etree.fromstring("<R/>"), schema) == {
        "ids": [],
        "tags": None,
    }
    listed = etree.fromstring("<R><ids>1</ids><ids>2</ids></R>")
    assert contract._element_to_value(listed, schema)["ids"] == [1, 2]


def test_categories_own_nil_example_reads_as_null() -> None:
    from mosaiq_kit.contracts import Contract

    root = Path(__file__).resolve().parents[3] / "contracts"
    contract = Contract(root, "categories")
    operation = contract.operation("updateCategory")
    raw = (
        root / "categories/examples/update-category-make-root.request.xml"
    ).read_bytes()
    value = contract.parse_xml(raw, operation)
    assert value == {"parent_category_id": None}
    contract.validate(value, operation.request_schema)
