"""K-means from the segment-run page (#335).

Before this, /segment-run/ always called RFM_RULES: the form had no method
field, and a forged POST with method=KMEANS still silently ran RFM_RULES.
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.services.cluster_labels import VocabularySizeMismatch
from web.services.kmeans import TooFewCustomers
from web.services.segmentation import RunResult


@pytest.fixture
def app() -> Flask:
    application = create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused-by-test",
        ),
        database_connector=Mock(return_value=MagicMock()),
    )
    application.config["PROPAGATE_EXCEPTIONS"] = False
    return application


def _sign_in(client: FlaskClient, role_code: str) -> None:
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "11111111-1111-1111-1111-000000000001"
        flask_session["role_code"] = role_code
        flask_session["name"] = f"{role_code.title()} user"


# ---------- AC 1: the form offers a method choice ----------


def test_the_form_offers_both_methods(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client, "ADMIN")

    body = client.get("/segment-run/").get_data(as_text=True)

    assert 'value="RFM_RULES"' in body
    assert 'value="KMEANS"' in body
    assert 'name="k"' in body
    assert 'name="seed"' in body


# ---------- AC 2: a KMEANS submission records method, params, executed_by ----------


def test_confirming_a_kmeans_run_calls_run_kmeans_with_its_params(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def _fake_run_kmeans(connection, window_days, params):
        captured["window_days"] = window_days
        captured["params"] = params
        return RunResult(
            window_days=window_days,
            processed=30,
            assigned=28,
            unmatched=2,
            reassigned=28,
            cleared=2,
            seconds=1.23,
            run_id=99,
            method="KMEANS",
        )

    monkeypatch.setattr("web.routes.segment_run.run_kmeans", _fake_run_kmeans)
    client = app.test_client()
    _sign_in(client, "ADMIN")

    response = client.post(
        "/segment-run/",
        data={
            "method": "KMEANS",
            "window": "180",
            "k": "6",
            "seed": "17",
            "max_iterations": "100",
            "tolerance": "0.0001",
            "confirm": "yes",
        },
    )

    assert response.status_code == 200
    assert captured["window_days"] == 180
    assert captured["params"].k == 6
    assert captured["params"].seed == 17
    body = response.get_data(as_text=True)
    assert "KMEANS" in body


def test_a_kmeans_submission_never_silently_runs_rfm_rules(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug as reported: method=KMEANS in the POST must not fall through
    to the RFM_RULES path."""
    rfm_called = []
    monkeypatch.setattr(
        "web.routes.segment_run.run", lambda *a, **k: rfm_called.append(1)
    )
    monkeypatch.setattr(
        "web.routes.segment_run.run_kmeans",
        lambda *a, **k: RunResult(
            window_days=180,
            processed=1,
            assigned=1,
            unmatched=0,
            reassigned=1,
            cleared=0,
            seconds=0.1,
            run_id=1,
            method="KMEANS",
        ),
    )
    client = app.test_client()
    _sign_in(client, "ADMIN")

    client.post(
        "/segment-run/",
        data={
            "method": "KMEANS",
            "window": "180",
            "k": "6",
            "seed": "17",
            "confirm": "yes",
        },
    )

    assert rfm_called == []


# ---------- AC 3: invalid K/params give a readable 400, never a silent run ----------


def test_a_non_numeric_k_is_refused_with_400(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client, "ADMIN")

    response = client.post(
        "/segment-run/",
        data={"method": "KMEANS", "window": "180", "k": "not-a-number", "seed": "1"},
    )

    assert response.status_code == 400


def test_a_k_that_mismatches_the_vocabulary_is_refused_with_400(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_a, **_k):
        raise VocabularySizeMismatch("k must equal 6, the label vocabulary size.")

    monkeypatch.setattr("web.routes.segment_run.run_kmeans", _raise)
    client = app.test_client()
    _sign_in(client, "ADMIN")

    response = client.post(
        "/segment-run/",
        data={
            "method": "KMEANS",
            "window": "180",
            "k": "3",
            "seed": "1",
            "confirm": "yes",
        },
    )

    assert response.status_code == 400
    assert "vocabulary" in response.get_data(as_text=True)


def test_fewer_customers_than_k_is_a_readable_400(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_a, **_k):
        raise TooFewCustomers("Only 4 customers have sales; k=6 needs at least 6.")

    monkeypatch.setattr("web.routes.segment_run.run_kmeans", _raise)
    client = app.test_client()
    _sign_in(client, "ADMIN")

    response = client.post(
        "/segment-run/",
        data={
            "method": "KMEANS",
            "window": "180",
            "k": "6",
            "seed": "1",
            "confirm": "yes",
        },
    )

    assert response.status_code == 400
    assert "Only 4 customers" in response.get_data(as_text=True)


def test_missing_k_is_refused_not_silently_defaulted(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client, "ADMIN")

    response = client.post(
        "/segment-run/",
        data={"method": "KMEANS", "window": "180", "seed": "1"},
    )

    assert response.status_code == 400


@pytest.mark.parametrize("role_code", ["ANALYST", "STORE_MANAGER"])
def test_only_segment_run_execute_reaches_it(app: Flask, role_code: str) -> None:
    client = app.test_client()
    _sign_in(client, role_code)

    assert client.post("/segment-run/", data={"method": "KMEANS"}).status_code == 403
