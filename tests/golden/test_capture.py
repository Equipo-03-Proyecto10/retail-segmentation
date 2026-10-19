"""The capture changes nothing, and repeats itself (F13-11, #470).

Run in CI's `database` job, against the seed database, as the web's role.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from tests.golden import capture

pytestmark = pytest.mark.postgres

# One statement, no table name composed into SQL.
STATE = """
SELECT (SELECT count(*) FROM transaction),
       (SELECT count(*) FROM transaction_line),
       (SELECT count(*) FROM inventory),
       (SELECT sum(quantity_on_hand) FROM inventory),
       (SELECT count(*) FROM app_user),
       (SELECT count(*) FROM app_user WHERE is_active),
       (SELECT count(*) FROM app_session),
       (SELECT count(*) FROM campaign),
       (SELECT string_agg(status, ',' ORDER BY campaign_id) FROM campaign),
       (SELECT count(*) FROM experiment),
       (SELECT count(*) FROM experiment_group),
       (SELECT count(*) FROM audit_log),
       (SELECT count(*) FROM customer),
       (SELECT count(*) FROM segmentation_run),
       (SELECT count(*) FROM customer_segment_history),
       (SELECT count(*) FROM product WHERE is_active)
"""


def _state(url: str) -> tuple[Any, ...]:
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(STATE)
        return cursor.fetchone()


def _capture_all(url: str) -> dict[str, dict[str, Any]]:
    with psycopg.connect(url) as connection:
        fixtures = {name: capture.capture(connection, name) for name in capture.PORTS}
        connection.rollback()
    return fixtures


@pytest.fixture(scope="module")
def two_captures(app_url: str) -> tuple[Any, ...]:
    before = _state(app_url)
    first = _capture_all(app_url)
    second = _capture_all(app_url)
    return before, _state(app_url), first, second


def test_a_full_capture_leaves_the_database_unchanged(two_captures) -> None:
    before, after, _, _ = two_captures
    assert after == before


def test_two_captures_differ_only_in_the_capture_date(two_captures) -> None:
    _, _, first, second = two_captures
    for name in capture.PORTS:
        a, b = first[name], second[name]
        a["header"].pop("captured_on")
        b["header"].pop("captured_on")
        assert a == b, name


def _outcome(fixtures: dict[str, Any], port: str, case_id: str) -> dict[str, Any]:
    return next(c for c in fixtures[port]["cases"] if c["id"] == case_id)["outcome"]


def test_the_cases_cover_what_adr_0045_lists(two_captures) -> None:
    _, _, fixtures, _ = two_captures
    assert _outcome(fixtures, "sales", "accepted-line")["outcome"] == "accepted"
    assert _outcome(fixtures, "sales", "service-namespace")["reason"].startswith(
        "malformed row"
    )
    assert _outcome(fixtures, "sales", "insufficient-stock")["outcome"] == "refused"

    statuses = {
        c["id"]: c["outcome"]["status"] for c in fixtures["recommendations"]["cases"]
    }
    seeded = [case_id for case_id in statuses if case_id.startswith("customer-")]
    assert len(seeded) == 30
    assert statuses["synthetic-no-history"] == "no_segment"
    assert statuses["synthetic-unassigned"] == "no_segment"
    assert statuses["synthetic-no-sale"] == "no_usual_store"

    assert _outcome(fixtures, "users", "create-second-administrator")["error"] == (
        "SingleAdministratorError"
    )
    assert _outcome(fixtures, "users", "transfer-administrator")["observed"] == {
        "from_role": "ANALYST",
        "to_role": "ADMIN",
    }
    assert _outcome(fixtures, "users", "create-case-variant-duplicate-email")[
        "error"
    ] == ("DuplicateEmailError")

    campaigns = {c["id"]: c["outcome"] for c in fixtures["campaigns"]["cases"]}
    assert len([c for c in campaigns if c.startswith("transition-")]) == 13
    assert "ended on" in campaigns["transition-draft-activate"]["reason"]
    assert campaigns["activate-draft-with-no-experiment"]["outcome"] == "accepted"
    assert "no treatment group" in (
        campaigns["activate-draft-with-control-only-experiment"]["reason"]
    )


def test_the_written_file_is_the_capture(app_url: str, tmp_path) -> None:
    assert (
        capture.main(
            ["--service", "users", "--out", str(tmp_path), "--database-url", app_url]
        )
        == 0
    )
    written = (tmp_path / "users.json").read_text()
    assert written.endswith("\n")
    assert '"port": "users"' in written
