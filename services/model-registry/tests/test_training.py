"""A K-means training in the registry (F22-12): started with stated parameters, run
after the
answer, and registered as a DRAFT version with what the fit produced.

Everything but the clock is real: the seeded database through the kit, Redis for the
session
check, and the registry's own fit. The seed holds 30 customers with 600 net lines, all
inside
a 3650-day window, which is the stable data the repeatability tests fit.
"""

from __future__ import annotations

import json
import random
import threading
import time
import tracemalloc
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import kmeans, rules, store, training

EXAMPLES = Path(__file__).resolve().parents[3] / "contracts/model-registry/examples"
PREFIX = "pytest-train-"
WINDOW = 3650
VOCABULARY = ["CHAMPION", "LOYAL", "POTENTIAL", "AT_RISK", "HIBERNATING", "LOST"]


def request_body(**changes: Any) -> dict[str, Any]:
    body = json.loads((EXAMPLES / "training.request.json").read_text())
    body.update(
        version_name=PREFIX + uuid.uuid4().hex[:12], window_days=WINDOW, seed=77
    )
    parameters = {**body["parameters"], "k": 3}
    parameters.update(changes.pop("parameters", {}))
    body["parameters"] = parameters
    body.update(changes)
    return body


@pytest.fixture(autouse=True)
def forget_trainings(owner: psycopg.Connection, app: Flask) -> Iterator[None]:
    """Trainings and the versions they register are immutable, so the owner removes the
    ones a test made by turning the triggers off for the cleanup only. The fit threads
    are
    joined first, so none writes after the cleanup."""
    yield
    for thread in app.extensions["training_threads"]:
        thread.join(timeout=60)
    owner.execute("SET session_replication_role = replica")
    owner.execute(
        "DELETE FROM models.model_training_cluster WHERE training_id IN"
        " (SELECT training_id FROM models.model_training WHERE version_name LIKE %s)",
        (PREFIX + "%",),
    )
    owner.execute(
        "DELETE FROM models.model_training_feature WHERE training_id IN"
        " (SELECT training_id FROM models.model_training WHERE version_name LIKE %s)",
        (PREFIX + "%",),
    )
    owner.execute(
        "DELETE FROM models.model_training WHERE version_name LIKE %s", (PREFIX + "%",)
    )
    owner.execute(
        "DELETE FROM models.model_version_feature WHERE version_id IN"
        " (SELECT version_id FROM models.model_version WHERE name LIKE %s)",
        (PREFIX + "%",),
    )
    owner.execute(
        "DELETE FROM models.model_version WHERE name LIKE %s", (PREFIX + "%",)
    )
    owner.execute("SET session_replication_role = origin")


def start(client: FlaskClient, token: Any, body: dict[str, Any] | None = None) -> Any:
    return client.post(
        "/v1/model-trainings",
        json=body if body is not None else request_body(),
        headers=token("ADMIN"),
    )


def finished(client: FlaskClient, token: Any, training_id: int) -> dict[str, Any]:
    """Poll the training until it is no longer RUNNING, as a client does."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        body = client.get(
            f"/v1/model-trainings/{training_id}", headers=token("ANALYST")
        ).get_json()
        if body["state"] != "RUNNING":
            return body
        time.sleep(0.05)
    pytest.fail("the training did not finish")


def complete(client: FlaskClient, token: Any, **changes: Any) -> dict[str, Any]:
    response = start(client, token, request_body(**changes))
    assert response.status_code == 201, response.get_json()
    done = finished(client, token, response.get_json()["training_id"])
    assert done["state"] == "COMPLETED", done
    return done


def version_of(client: FlaskClient, token: Any, done: dict[str, Any]) -> dict[str, Any]:
    response = client.get(f"/v1/model-versions/{done['version_id']}", headers=token())
    assert response.status_code == 200
    return response.get_json()


# ---------- launching and polling ----------


def test_the_request_returns_at_once_with_a_run_to_poll(
    client: FlaskClient,
    token: Any,
    monkeypatch: pytest.MonkeyPatch,
    role_subjects: dict[str, str],
) -> None:
    """The fit is held open: the answer comes anyway, the run reads RUNNING until it is
    released, and a second one is refused while it runs."""
    gate, reached = threading.Event(), threading.Event()
    original = training.fit_training

    def slow(*arguments: Any, **keywords: Any) -> Any:
        reached.set()
        assert gate.wait(timeout=30)
        return original(*arguments, **keywords)

    monkeypatch.setattr(training, "fit_training", slow)
    began = time.monotonic()
    response = start(client, token)
    assert time.monotonic() - began < 5
    assert response.status_code == 201, response.get_json()
    created = response.get_json()
    assert (
        response.headers["Location"] == f"/v1/model-trainings/{created['training_id']}"
    )
    assert created["state"] == "RUNNING" and created["method"] == "KMEANS"
    assert created["seed"] == 77 and created["started_by"] == role_subjects["ADMIN"]
    assert created["window_days"] == WINDOW
    assert created["parameters"]["features"] == ["R", "F", "M"]
    assert (
        created["parameters"]["k"] == 3 and created["parameters"]["rule_set_id"] is None
    )
    for empty in ("dataset", "metrics", "iterations", "converged", "version_id"):
        assert created[empty] is None
    assert created["finished_at"] is None and created["failure_code"] is None

    assert reached.wait(timeout=30)
    polled = client.get(
        f"/v1/model-trainings/{created['training_id']}", headers=token()
    )
    assert polled.get_json()["state"] == "RUNNING"
    refused = start(client, token)
    assert refused.status_code == 409
    assert refused.get_json()["code"] == "training_in_progress"

    gate.set()
    done = finished(client, token, created["training_id"])
    assert done["state"] == "COMPLETED" and done["finished_at"] is not None
    # The slot is free again.
    assert start(client, token).status_code == 201


def test_a_completed_training_registers_a_draft_and_publishes_nothing(
    client: FlaskClient,
    token: Any,
    owner: psycopg.Connection,
    role_subjects: dict[str, str],
) -> None:
    before = owner.execute(
        "SELECT count(*), max(publication_id) FROM models.model_publication"
    ).fetchone()
    published_before = client.get(
        "/v1/model-versions?state=PUBLISHED", headers=token()
    ).get_json()

    done = complete(client, token)
    version = version_of(client, token, done)

    assert version["state"] == "DRAFT" and version["source"] == "TRAINING"
    assert version["training_id"] == done["training_id"]
    assert version["name"] == done["version_name"] and version["method"] == "KMEANS"
    assert version["owner_id"] == role_subjects["ADMIN"]
    assert version["seed"] == 77 and version["window_days"] == WINDOW
    assert version["published_at"] is None
    assert version["parameters"] == done["parameters"]
    # What the fit left, on the training and on the version it registered.
    assert done["iterations"] >= 1 and isinstance(done["converged"], bool)
    assert done["metrics"] == version["metrics"]
    assert done["metrics"]["inertia"] > 0 and -1 <= done["metrics"]["silhouette"] <= 1
    assert done["metrics"]["stability"] is None
    assert done["dataset"] == version["dataset"]
    assert len(done["dataset"]["fingerprint"]) == 64
    assert done["dataset"]["origin"] == "SEEDED" and done["dataset"]["row_count"] == 600

    centroids = version["frozen_scoring"]["centroids"]
    assert [c["cluster_index"] for c in centroids] == [0, 1, 2]
    assert {c["label_code"] for c in centroids} <= set(VOCABULARY)
    assert (
        centroids[0]["label_code"] == "CHAMPION"
        and centroids[-1]["label_code"] == "LOST"
    )
    sums = [c["r"] + c["f"] + c["m"] for c in centroids]
    assert sums == sorted(sums, reverse=True)
    assert version["frozen_scoring"]["quintile_boundaries"] is None
    bounds = version["frozen_scoring"]["normalization_bounds"]
    assert (
        bounds["r_min"] == 0
        and bounds["f_min"] >= 1
        and bounds["m_max"] > bounds["m_min"]
    )
    reference = version["reference_distribution"]
    assert reference["sample_size"] == 30
    assert [f["feature"] for f in reference["features"]] == ["R", "F", "M"]
    for feature in reference["features"]:
        assert len(feature["proportions"]) == len(feature["bin_edges"]) - 1
        assert sum(feature["proportions"]) == pytest.approx(1.0, abs=1e-9)
    assert sum(label["share"] for label in reference["labels"]) == pytest.approx(1.0)
    # The registry's own rules accept what it built.
    assert rules.invalid_details({**version, "dataset": version["dataset"]}) == []

    sizes = owner.execute(
        "SELECT cluster_index, customer_count FROM models.model_training_cluster"
        " WHERE training_id = %s ORDER BY cluster_index",
        (done["training_id"],),
    ).fetchall()
    assert [index for index, _ in sizes] == [0, 1, 2]
    assert sum(count for _, count in sizes) == 30 and all(
        count >= 1 for _, count in sizes
    )
    shares: dict[str, int] = {}
    for centroid, (_, count) in zip(centroids, sizes, strict=True):
        shares[centroid["label_code"]] = shares.get(centroid["label_code"], 0) + count
    assert {
        label["label_code"]: round(label["share"] * 30) for label in reference["labels"]
    } == shares

    assert (
        owner.execute(
            "SELECT count(*), max(publication_id) FROM models.model_publication"
        ).fetchone()
        == before
    )
    assert (
        client.get("/v1/model-versions?state=PUBLISHED", headers=token()).get_json()
        == published_before
    )


def test_the_same_data_and_configuration_give_the_same_fit(
    client: FlaskClient, token: Any
) -> None:
    first = complete(client, token)
    second = complete(client, token)
    assert first["training_id"] != second["training_id"]
    a, b = version_of(client, token, first), version_of(client, token, second)
    assert a["version_id"] != b["version_id"]
    for field in ("frozen_scoring", "metrics", "dataset", "parameters", "seed"):
        assert a[field] == b[field], field
    for field in ("reference_distribution",):
        left, right = dict(a[field]), dict(b[field])
        left.pop("captured_at"), right.pop("captured_at")
        assert left == right
    for field in ("iterations", "converged", "dataset", "metrics"):
        assert first[field] == second[field], field


def test_the_assignments_repeat_too(app: Flask) -> None:
    """The fit is a function of the data and the configuration: customers in another
    order
    reach the same partition (RN-58), and so does a second run."""
    db = app.extensions["mosaiq_kit"].db
    with db.connection() as connection:
        data = training.read_window(connection, WINDOW)
    request = {**request_body(), "k": 3}
    configured = {
        "version_name": "n/a",
        "window_days": WINDOW,
        "seed": 77,
        "k": 3,
        "max_iterations": 100,
        "tolerance": 0.0001,
        "features": ["R", "F", "M"],
    }
    del request
    runs = []
    for sales in (data["sales"], list(reversed(data["sales"]))):
        prepared = kmeans.prepare(sales)
        fitted = kmeans.fit(
            prepared, k=3, seed=77, max_iterations=100, tolerance=0.0001
        )
        runs.append(
            (fitted.customer_ids, fitted.assignments, fitted.inertia, fitted.silhouette)
        )
    assert runs[0] == runs[1]
    body, _, _ = training.fit_training({**configured, "started_by": "x"}, data)
    again, _, _ = training.fit_training({**configured, "started_by": "x"}, data)
    assert body["frozen_scoring"] == again["frozen_scoring"]
    assert body["metrics"] == again["metrics"]


def test_a_seed_left_out_is_chosen_and_recorded_so_the_run_can_be_repeated(
    client: FlaskClient, token: Any
) -> None:
    body = request_body()
    del body["seed"]
    response = start(client, token, body)
    assert response.status_code == 201
    chosen = response.get_json()["seed"]
    assert isinstance(chosen, int) and chosen >= 0
    first = finished(client, token, response.get_json()["training_id"])
    assert first["state"] == "COMPLETED"
    repeat = complete(client, token, seed=chosen)
    assert repeat["metrics"] == first["metrics"]
    assert repeat["iterations"] == first["iterations"]
    assert repeat["dataset"] == first["dataset"]


def test_a_variable_set_is_fitted_and_recorded(client: FlaskClient, token: Any) -> None:
    full = version_of(client, token, complete(client, token))
    pair = version_of(
        client, token, complete(client, token, parameters={"features": ["R", "F"]})
    )
    assert pair["parameters"]["features"] == ["R", "F"]
    assert [f["feature"] for f in pair["reference_distribution"]["features"]] == [
        "R",
        "F",
    ]
    # Every centroid is still described by all three coordinates.
    assert all({"r", "f", "m"} <= set(c) for c in pair["frozen_scoring"]["centroids"])
    assert pair["metrics"] != full["metrics"]
    assert pair["dataset"] == full["dataset"]


def test_the_window_is_the_trainings_own(client: FlaskClient, token: Any) -> None:
    wide = complete(client, token)
    narrow = complete(client, token, window_days=45)
    assert narrow["window_days"] == 45
    assert narrow["dataset"]["row_count"] < wide["dataset"]["row_count"]
    assert narrow["dataset"]["fingerprint"] != wide["dataset"]["fingerprint"]


# ---------- what it reads ----------


def test_the_registry_reads_the_customers_the_web_reads(
    app: Flask, owner: psycopg.Connection
) -> None:
    """Same net purchases, same frequency, recency and spend per customer as the web's
    own
    `net_sale` (ADR-0062): the fit starts from the data the web's run starts from."""
    db = app.extensions["mosaiq_kit"].db
    with db.connection() as connection:
        data = training.read_window(connection, 180)
    web = {
        str(customer): (last, frequency, total)
        for customer, last, frequency, total in owner.execute(
            "SELECT customer_id, max(occurred_at), count(*), sum(net_total)"
            " FROM net_sale WHERE occurred_at >= now() - make_interval(days => 180)"
            " AND occurred_at <= now() GROUP BY customer_id"
        ).fetchall()
    }
    ours = {
        s.customer_id: (s.last_purchase_at, s.frequency, s.monetary)
        for s in data["sales"]
    }
    assert ours == web
    assert data["vocabulary"] == VOCABULARY


def test_the_fingerprint_names_the_data_and_changes_with_it(
    app: Flask, owner: psycopg.Connection
) -> None:
    db = app.extensions["mosaiq_kit"].db
    with db.connection() as connection:
        one = training.read_window(connection, WINDOW)["dataset"]
        two = training.read_window(connection, WINDOW)["dataset"]
        other = training.read_window(connection, 30)["dataset"]
    assert one == two and one["row_count"] == 600
    assert other["fingerprint"] != one["fingerprint"]


def test_the_service_role_reads_sales_and_labels_only_through_their_views(
    service_db: psycopg.Connection,
) -> None:
    assert service_db.execute(
        "SELECT count(*) FROM api_segments.label_v1"
    ).fetchone() == (6,)
    assert (
        service_db.execute(
            "SELECT count(*) FROM api_sales.net_purchase_line_v1"
        ).fetchone()[0]
        > 0
    )
    for statement in (
        "SELECT 1 FROM public.segment_label LIMIT 1",
        "SELECT 1 FROM public.transaction LIMIT 1",
        "SELECT 1 FROM public.transaction_line LIMIT 1",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            service_db.execute(statement)


# ---------- refusals and failures ----------


def test_a_name_in_use_is_a_409(client: FlaskClient, token: Any) -> None:
    done = complete(client, token)
    again = start(client, token, request_body(version_name=done["version_name"]))
    assert again.status_code == 409
    assert again.get_json()["code"] == "model_version_name_taken"
    seeded = start(client, token, request_body(version_name="web-rfm-rules-180d"))
    assert seeded.get_json()["code"] == "model_version_name_taken"


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"parameters": {"rule_set_id": 3}}, "parameters"),
        ({"parameters": {"k": None}}, "parameters"),
        ({"parameters": {"tolerance": None}}, "parameters"),
        ({"parameters": {"normalization": "NONE"}}, "parameters.normalization"),
    ],
)
def test_parameters_that_are_not_kmeans_are_a_422(
    client: FlaskClient, token: Any, changes: dict[str, Any], field: str
) -> None:
    response = start(client, token, request_body(**changes))
    assert response.status_code == 422, response.get_json()
    body = response.get_json()
    assert body["code"] == "invalid_body"
    assert body["details"] == [{"field": field, "code": "method_mismatch"}]


@pytest.mark.parametrize(
    "changes",
    [
        {"parameters": {"k": 1}},
        {"parameters": {"k": 21}},
        {"parameters": {"tolerance": 0}},
        {"parameters": {"max_iterations": 0}},
        {"parameters": {"features": []}},
        {"method": "RFM_RULES"},
        {"window_days": 0},
    ],
)
def test_a_request_outside_the_contract_is_a_422(
    client: FlaskClient, token: Any, changes: dict[str, Any]
) -> None:
    response = start(client, token, request_body(**changes))
    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_body"


def test_a_window_with_fewer_customers_than_clusters_ends_failed(
    client: FlaskClient, token: Any, owner: psycopg.Connection
) -> None:
    response = start(client, token, request_body(window_days=1, parameters={"k": 5}))
    assert response.status_code == 201
    done = finished(client, token, response.get_json()["training_id"])
    assert done["state"] == "FAILED" and done["failure_code"] == "too_few_customers"
    for empty in ("dataset", "metrics", "iterations", "converged", "version_id"):
        assert done[empty] is None
    assert done["finished_at"] is not None
    assert owner.execute(
        "SELECT count(*) FROM models.model_version WHERE name = %s",
        (done["version_name"],),
    ).fetchone() == (0,)
    # Nothing is left running, so the next training starts.
    assert complete(client, token)["state"] == "COMPLETED"


def test_a_training_past_its_deadline_ends_timed_out(
    app: Flask, owner: psycopg.Connection
) -> None:
    db = app.extensions["mosaiq_kit"].db
    with db.transaction() as connection:
        training_id = store.insert_training(
            connection, request_body(), 77, _admin(owner)
        )
    training.run(db, training_id, 0)
    with db.connection() as connection:
        row = store.get_training(connection, training_id)
    assert row["state"] == "FAILED" and row["failure_code"] == "timed_out"
    assert row["version_id"] is None


def _admin(owner: psycopg.Connection) -> str:
    row = owner.execute(
        "SELECT min(u.user_id::text) FROM public.app_user AS u"
        " JOIN public.role AS r USING (role_id) WHERE r.code = 'ADMIN' AND u.is_active"
    ).fetchone()
    return row[0]


def test_a_training_that_died_does_not_hold_the_slot_for_ever(
    client: FlaskClient, token: Any, owner: psycopg.Connection
) -> None:
    owner.execute("SET session_replication_role = replica")
    (orphan,) = owner.execute(
        "INSERT INTO models.model_training (method, version_name, window_days, seed, k,"
        " max_iterations, tolerance, normalization, started_by, started_at)"
        " VALUES ('KMEANS', %s, 30, 1, 3, 100, 0.0001, 'MIN_MAX', %s,"
        " now() - interval '2 hours') RETURNING training_id",
        (PREFIX + "orphan", _admin(owner)),
    ).fetchone()
    owner.execute("SET session_replication_role = origin")
    response = start(client, token)
    assert response.status_code == 201, response.get_json()
    closed = client.get(f"/v1/model-trainings/{orphan}", headers=token()).get_json()
    assert closed["state"] == "FAILED" and closed["failure_code"] == "timed_out"
    assert (
        finished(client, token, response.get_json()["training_id"])["state"]
        == "COMPLETED"
    )


def test_a_restart_closes_the_trainings_it_interrupted(
    app: Flask, owner: psycopg.Connection
) -> None:
    db = app.extensions["mosaiq_kit"].db
    with db.transaction() as connection:
        running = store.insert_training(connection, request_body(), 5, _admin(owner))
    with db.transaction() as connection:
        assert store.fail_running_trainings(connection, "interrupted") == 1
    with db.connection() as connection:
        row = store.get_training(connection, running)
    assert (row["state"], row["failure_code"]) == ("FAILED", "interrupted")


# ---------- reading ----------


def test_trainings_are_listed_newest_first_and_filtered(
    client: FlaskClient, token: Any
) -> None:
    done = complete(client, token)
    failed = start(client, token, request_body(window_days=1, parameters={"k": 5}))
    finished(client, token, failed.get_json()["training_id"])
    page = client.get("/v1/model-trainings", headers=token("AUDITOR")).get_json()
    ids = [t["training_id"] for t in page["trainings"]]
    assert ids == sorted(ids, reverse=True) and page["page_info"]["total"] >= 2
    only = client.get("/v1/model-trainings?state=FAILED", headers=token()).get_json()
    assert {t["state"] for t in only["trainings"]} == {"FAILED"}
    completed = client.get(
        "/v1/model-trainings?state=COMPLETED", headers=token()
    ).get_json()
    assert done["training_id"] in [t["training_id"] for t in completed["trainings"]]
    assert (
        client.get("/v1/model-trainings?state=NOPE", headers=token()).status_code == 422
    )
    assert (
        client.get("/v1/model-trainings?method=RFM_RULES", headers=token()).get_json()[
            "trainings"
        ]
        == []
    )


def test_an_unknown_training_is_a_404(client: FlaskClient, token: Any) -> None:
    response = client.get("/v1/model-trainings/999999999", headers=token())
    assert response.status_code == 404
    assert response.get_json()["code"] == "model_training_not_found"


def test_roles_follow_the_contract(client: FlaskClient, token: Any) -> None:
    for role in ("ANALYST", "MARKETING", "AUDITOR", "STORE_MANAGER", "CUSTOMER"):
        refused = client.post(
            "/v1/model-trainings", json=request_body(), headers=token(role)
        )
        assert refused.status_code == 403, role
    for role in ("ADMIN", "ANALYST", "MARKETING", "AUDITOR"):
        assert client.get("/v1/model-trainings", headers=token(role)).status_code == 200
    for role in ("STORE_MANAGER", "CUSTOMER", "INVENTORY_PLANNER"):
        assert client.get("/v1/model-trainings", headers=token(role)).status_code == 403


def test_a_training_reads_the_same_in_xml(client: FlaskClient, token: Any) -> None:
    done = complete(client, token)
    response = client.get(
        f"/v1/model-trainings/{done['training_id']}",
        headers={**token(), "Accept": "application/xml"},
    )
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "<ModelTraining" in text and "<state>COMPLETED</state>" in text
    assert f"<training_id>{done['training_id']}</training_id>" in text


def test_a_training_can_be_started_in_xml(client: FlaskClient, token: Any) -> None:
    xml = (EXAMPLES / "training.request.xml").read_text()
    name = PREFIX + uuid.uuid4().hex[:12]
    xml = xml.replace("kmeans-k5-trial-2", name).replace(
        "<window_days>180</window_days>", f"<window_days>{WINDOW}</window_days>"
    )
    response = client.post(
        "/v1/model-trainings",
        data=xml,
        headers={
            **token(),
            "Content-Type": "application/xml",
            "Accept": "application/xml",
        },
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    assert b"<state>RUNNING</state>" in response.data


# ---------- the tables refuse what the service would not do ----------


def test_a_finished_training_cannot_be_changed_or_deleted_by_anyone(
    client: FlaskClient,
    token: Any,
    owner: psycopg.Connection,
    service_db: psycopg.Connection,
) -> None:
    done = complete(client, token)
    tid = done["training_id"]
    for statement in (
        "UPDATE models.model_training SET seed = 1 WHERE training_id = %s",
        "UPDATE models.model_training SET state = 'RUNNING', finished_at = NULL,"
        " version_id = NULL, dataset_fingerprint = NULL, dataset_origin = NULL,"
        " dataset_row_count = NULL, inertia = NULL, silhouette = NULL, iterations ="
        " NULL,"
        " converged = NULL, stopped_on = NULL, final_shift = NULL,"
        " empty_cluster_events = NULL WHERE training_id = %s",
        "UPDATE models.model_training SET inertia = 0 WHERE training_id = %s",
        "DELETE FROM models.model_training WHERE training_id = %s",
        "UPDATE models.model_training_cluster SET customer_count = 99 WHERE"
        " training_id = %s",
        "DELETE FROM models.model_training_cluster WHERE training_id = %s",
        "DELETE FROM models.model_training_feature WHERE training_id = %s",
    ):
        with pytest.raises(psycopg.errors.Error):
            owner.execute(statement, (tid,))
    for statement in (
        "UPDATE models.model_training SET seed = 1 WHERE training_id = %s",
        "DELETE FROM models.model_training WHERE training_id = %s",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            service_db.execute(statement, (tid,))


def test_only_one_training_runs_at_a_time_and_the_table_says_so(
    owner: psycopg.Connection,
) -> None:
    owner.execute("SET session_replication_role = origin")
    admin = _admin(owner)
    insert = (
        "INSERT INTO models.model_training (method, version_name, window_days, seed, k,"
        " max_iterations, tolerance, normalization, started_by)"
        " VALUES ('KMEANS', %s, 30, 1, 3, 100, 0.0001, 'MIN_MAX', %s)"
    )
    owner.execute(insert, (PREFIX + "first", admin))
    with pytest.raises(psycopg.errors.UniqueViolation) as error:
        owner.execute(insert, (PREFIX + "second", admin))
    assert error.value.diag.constraint_name == "model_training_one_running"


def test_a_training_row_cannot_claim_a_result_it_did_not_have(
    owner: psycopg.Connection,
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute(
            "INSERT INTO models.model_training (method, version_name, window_days,"
            " seed,"
            " k, max_iterations, tolerance, normalization, started_by, inertia)"
            " VALUES ('KMEANS', %s, 30, 1, 3, 100, 0.0001, 'MIN_MAX', %s, 1.5)",
            (PREFIX + "claims", _admin(owner)),
        )


# ---------- memory and time on the evaluation dataset ----------


def evaluation_sales(n: int, seed: int) -> list[kmeans.Sales]:
    """A set the size of F23-01's evaluation dataset in the window (3,979 customers),
    drawn as that dataset is: a few spenders far above the rest, mostly one or two
    purchases."""
    rng = random.Random(seed)
    reference = datetime(2026, 10, 31, tzinfo=UTC)
    rows = []
    for index in range(n):
        spender = rng.random() < 0.012
        frequency = rng.choice([1, 1, 2, 2, 3, 5, 8, 15])
        cents = (
            rng.randrange(500, 900000)
            if not spender
            else rng.randrange(10**6, 5 * 10**7)
        )
        rows.append(
            kmeans.Sales(
                f"{index:08d}-0000-4000-8000-{index:012d}",
                reference - timedelta(hours=rng.randrange(0, 24 * 180)),
                frequency,
                Decimal(cents) / 100,
            )
        )
    return rows


def test_a_training_on_the_evaluation_dataset_stays_far_inside_its_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service has 128 MB for the whole process (ADR-0059). A fit over the 3,979
    customers of F23-01's evaluation dataset, silhouette sampled, allocates a few
    megabytes on top of what the process already holds, and finishes in seconds."""
    sales = evaluation_sales(3979, 20261031)
    data = {
        "sales": sales,
        "dataset": {"fingerprint": "0" * 64, "origin": "SEEDED", "row_count": 51954},
        "vocabulary": VOCABULARY,
    }
    configured = {
        "version_name": "n/a",
        "window_days": 180,
        "seed": 11,
        "k": 6,
        "max_iterations": 100,
        "tolerance": 0.0001,
        "features": ["R", "F", "M"],
        "started_by": "x",
    }
    tracemalloc.start()
    began = time.monotonic()
    body, fitted, _ = training.fit_training(configured, data)
    elapsed = time.monotonic() - began
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert (
        fitted.silhouette_sample == 1000 and body["metrics"]["silhouette"] is not None
    )
    assert peak < 24 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB"
    assert elapsed < 120
