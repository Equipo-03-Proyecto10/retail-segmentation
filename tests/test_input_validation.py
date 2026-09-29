"""#347 and #349: input that cannot be valid is refused, never a 500.

The NUL sweep walks every registered route rather than the ones the QA round
happened to find, so a form added later is covered without anyone listing it.
The URLs from #349 are requested as written in the issue.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from werkzeug.routing import IntegerConverter, UUIDConverter

from web.app import create_app
from web.config import Config
from web.parsing import (
    BIGINT_MAX,
    DATE_MAX,
    DATE_MIN,
    INT_MAX,
    iso_date,
    page_number,
    whole_number,
)

_USER = "11111111-1111-1111-1111-000000000001"
_CUSTOMER = "00000000-0000-0000-0000-000000000001"


class _Cursor:
    """Answers `count(*)` with 0 and everything else with no row, so a route
    that pages a listing sees an empty one rather than a mock's surprises."""

    def __init__(self) -> None:
        self._statement = ""

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def execute(self, statement, parameters=None) -> None:
        self._statement = " ".join(str(statement).lower().split())

    def fetchall(self) -> list:
        return []

    def fetchone(self):
        return (0,) if "count(" in self._statement else None


@pytest.fixture
def app(tmp_path) -> Flask:
    connection = MagicMock()
    connection.closed = False
    connection.cursor.side_effect = _Cursor
    app = create_app(
        Config(
            secret_key="input-test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused-by-test",
            upload_dir=str(tmp_path / "uploads"),
        ),
        database_connector=Mock(return_value=connection),
    )
    app.config["PROPAGATE_EXCEPTIONS"] = False
    return app


def _admin(app: Flask):
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=_USER, role_code="ADMIN", name="Admin")
    return client


def _refused_as_a_page(response) -> bool:
    body = response.get_data(as_text=True)
    return "MOSAIQ" in body and "Traceback" not in body


# ---------- the parsers ----------


@pytest.mark.parametrize("raw", ["²", "٢٩", "1٣", "١", "", " 1", "1 ", "-1", "1.0"])
def test_a_whole_number_must_be_ascii_digits(raw: str) -> None:
    assert whole_number(raw) is None


def test_a_whole_number_respects_its_bound() -> None:
    assert whole_number(str(INT_MAX)) == INT_MAX
    assert whole_number(str(INT_MAX + 1)) is None
    assert whole_number(str(BIGINT_MAX), BIGINT_MAX) == BIGINT_MAX
    assert whole_number(str(BIGINT_MAX + 1), BIGINT_MAX) is None
    assert whole_number(None) is None


@pytest.mark.parametrize(
    "raw",
    [
        "0001-01-01",
        "9999-12-31",
        "1899-12-31",
        "2101-01-01",
        "20260101",
        "2026-W01-1",
        "２０２６-01-01",
        "2026-13-01",
        "2026-02-30",
        "",
    ],
)
def test_a_date_is_one_iso_day_inside_the_calendar_the_data_lives_in(raw: str) -> None:
    assert iso_date(raw) is None


def test_the_ends_of_the_calendar_are_accepted() -> None:
    assert iso_date(DATE_MIN.isoformat()) == DATE_MIN
    assert iso_date(DATE_MAX.isoformat()) == DATE_MAX
    assert iso_date("2026-09-29") == date(2026, 9, 29)


@pytest.mark.parametrize("raw", [None, "", "0", "-3", "x", "1٣", "9223372036854775808"])
def test_a_page_that_is_not_usable_is_the_first(raw: str | None) -> None:
    assert page_number(raw) == 1


def test_a_page_is_read_as_written() -> None:
    assert page_number("13") == 13


# ---------- #349: the URLs from the issue ----------

_URLS_349 = [
    "/migration-matrix/?run_a=%C2%B2&run_b=30",
    "/migration-matrix/?run_a=30&run_b=%C2%B2",
    "/migration-matrix/?run_a=%D9%A2%D9%A9&run_b=30",
    f"/migration-explanation/?run_a=%C2%B2&run_b=30&customer_id={_CUSTOMER}",
    f"/migration-explanation/?run_a=1&run_b=2&customer_id=urn:uuid:{_CUSTOMER}",
    "/catalog/stock?store=%C2%B2",
    "/catalog/stock?store=2147483648",
    "/catalog/stock?store=99999999999999999999",
    "/consumption-reports/?as_of=0001-01-01",
    "/consumption-reports/?as_of=9999-12-31",
    "/consumption-reports/?window_days=400000",
    "/consumption-reports/?store=2147483648",
    "/experiment-report/?page=9223372036854775808",
    "/segment-history-report/?period_start=0001-01-01",
    "/audit/?from=0001-01-01",
    "/segmentation-dashboard/?run=99999999999999999999",
    "/model-comparison/?rules_run=99999999999999999999",
]


@pytest.mark.parametrize("url", _URLS_349)
def test_input_from_349_is_answered_with_a_page_and_never_a_500(
    app: Flask, url: str
) -> None:
    response = _admin(app).get(url)

    assert response.status_code < 500, f"{url} -> {response.status_code}"
    assert _refused_as_a_page(response)


def test_a_unicode_digit_is_not_read_as_a_run_id(app: Flask) -> None:
    body = _admin(app).get("/migration-matrix/?run_a=%D9%A2%D9%A9&run_b=30")

    assert "Choose two runs to compare." in body.get_data(as_text=True)


def test_an_unusable_window_is_refused_with_its_bounds(app: Flask) -> None:
    response = _admin(app).get("/consumption-reports/?window_days=400000")

    assert response.status_code == 400
    assert "1 to 3650" in response.get_data(as_text=True)


@pytest.mark.parametrize(
    "url",
    [
        "/catalog/stock?store=%C2%B2",
        "/catalog/stock?store=2147483648",
        "/segmentation-dashboard/?run=99999999999999999999",
        "/model-comparison/?rules_run=99999999999999999999",
    ],
)
def test_an_out_of_domain_id_is_refused_before_it_reaches_sql(
    app: Flask, url: str
) -> None:
    response = _admin(app).get(url)

    assert response.status_code == 400
    assert _refused_as_a_page(response)


# ---------- #347: a NUL byte ----------


def _path_for(rule) -> str | None:
    values = {}
    for name, converter in rule._converters.items():
        if isinstance(converter, IntegerConverter):
            values[name] = 1
        elif isinstance(converter, UUIDConverter):
            values[name] = uuid.UUID(_CUSTOMER)
        else:
            values[name] = "x"
    try:
        return rule.build(values, append_unknown=False)[1]
    except Exception:  # pragma: no cover - a rule this helper cannot fill in
        return None


def _every_route(app: Flask) -> list[tuple[str, str]]:
    routes = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        path = _path_for(rule)
        assert path is not None, f"cannot build a path for {rule.rule}"
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            routes.append((method, path))
    return routes


def test_the_sweep_finds_the_routes(app: Flask) -> None:
    routes = _every_route(app)

    assert ("POST", "/login") in routes
    assert ("POST", "/campaigns/new") in routes
    assert ("GET", "/catalog/customers") in routes
    assert len(routes) > 80


def test_a_nul_in_a_query_value_is_a_400_on_every_route(app: Flask) -> None:
    client = _admin(app)
    for method, path in _every_route(app):
        response = client.open(f"{path}?q=a%00b", method=method)

        assert response.status_code == 400, f"{method} {path} -> {response.status_code}"
        assert _refused_as_a_page(response)


def test_a_nul_in_a_form_value_or_field_name_is_a_400_on_every_route(
    app: Flask,
) -> None:
    client = _admin(app)
    for method, path in _every_route(app):
        if method != "POST":
            continue
        for data in ({"name": "a\x00b"}, {"na\x00me": "ab"}):
            response = client.post(path, data=data)

            assert (
                response.status_code == 400
            ), f"{path} {data} -> {response.status_code}"


def test_the_sign_in_form_refuses_a_nul_in_the_email(app: Flask) -> None:
    """The case the previous QA round reported and #347 found still present."""
    response = app.test_client().post(
        "/login", data={"email": "a\x00b@x.com", "password": "Password123!"}
    )

    assert response.status_code == 400
    assert _refused_as_a_page(response)


def test_a_nul_in_a_path_segment_is_refused(app: Flask) -> None:
    response = _admin(app).get("/admin/products/image/a%00b.png")

    assert response.status_code == 400


def test_a_request_without_a_nul_is_untouched(app: Flask) -> None:
    response = _admin(app).get("/catalog/customers?q=ada")

    assert response.status_code == 200


def test_authorization_still_refuses_first(app: Flask) -> None:
    """A NUL does not change what a stranger is told: they are sent to sign in."""
    response = app.test_client().get("/catalog/customers?q=a%00b")

    assert response.status_code == 302
