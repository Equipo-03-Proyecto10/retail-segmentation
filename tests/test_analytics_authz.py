"""F4-08 (#231): the Delivery 2 analytics surface is default-deny and holds to
the permission map F4-07 wrote (docs/analytics-permission-map.md, ADR-0007).

`tests/test_authz.py` proves the gate works and `tests/test_negative_flows.py`
proves it refuses. What neither states is the map itself: which profile is
permitted which analytics route. This file is that statement, one row per
route, checked in both directions for every profile:

* a route in the table is declared with exactly the permission the table names;
* a profile holding that permission is served, one that does not is refused
  before the view runs;
* a route that reaches analytics data but is missing from the table fails the
  build, so a new route cannot arrive unmapped.

Only the PostgreSQL connector is replaced, as in the other authorization tests.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask

from tests.test_single_administrator import _Connection
from web.app import create_app
from web.config import Config
from web.middleware.authz import (
    ALL_PERMISSIONS,
    CAMPAIGN_READ,
    CAMPAIGN_WRITE,
    PERMISSIONS,
    REPORT_READ,
    SALES_INGEST_EXECUTE,
    SEGMENT_READ,
    SEGMENT_RUN_EXECUTE,
    SEGMENT_WRITE,
    permissions_for,
    requirement_of,
)
from web.services.users import SingleAdministratorError, create_user

USER_ID = "11111111-1111-1111-1111-000000000007"

# (endpoint, method, concrete path, the one permission the map assigns it)
ANALYTICS_ROUTES: tuple[tuple[str, str, str, str], ...] = (
    # Phases 7-10: segments, runs, migration, model comparison, recommendations.
    ("catalog.segments", "GET", "/catalog/segments", SEGMENT_READ),
    ("catalog.segment_detail", "GET", "/catalog/segments/1", SEGMENT_READ),
    ("catalog.customers", "GET", "/catalog/customers", SEGMENT_READ),
    ("catalog.customer_detail", "GET", f"/catalog/customers/{USER_ID}", SEGMENT_READ),
    (
        "catalog.customer_profile",
        "GET",
        f"/catalog/customers/{USER_ID}/profile",
        SEGMENT_READ,
    ),
    (
        "catalog.customer_recommendations",
        "GET",
        f"/catalog/customers/{USER_ID}/recommendations",
        SEGMENT_READ,
    ),
    ("run_history.index", "GET", "/run-history/", SEGMENT_READ),
    ("run_history.detail", "GET", "/run-history/1", SEGMENT_READ),
    ("migration_matrix.index", "GET", "/migration-matrix/", SEGMENT_READ),
    ("migration_explanation.index", "GET", "/migration-explanation/", SEGMENT_READ),
    ("model_comparison.index", "GET", "/model-comparison/", SEGMENT_READ),
    ("segment_run.index", "GET", "/segment-run/", SEGMENT_RUN_EXECUTE),
    ("segment_run.execute", "POST", "/segment-run/", SEGMENT_RUN_EXECUTE),
    # Phase 11: campaigns and experiments.
    ("campaigns.index", "GET", "/campaigns/", CAMPAIGN_READ),
    ("campaigns.create", "GET", "/campaigns/new", CAMPAIGN_WRITE),
    ("campaigns.create", "POST", "/campaigns/new", CAMPAIGN_WRITE),
    ("campaigns.edit", "GET", "/campaigns/1/edit", CAMPAIGN_WRITE),
    ("campaigns.edit", "POST", "/campaigns/1/edit", CAMPAIGN_WRITE),
    ("campaigns.change_status", "POST", "/campaigns/1/activate", CAMPAIGN_WRITE),
    ("experiments.index", "GET", "/experiments/", CAMPAIGN_READ),
    ("experiments.create", "GET", "/experiments/new", CAMPAIGN_WRITE),
    ("experiments.create", "POST", "/experiments/new", CAMPAIGN_WRITE),
    ("experiments.edit", "GET", "/experiments/1/edit", CAMPAIGN_WRITE),
    ("experiments.edit", "POST", "/experiments/1/edit", CAMPAIGN_WRITE),
    ("experiments.assign", "GET", "/experiments/1/assign", CAMPAIGN_WRITE),
    ("experiments.assign", "POST", "/experiments/1/assign", CAMPAIGN_WRITE),
    ("experiments.exposure", "GET", "/experiments/1/exposure", CAMPAIGN_READ),
    ("experiments.record_exposure", "POST", "/experiments/1/exposure", CAMPAIGN_WRITE),
    ("experiments.conversion", "GET", "/experiments/1/conversion", CAMPAIGN_READ),
    (
        "experiments.evaluate_conversion",
        "POST",
        "/experiments/1/conversion",
        CAMPAIGN_WRITE,
    ),
    ("experiments.uplift", "GET", "/experiments/1/uplift", CAMPAIGN_READ),
    # Phase 12: dashboards and filtered reports.
    ("segmentation_dashboard.index", "GET", "/segmentation-dashboard/", SEGMENT_READ),
    ("segment_history_report.index", "GET", "/segment-history-report/", SEGMENT_READ),
    ("consumption_reports.index", "GET", "/consumption-reports/", SEGMENT_READ),
    ("experiment_report.index", "GET", "/experiment-report/", CAMPAIGN_READ),
    ("experiment_report.export", "GET", "/experiment-report/export.csv", CAMPAIGN_READ),
    ("reports.index", "GET", "/reports/", REPORT_READ),
)

# Blueprints that are not analytics. A blueprint absent from both this set and
# the table above fails the completeness test, so a new surface has to be
# classified by whoever adds it.
NOT_ANALYTICS_BLUEPRINTS = frozenset({"admin", "audit", "auth", "home"})
ANALYTICS_PERMISSIONS = frozenset(
    {
        SEGMENT_READ,
        SEGMENT_WRITE,
        SEGMENT_RUN_EXECUTE,
        CAMPAIGN_READ,
        CAMPAIGN_WRITE,
        REPORT_READ,
        SALES_INGEST_EXECUTE,
    }
)
ROLES = sorted(PERMISSIONS)
_TABLE_ENDPOINTS = {endpoint for endpoint, *_ in ANALYTICS_ROUTES}


@pytest.fixture
def connection() -> MagicMock:
    connection = MagicMock()
    connection.closed = False
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    return connection


@pytest.fixture
def app(connection: MagicMock, tmp_path) -> Flask:
    connector = Mock(side_effect=[Mock(), connection])
    app = create_app(
        Config(
            secret_key="analytics-authz-test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused-by-test",
            upload_dir=str(tmp_path / "uploads"),
        ),
        database_connector=connector,
    )
    app.config["PROPAGATE_EXCEPTIONS"] = False
    connector.reset_mock()
    return app


def _sign_in(client, role_code: str) -> None:
    with client.session_transaction() as session:
        session.update(user_id=USER_ID, role_code=role_code, name="Test User")


def _ids(routes) -> list[str]:
    return [f"{method} {path}" for _, method, path, _ in routes]


# ---------- every analytics route declares a permission, and the right one ----------


def test_every_analytics_route_declares_a_permission(app: Flask) -> None:
    """The first acceptance criterion: an undeclared route fails the build."""
    undeclared = [
        rule.endpoint
        for rule in app.url_map.iter_rules()
        if rule.endpoint in _TABLE_ENDPOINTS
        and requirement_of(app.view_functions[rule.endpoint]) is None
    ]

    assert undeclared == []


@pytest.mark.parametrize(
    "endpoint,method,path,permission", ANALYTICS_ROUTES, ids=_ids(ANALYTICS_ROUTES)
)
def test_a_route_is_declared_with_exactly_the_mapped_permission(
    app: Flask, endpoint: str, method: str, path: str, permission: str
) -> None:
    matched, _ = app.url_map.bind("localhost").match(path, method=method)
    requirement = requirement_of(app.view_functions[endpoint])

    assert matched == endpoint
    assert requirement is not None and not requirement.anonymous_allowed
    assert requirement.permissions == frozenset({permission}), (
        f"{endpoint} is declared with {sorted(requirement.permissions)}, "
        f"but docs/analytics-permission-map.md assigns it {permission}."
    )


def test_no_route_reaching_analytics_is_missing_from_the_table(app: Flask) -> None:
    """A new route cannot arrive unmapped, wherever it is mounted."""
    unmapped = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static" or rule.endpoint in _TABLE_ENDPOINTS:
            continue
        blueprint = rule.endpoint.split(".")[0]
        requirement = requirement_of(app.view_functions[rule.endpoint])
        holds_analytics = (
            requirement is not None and requirement.permissions & ANALYTICS_PERMISSIONS
        )
        if holds_analytics or blueprint not in (NOT_ANALYTICS_BLUEPRINTS | {"catalog"}):
            unmapped.append(rule.endpoint)

    assert unmapped == [], (
        f"These routes are neither in ANALYTICS_ROUTES nor known to be outside "
        f"analytics: {', '.join(sorted(unmapped))}. Add them, and to "
        "docs/analytics-permission-map.md."
    )


def test_every_permission_the_table_names_exists(app: Flask) -> None:
    assert {permission for *_, permission in ANALYTICS_ROUTES} <= ALL_PERMISSIONS


# ---------- anonymous ----------


@pytest.mark.parametrize(
    "endpoint,method,path,permission", ANALYTICS_ROUTES, ids=_ids(ANALYTICS_ROUTES)
)
def test_an_anonymous_request_is_refused_before_the_view_runs(
    app: Flask, endpoint: str, method: str, path: str, permission: str
) -> None:
    response = app.test_client().open(path, method=method)

    if method == "GET":
        assert response.status_code == 302
        target = urlsplit(response.location)
        assert target.path == "/login" and not target.netloc
        assert parse_qs(target.query) == {"next": [path]}
    else:
        assert response.status_code == 403
    app.extensions["database_connector"].assert_not_called()


# ---------- every profile, every route ----------


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "endpoint,method,path,permission", ANALYTICS_ROUTES, ids=_ids(ANALYTICS_ROUTES)
)
def test_a_profile_is_permitted_exactly_the_routes_the_map_assigns_it(
    app: Flask,
    caplog: pytest.LogCaptureFixture,
    role: str,
    endpoint: str,
    method: str,
    path: str,
    permission: str,
) -> None:
    client = app.test_client()
    _sign_in(client, role)

    with caplog.at_level(logging.INFO):
        response = client.open(path, method=method)

    refused = "Access denied" in caplog.text
    if permission in permissions_for(role):
        assert not refused, f"{role} should reach {method} {path} ({permission})"
    else:
        assert refused, f"{role} must not reach {method} {path} ({permission})"
        assert response.status_code == 403
        assert response.mimetype == "text/html"
        assert USER_ID in caplog.text
        app.extensions["database_connector"].assert_not_called()


@pytest.mark.parametrize("role", ROLES)
def test_a_profile_reaches_at_least_one_route_only_if_it_holds_an_analytics_permission(
    role: str,
) -> None:
    """The matrix is the only source of what a profile may reach: a profile
    holding no analytics permission reaches no row of the table."""
    reachable = [
        endpoint
        for endpoint, _, _, permission in ANALYTICS_ROUTES
        if permission in permissions_for(role)
    ]

    assert bool(reachable) == bool(permissions_for(role) & ANALYTICS_PERMISSIONS)


def test_only_the_administrator_runs_segmentation_or_ingests_sales() -> None:
    """`segment_run.execute` and `sales_ingest.execute` are ADMIN-only in the map."""
    for permission in (SEGMENT_RUN_EXECUTE, SALES_INGEST_EXECUTE):
        holders = {role for role in ROLES if permission in permissions_for(role)}
        assert holders == {"ADMIN"}, f"{permission} is held by {sorted(holders)}"


def test_the_customer_reaches_no_analytics_route(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client, "CUSTOMER")

    statuses = {
        client.open(path, method=method).status_code
        for _, method, path, _ in ANALYTICS_ROUTES
    }

    assert statuses == {403}
    app.extensions["database_connector"].assert_not_called()


# ---------- the single-administrator rule, both halves (constraint C-4) ----------


def test_a_second_administrator_is_refused_by_the_application_and_by_the_index() -> (
    None
):
    """Phase 7-12 leave C-4 unchanged, and this is the check that it still holds.

    The application half runs here. The schema half cannot run without
    PostgreSQL, so it is asserted as far as a unit test can: the partial unique
    index is in the schema, and `sql/verify_integrity.sql` still holds the
    direct INSERT and UPDATE cases (N17, N18) that the CI database job runs and
    expects to fail with 23505.
    """
    connection = _Connection(administrators=1)

    with pytest.raises(SingleAdministratorError):
        create_user(
            connection,
            name="Second admin",
            email="second@mosaiq-demo.com",
            password="Password123!",
            role_code="ADMIN",
        )
    assert connection.writes == []

    schema = Path("sql/01_schema.sql").read_text(encoding="utf-8")
    assert "CREATE UNIQUE INDEX ux_app_user_single_administrator" in schema
    verification = Path("sql/verify_integrity.sql").read_text(encoding="utf-8")
    for case in ("N17: a second administrator", "N18: promoting a second user"):
        assert f"-- {case}" in verification
        assert "[expect: 23505 unique_violation]" in verification.split(case)[1][:120]
