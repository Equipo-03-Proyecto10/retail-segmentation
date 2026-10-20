"""Contract serving, representation negotiation and hardened body parsing."""

from __future__ import annotations

from typing import Any

import pytest
from lxml import etree

from tests.conftest import EMAIL, PASSWORD, SERVICE

pytestmark = [pytest.mark.postgres, pytest.mark.redis]


def _code(response: Any) -> tuple[int, str | None]:
    return response.status_code, (response.get_json(silent=True) or {}).get("code")


def test_meta_routes_serve_the_repository_contract_bytes(
    client: Any,
) -> None:
    root = SERVICE.parents[1]
    assert client.get("/health/live").status_code == 204
    assert client.get("/health/ready").status_code == 204
    assert (
        client.get("/openapi.yaml").data
        == (root / "contracts/auth/openapi.yaml").read_bytes()
    )
    assert (
        client.get("/schema.xsd").data
        == (root / "contracts/auth/auth.xsd").read_bytes()
    )


def test_sign_in_and_refresh_answer_schema_valid_xml(app: Any, client: Any) -> None:
    sign_in = client.post(
        "/v1/sessions",
        data=(
            '<SignInRequest xmlns="https://mosaiq.maxthecoder.online/xml/auth/v1">'
            f"<email>{EMAIL}</email><password>{PASSWORD}</password>"
            "<device_label>pytest</device_label></SignInRequest>"
        ),
        content_type="application/xml",
        headers={"Accept": "application/xml", "X-Real-IP": "192.0.2.80"},
    )
    schema = app.extensions["mosaiq_kit"].contract.xml_schema
    document = etree.fromstring(sign_in.data)
    assert sign_in.status_code == 201 and schema.validate(document), schema.error_log
    token = document.findtext("{*}refresh_token")
    refresh = client.post(
        "/v1/sessions/refresh",
        data=(
            '<RefreshRequest xmlns="https://mosaiq.maxthecoder.online/xml/auth/v1">'
            f"<refresh_token>{token}</refresh_token></RefreshRequest>"
        ),
        content_type="application/xml",
        headers={"Accept": "application/xml"},
    )
    document = etree.fromstring(refresh.data)
    assert refresh.status_code == 200 and schema.validate(document), schema.error_log


@pytest.mark.parametrize(
    "body",
    [
        b"{not-json",
        b'<!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>',
        (
            b'<!DOCTYPE x [<!ENTITY a "1234567890"><!ENTITY b "&a;&a;&a;&a;">]>'
            b"<x>&b;</x>"
        ),
        b'<!DOCTYPE x SYSTEM "file:///etc/passwd"><x/>',
    ],
)
def test_malformed_doctype_billion_laughs_and_external_entities_are_refused(
    client: Any, body: bytes
) -> None:
    content_type = "application/json" if body.startswith(b"{") else "application/xml"
    assert _code(client.post("/v1/sessions", data=body, content_type=content_type)) == (
        400,
        "malformed_body",
    )


def test_406_415_and_413_use_the_contract_errors(client: Any) -> None:
    valid = {"email": EMAIL, "password": PASSWORD, "device_label": "pytest"}
    assert _code(
        client.post(
            "/v1/sessions",
            json=valid,
            headers={"Accept": "text/plain", "X-Real-IP": "192.0.2.81"},
        )
    ) == (406, "not_acceptable")
    assert _code(
        client.post(
            "/v1/sessions",
            data="email=x",
            content_type="application/x-www-form-urlencoded",
        )
    ) == (415, "unsupported_media_type")
    assert _code(
        client.post(
            "/v1/sessions",
            data=b"x" * 65_537,
            content_type="application/json",
        )
    ) == (413, "payload_too_large")


def test_protected_operations_refuse_missing_and_tampered_tokens(
    client: Any, session: dict[str, str]
) -> None:
    missing = client.get("/v1/sessions/current")
    assert _code(missing) == (401, "invalid_token")
    token = session["access_token"]
    replacement = "A" if token[-1] != "A" else "B"
    tampered = client.get(
        "/v1/sessions/current",
        headers={"Authorization": "Bearer " + token[:-1] + replacement},
    )
    assert _code(tampered) == (401, "invalid_token")
