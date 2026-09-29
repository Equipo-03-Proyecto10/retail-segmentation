"""The reports index (#344): the placeholder is gone, each profile is offered
the reports it can open and no others, and the revenue summary reads the
newest window of sales with parameterized SQL."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, Mock

import pytest

from web.app import create_app
from web.config import Config
from web.db.revenue_summary import read_revenue_summary
from web.middleware.authz import PERMISSIONS, permissions_for
from web.routes.reports import REPORT_GROUPS

_URL = "/reports/"
_USER = "11111111-1111-1111-1111-000000000007"
_UNTIL = datetime(2026, 9, 28, tzinfo=UTC)


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


def _connection(with_sales: bool = True) -> MagicMock:
    connection = MagicMock()
    connection.closed = False
    cursor = _cursor(connection)
    if with_sales:
        cursor.fetchone.return_value = (_UNTIL, 3, Decimal("300.50"))
        cursor.fetchall.side_effect = [
            [("Online", 2, Decimal("200.00")), ("Store", 1, Decimal("100.50"))],
            [("Centro", 3, Decimal("300.50"))],
        ]
    else:
        cursor.fetchone.return_value = (None, 0, Decimal("0"))
        cursor.fetchall.return_value = []
    return connection


def _client(connection: MagicMock, role: str):
    app = create_app(
        Config(
            secret_key="reports-test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused-by-test",
        ),
        database_connector=Mock(side_effect=[Mock(), connection]),
    )
    app.config["PROPAGATE_EXCEPTIONS"] = False
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=_USER, role_code=role, name="Test User")
    return client


def test_the_placeholder_is_gone() -> None:
    response = _client(_connection(), "ANALYST").get(_URL)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "still being built" not in body
    assert "deferred in docs/roadmap.md" not in body
    assert "Still building" not in body


def test_the_revenue_summary_shows_totals_and_both_splits() -> None:
    body = _client(_connection(), "ANALYST").get(_URL).get_data(as_text=True)

    for text in ("300.50", "Online", "Centro", "2026-09-28", "Revenue by channel"):
        assert text in body


def test_no_sales_says_so_instead_of_showing_zeroes() -> None:
    body = _client(_connection(False), "ANALYST").get(_URL).get_data(as_text=True)

    assert "No sales yet" in body


@pytest.mark.parametrize(
    "role,links",
    [
        ("ADMIN", 4),
        ("ANALYST", 4),
        ("MARKETING", 4),
        ("STORE_MANAGER", 3),
        ("INVENTORY_PLANNER", 0),
    ],
)
def test_a_profile_is_offered_only_the_reports_it_can_open(
    role: str, links: int
) -> None:
    body = _client(_connection(), role).get(_URL).get_data(as_text=True)

    offered = [
        path
        for path in (
            "/segmentation-dashboard/",
            "/segment-history-report/",
            "/consumption-reports/",
            "/experiment-report/",
        )
        if f'href="{path}"' in body
    ]
    assert len(offered) == links


@pytest.mark.parametrize("role", sorted(PERMISSIONS))
def test_no_link_is_offered_that_the_profile_would_be_refused(role: str) -> None:
    with_permission = {
        link.permission
        for _, links in REPORT_GROUPS
        for link in links
        if link.permission in permissions_for(role)
    }

    assert with_permission <= permissions_for(role)


def test_a_profile_holding_only_report_read_still_gets_the_revenue_summary() -> None:
    assert (
        permissions_for("INVENTORY_PLANNER") & {"segment.read", "campaign.read"}
        == set()
    )

    body = _client(_connection(), "INVENTORY_PLANNER").get(_URL).get_data(as_text=True)

    assert "Revenue by store" in body


@pytest.mark.parametrize("role", ["CUSTOMER", "AUDITOR"])
def test_a_profile_is_refused_or_served_by_report_read_alone(role: str) -> None:
    connection = _connection()
    response = _client(connection, role).get(_URL)

    if "report.read" in permissions_for(role):
        assert response.status_code == 200
    else:
        assert response.status_code == 403
        _cursor(connection).execute.assert_not_called()


def test_the_revenue_read_is_parameterized_and_window_bound() -> None:
    connection = _connection()

    summary = read_revenue_summary(connection, 90)

    statements = [call.args for call in _cursor(connection).execute.call_args_list]
    assert len(statements) == 3
    for statement, parameters in statements:
        assert "make_interval(days => %s)" in statement
        assert parameters == (90,)
    assert summary.revenue == Decimal("300.50")
    assert [row.name for row in summary.by_channel] == ["Online", "Store"]
