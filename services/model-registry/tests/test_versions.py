"""The registry against the seeded database, through the real kit and Redis."""

from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import psycopg
import pytest
from flask.testing import FlaskClient

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = Path(__file__).resolve().parents[3] / "contracts/model-registry/examples"
PREFIX = "pytest-"


def request_body(name: str = "model-version.request.json") -> dict[str, Any]:
    body = json.loads((EXAMPLES / name).read_text())
    body["name"] = PREFIX + uuid.uuid4().hex[:12]
    return body


@pytest.fixture(autouse=True)
def forget_registered(owner: psycopg.Connection) -> Any:
    """Registered versions are immutable, so the owner removes them by turning
    the trigger off for the cleanup only. Publication tests also restore every
    seeded version's projection and remove their appended publication facts."""
    original = owner.execute(
        "SELECT version_id, state, published_at, retired_at"
        " FROM models.model_version ORDER BY version_id"
    ).fetchall()
    (publication_floor,) = owner.execute(
        "SELECT coalesce(max(publication_id), 0) FROM models.model_publication"
    ).fetchone()
    yield
    owner.execute("SET session_replication_role = replica")
    owner.execute(
        "DELETE FROM models_events.outbox WHERE entity_id::bigint > %s",
        (publication_floor,),
    )
    owner.execute(
        "DELETE FROM models.model_publication WHERE publication_id > %s",
        (publication_floor,),
    )
    owner.execute(
        "DELETE FROM models.model_version_feature WHERE version_id IN"
        " (SELECT version_id FROM models.model_version WHERE name LIKE %s)",
        (PREFIX + "%",),
    )
    owner.execute(
        "DELETE FROM models.model_version WHERE name LIKE %s", (PREFIX + "%",)
    )
    for version_id, state, published_at, retired_at in original:
        owner.execute(
            "UPDATE models.model_version SET state = %s, published_at = %s,"
            " retired_at = %s WHERE version_id = %s",
            (state, published_at, retired_at, version_id),
        )
    owner.execute("SET session_replication_role = origin")


def test_the_seed_registers_the_rules_and_the_kmeans_run(
    client: FlaskClient, token: Any
) -> None:
    response = client.get("/v1/model-versions", headers=token("ANALYST"))
    assert response.status_code == 200
    by_method = {v["method"]: v for v in response.get_json()["versions"]}
    rules, kmeans = by_method["RFM_RULES"], by_method["KMEANS"]
    assert (rules["state"], kmeans["state"]) == ("PUBLISHED", "DRAFT")
    assert rules["source"] == kmeans["source"] == "WEB_RUN"
    assert rules["owner_id"] is None and kmeans["owner_id"] is None
    assert kmeans["seed"] == 463 and kmeans["parameters"]["k"] == 4
    assert len(kmeans["frozen_scoring"]["centroids"]) == 4
    assert kmeans["metrics"]["silhouette"] == pytest.approx(0.236376)
    for version in (rules, kmeans):
        assert len(version["dataset"]["fingerprint"]) == 64
        assert version["dataset"]["origin"] == "SEEDED"
        assert version["source_run_id"] is not None


def test_a_filter_returns_only_the_published_version(
    client: FlaskClient, token: Any
) -> None:
    response = client.get("/v1/model-versions?state=PUBLISHED", headers=token())
    versions = response.get_json()["versions"]
    assert [v["method"] for v in versions] == ["RFM_RULES"]
    assert (
        client.get("/v1/model-versions?state=NOPE", headers=token()).status_code == 422
    )
    assert (
        client.get("/v1/model-versions?method=NOPE", headers=token()).get_json()["code"]
        == "invalid_query"
    )


def test_registering_stores_a_draft_with_its_owner_and_nothing_is_published(
    client: FlaskClient, token: Any, role_subjects: dict[str, str]
) -> None:
    body = request_body()
    response = client.post("/v1/model-versions", json=body, headers=token("ADMIN"))
    assert response.status_code == 201, response.get_json()
    created = response.get_json()
    assert response.headers["Location"] == f"/v1/model-versions/{created['version_id']}"
    assert created["state"] == "DRAFT" and created["source"] == "REGISTERED"
    assert created["owner_id"] == role_subjects["ADMIN"]
    assert created["dataset"] == body["dataset"]
    assert created["parameters"]["features"] == ["R", "F", "M"]
    assert created["frozen_scoring"] == body["frozen_scoring"]
    assert created["published_at"] is None and created["training_id"] is None
    again = client.get(f"/v1/model-versions/{created['version_id']}", headers=token())
    assert again.get_json() == created
    published = client.get("/v1/model-versions?state=PUBLISHED", headers=token())
    assert len(published.get_json()["versions"]) == 1


def test_registering_in_xml_is_the_same_version(
    client: FlaskClient, token: Any
) -> None:
    xml = (EXAMPLES / "model-version-kmeans.request.xml").read_text()
    xml = xml.replace(
        xml.split("<name>")[1].split("</name>")[0], PREFIX + uuid.uuid4().hex[:12]
    )
    response = client.post(
        "/v1/model-versions",
        data=xml,
        headers={
            **token(),
            "Content-Type": "application/xml",
            "Accept": "application/xml",
        },
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    assert b"<state>DRAFT</state>" in response.data


def test_a_name_in_use_is_a_409(client: FlaskClient, token: Any) -> None:
    body = request_body()
    assert (
        client.post("/v1/model-versions", json=body, headers=token()).status_code == 201
    )
    second = client.post("/v1/model-versions", json=body, headers=token())
    assert second.status_code == 409
    assert second.get_json()["code"] == "model_version_name_taken"


def test_a_version_that_breaks_its_rules_is_a_422_with_the_detail_code(
    client: FlaskClient, token: Any
) -> None:
    body = request_body()
    body["seed"] = 7
    response = client.post("/v1/model-versions", json=body, headers=token())
    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_body"
    assert response.get_json()["details"] == [
        {"field": "seed", "code": "method_mismatch"}
    ]


@pytest.mark.parametrize(
    "role", ["ANALYST", "MARKETING", "AUDITOR", "STORE_MANAGER", "CUSTOMER"]
)
def test_only_the_administrator_registers(
    client: FlaskClient, token: Any, role: str
) -> None:
    response = client.post(
        "/v1/model-versions", json=request_body(), headers=token(role)
    )
    assert response.status_code == 403


def test_reads_are_for_four_roles(client: FlaskClient, token: Any) -> None:
    for role in ("ADMIN", "ANALYST", "MARKETING", "AUDITOR"):
        assert client.get("/v1/model-versions", headers=token(role)).status_code == 200
    for role in ("STORE_MANAGER", "CUSTOMER", "INVENTORY_PLANNER"):
        assert client.get("/v1/model-versions", headers=token(role)).status_code == 403


def test_an_unknown_version_is_a_404(client: FlaskClient, token: Any) -> None:
    for path in ("/v1/model-versions/99999", "/v1/model-versions/99999/publications"):
        response = client.get(path, headers=token())
        assert response.status_code == 404
        assert response.get_json()["code"] == "model_version_not_found"


def test_the_publications_of_the_published_version(
    client: FlaskClient, token: Any
) -> None:
    published = client.get("/v1/model-versions?state=PUBLISHED", headers=token())
    version_id = published.get_json()["versions"][0]["version_id"]
    response = client.get(
        f"/v1/model-versions/{version_id}/publications", headers=token()
    )
    body = response.get_json()
    assert body["page_info"]["total"] == 1
    assert body["publications"][0]["action"] == "PUBLISH"
    assert body["publications"][0]["previous_version_id"] is None


def test_there_is_no_route_to_edit_or_delete_a_version(
    client: FlaskClient, token: Any
) -> None:
    for method in ("put", "patch", "delete"):
        response = getattr(client, method)("/v1/model-versions/1", headers=token())
        assert response.status_code == 405
        assert response.get_json()["code"] == "method_not_allowed"


def test_the_planned_operations_answer_404_or_405(
    client: FlaskClient, token: Any
) -> None:
    for method, path in (
        ("get", "/v1/model-trainings"),
        ("post", "/v1/model-trainings"),
        ("get", "/v1/model-trainings/1"),
    ):
        assert getattr(client, method)(path, headers=token()).status_code in (404, 405)


def _create_draft(client: FlaskClient, token: Any) -> dict[str, Any]:
    response = client.post("/v1/model-versions", json=request_body(), headers=token())
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _current(client: FlaskClient, token: Any) -> dict[str, Any]:
    response = client.get("/v1/model-versions?state=PUBLISHED", headers=token())
    assert response.status_code == 200
    versions = response.get_json()["versions"]
    assert len(versions) == 1
    return versions[0]


def _history_digest(owner: psycopg.Connection) -> tuple[int, str]:
    return owner.execute(
        "SELECT count(*), md5(string_agg(to_jsonb(h)::text, '|'"
        " ORDER BY h.history_id)) FROM public.customer_segment_history AS h"
    ).fetchone()


def test_publish_is_atomic_audited_and_visible_in_the_published_view(
    client: FlaskClient,
    token: Any,
    owner: psycopg.Connection,
    role_subjects: dict[str, str],
) -> None:
    previous = _current(client, token)["version_id"]
    draft = _create_draft(client, token)
    response = client.post(
        f"/v1/model-versions/{draft['version_id']}/publications",
        json={"reason": "Use the verified rule set."},
        headers={**token(), "X-Correlation-ID": "publish-model-test"},
    )
    assert response.status_code == 201, response.get_json()
    publication = response.get_json()
    assert publication["action"] == "PUBLISH"
    assert publication["previous_version_id"] == previous
    assert publication["published_by"] == role_subjects["ADMIN"]
    assert _current(client, token)["version_id"] == draft["version_id"]
    states = owner.execute(
        "SELECT version_id, state FROM models.model_version"
        " WHERE version_id = ANY(%s) ORDER BY version_id",
        ([previous, draft["version_id"]],),
    ).fetchall()
    assert dict(states) == {previous: "RETIRED", draft["version_id"]: "PUBLISHED"}
    view = owner.execute(
        "SELECT version_id, method, name, features, publication_id"
        " FROM api_models.published_version_v1"
    ).fetchone()
    assert view == (
        draft["version_id"],
        draft["method"],
        draft["name"],
        ["R", "F", "M"],
        publication["publication_id"],
    )
    event = owner.execute(
        "SELECT actor_user_id::text, actor_role, subject_id, model_version_id,"
        " correlation_id, details FROM audit_events.domain_event"
        " WHERE kind = 'MODEL_PUBLISHED' AND subject_id = %s"
        " ORDER BY seq DESC LIMIT 1",
        (str(draft["version_id"]),),
    ).fetchone()
    assert event == (
        role_subjects["ADMIN"],
        "ADMIN",
        str(draft["version_id"]),
        draft["version_id"],
        "publish-model-test",
        {"previous_version_id": previous, "reason_code": "PUBLISH"},
    )


def test_rollback_restores_the_version_without_changing_segment_history(
    client: FlaskClient, token: Any, owner: psycopg.Connection
) -> None:
    restored_id = _current(client, token)["version_id"]
    draft = _create_draft(client, token)
    published = client.post(
        f"/v1/model-versions/{draft['version_id']}/publications",
        json={"reason": "Evaluate the new boundaries."},
        headers=token(),
    )
    assert published.status_code == 201
    before = _history_digest(owner)
    response = client.post(
        f"/v1/model-versions/{restored_id}/rollbacks",
        json={"reason": "Restore the prior verified version."},
        headers=token(),
    )
    assert response.status_code == 201, response.get_json()
    rollback = response.get_json()
    assert rollback["action"] == "ROLLBACK"
    assert rollback["version_id"] == restored_id
    assert rollback["previous_version_id"] == draft["version_id"]
    assert _history_digest(owner) == before
    assert _current(client, token)["version_id"] == restored_id
    assert owner.execute(
        "SELECT version_id, publication_id FROM api_models.published_version_v1"
    ).fetchone() == (restored_id, rollback["publication_id"])


def test_publication_and_rollback_support_xml_and_validate_their_answers(
    client: FlaskClient, token: Any, owner: psycopg.Connection
) -> None:
    from lxml import etree

    schema = etree.XMLSchema(
        etree.parse(str(ROOT / "contracts/model-registry/model-registry.xsd"))
    )
    restored_id = _current(client, token)["version_id"]
    draft = _create_draft(client, token)
    headers = {
        **token(),
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    forbidden = client.post(
        f"/v1/model-versions/{draft['version_id']}/publications",
        data=(
            '<ModelPublicationRequest xmlns="https://mosaiq.maxthecoder.online/'
            'xml/model-registry/v1"><reason>Forbidden.</reason>'
            "</ModelPublicationRequest>"
        ),
        headers={**headers, **token("ANALYST")},
    )
    assert forbidden.status_code == 403
    assert b"<code>forbidden_role</code>" in forbidden.data
    for version_id, resource, reason, action in (
        (draft["version_id"], "publications", "Publish XML version.", "PUBLISH"),
        (restored_id, "rollbacks", "Restore XML version.", "ROLLBACK"),
    ):
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<ModelPublicationRequest xmlns="https://mosaiq.maxthecoder.online/'
            f'xml/model-registry/v1"><reason>{reason}</reason>'
            "</ModelPublicationRequest>"
        )
        response = client.post(
            f"/v1/model-versions/{version_id}/{resource}",
            data=body,
            headers=headers,
        )
        assert response.status_code == 201, response.get_data(as_text=True)
        document = etree.fromstring(response.data)
        schema.assertValid(document)
        assert document.xpath("string(//*[local-name()='action'])") == action
    assert owner.execute(
        "SELECT version_id FROM api_models.published_version_v1"
    ).fetchone() == (restored_id,)


@pytest.mark.parametrize("resource", ["publications", "rollbacks"])
@pytest.mark.parametrize("role", ["ANALYST", "MARKETING", "AUDITOR", "CUSTOMER"])
def test_only_the_administrator_changes_the_published_version(
    client: FlaskClient, token: Any, resource: str, role: str
) -> None:
    response = client.post(
        f"/v1/model-versions/1/{resource}",
        json={"reason": "Not allowed."},
        headers=token(role),
    )
    assert response.status_code == 403
    assert response.get_json()["code"] == "forbidden_role"


def test_publication_refusals_match_the_contract(
    client: FlaskClient, token: Any
) -> None:
    current = _current(client, token)["version_id"]
    assert (
        client.post(
            f"/v1/model-versions/{current}/publications",
            json={"reason": "Already current."},
            headers=token(),
        ).get_json()["code"]
        == "version_not_draft"
    )
    draft = _create_draft(client, token)
    assert (
        client.post(
            f"/v1/model-versions/{draft['version_id']}/rollbacks",
            json={"reason": "Never published."},
            headers=token(),
        ).get_json()["code"]
        == "version_not_retired"
    )
    missing = client.post(
        "/v1/model-versions/999999/publications",
        json={"reason": "Missing."},
        headers=token(),
    )
    assert (missing.status_code, missing.get_json()["code"]) == (
        404,
        "model_version_not_found",
    )
    for body in ({}, {"reason": ""}, {"reason": "valid", "extra": True}):
        invalid = client.post(
            f"/v1/model-versions/{draft['version_id']}/publications",
            json=body,
            headers=token(),
        )
        assert invalid.status_code == 422
        assert invalid.get_json()["code"] == "invalid_body"


def _lock_waiters(owner_url: str, count: int) -> None:
    """Wait until `count` sessions wait for the published row's lock."""
    deadline = time.monotonic() + 15
    with psycopg.connect(owner_url, autocommit=True) as watcher:
        while time.monotonic() < deadline:
            waiting = watcher.execute(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE wait_event_type = 'Lock' AND query LIKE %s",
                ("%state = 'PUBLISHED' FOR UPDATE%",),
            ).fetchone()[0]
            if waiting >= count:
                return
            time.sleep(0.05)
    raise AssertionError(f"{count} publications never waited for the published row")


def test_two_concurrent_draft_publications_leave_exactly_one_current(
    app: Any, token: Any, owner_url: str
) -> None:
    """Two publications that overlap: one wins the slot, the other gets 409.

    The overlap is made, not hoped for. A version is published first, and its
    row is held with FOR UPDATE until both requests have locked their own
    draft and wait for it. Without that, a slow runner may finish one request
    before the other starts, and then the second legitimately supersedes the
    first: both answer 201, and the invariant still holds.
    """
    seed_client = app.test_client()
    first = _create_draft(seed_client, token)
    published = seed_client.post(
        f"/v1/model-versions/{first['version_id']}/publications",
        json={"reason": "The version the two will contend to replace."},
        headers=token(),
    )
    assert published.status_code == 201, published.get_json()
    drafts = [_create_draft(seed_client, token) for _ in range(2)]

    def publish(version_id: int) -> tuple[int, str | None]:
        with app.test_client() as concurrent_client:
            response = concurrent_client.post(
                f"/v1/model-versions/{version_id}/publications",
                json={"reason": "Concurrent publication."},
                headers=token(),
            )
            body = response.get_json()
            return response.status_code, body.get("code")

    with psycopg.connect(owner_url) as holder:
        holder.execute(
            "SELECT version_id FROM models.model_version"
            " WHERE state = 'PUBLISHED' FOR UPDATE"
        ).fetchall()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(publish, d["version_id"]) for d in drafts]
            try:
                _lock_waiters(owner_url, 2)
            finally:
                holder.commit()
            results = [future.result(timeout=30) for future in futures]
    assert sorted(status for status, _ in results) == [201, 409]
    assert [code for status, code in results if status == 409] == [
        "publication_conflict"
    ]
    assert _current(seed_client, token)["version_id"] in {
        draft["version_id"] for draft in drafts
    }


def _wait_for_stream(redis_connection: Any, count: int) -> list[Any]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        entries = redis_connection.xrange("stream:models.v1.publication_recorded")
        if len(entries) >= count:
            return entries
        time.sleep(0.05)
    return redis_connection.xrange("stream:models.v1.publication_recorded")


def test_publish_and_rollback_reach_the_documented_stream(
    monkeypatch: pytest.MonkeyPatch,
    service_url: str,
    redis_connection: Any,
    token: Any,
    owner: psycopg.Connection,
) -> None:
    from app import create_app

    redis_connection.delete("stream:models.v1.publication_recorded")
    owner.execute("DELETE FROM models_events.outbox")
    monkeypatch.setenv("DATABASE_URL", service_url)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    application = create_app(start_relay=True)
    application.config["TESTING"] = True
    relay_client = application.test_client()
    restored_id = _current(relay_client, token)["version_id"]
    draft = _create_draft(relay_client, token)
    publish = relay_client.post(
        f"/v1/model-versions/{draft['version_id']}/publications",
        json={"reason": "Announce publication."},
        headers=token(),
    ).get_json()
    rollback = relay_client.post(
        f"/v1/model-versions/{restored_id}/rollbacks",
        json={"reason": "Announce rollback."},
        headers=token(),
    ).get_json()
    entries = _wait_for_stream(redis_connection, 2)
    assert len(entries) == 2
    assert [entry[1]["entity_id"] for entry in entries] == [
        str(publish["publication_id"]),
        str(rollback["publication_id"]),
    ]
    assert all(
        entry[1]["event_type"] == "INSERT" and entry[1]["v"] == "1" for entry in entries
    )
    application.extensions["models_relay"].stop()
    application.extensions["mosaiq_kit"].db.close()


def test_an_audit_failure_rolls_back_state_publication_and_outbox(
    client: FlaskClient,
    token: Any,
    owner: psycopg.Connection,
) -> None:
    draft = _create_draft(client, token)
    current = _current(client, token)["version_id"]
    before_count, publication_floor = owner.execute(
        "SELECT count(*), coalesce(max(publication_id), 0)"
        " FROM models.model_publication"
    ).fetchone()
    revoke = (
        "REVOKE EXECUTE ON FUNCTION api_audit.record_event_v1("
        "uuid, text, text, timestamptz, text, uuid, text, smallint, text, text,"
        " bigint, bigint, text, jsonb) FROM svc_models"
    )
    grant = (
        "GRANT EXECUTE ON FUNCTION api_audit.record_event_v1("
        "uuid, text, text, timestamptz, text, uuid, text, smallint, text, text,"
        " bigint, bigint, text, jsonb) TO svc_models"
    )
    owner.execute(revoke)
    try:
        response = client.post(
            f"/v1/model-versions/{draft['version_id']}/publications",
            json={"reason": "Must not survive without audit."},
            headers=token(),
        )
    finally:
        owner.execute(grant)
    assert response.status_code == 503
    assert response.get_json()["code"] == "database_unavailable"
    assert _current(client, token)["version_id"] == current
    assert owner.execute(
        "SELECT state FROM models.model_version WHERE version_id = %s",
        (draft["version_id"],),
    ).fetchone() == ("DRAFT",)
    assert owner.execute(
        "SELECT count(*) FROM models.model_publication"
    ).fetchone() == (before_count,)
    assert owner.execute(
        "SELECT count(*) FROM models_events.outbox WHERE entity_id::bigint > %s",
        (publication_floor,),
    ).fetchone() == (0,)


def test_outbox_is_trigger_owned_and_consumer_grants_are_deferred(
    service_db: psycopg.Connection, owner: psycopg.Connection
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        service_db.execute(
            "INSERT INTO models_events.outbox (stream, event_type, entity_id)"
            " VALUES (%s, %s, %s)",
            ("stream:models.v1.publication_recorded", "INSERT", "1"),
        )
    for role in ("svc_rfm", "svc_kpis"):
        exists = owner.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
        ).fetchone()
        if exists is not None:
            assert owner.execute(
                "SELECT has_schema_privilege(%s, %s, 'USAGE'),"
                " has_table_privilege(%s, %s, 'SELECT')",
                (
                    role,
                    "api_models",
                    role,
                    "api_models.published_version_v1",
                ),
            ).fetchone() == (False, False)


# The schema's half of immutability, as svc_models and as the owner.


def test_no_role_can_edit_or_delete_a_version(
    service_db: psycopg.Connection, owner: psycopg.Connection
) -> None:
    for connection in (service_db, owner):
        with pytest.raises(psycopg.Error):
            connection.execute("UPDATE models.model_version SET name = 'edited'")
        with pytest.raises(psycopg.Error):
            connection.execute("DELETE FROM models.model_version")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        service_db.execute("UPDATE models.model_version SET k = 9")


def test_even_the_owner_cannot_change_a_definition_or_history(
    owner: psycopg.Connection,
) -> None:
    for statement in (
        "UPDATE models.model_version SET window_days = 30",
        "UPDATE models.model_version SET frozen_scoring = '{}'::jsonb",
        "UPDATE models.model_version_feature SET feature = 'R'",
        "DELETE FROM models.model_version_feature",
        "UPDATE models.model_publication SET reason = 'x'",
        "DELETE FROM models.model_publication",
        "UPDATE models.run_version SET version_id = version_id",
        "DELETE FROM models.run_version",
    ):
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            owner.execute(statement)


def test_a_second_published_row_is_refused_by_the_partial_unique_index(
    owner: psycopg.Connection,
) -> None:
    with pytest.raises(psycopg.errors.UniqueViolation) as raised:
        owner.execute(
            "UPDATE models.model_version SET state = 'PUBLISHED', published_at = now()"
            " WHERE method = 'KMEANS'"
        )
    assert raised.value.diag.constraint_name == "model_version_one_published"


def test_every_segmentation_run_has_a_version(owner: psycopg.Connection) -> None:
    runs, linked = owner.execute(
        "SELECT (SELECT count(*) FROM public.segmentation_run),"
        " (SELECT count(*) FROM models.run_version)"
    ).fetchone()
    assert runs == linked >= 30


def test_the_web_role_cannot_even_name_the_schema(owner_url: str) -> None:
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(owner_url)
    netloc = f"retail_app:retail_app@{parts.hostname}:{parts.port}"
    url = urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
    with psycopg.connect(url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT count(*) FROM models.model_version")


def test_xml_answers_validate_against_the_contract_schema(
    client: FlaskClient, token: Any
) -> None:
    from lxml import etree

    schema = etree.XMLSchema(
        etree.parse(str(ROOT / "contracts/model-registry/model-registry.xsd"))
    )
    xml = {**token(), "Accept": "application/xml"}
    current = client.get("/v1/model-versions?state=PUBLISHED", headers=token())
    version_id = current.get_json()["versions"][0]["version_id"]
    for path in (
        "/v1/model-versions",
        f"/v1/model-versions/{version_id}",
        f"/v1/model-versions/{version_id}/publications",
    ):
        response = client.get(path, headers=xml)
        assert response.status_code == 200
        schema.assertValid(etree.fromstring(response.data))
