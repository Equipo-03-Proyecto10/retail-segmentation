"""The segmentation dashboard page (F12-01).

`build_dashboard` is built and tested in web/services/segmentation_dashboard.py.
What is covered here is the page: who may open it, that the chart data is
embedded server-rendered JSON and not fetched, that the page never names a
method or a cluster, that a bad or unknown run is refused properly, and that
the empty state says so instead of an empty page. The database is a mock and
`build_dashboard` is replaced, so each test states exactly the dashboard the
page was given.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from html.parser import HTMLParser
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.segments import SegmentationRun
from web.services.segmentation_dashboard import (
    Dashboard,
    FlowLink,
    HeatmapCell,
    LabelRevenue,
    MigrationFlow,
    NoRuns,
    SegmentSize,
)

_URL = "/segmentation-dashboard/"
_WHEN = datetime(2026, 9, 28, 9, 30, tzinfo=UTC)


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


def _run(run_id: int, method: str = "RFM_RULES", **overrides) -> SegmentationRun:
    defaults = dict(
        run_id=run_id,
        method=method,
        window_days=180,
        parameters={},
        customer_count=3,
        executed_by=None,
        executed_by_name=None,
        run_at=_WHEN,
    )
    return SegmentationRun(**{**defaults, **overrides})


def _heatmap() -> tuple[HeatmapCell, ...]:
    return tuple(
        HeatmapCell(r, f, 1 if (r, f) == (5, 5) else 0)
        for r in range(1, 6)
        for f in range(1, 6)
    )


def _dashboard(**overrides) -> Dashboard:
    defaults = dict(
        run=_run(31),
        previous_run=_run(30),
        sizes=(
            SegmentSize("CHAMPION", 2),
            SegmentSize("LOST", 1),
            SegmentSize(None, 0),
        ),
        revenue=(
            LabelRevenue("CHAMPION", Decimal("900.00")),
            LabelRevenue("LOST", Decimal("10.00")),
        ),
        heatmap=_heatmap(),
        migration=MigrationFlow(
            links=(FlowLink("CHAMPION", "CHAMPION", 2),),
            unassigned_label="Unassigned",
            new_to_population=1,
            left_the_population=0,
        ),
        revenue_window_start=_WHEN,
        revenue_window_end=_WHEN,
    )
    return Dashboard(**{**defaults, **overrides})


def _open(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    dashboard: Dashboard | None = None,
    *,
    role: str = "ANALYST",
    url: str = _URL,
    side_effect=None,
    runs: list | None = None,
):
    fake = Mock(return_value=dashboard if dashboard is not None else _dashboard())
    if side_effect is not None:
        fake = Mock(side_effect=side_effect)
    monkeypatch.setattr("web.routes.segmentation_dashboard.build_dashboard", fake)
    monkeypatch.setattr(
        "web.routes.segmentation_dashboard.list_runs",
        Mock(return_value=(runs if runs is not None else [_run(31), _run(30)], 2)),
    )
    monkeypatch.setattr(
        "web.routes.segmentation_dashboard.get_run",
        lambda _c, run_id: _run(run_id),
    )
    client = app.test_client()
    _sign_in(client, role)
    return client.get(url), fake


def _body(response) -> str:
    return response.get_data(as_text=True)


# ---------- who may open it ----------


@pytest.mark.parametrize("role_code", ["INVENTORY_PLANNER", "CUSTOMER"])
def test_a_profile_without_segment_read_is_refused(app, monkeypatch, role_code) -> None:
    response, fake = _open(app, monkeypatch, role=role_code)

    assert response.status_code == 403
    fake.assert_not_called()


@pytest.mark.parametrize(
    "role_code", ["ADMIN", "ANALYST", "MARKETING", "AUDITOR", "STORE_MANAGER"]
)
def test_the_roles_that_read_segments_may_open_it(app, monkeypatch, role_code) -> None:
    response, _ = _open(app, monkeypatch, role=role_code)

    assert response.status_code == 200


def test_signed_out_it_sends_you_to_sign_in(app) -> None:
    response = app.test_client().get(_URL)

    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_the_page_only_reads(app, monkeypatch) -> None:
    _open(app, monkeypatch)
    client = app.test_client()
    _sign_in(client)

    assert client.post(_URL).status_code == 405


# ---------- the chart data is embedded, not fetched ----------


def test_the_chart_data_is_embedded_as_inert_json_not_a_script(
    app, monkeypatch
) -> None:
    body = _body(_open(app, monkeypatch)[0])

    match = re.search(
        r'<script type="application/json" id="mq-dashboard-data">(.*?)</script>',
        body,
        re.DOTALL,
    )
    assert match is not None
    payload = json.loads(match.group(1))
    assert payload["run"]["run_id"] == 31
    assert {p["name"]: p["y"] for p in payload["sizes"]} == {
        "CHAMPION": 2,
        "LOST": 1,
        "Unassigned": 0,
    }
    assert {p["name"]: p["y"] for p in payload["revenue"]} == {
        "CHAMPION": 900.0,
        "LOST": 10.0,
    }
    assert len(payload["heatmap"]) == 25
    assert payload["migration"]["data"] == [
        ["CHAMPION (before)", "CHAMPION (after)", 2]
    ]


def test_highcharts_and_the_page_script_are_loaded_same_origin_only(
    app, monkeypatch
) -> None:
    body = _body(_open(app, monkeypatch)[0])

    assert 'src="/static/vendor/highcharts/highcharts.js"' in body
    assert 'src="/static/vendor/highcharts/highcharts-more.js"' in body
    assert 'src="/static/vendor/highcharts/modules/heatmap.js"' in body
    assert 'src="/static/vendor/highcharts/modules/sankey.js"' in body
    assert 'src="/static/css/mosaiq/charts/mosaiq-highcharts-theme.js"' in body
    assert 'src="/static/js/segmentation-dashboard.js"' in body
    assert "unpkg.com" not in body
    assert "cdn." not in body


def test_no_other_inline_script_carries_executable_code(app, monkeypatch) -> None:
    """Only the inert application/json block may lack a src=; every other
    <script> tag must be same-origin, or the page would depend on
    'unsafe-inline', which application pages do not have."""
    body = _body(_open(app, monkeypatch)[0])

    for match in re.finditer(r"<script([^>]*)>", body, re.IGNORECASE):
        attrs = match.group(1)
        if 'type="application/json"' in attrs:
            continue
        assert 'src="/static/' in attrs, attrs


class _InlineHandlers(HTMLParser):
    """Every on* attribute on any element, found the way a browser tokenises
    tags rather than with a regex."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.found += [f"<{tag} {name}>" for name, _ in attrs if name.startswith("on")]


def test_no_element_carries_an_inline_event_handler(app, monkeypatch) -> None:
    """An onchange= or onclick= attribute is inline script too: the instance's
    same-origin script-src refuses it, so the handler silently never runs."""
    audit = _InlineHandlers()
    audit.feed(_body(_open(app, monkeypatch)[0]))

    assert audit.found == []


# ---------- which run, and refusing a bad one ----------


def test_with_no_choice_the_newest_run_is_used(app, monkeypatch) -> None:
    _, fake = _open(app, monkeypatch)

    assert fake.call_args.args[1] is None


def test_a_chosen_run_id_reaches_the_service(app, monkeypatch) -> None:
    _, fake = _open(app, monkeypatch, url=_URL + "?run=30")

    assert fake.call_args.args[1] == 30


@pytest.mark.parametrize("raw", ["abc", "1.5", "-1", ""])
def test_a_run_choice_that_is_not_a_whole_number_is_a_400(
    app, monkeypatch, raw
) -> None:
    response, fake = _open(app, monkeypatch, url=_URL + f"?run={raw}")

    if raw == "":
        assert response.status_code == 200
        fake.assert_called_once()
    else:
        assert response.status_code == 400
        fake.assert_not_called()


def test_an_unknown_run_id_is_a_404(app, monkeypatch) -> None:
    monkeypatch.setattr(
        "web.routes.segmentation_dashboard.get_run", lambda _c, _id: None
    )
    fake = Mock()
    monkeypatch.setattr("web.routes.segmentation_dashboard.build_dashboard", fake)
    monkeypatch.setattr(
        "web.routes.segmentation_dashboard.list_runs", Mock(return_value=([], 0))
    )
    client = app.test_client()
    _sign_in(client)

    response = client.get(_URL + "?run=999")

    assert response.status_code == 404
    fake.assert_not_called()


def test_the_run_picker_offers_the_newest_runs(app, monkeypatch) -> None:
    body = _body(_open(app, monkeypatch)[0])

    assert "#31" in body and "#30" in body


def test_a_run_older_than_the_option_list_still_appears_selected(
    app, monkeypatch
) -> None:
    body = _body(
        _open(
            app,
            monkeypatch,
            _dashboard(run=_run(5)),
            runs=[_run(31), _run(30)],
            url=_URL + "?run=5",
        )[0]
    )

    assert "#5" in body and 'value="5" selected' in body.replace("  ", " ")


# ---------- the empty state ----------


def test_with_no_runs_at_all_it_says_so(app, monkeypatch) -> None:
    response, fake = _open(app, monkeypatch, side_effect=NoRuns)
    body = _body(response)

    assert response.status_code == 200
    assert "No segmentation run yet" in body
    assert "mq-chart" not in body


# ---------- what the page must never do ----------


def test_the_page_never_names_a_method_or_a_cluster(app, monkeypatch) -> None:
    body = _body(_open(app, monkeypatch)[0]).lower()

    for word in ("kmeans", "k-means", "cluster"):
        assert word not in body
    # "method" legitimately appears only inside a run's own recorded kind,
    # e.g. "RFM_RULES", shown as metadata about a run and never branched on.


def test_the_synthetic_badge_shows_when_the_data_is_marked_synthetic(
    app, monkeypatch
) -> None:
    app.config["APP_CONFIG"] = Config(
        secret_key="test",
        environment="testing",
        port=5000,
        log_level="INFO",
        session_cookie_secure=False,
        database_url="unused-by-test",
        data_is_synthetic=True,
    )
    body = _body(_open(app, monkeypatch)[0])

    assert "Synthetic" in body


def test_the_synthetic_badge_is_hidden_when_the_data_is_not_marked_synthetic(
    app, monkeypatch
) -> None:
    app.config["APP_CONFIG"] = Config(
        secret_key="test",
        environment="testing",
        port=5000,
        log_level="INFO",
        session_cookie_secure=False,
        database_url="unused-by-test",
        data_is_synthetic=False,
    )
    body = _body(_open(app, monkeypatch)[0])

    assert "Synthetic" not in body


def test_the_migration_panel_explains_itself_with_no_previous_run(
    app, monkeypatch
) -> None:
    body = _body(
        _open(app, monkeypatch, _dashboard(previous_run=None, migration=None))[0]
    )

    assert "no earlier run" in body.lower()
