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
