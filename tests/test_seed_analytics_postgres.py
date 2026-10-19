"""The seed shows every analytics path it exists to demonstrate (#463).

These tests read the loaded seed as ``retail_app`` and check what the seed says
about itself: the K-means run is the application's own fit of the sales it
carries, customers differ enough for every quintile and every segment label to
be reached, every experiment event is inside its experiment's dates and
conversion window, and no table is exempt from the 30-row minimum without a
domain that small.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import Connection, sql

from web.services.cluster_labels import describe_mapping, label_pairs
from web.services.kmeans import (
    KMeansParams,
    RawRfm,
    fit_customers,
    recorded_parameters,
)

pytestmark = pytest.mark.postgres

_EXEMPT = Path(__file__).parents[1] / "sql/seed-exempt.txt"


@pytest.fixture
def connection(app_url: str) -> Iterator[Connection[Any]]:
    """The application's role; the tests only read."""
    connection = psycopg.connect(app_url)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def test_the_seeded_kmeans_run_is_the_applications_own_fit(
    connection: Connection[Any],
) -> None:
    """Refit the raw values the run's rows carry with the parameters it records:
    the labels and the recorded quality measures must be what the fit gives."""
    run_id, window_days, recorded = connection.execute("""
        SELECT run_id, window_days, parameters
          FROM segmentation_run
         WHERE method = 'KMEANS'
         ORDER BY run_at DESC
         LIMIT 1
        """).fetchone()
    rows = connection.execute(
        """
        SELECT customer_id::text, label_code, recency_last_purchase_at,
               frequency_count, monetary_total, segment_id,
               r_score, f_score, m_score
          FROM customer_segment_history
         WHERE run_id = %s
         ORDER BY customer_id
        """,
        (run_id,),
    ).fetchall()
    vocabulary = [
        code
        for (code,) in connection.execute(
            "SELECT label_code FROM segment_label ORDER BY ordinal_position"
        ).fetchall()
    ]

    assert len(rows) == 30
    # ADR-0018: a K-means row has a label and raw values, no segment, no scores.
    assert all(row[5:] == (None, None, None, None) for row in rows)

    measured = [row for row in rows if row[3] is not None]
    params = KMeansParams(
        k=recorded["k"],
        seed=recorded["seed"],
        max_iterations=recorded["max_iterations"],
        tolerance=recorded["tolerance"],
    )
    fitted = fit_customers(
        [RawRfm(row[0], row[2], row[3], row[4]) for row in measured], params
    )

    assert {row[0]: row[1] for row in measured} == label_pairs(fitted, vocabulary)
    assert all(row[1] is None for row in rows if row[3] is None)
    expected = recorded_parameters(fitted, window_days)
    expected["quality"]["customers_unassigned"] = len(rows) - len(measured)
    expected["label_mapping"] = describe_mapping(params.k, vocabulary)
    assert recorded == expected


def test_the_newest_run_reaches_every_score_and_every_label(
    connection: Connection[Any],
) -> None:
    """Customers differ in recency, frequency and spend, so no score and no
    segment label is unreachable in what the application shows by default."""
    scores = connection.execute("""
        SELECT array_agg(DISTINCT r_score ORDER BY r_score),
               array_agg(DISTINCT f_score ORDER BY f_score),
               array_agg(DISTINCT m_score ORDER BY m_score),
               array_agg(DISTINCT label_code ORDER BY label_code)
          FROM customer_segment_history
         WHERE run_id = (SELECT run_id FROM segmentation_run
                          ORDER BY run_at DESC, run_id DESC LIMIT 1)
        """).fetchone()
    labels = [
        code
        for (code,) in connection.execute(
            "SELECT label_code FROM segment_label ORDER BY label_code"
        ).fetchall()
    ]

    assert scores[0] == scores[1] == scores[2] == [1, 2, 3, 4, 5]
    assert scores[3] == labels


def test_customers_do_not_all_buy_the_same_number_of_times(
    connection: Connection[Any],
) -> None:
    counts = connection.execute("""
        SELECT count(*) FROM transaction
         WHERE source_transaction_id LIKE 'TXN-%'
         GROUP BY customer_id
        """).fetchall()

    assert len({count for (count,) in counts}) >= 10
    assert sum(count for (count,) in counts) == 300


def test_every_experiment_event_is_inside_its_dates_and_window(
    connection: Connection[Any],
) -> None:
    """Assignment, exposure and conversion fall inside the experiment's dates,
    an exposure follows its assignment, and a conversion is the assigned
    customer's own sale inside the conversion window."""
    violations = connection.execute("""
        SELECT 'assignment', a.assignment_id
          FROM experiment_assignment AS a
          JOIN experiment AS e USING (experiment_id)
         WHERE a.assigned_at < e.starts_on
            OR a.assigned_at >= e.ends_on + 1
            OR a.assigned_at > now()
        UNION ALL
        SELECT 'exposure', x.exposure_id
          FROM experiment_exposure AS x
          JOIN experiment_assignment AS a USING (assignment_id)
          JOIN experiment AS e USING (experiment_id)
         WHERE x.exposed_at < a.assigned_at
            OR x.exposed_at >= e.ends_on + 1
            OR x.exposed_at > now()
        UNION ALL
        SELECT 'conversion', c.conversion_id
          FROM experiment_conversion AS c
          JOIN experiment_assignment AS a USING (assignment_id)
          JOIN experiment AS e USING (experiment_id)
          JOIN transaction AS t USING (transaction_id)
         WHERE t.customer_id <> a.customer_id
            OR t.occurred_at < a.assigned_at
            OR t.occurred_at >= a.assigned_at
                                + make_interval(days => e.conversion_window_days)
            OR t.occurred_at >= e.ends_on + 1
            OR c.converted_at <> t.occurred_at
        UNION ALL
        SELECT 'observed', experiment_id
          FROM experiment
         WHERE data_origin = 'OBSERVED'
        """).fetchall()

    assert violations == []
    exposures, conversions = connection.execute(
        "SELECT (SELECT count(*) FROM experiment_exposure),"
        "       (SELECT count(*) FROM experiment_conversion)"
    ).fetchone()
    assert exposures >= 30
    assert conversions >= 30


def test_an_exempt_table_really_holds_fewer_than_thirty_rows(
    connection: Connection[Any],
) -> None:
    """AGENTS.md: an exemption is for a domain smaller than the minimum. A listed
    table that holds 30 rows or more no longer needs one."""
    exempt = [
        line.strip()
        for line in _EXEMPT.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert exempt
    for name in exempt:
        (rows,) = connection.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(*name.split(".")))
        ).fetchone()
        assert rows < 30, f"{name} holds {rows} rows and needs no exemption"
