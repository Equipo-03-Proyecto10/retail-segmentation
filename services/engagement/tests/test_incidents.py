"""Capture incidents against real MongoDB, PostgreSQL and Redis (ADR-0071)."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lxml import etree
from pymongo.errors import WriteError

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/engagement"
NAMESPACE = "https://mosaiq.maxthecoder.online/xml/engagement/v1"
SUPERVISOR = "11111111-1111-1111-1111-000000000033"
SELLER = "11111111-1111-1111-1111-000000000031"
ADMIN = "11111111-1111-1111-1111-000000000001"

pytestmark = [pytest.mark.postgres, pytest.mark.mongo, pytest.mark.redis]


def body(**overrides):
    value = {
        "client_incident_id": str(uuid.uuid4()),
        "source_type": "SALE",
        "source_operation_id": str(uuid.uuid4()),
        "source_correlation_id": "corr-refused-sale",
        "store_id": 8,
        "refused_status": 409,
        "refused_code": "stock_unavailable",
        "attempts": 3,
        "occurred_at": "1999-04-05T12:30:00Z",
        "line_count": 2,
    }
    return value | overrides


@pytest.fixture
def incidents(make_app, mongo_uri, service_url, redis_url, token, mongo):
    app = make_app(mongo_uri=mongo_uri, database_url=service_url, redis_url=redis_url)
    database = mongo.get_default_database(default="engagement")
    client_ids: list[str] = []

    class Context:
        client = app.test_client()
        supervisor = token("COMMERCIAL_SUPERVISOR", SUPERVISOR)
        seller = token("SALES_ASSOCIATE", SELLER)
        admin = token("ADMIN", ADMIN)

        def post(self, value, headers=None):
            client_ids.append(value["client_incident_id"].lower())
            return self.client.post(
                "/v1/capture-incidents",
                json=value,
                headers=headers or self.supervisor,
            )

        def track(self, client_incident_id: str) -> None:
            client_ids.append(client_incident_id.lower())

    context = Context()
    yield context
    database.capture_incident.delete_many({"client_incident_id": {"$in": client_ids}})


def assert_valid_xml(payload: bytes) -> etree._Element:
    schema = etree.XMLSchema(etree.parse(str(CONTRACT / "engagement.xsd")))
    root = etree.fromstring(payload)
    assert schema.validate(root), schema.error_log
    return root


def test_a_report_replayed_twice_is_exactly_one_incident(incidents, mongo):
    value = body()
    first = incidents.post(value)
    second = incidents.post(value)
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.get_json() == first.get_json()
    collection = mongo.get_default_database(default="engagement").capture_incident
    assert (
        collection.count_documents({"client_incident_id": value["client_incident_id"]})
        == 1
    )


def test_concurrent_reports_are_one_incident(incidents, mongo):
    value = body()
    results: list[tuple[int, str]] = []
    gate = threading.Barrier(8)

    def send():
        client = incidents.client.application.test_client()
        gate.wait()
        response = client.post(
            "/v1/capture-incidents", json=value, headers=incidents.supervisor
        )
        results.append((response.status_code, response.get_json()["incident_id"]))

    threads = [threading.Thread(target=send) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    incidents.post(value)
    statuses = [status for status, _ in results]
    assert statuses.count(201) == 1 and statuses.count(200) == 7
    assert len({incident_id for _, incident_id in results}) == 1
    collection = mongo.get_default_database(default="engagement").capture_incident
    assert (
        collection.count_documents(
            {"source_operation_id": value["source_operation_id"]}
        )
        == 1
    )


def test_the_operation_identity_is_also_unique_and_conflicts_are_explicit(incidents):
    value = body()
    first = incidents.post(value)
    same_operation = incidents.post(value | {"client_incident_id": str(uuid.uuid4())})
    conflict = incidents.post(value | {"source_operation_id": str(uuid.uuid4())})
    assert first.status_code == 201
    assert same_operation.status_code == 200
    assert same_operation.get_json()["incident_id"] == first.get_json()["incident_id"]
    assert (conflict.status_code, conflict.get_json()["code"]) == (
        409,
        "client_incident_conflict",
    )


def test_the_stored_document_is_an_allow_list_and_rejects_personal_or_secret_fields(
    incidents, mongo
):
    value = body()
    refused = incidents.post(
        value
        | {
            "token": "secret-token",
            "password": "secret-password",
            "Authorization": "Bearer secret-token",
            "cookie": "session=secret",
            "customer_id": str(uuid.uuid4()),
            "customer_name": "Ada Lovelace",
            "email": "ada@example.test",
        }
    )
    assert (refused.status_code, refused.get_json()["code"]) == (422, "invalid_body")
    collection = mongo.get_default_database(default="engagement").capture_incident
    assert (
        collection.count_documents({"client_incident_id": value["client_incident_id"]})
        == 0
    )

    accepted = incidents.post(value)
    assert accepted.status_code == 201
    stored = collection.find_one({"client_incident_id": value["client_incident_id"]})
    assert set(stored) == {
        "_id",
        "client_incident_id",
        "source_type",
        "source_operation_id",
        "source_correlation_id",
        "store_id",
        "refused_status",
        "refused_code",
        "attempts",
        "occurred_at",
        "line_count",
        "reported_by",
        "recorded_at",
        "status",
        "history",
    }
    assert "secret" not in str(stored) and "Ada" not in str(stored)
    bypass = stored | {
        "_id": str(uuid.uuid4()),
        "client_incident_id": str(uuid.uuid4()),
        "source_operation_id": str(uuid.uuid4()),
        "token": "secret-token",
    }
    with pytest.raises(WriteError) as rejected:
        collection.insert_one(bypass)
    assert rejected.value.code == 121


def test_resolving_keeps_the_resolution_and_token_author_in_json_and_xml(incidents):
    created = incidents.post(body()).get_json()
    reviewer = str(uuid.uuid4())
    response = incidents.client.post(
        f"/v1/capture-incidents/{created['incident_id']}/reviews",
        json={"status": "RESOLVED", "note": "Stock corrected."},
        headers=incidents.supervisor
        | {"Authorization": incidents.supervisor["Authorization"]},
    )
    # The fixture supervisor is the token author; a fresh UUID above must not
    # leak into the result merely because the client supplied local data.
    assert reviewer not in response.get_data(as_text=True)
    assert response.status_code == 201
    resolved = response.get_json()
    assert resolved["status"] == "RESOLVED"
    assert resolved["history"] == [
        {
            "status": "RESOLVED",
            "note": "Stock corrected.",
            "by": SUPERVISOR,
            "at": resolved["history"][0]["at"],
        }
    ]
    read = incidents.client.get(
        f"/v1/capture-incidents/{created['incident_id']}",
        headers=incidents.supervisor | {"Accept": "application/xml"},
    )
    root = assert_valid_xml(read.data)
    assert root.findtext(f"{{{NAMESPACE}}}status") == "RESOLVED"
    assert root.findtext(f"{{{NAMESPACE}}}history/{{{NAMESPACE}}}by") == SUPERVISOR
    assert (
        root.findtext(f"{{{NAMESPACE}}}history/{{{NAMESPACE}}}note")
        == "Stock corrected."
    )


def test_backward_review_is_refused_and_the_incident_is_not_deleted(incidents, mongo):
    created = incidents.post(body()).get_json()
    first = incidents.client.post(
        f"/v1/capture-incidents/{created['incident_id']}/reviews",
        json={"status": "RESOLVED"},
        headers=incidents.supervisor,
    )
    backward = incidents.client.post(
        f"/v1/capture-incidents/{created['incident_id']}/reviews",
        json={"status": "ACKNOWLEDGED"},
        headers=incidents.supervisor,
    )
    assert first.status_code == 201
    assert (backward.status_code, backward.get_json()["code"]) == (
        409,
        "invalid_transition",
    )
    collection = mongo.get_default_database(default="engagement").capture_incident
    assert collection.count_documents({"_id": created["incident_id"]}) == 1


def test_roles_and_current_store_assignments_narrow_every_operation(
    incidents, token, owner
):
    created = incidents.post(body(store_id=6), headers=incidents.admin).get_json()
    seller_read = incidents.client.get(
        "/v1/capture-incidents", headers=incidents.seller
    )
    wrong_store = incidents.client.get(
        f"/v1/capture-incidents/{created['incident_id']}",
        headers=incidents.supervisor,
    )
    assert (seller_read.status_code, seller_read.get_json()["code"]) == (
        403,
        "forbidden_role",
    )
    assert (wrong_store.status_code, wrong_store.get_json()["code"]) == (
        403,
        "forbidden_store",
    )

    assigned = incidents.post(body(store_id=8))
    assert assigned.status_code == 201
    owner.execute(
        "DELETE FROM users.store_assignment WHERE user_id = %s::uuid AND store_id = %s",
        (SUPERVISOR, 8),
    )
    try:
        next_request = incidents.client.get(
            "/v1/capture-incidents?store_id=8", headers=incidents.supervisor
        )
        assert (next_request.status_code, next_request.get_json()["code"]) == (
            403,
            "forbidden_store",
        )
    finally:
        owner.execute(
            "INSERT INTO users.store_assignment (user_id, store_id)"
            " VALUES (%s::uuid, %s)",
            (SUPERVISOR, 8),
        )
    admin_read = incidents.client.get(
        f"/v1/capture-incidents/{created['incident_id']}", headers=incidents.admin
    )
    assert admin_read.status_code == 200


def test_lists_summaries_and_xml_requests_follow_the_contract(incidents):
    xml_id = str(uuid.uuid4())
    incidents.track(xml_id)
    operation_id = str(uuid.uuid4())
    payload = f"""<?xml version="1.0" encoding="UTF-8"?>
<CaptureIncidentRequest xmlns="{NAMESPACE}">
  <client_incident_id>{xml_id}</client_incident_id>
  <source_type>CHECK_IN</source_type>
  <source_operation_id>{operation_id}</source_operation_id>
  <source_correlation_id>corr-check-in</source_correlation_id>
  <store_id>8</store_id>
  <refused_status>422</refused_status>
  <refused_code>outside_geofence</refused_code>
  <attempts>1</attempts>
  <occurred_at>1999-04-05T12:30:00Z</occurred_at>
</CaptureIncidentRequest>"""
    response = incidents.client.post(
        "/v1/capture-incidents",
        data=payload,
        content_type="application/xml",
        headers=incidents.supervisor | {"Accept": "application/xml"},
    )
    assert response.status_code == 201
    created = assert_valid_xml(response.data)
    incident_id = created.findtext(f"{{{NAMESPACE}}}incident_id")

    page = incidents.client.get(
        "/v1/capture-incidents?status=OPEN&source_type=CHECK_IN&store_id=8",
        headers=incidents.supervisor | {"Accept": "application/xml"},
    )
    root = assert_valid_xml(page.data)
    assert root.find(f"{{{NAMESPACE}}}CaptureIncident") is not None
    review = incidents.client.post(
        f"/v1/capture-incidents/{incident_id}/reviews",
        data=(
            f'<CaptureIncidentReviewRequest xmlns="{NAMESPACE}">'
            "<status>ACKNOWLEDGED</status>"
            "</CaptureIncidentReviewRequest>"
        ),
        content_type="application/xml",
        headers=incidents.supervisor | {"Accept": "application/xml"},
    )
    assert review.status_code == 201
    assert_valid_xml(review.data)
    summary = incidents.client.get(
        "/v1/capture-incident-summaries?group_by=source_type&store_id=8",
        headers=incidents.supervisor,
    )
    assert summary.status_code == 200
    groups = {row["key"]: row["count"] for row in summary.get_json()["groups"]}
    assert groups["CHECK_IN"] >= 1


def test_future_reports_invalid_queries_and_unknown_stores_are_refused(incidents):
    future = incidents.post(
        body(occurred_at=(datetime.now(UTC) + timedelta(minutes=6)).isoformat())
    )
    unknown = incidents.post(body(store_id=9999), headers=incidents.admin)
    bad_list = incidents.client.get(
        "/v1/capture-incidents?status=NOPE", headers=incidents.supervisor
    )
    bad_summary = incidents.client.get(
        "/v1/capture-incident-summaries?group_by=nope",
        headers=incidents.supervisor,
    )
    assert (future.status_code, future.get_json()["code"]) == (
        422,
        "occurred_in_future",
    )
    assert (unknown.status_code, unknown.get_json()["code"]) == (422, "unknown_store")
    assert (bad_list.status_code, bad_list.get_json()["code"]) == (422, "invalid_query")
    assert (bad_summary.status_code, bad_summary.get_json()["code"]) == (
        422,
        "invalid_query",
    )
