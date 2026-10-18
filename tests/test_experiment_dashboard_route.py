"""The experiment dashboard page (AN-03).

`build_dashboard` is built and tested in web/services/experiment_dashboard.py.
Covered here is the page: who may open it, that the chart data is embedded
server-rendered JSON and not fetched, that Highcharts comes from this
application, that every chart of a non-observed experiment is marked Synthetic,
that a bad or unknown experiment is refused properly, and that the empty states
say so. The database is a mock and the service is replaced, so each test states
exactly the dashboard the page was given.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from tests.test_experiment_assignment import _experiment
from tests.test_experiment_dashboard import CONTROLLED, CONTROLLED_EXPOSED
from web.app import create_app
from web.config import Config
from web.routes import experiment_dashboard as route
from web.services import experiment_dashboard as service
from web.services import experiment_uplift as uplift
from web.services.experiments import ExperimentNotFound

_URL = "/experiment-dashboard/"
NOW = datetime(2026, 10, 20, tzinfo=UTC)
_DATA = re.compile(
    r'<script type="application/json" id="mq-dashboard-data">(.*?)</script>', re.S
)


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


def _sign_in(client: FlaskClient, role_code: str = "ANALYST") -> None:
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "11111111-1111-1111-1111-000000000001"
        flask_session["role_code"] = role_code
        flask_session["name"] = f"{role_code.title()} user"


def _dashboard(
    origin: str = "OBSERVED", name: str = "Win-back offer", refusal=None
) -> service.ExperimentDashboard:
    experiment = _experiment(assignments=2000, data_origin=origin, name=name)
    arms = service._arm_rates(CONTROLLED, CONTROLLED_EXPOSED)
    comparison = uplift.compare_proportions(1000, 100, 1000, 150)
    shares = tuple(min(0.15, 0.01 * d) for d in range(14))
    return service.ExperimentDashboard(
        experiment,
        arms,
        None if refusal else service.Baseline("control", 61, "Holdout"),
        () if refusal else (service.Difference(62, "Offer", comparison),),
        refusal,
        service.OverTime(14, 14, tuple((arm, shares) for arm in arms)),
        0,
        0,
        NOW,
    )


def _wire(monkeypatch, dashboard=None, options=None, found=True, error=None):
    options = [_experiment(assignments=2000)] if options is None else options
    monkeypatch.setattr(
        route, "list_report_experiments", Mock(return_value=(list(options), 1))
    )
    monkeypatch.setattr(
        route,
        "get_experiment",
        Mock(return_value=options[0] if options and found else None),
    )
    if error is not None:
        monkeypatch.setattr(route.service, "build_dashboard", Mock(side_effect=error))
    else:
        monkeypatch.setattr(
            route.service, "build_dashboard", Mock(return_value=dashboard)
        )


def _get(app, query: str = "", role: str = "ANALYST"):
    client = app.test_client()
    _sign_in(client, role)
    return client.get(_URL + query)


def _embedded(response) -> dict:
    match = _DATA.search(response.get_data(as_text=True))
    assert match, "the chart data is not embedded in the page"
    return json.loads(match.group(1))


# ---------- who may open it ----------


@pytest.mark.parametrize("role", ["ADMIN", "ANALYST", "MARKETING", "AUDITOR"])
def test_a_role_with_campaign_read_opens_it(app, monkeypatch, role: str) -> None:
    _wire(monkeypatch, _dashboard())

    assert _get(app, role=role).status_code == 200


@pytest.mark.parametrize("role", ["STORE_MANAGER", "INVENTORY_PLANNER", "CUSTOMER"])
def test_a_role_without_campaign_read_gets_403_and_nothing_is_read(
    app, monkeypatch, role: str
) -> None:
    _wire(monkeypatch, _dashboard())

    response = _get(app, role=role)

    assert response.status_code == 403
    route.service.build_dashboard.assert_not_called()


def test_an_anonymous_visitor_is_sent_to_sign_in(app) -> None:
    response = app.test_client().get(_URL)

    assert response.status_code == 302 and "/login" in response.location


# ---------- server-rendered, no endpoint, served locally ----------


def test_the_chart_data_is_embedded_in_the_page_and_no_json_endpoint_exists(
    app, monkeypatch
) -> None:
    _wire(monkeypatch, _dashboard())

    response = _get(app)
    data = _embedded(response)

    assert data["rates"]["categories"] == ["Holdout", "Offer"]
    assert data["rates"]["assigned"] == [10.0, 15.0]
    assert data["rates"]["exposed"] == [None, 17.5]
    assert data["difference"]["baseline"]["kind"] == "control"
    assert len(data["over_time"]["series"]) == 2
    rules = [
        r
        for r in app.url_map.iter_rules()
        if r.endpoint.startswith("experiment_dashboard.")
    ]
    assert [(r.rule, sorted(r.methods - {"HEAD", "OPTIONS"})) for r in rules] == [
        ("/experiment-dashboard/", ["GET"])
    ]


def test_highcharts_is_served_by_the_application_not_a_cdn(app, monkeypatch) -> None:
    _wire(monkeypatch, _dashboard())

    html = _get(app).get_data(as_text=True)

    scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
    assert any(s.endswith("vendor/highcharts/highcharts.js") for s in scripts)
    assert all(s.startswith("/static/") for s in scripts)
    assert "<script>" not in html and " onchange=" not in html


def test_a_name_cannot_close_the_data_block_early(app, monkeypatch) -> None:
    _wire(monkeypatch, _dashboard(name="</script><img src=x onerror=alert(1)>"))

    html = _get(app).get_data(as_text=True)

    assert "</script><img" not in html
    assert _embedded(_get(app))["experiment"]["name"].startswith("</script>")


# ---------- Synthetic on every chart ----------


@pytest.mark.parametrize("origin", ["SEEDED", "INJECTED"])
def test_every_chart_of_a_non_observed_experiment_is_marked_synthetic(
    app, monkeypatch, origin: str
) -> None:
    _wire(monkeypatch, _dashboard(origin))

    response = _get(app)
    html = response.get_data(as_text=True)
    data = _embedded(response)

    assert data["experiment"]["synthetic"] is True
    assert data["experiment"]["label"] == "Synthetic"
    charts = re.findall(r'<section class="mq-panel">.*?</section>', html, re.S)
    chart_panels = [c for c in charts if 'class="mq-chart"' in c]
    assert len(chart_panels) == 3
    for panel in chart_panels:
        assert "Synthetic" in panel.split('class="mq-chart"')[0]
        assert 'aria-label="Synthetic.' in panel


def test_an_observed_experiment_carries_no_synthetic_mark(app, monkeypatch) -> None:
    _wire(monkeypatch, _dashboard("OBSERVED"))

    response = _get(app)

    assert "Synthetic" not in response.get_data(as_text=True)
    assert _embedded(response)["experiment"]["synthetic"] is False


# ---------- the baseline is named for what it is ----------


def test_a_reference_arm_is_never_called_an_uplift_over_a_control(
    app, monkeypatch
) -> None:
    dashboard = _dashboard()
    reference = service.Baseline("reference", 71, "First offer")
    dashboard = service.ExperimentDashboard(
        **{**dashboard.__dict__, "baseline": reference}
    )
    _wire(monkeypatch, dashboard)

    html = _get(app).get_data(as_text=True)

    assert "Difference from the reference arm" in html
    assert "no control" in html and "ADR-0028" in html
    assert "Uplift against the control" not in html


def test_a_refused_difference_says_why_and_still_draws_the_rates(
    app, monkeypatch
) -> None:
    _wire(monkeypatch, _dashboard(refusal="Experiment 31 has no assignments."))

    response = _get(app)
    html = response.get_data(as_text=True)

    assert "Experiment 31 has no assignments." in html
    assert 'id="mq-chart-difference"' not in html
    assert 'id="mq-chart-rates"' in html
    assert _embedded(response)["difference"] is None


# ---------- selecting an experiment ----------


def test_a_choice_that_is_not_a_number_is_a_400(app, monkeypatch) -> None:
    _wire(monkeypatch, _dashboard())

    response = _get(app, "?experiment=abc")

    assert response.status_code == 400
    assert "Choose an experiment from the list." in response.get_data(as_text=True)
    route.service.build_dashboard.assert_not_called()


def test_an_experiment_that_does_not_exist_is_a_404(app, monkeypatch) -> None:
    _wire(monkeypatch, _dashboard(), found=False)

    assert _get(app, "?experiment=999").status_code == 404


def test_an_experiment_removed_after_the_check_is_a_404(app, monkeypatch) -> None:
    _wire(monkeypatch, error=ExperimentNotFound(31))

    assert _get(app, "?experiment=31").status_code == 404


def test_the_newest_experiment_with_assignments_is_the_default(
    app, monkeypatch
) -> None:
    options = [
        _experiment(experiment_id=33, assignments=0),
        _experiment(experiment_id=32, assignments=10),
        _experiment(experiment_id=31, assignments=10),
    ]
    _wire(monkeypatch, _dashboard(), options)

    _get(app)

    assert route.service.build_dashboard.call_args.args[1] == 32


def test_with_no_experiment_at_all_the_page_says_so(app, monkeypatch) -> None:
    _wire(monkeypatch, None, options=[])

    response = _get(app)

    assert response.status_code == 200
    assert "No experiment yet" in response.get_data(as_text=True)
    route.service.build_dashboard.assert_not_called()


def test_an_experiment_with_no_assignments_says_so_and_charts_nothing(
    app, monkeypatch
) -> None:
    dashboard = service.ExperimentDashboard(
        **{
            **_dashboard().__dict__,
            "experiment": _experiment(assignments=0),
            "over_time": None,
            "baseline": None,
            "differences": (),
        }
    )
    _wire(monkeypatch, dashboard)

    html = _get(app).get_data(as_text=True)

    assert "No assignments yet" in html
    assert 'class="mq-chart"' not in html
