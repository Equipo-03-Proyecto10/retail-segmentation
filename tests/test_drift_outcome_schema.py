"""The outcome test's storage and its read of past segments (F23-11, ADR-0089).

`api_segments.assignment_interval_v1` gives model-registry each customer's segment at a
past instant, and `models.drift_outcome_segment` holds one row per segment of an outcome
test. Every write here happens in a transaction that is rolled back.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (ROOT / "contracts/db/api_segments.assignment_interval.v1.md").read_text()
COLUMNS = [
    "customer_id",
    "label_code",
    "run_id",
    "valid_from",
    "valid_to",
    "data_origin",
]

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def database(owner_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(owner_url, autocommit=True) as connection:
        yield connection


@pytest.fixture
def rolled_back(owner_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(owner_url) as connection:
        yield connection
        connection.rollback()


def _read_as(connection: psycopg.Connection, role: str) -> list[tuple]:
    with connection.cursor() as cur:
        cur.execute(f"SET ROLE {role}")
        try:
            cur.execute(
                "SELECT customer_id, label_code, run_id, valid_from, valid_to,"
                " data_origin FROM api_segments.assignment_interval_v1"
            )
            return cur.fetchall()
        finally:
            cur.execute("RESET ROLE")


def _report(connection: psycopg.Connection, kind: str) -> int:
    (version_id,) = connection.execute(
        "SELECT version_id FROM models.model_version WHERE state = 'PUBLISHED'"
    ).fetchone()
    (report_id,) = connection.execute(
        "INSERT INTO models.drift_report (version_id, kind, comparison, window_start,"
        " window_end, customer_count) VALUES (%s, %s, 'COMPARED',"
        " '2001-01-01T03:00Z', '2001-01-31T03:00Z', 1000) RETURNING report_id",
        (version_id, kind),
    ).fetchone()
    return report_id


def _savepoint_refuses(connection: psycopg.Connection, sql: str, params=()) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        with connection.transaction():
            connection.execute(sql, params)


def test_the_contract_names_its_object_producer_consumer_and_columns() -> None:
    assert "| Object | `api_segments.assignment_interval_v1` |" in CONTRACT
    assert "| Producer | segments |" in CONTRACT
    assert "| Consumers | model-registry |" in CONTRACT
    table = CONTRACT.split("## Columns", 1)[1].split("## What is a row", 1)[0]
    assert re.findall(r"^\| `(\w+)` \|", table, re.M) == COLUMNS


def test_the_view_is_the_history_row_for_row(database: psycopg.Connection) -> None:
    columns = database.execute(
        "SELECT a.attname FROM pg_attribute a"
        " WHERE a.attrelid = 'api_segments.assignment_interval_v1'::regclass"
        " AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum"
    ).fetchall()
    assert [c[0] for c in columns] == COLUMNS
    (history,) = database.execute(
        "SELECT count(*) FROM public.customer_segment_history"
    ).fetchone()
    assert history >= 30
    (same,) = database.execute(
        "SELECT count(*) FROM api_segments.assignment_interval_v1 v"
        " JOIN public.customer_segment_history h"
        " USING (customer_id, run_id, valid_from)"
        " WHERE v.label_code IS NOT DISTINCT FROM h.label_code"
        " AND v.valid_to IS NOT DISTINCT FROM h.valid_to"
    ).fetchone()
    assert same == history


def test_at_any_instant_a_customer_has_at_most_one_interval(
    database: psycopg.Connection,
) -> None:
    (overlapping,) = database.execute(
        "SELECT count(*) FROM (SELECT v.customer_id FROM"
        " api_segments.assignment_interval_v1 v,"
        " LATERAL (SELECT now() - interval '1 second' AS t) i"
        " WHERE v.valid_from <= i.t AND (v.valid_to IS NULL OR v.valid_to > i.t)"
        " GROUP BY v.customer_id HAVING count(*) > 1) x"
    ).fetchone()
    assert overlapping == 0


def test_model_registry_reads_it_and_no_other_service_does(
    database: psycopg.Connection,
) -> None:
    assert _read_as(database, "svc_models")
    for role in ("svc_sales", "svc_recommendations", "retail_app"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _read_as(database, role)
    with database.cursor() as cur:
        cur.execute("SET ROLE svc_models")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("SELECT 1 FROM public.customer_segment_history")
        finally:
            cur.execute("RESET ROLE")


def test_an_outcome_test_holds_its_variable_its_segments_and_a_concept_drift_alert(
    rolled_back: psycopg.Connection,
) -> None:
    first = _report(rolled_back, "OUTCOME_TEST")
    report = rolled_back.execute(
        "INSERT INTO models.drift_report (version_id, kind, comparison, window_start,"
        " window_end, customer_count, previous_report_id)"
        " SELECT version_id, kind, comparison, window_start + interval '30 days',"
        " window_end + interval '30 days', customer_count, report_id"
        " FROM models.drift_report WHERE report_id = %s RETURNING report_id",
        (first,),
    ).fetchone()[0]
    rolled_back.execute(
        "INSERT INTO models.drift_variable (report_id, variable, state)"
        " VALUES (%s, 'REPURCHASE_RATE', 'ALARM')",
        (report,),
    )
    rolled_back.execute(
        "INSERT INTO models.drift_outcome_segment (report_id, label_code,"
        " reference_customers, reference_repurchasers, current_customers,"
        " current_repurchasers, z, p_value, adjusted_p_value, result, persisted)"
        " VALUES (%s, 'LOYAL', 500, 240, 480, 170, -3.4, 0.0007, 0.0028,"
        " 'PASSED', true), (%s, 'LOST', 90, 7, 400, 30, NULL, NULL, NULL,"
        " 'NOT_TESTED', false)",
        (report, report),
    )
    rolled_back.execute(
        "INSERT INTO models.drift_alert (report_id, variable, previous_report_id,"
        " claim, review_code) VALUES (%s, 'REPURCHASE_RATE', %s, 'CONCEPT_DRIFT',"
        " 'REVIEW_PREDICTIVE_RELATION')",
        (report, first),
    )
    with pytest.raises(psycopg.errors.Error):
        with rolled_back.transaction():
            rolled_back.execute(
                "UPDATE models.drift_outcome_segment SET persisted = false"
                " WHERE report_id = %s",
                (report,),
            )


def test_a_data_drift_report_can_never_claim_concept_drift(
    rolled_back: psycopg.Connection,
) -> None:
    data = _report(rolled_back, "DATA_DRIFT")
    _savepoint_refuses(
        rolled_back,
        "INSERT INTO models.drift_variable (report_id, variable, state)"
        " VALUES (%s, 'REPURCHASE_RATE', 'ALARM')",
        (data,),
    )
    rolled_back.execute(
        "INSERT INTO models.drift_variable (report_id, variable, state)"
        " VALUES (%s, 'R', 'ALARM')",
        (data,),
    )
    for claim, review in (
        ("CONCEPT_DRIFT", "REVIEW_FEATURE_DISTRIBUTION"),
        ("CONCEPT_DRIFT", "REVIEW_PREDICTIVE_RELATION"),
        ("DISTRIBUTION_CHANGE", "REVIEW_PREDICTIVE_RELATION"),
        ("DISTRIBUTION_CHANGE", "REVIEW_SEGMENT_MIX"),
    ):
        _savepoint_refuses(
            rolled_back,
            "INSERT INTO models.drift_alert (report_id, variable,"
            " previous_report_id, claim, review_code)"
            " VALUES (%s, 'R', 1, %s, %s)",
            (data, claim, review),
        )


def test_an_outcome_report_holds_no_distribution_variable(
    rolled_back: psycopg.Connection,
) -> None:
    report = _report(rolled_back, "OUTCOME_TEST")
    for variable in ("SEGMENT_SHARES", "R", "F", "M"):
        _savepoint_refuses(
            rolled_back,
            "INSERT INTO models.drift_variable (report_id, variable, state)"
            " VALUES (%s, %s, 'NORMAL')",
            (report, variable),
        )


@pytest.mark.parametrize(
    "values",
    [
        # A segment below the minimum has no statistic.
        (50, 5, 400, 40, 1.0, 0.3, 0.6, "NOT_TESTED", False),
        # A tested segment has all three.
        (500, 50, 400, 40, 1.0, None, None, "NOT_PASSED", False),
        # Only a segment that passed here can persist.
        (500, 50, 400, 80, 3.0, 0.002, 0.004, "NOT_PASSED", True),
        # More repurchasers than customers.
        (500, 501, 400, 40, 1.0, 0.3, 0.6, "NOT_PASSED", False),
        # Holm never lowers a p-value.
        (500, 50, 400, 40, 1.0, 0.3, 0.2, "NOT_PASSED", False),
    ],
)
def test_a_segment_row_that_breaks_the_test_is_refused(
    rolled_back: psycopg.Connection, values: tuple
) -> None:
    report = _report(rolled_back, "OUTCOME_TEST")
    _savepoint_refuses(
        rolled_back,
        "INSERT INTO models.drift_outcome_segment (report_id, label_code,"
        " reference_customers, reference_repurchasers, current_customers,"
        " current_repurchasers, z, p_value, adjusted_p_value, result, persisted)"
        " VALUES (%s, 'LOYAL', %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (report, *values),
    )
