"""The registry against the seeded database, through the real kit and Redis."""

from __future__ import annotations

import json
import uuid
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
    the trigger off for the cleanup only."""
    yield
    owner.execute("SET session_replication_role = replica")
    owner.execute(
        "DELETE FROM models.model_version_feature WHERE version_id IN"
        " (SELECT version_id FROM models.model_version WHERE name LIKE %s)",
        (PREFIX + "%",),
    )
    owner.execute(
        "DELETE FROM models.model_version WHERE name LIKE %s", (PREFIX + "%",)
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
        ("post", "/v1/model-versions/1/publications"),
        ("post", "/v1/model-versions/1/rollbacks"),
        ("get", "/v1/model-trainings"),
        ("post", "/v1/model-trainings"),
        ("get", "/v1/model-trainings/1"),
    ):
        assert getattr(client, method)(path, headers=token()).status_code in (404, 405)


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
