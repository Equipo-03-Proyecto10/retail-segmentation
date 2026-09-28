"""The model comparison page (F9-04).

The comparison itself is built and tested in web/services/model_comparison.py. What
is covered here is the page: who may open it, which two runs it compares, what it
says when there is nothing to compare, and that the kind of a run is only ever
shown and used to offer one run of each kind, never used to read an assignment.
The database is a mock and the reads are replaced, so each test states exactly the
runs and rows the page was given.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.segments import SegmentationRun
from web.services import model_comparison

_URL = "/model-comparison/"
_ORDINALS = {"CHAMPION": 1, "LOYAL": 2, "LOST": 3}
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


def _run(run_id: int, method: str, **overrides) -> SegmentationRun:
    defaults = dict(
        run_id=run_id,
        method=method,
        window_days=180,
        parameters={"window_days": 180},
        customer_count=3,
        executed_by=None,
        executed_by_name=None,
        run_at=_WHEN,
    )
    return SegmentationRun(**{**defaults, **overrides})


def _customers(n: int, label: str = "LOYAL"):
    return [(f"c{i:03d}", f"Customer {i:03d}", label) for i in range(n)]


class _World:
    """The runs and rows the page's reads return."""

    def __init__(self) -> None:
        self.runs = {
            9: _run(9, "RFM_RULES"),
            5: _run(5, "RFM_RULES"),
            12: _run(12, "KMEANS", parameters={"k": 3, "quality": {"inertia": 0.5}}),
            6: _run(6, "KMEANS"),
        }
        self.rows = {
            9: [("a", "Ada", "CHAMPION"), ("b", "Bob", "LOYAL"), ("c", "Cal", "LOST")],
            12: [("a", "Ada", "CHAMPION"), ("b", "Bob", "LOST"), ("c", "Cal", "LOST")],
            5: [("a", "Ada", "LOST")],
            6: [("a", "Ada", "LOYAL")],
        }
        self.read = Mock(side_effect=lambda _c, run_id: list(self.rows[run_id]))


def _world(monkeypatch: pytest.MonkeyPatch, world: _World | None = None) -> _World:
    world = world or _World()

    def of_method(_connection, method, *, limit):
        return sorted(
            (r for r in world.runs.values() if r.method == method),
            key=lambda r: (r.run_at, r.run_id),
            reverse=True,
        )[:limit]

    monkeypatch.setattr("web.routes.model_comparison.list_runs_of_method", of_method)
    monkeypatch.setattr(
        "web.routes.model_comparison.get_run", lambda _c, run_id: world.runs.get(run_id)
    )
    monkeypatch.setattr("web.routes.model_comparison.list_run_label_rows", world.read)
    monkeypatch.setattr(
        "web.routes.model_comparison.get_label_ordinals", lambda _c: dict(_ORDINALS)
    )
    return world


def _open(app: Flask, url: str = _URL, role: str = "ANALYST"):
    client = app.test_client()
    _sign_in(client, role)
    return client.get(url)


def _body(response) -> str:
    return response.get_data(as_text=True)


# ---------- who may open it ----------


@pytest.mark.parametrize("role", ["STORE_MANAGER", "INVENTORY_PLANNER", "CUSTOMER"])
def test_a_profile_without_segment_read_is_refused(app, monkeypatch, role) -> None:
    world = _world(monkeypatch)

    assert _open(app, role=role).status_code == 403
    world.read.assert_not_called()


@pytest.mark.parametrize("role", ["ADMIN", "ANALYST", "MARKETING", "AUDITOR"])
def test_the_roles_that_read_segments_may_open_it(app, monkeypatch, role) -> None:
    _world(monkeypatch)

    assert _open(app, role=role).status_code == 200


def test_signed_out_it_sends_you_to_sign_in(app) -> None:
    response = app.test_client().get(_URL)

    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_the_page_only_reads(app, monkeypatch) -> None:
    _world(monkeypatch)
    client = app.test_client()
    _sign_in(client)

    assert client.post(_URL).status_code == 405


# ---------- which two runs it compares ----------


def test_with_no_choice_it_compares_the_newest_run_of_each_kind(
    app, monkeypatch
) -> None:
    world = _world(monkeypatch)

    _open(app)

    assert sorted(call.args[1] for call in world.read.call_args_list) == [9, 12]


def test_a_chosen_pair_is_the_pair_compared(app, monkeypatch) -> None:
    world = _world(monkeypatch)

    _open(app, _URL + "?rules_run=5&kmeans_run=6")

    assert sorted(call.args[1] for call in world.read.call_args_list) == [5, 6]


def test_an_older_chosen_run_stays_selectable(app, monkeypatch) -> None:
    """A run past the newest offered is looked up by id and added to its list."""
    world = _world(monkeypatch)
    monkeypatch.setattr("web.routes.model_comparison._RUN_OPTIONS", 1)

    body = _body(_open(app, _URL + "?rules_run=5&kmeans_run=6"))

    assert re.search(r'value="5"\s+selected', body)
    assert re.search(r'value="6"\s+selected', body)
    assert sorted(call.args[1] for call in world.read.call_args_list) == [5, 6]


def test_the_comparison_is_built_by_one_function_and_is_never_given_a_kind(
    app, monkeypatch
) -> None:
    _world(monkeypatch)
    spy = Mock(wraps=model_comparison.compare_runs)
    monkeypatch.setattr("web.routes.model_comparison.compare_runs", spy)

    _open(app)

    (call,) = spy.call_args_list
    assert set(call.kwargs) == {"first_run_id", "second_run_id"}
    assert len(call.args) == 3  # the two runs' rows and the vocabulary


# ---------- what the page shows ----------


def test_it_reports_each_runs_population_per_label(app, monkeypatch) -> None:
    body = _body(_open_with_world(app, monkeypatch))

    for label in ("CHAMPION", "LOYAL", "LOST", "Unassigned"):
        assert label in body


def _open_with_world(app, monkeypatch, url: str = _URL):
    _world(monkeypatch)
    return _open(app, url)


def test_it_shows_per_customer_where_the_two_agree_and_disagree(
    app, monkeypatch
) -> None:
    body = _body(_open_with_world(app, monkeypatch))

    assert "Ada" in body and "Bob" in body and "Cal" in body
    assert "Agree" in body and "Disagree" in body


def test_it_states_the_counts_that_reconcile(app, monkeypatch) -> None:
    body = _body(_open_with_world(app, monkeypatch))

    assert "Customers in both runs" in body
    assert "Agreement rate" in body
    assert "66.7" in body or "67" in body  # two of three agree


def test_the_kind_of_each_run_is_shown_as_a_description_of_that_run(
    app, monkeypatch
) -> None:
    body = _body(_open_with_world(app, monkeypatch))

    assert "Rule-based run #9" in body and "K-means run #12" in body
    assert "RFM_RULES" in body and "KMEANS" in body


def test_each_runs_recorded_parameters_are_listed_as_recorded(app, monkeypatch) -> None:
    body = _body(_open_with_world(app, monkeypatch))

    assert "quality.inertia" in body and "0.5" in body
    assert "window_days" in body


def test_a_customer_only_one_run_scored_is_named_as_such(app, monkeypatch) -> None:
    world = _world(monkeypatch)
    world.rows[12].append(("d", "Dan", "LOST"))

    body = _body(_open(app))

    assert "Only in K-means run" in body


def test_a_customer_name_is_escaped(app, monkeypatch) -> None:
    world = _world(monkeypatch)
    world.rows[9][0] = ("a", "<script>alert(1)</script>", "CHAMPION")

    body = _body(_open(app))

    assert "<script>alert(1)</script>" not in body and "&lt;script&gt;" in body


# ---------- filtering and paging ----------


def test_the_list_can_be_narrowed_to_disagreements(app, monkeypatch) -> None:
    world = _world(monkeypatch)
    world.rows[9] = [("a", "Amy Agrees", "LOYAL"), ("b", "Ben Differs", "LOYAL")]
    world.rows[12] = [("a", "Amy Agrees", "LOYAL"), ("b", "Ben Differs", "LOST")]

    everyone = _body(_open(app))
    disagreeing = _body(_open(app, _URL + "?status=disagree"))
    agreeing = _body(_open(app, _URL + "?status=agree"))

    assert "Amy Agrees" in everyone and "Ben Differs" in everyone
    assert "Ben Differs" in disagreeing and "Amy Agrees" not in disagreeing
    assert "Amy Agrees" in agreeing and "Ben Differs" not in agreeing


def test_an_unknown_filter_is_a_400(app, monkeypatch) -> None:
    assert (
        _open_with_world(app, monkeypatch, _URL + "?status=everything").status_code
        == 400
    )


def test_the_customer_list_is_paged(app, monkeypatch) -> None:
    world = _world(monkeypatch)
    world.rows[9] = _customers(45)
    world.rows[12] = _customers(45, "LOST")

    first = _body(_open(app))
    second = _body(_open(app, _URL + "?page=3"))

    assert "Customer 000" in first and "Customer 044" not in first
    assert "Customer 044" in second


def test_a_page_past_the_end_goes_to_the_last_page(app, monkeypatch) -> None:
    world = _world(monkeypatch)
    world.rows[9] = _customers(25)
    world.rows[12] = _customers(25)

    response = _open(app, _URL + "?page=99")

    assert response.status_code == 302 and "page=2" in response.headers["Location"]


# ---------- errors ----------


def test_a_run_of_the_wrong_kind_in_a_slot_is_a_400_and_compares_nothing(
    app, monkeypatch
) -> None:
    world = _world(monkeypatch)

    response = _open(app, _URL + "?rules_run=12&kmeans_run=12")

    assert response.status_code == 400
    assert "Run #12 is not a rule-based run" in _body(response)
    world.read.assert_not_called()


@pytest.mark.parametrize("value", ["abc", "1.5", "-", "9;DROP"])
def test_a_choice_that_is_not_a_run_number_is_a_400(app, monkeypatch, value) -> None:
    world = _world(monkeypatch)

    response = _open(app, _URL + f"?rules_run={value}&kmeans_run=12")

    assert response.status_code == 400
    world.read.assert_not_called()


def test_a_run_that_does_not_exist_is_a_404(app, monkeypatch) -> None:
    _world(monkeypatch)

    assert _open(app, _URL + "?rules_run=999&kmeans_run=12").status_code == 404


# ---------- when there is nothing to compare ----------


def test_with_no_kmeans_run_it_says_so_and_offers_no_comparison(
    app, monkeypatch
) -> None:
    world = _World()
    world.runs = {k: v for k, v in world.runs.items() if v.method == "RFM_RULES"}
    _world(monkeypatch, world)

    response = _open(app)
    body = _body(response)

    assert response.status_code == 200
    assert "No K-means run has been recorded yet" in body
    world.read.assert_not_called()


def test_with_no_rule_based_run_it_says_so(app, monkeypatch) -> None:
    world = _World()
    world.runs = {k: v for k, v in world.runs.items() if v.method == "KMEANS"}
    _world(monkeypatch, world)

    assert "No rule-based run has been recorded yet" in _body(_open(app))


def test_with_no_runs_at_all_it_says_both(app, monkeypatch) -> None:
    world = _World()
    world.runs = {}
    _world(monkeypatch, world)

    body = _body(_open(app))

    assert "No rule-based run has been recorded yet" in body
    assert "No K-means run has been recorded yet" in body


# ---------- reaching it ----------


def test_the_menu_offers_it_to_a_visitor_who_may_read_segments(
    app, monkeypatch
) -> None:
    _world(monkeypatch)

    assert "Model comparison" in _body(_open(app))
