"""Analytics data-access queries executed by PostgreSQL as ``retail_app``.

The focused unit tests keep their mocks for query-shape assertions. These
tests complement them by making PostgreSQL parse and execute representative
sales, segmentation and experiment queries against the committed seed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from psycopg import Connection

from web.db import (
    campaigns,
    experiment_dashboard,
    experiments,
    sales,
    segmentation_dashboard,
    segments,
)

pytestmark = pytest.mark.postgres

_CUSTOMER_1 = "00000000-0000-0000-0000-000000000001"
_CUSTOMER_2 = "00000000-0000-0000-0000-000000000002"


@pytest.fixture
def app_connection(app_url: str) -> Iterator[Connection[Any]]:
    """Use the application's role and leave every test transaction rolled back."""
    connection = psycopg.connect(app_url)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def test_sales_queries_execute_against_the_seed(
    app_connection: Connection[Any],
) -> None:
    header = sales.get_transaction_by_source_id(app_connection, "TXN-00000001")
    references = sales.get_sale_references(
        app_connection, customer_id=_CUSTOMER_1, product_id=1
    )

    assert header is not None
    assert header.transaction_id == 1
    assert header.customer_id == _CUSTOMER_1
    assert references is not None
    assert references.product_is_active is True


def test_segmentation_queries_execute_against_the_seed(
    app_connection: Connection[Any],
) -> None:
    runs, total = segments.list_runs(app_connection, page=1, per_page=5)
    assignments = segments.get_current_assignments(
        app_connection, [_CUSTOMER_1, _CUSTOMER_2]
    )

    assert total >= 30
    assert len(runs) == 5
    assert set(assignments) == {_CUSTOMER_1, _CUSTOMER_2}

    latest = runs[0]
    rows = segmentation_dashboard.list_run_rfm_rows(app_connection, latest.run_id)
    revenue = segmentation_dashboard.list_run_revenue_by_label(
        app_connection,
        latest.run_id,
        latest.run_at - timedelta(days=latest.window_days),
        latest.run_at,
    )

    assert len(rows) == latest.customer_count == 30
    assert revenue
    assert sum(revenue.values()) > 0


def test_experiment_queries_execute_against_the_seed(
    app_connection: Connection[Any],
) -> None:
    page, total = experiments.list_experiments(app_connection, page=1, per_page=5)
    experiment = experiments.get_experiment(app_connection, 1)
    days = experiment_dashboard.list_conversion_days(app_connection, 1)

    assert total >= 30
    assert len(page) == 5
    assert experiment is not None
    assert (experiment.control_groups, experiment.treatment_groups) == (1, 1)
    assert experiment.assignments == 4
    # Experiment 1 (#463): customer 1 (control) first converts two days after
    # assignment and customer 3 (treatment) six days after; customers 2 and 4
    # have no sale in their 7-day window.
    assert [(row.group_id, row.day, row.customers) for row in days] == [
        (1, 2, 1),
        (2, 6, 1),
    ]


# ---------- list filters (#538): `%` and `_` match literally ----------


def _rename(connection: Connection[Any], table: str, ident: int, name: str) -> None:
    """Rename one seeded row inside the test's rolled-back transaction."""
    statements = {
        "campaign": "UPDATE campaign SET name = %s WHERE campaign_id = %s",
        "experiment": "UPDATE experiment SET name = %s WHERE experiment_id = %s",
    }
    with connection.cursor() as cursor:
        cursor.execute(statements[table], (name, ident))
        assert cursor.rowcount == 1, f"{table} {ident} is not seeded"


def test_campaign_filters_match_wildcards_literally(
    app_connection: Connection[Any],
) -> None:
    _rename(app_connection, "campaign", 1, "100% loyal")
    _rename(app_connection, "campaign", 2, "spring_sale")

    percent, percent_total = campaigns.list_campaigns(
        app_connection, page=1, per_page=50, search="%"
    )
    underscore, underscore_total = campaigns.list_campaigns(
        app_connection, page=1, per_page=50, search="_"
    )
    _all, everything = campaigns.list_campaigns(app_connection, page=1, per_page=50)

    assert everything >= 30
    assert [c.campaign_id for c in percent] == [1] and percent_total == 1
    assert [c.campaign_id for c in underscore] == [2] and underscore_total == 1


def test_campaign_status_and_search_combine(app_connection: Connection[Any]) -> None:
    _rename(app_connection, "campaign", 1, "100% loyal")
    status = campaigns.get_campaign(app_connection, 1).status
    other = next(s for s in ("DRAFT", "ACTIVE", "FINISHED", "CANCELLED") if s != status)

    same, same_total = campaigns.list_campaigns(
        app_connection, page=1, per_page=50, search="100%", status=status
    )
    different, different_total = campaigns.list_campaigns(
        app_connection, page=1, per_page=50, search="100%", status=other
    )

    assert [c.campaign_id for c in same] == [1] and same_total == 1
    assert different == [] and different_total == 0


def test_experiment_filters_match_wildcards_literally(
    app_connection: Connection[Any],
) -> None:
    _rename(app_connection, "experiment", 1, "50% holdout")
    _rename(app_connection, "experiment", 2, "arm_b test")
    metric = experiments.get_experiment(app_connection, 1).target_metric

    percent, percent_total = experiments.list_experiments(
        app_connection, page=1, per_page=50, search="%"
    )
    underscore, underscore_total = experiments.list_experiments(
        app_connection, page=1, per_page=50, search="_"
    )
    by_metric, by_metric_total = experiments.list_experiments(
        app_connection, page=1, per_page=50, search="50%", target_metric=metric
    )

    # Campaign names count too, so a match may come from either name.
    assert {e.experiment_id for e in percent} == {1} and percent_total == 1
    assert {e.experiment_id for e in underscore} == {2} and underscore_total == 1
    assert [e.experiment_id for e in by_metric] == [1] and by_metric_total == 1


def test_run_filters_match_wildcards_literally(
    app_connection: Connection[Any],
) -> None:
    with app_connection.cursor() as cursor:
        cursor.execute(
            "SELECT user_id FROM app_user WHERE email = %s",
            ("admin@mosaiq-demo.com",),
        )
        admin_id = cursor.fetchone()[0]
        cursor.execute(
            "UPDATE app_user SET name = %s WHERE user_id = %s",
            ("Ana_100%", admin_id),
        )
        cursor.execute(
            "INSERT INTO segmentation_run (method, window_days, customer_count,"
            " executed_by, data_origin) VALUES (%s, %s, %s, %s, %s)"
            " RETURNING run_id",
            ("KMEANS", 180, 0, admin_id, "SEEDED"),
        )
        run_id = cursor.fetchone()[0]

    named, named_total = segments.list_runs(
        app_connection, page=1, per_page=50, search="_100%"
    )
    wrong_method, wrong_total = segments.list_runs(
        app_connection, page=1, per_page=50, search="_100%", method="RFM_RULES"
    )
    _rows, kmeans_total = segments.list_runs(
        app_connection, page=1, per_page=50, method="KMEANS"
    )

    assert [r.run_id for r in named] == [run_id] and named_total == 1
    assert wrong_method == [] and wrong_total == 0
    assert kmeans_total >= 1


# ---------- #560: every read behind a Synthetic marker, on the seed ----------


def test_every_marker_read_executes_and_finds_the_seed_seeded(
    app_connection: Connection[Any],
) -> None:
    """ADR-0052: the seed is SEEDED throughout, so each flag a page marks itself
    by must come back true from PostgreSQL, as retail_app."""
    from datetime import date

    from web.db import (
        consumption,
        consumption_shift,
        customer_timeline,
        experiment_report,
        revenue_summary,
        segment_history_report,
    )

    with app_connection.cursor() as cursor:
        cursor.execute("SELECT min(occurred_at), max(occurred_at) FROM transaction")
        first, last = cursor.fetchone()
    since, until = first - timedelta(days=1), last + timedelta(days=1)

    totals = consumption.get_sales_totals(app_connection, _CUSTOMER_1, since, until)
    assert totals.purchases > 0 and totals.seeded and totals.window_seeded

    current, _previous = consumption.get_current_and_previous_history_rows(
        app_connection, _CUSTOMER_1
    )
    assert current is not None and current.data_origin == "SEEDED"
    history = customer_timeline.list_history_rows(app_connection, _CUSTOMER_1)
    assert history and {row.data_origin for row in history} == {"SEEDED"}

    entries = segment_history_report.list_history_entries(
        app_connection, page=1, per_page=25
    )
    assert entries and {entry.data_origin for entry in entries} == {"SEEDED"}

    assert revenue_summary.read_revenue_summary(app_connection, 90).seeded is True

    middle = first + (last - first) / 2
    *_totals, shift_seeded = consumption_shift.list_totals_for_periods(
        app_connection, since, middle, middle, until
    )
    assert shift_seeded is True

    listed, _total = experiments.list_experiments(app_connection, page=1, per_page=50)
    assert any(e.seeded_conversion for e in listed)
    marked = next(e for e in listed if e.seeded_conversion)
    fetched = experiments.get_experiment(app_connection, marked.experiment_id)
    assert fetched is not None and fetched.seeded_conversion is True
    reported, _ = experiment_report.list_report_experiments(
        app_connection, campaign_id=None, data_origin=None, limit=50, offset=0
    )
    assert any(e.seeded_conversion for e in reported)
    experiment_report.list_active_report_experiments(
        app_connection, active_on=date.today(), limit=50
    )
