"""Authenticated current-session, sign-out and JWK publication."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import jwt
import pytest
import redis
from lxml import etree

from app.store import auth_unavailable, refresh_fingerprint

pytestmark = [pytest.mark.postgres, pytest.mark.redis]


def _authorization(session: dict[str, str]) -> dict[str, str]:
    return {"Authorization": "Bearer " + session["access_token"]}


def _claims(session: dict[str, str]) -> dict[str, Any]:
    return jwt.decode(session["access_token"], options={"verify_signature": False})


def test_current_session_answers_json_and_schema_valid_xml(
    app: Any, client: Any, session: dict[str, str]
) -> None:
    response = client.get("/v1/sessions/current", headers=_authorization(session))
    assert response.status_code == 200
    assert set(response.get_json()) == {"sub", "sid", "role", "issued_at", "expires_at"}
    xml = client.get(
        "/v1/sessions/current",
        headers=_authorization(session) | {"Accept": "application/xml"},
    )
    document = etree.fromstring(xml.data)
    schema = app.extensions["mosaiq_kit"].contract.xml_schema
    assert xml.status_code == 200 and schema.validate(document), schema.error_log


def test_sign_out_deletes_session_and_indexed_refresh_family_then_revokes_token(
    client: Any, session: dict[str, str], redis_admin: redis.Redis
) -> None:
    claims = _claims(session)
    sid = claims["sid"]
    token_hash = refresh_fingerprint(session["refresh_token"])
    response = client.delete("/v1/sessions/current", headers=_authorization(session))
    assert response.status_code == 204 and not response.data
    assert not redis_admin.exists("pub:auth:v1:live:" + sid)
    assert not redis_admin.exists("auth:session:" + sid)
    assert not redis_admin.exists("auth:refresh_family:" + sid)
    assert not redis_admin.exists("auth:refresh:" + token_hash)
    refused = client.get("/v1/sessions/current", headers=_authorization(session))
    assert (refused.status_code, refused.get_json()["code"]) == (
        401,
        "session_revoked",
    )


def test_sign_out_waitaof_failure_is_auth_unavailable(
    app: Any,
    client: Any,
    session: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = app.extensions["auth_store"]
    monkeypatch.setattr(
        store, "_require_aof", lambda _result: (_ for _ in ()).throw(auth_unavailable())
    )
    response = client.delete("/v1/sessions/current", headers=_authorization(session))
    assert (response.status_code, response.get_json()["code"]) == (
        503,
        "auth_unavailable",
    )


def test_jwks_is_json_only_and_contains_public_members_only(client: Any) -> None:
    response = client.get(
        "/v1/jwks.json", headers={"Accept": "application/jwk-set+json"}
    )
    assert response.status_code == 200
    assert response.content_type.startswith("application/jwk-set+json")
    for key in response.get_json()["keys"]:
        assert set(key) == {"kty", "kid", "alg", "use", "n", "e"}
        assert not ({"d", "p", "q", "dp", "dq", "qi"} & set(key))
    refused = client.get("/v1/jwks.json", headers={"Accept": "application/xml"})
    assert (refused.status_code, refused.get_json(silent=True)) == (406, None)


def test_startup_refuses_private_key_not_present_under_auth_kid(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import create_app

    source = json.loads(Path(os.environ["AUTH_JWKS_PATH"]).read_text(encoding="utf-8"))
    source["keys"][0]["kid"] = "another-kid"
    mismatch = tmp_path / "jwks.json"
    mismatch.write_text(json.dumps(source), encoding="utf-8")
    monkeypatch.setenv("AUTH_JWKS_PATH", str(mismatch))
    with pytest.raises(RuntimeError, match="public half.*AUTH_KID"):
        create_app(start_background=False)
