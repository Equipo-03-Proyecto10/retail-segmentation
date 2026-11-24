"""The outcome test (F23-11, ADR-0065, ADR-0089), pure and against the seeded database.

The database tests feed each cohort through a temporary table in place of the read of
the assignment history and the net purchases, as `test_monitor.py` feeds profiles, so a
cohort holds exactly the customers and repurchases a test plants. One test runs the
real read as `svc_models`, to prove the grants and the view.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import psycopg
import pytest
from flask.testing import FlaskClient
from psycopg import sql

from app import outcome
from app.outcome import Side

H = outcome.HORIZON


# ---------- the statistics ----------


def test_the_z_test_is_the_contracts_example() -> None:
    z, p_value = outcome.z_test(Side(5714, 2743), Side(5421, 2063))
    assert round(z, 2) == -10.59
    assert f"{p_value:.2e}" == "3.16e-26"


def test_equal_rates_of_zero_or_one_differ_by_nothing() -> None:
    assert outcome.z_test(Side(200, 0), Side(300, 0)) == (0.0, 1.0)
    assert outcome.z_test(Side(200, 200), Side(300, 300)) == (0.0, 1.0)


def test_holm_steps_down_and_never_decreases() -> None:
    assert outcome.holm([0.01, 0.04, 0.03, 0.005]) == pytest.approx(
        [0.03, 0.06, 0.06, 0.02]
    )
    assert outcome.holm([0.9, 0.8]) == [1.0, 1.0]


LABELS = ["CHAMPION", "LOYAL", "LOST"]
REFERENCE = {
    "CHAMPION": Side(300, 180),
    "LOYAL": Side(500, 240),
    "LOST": Side(400, 32),
}


def test_a_change_in_the_population_alone_passes_nothing() -> None:
    # Three times the champions, a fifth of the loyal, the same rate in each.
    current = {
        "CHAMPION": Side(900, 540),
        "LOYAL": Side(100, 48),
        "LOST": Side(1200, 96),
    }
    found = outcome.judge(LABELS, REFERENCE, current, set())
    assert [s.result for s in found] == ["NOT_PASSED"] * 3
    assert outcome.state(found) == "NORMAL"


def test_a_change_in_what_a_segment_predicts_passes_and_persists_the_second_time() -> (
    None
):
    current = {**REFERENCE, "LOYAL": Side(500, 150)}  # 48% to 30%
    first = outcome.judge(LABELS, REFERENCE, current, set())
    assert [s.result for s in first] == ["NOT_PASSED", "PASSED", "NOT_PASSED"]
    assert outcome.state(first) == "ALARM_PENDING"
    second = outcome.judge(LABELS, REFERENCE, current, {"LOYAL"})
    assert [s.persisted for s in second] == [False, True, False]
    assert outcome.state(second) == "ALARM"


def test_a_significant_change_under_five_points_does_not_pass() -> None:
    big = {"LOYAL": Side(20000, 9600)}
    found = outcome.judge(["LOYAL"], big, {"LOYAL": Side(20000, 9000)}, set())
    assert found[0].adjusted_p_value < outcome.ALPHA
    assert found[0].result == "NOT_PASSED"  # 3 points


def test_a_segment_under_the_minimum_on_either_side_is_not_tested() -> None:
    current = {**REFERENCE, "LOST": Side(99, 50)}
    found = {s.label_code: s for s in outcome.judge(LABELS, REFERENCE, current, set())}
    assert found["LOST"].result == "NOT_TESTED"
    assert found["LOST"].z is None and found["LOST"].adjusted_p_value is None


def test_cohorts_start_at_the_tenure_and_are_one_horizon_apart() -> None:
    published = datetime.fromisoformat("2026-01-10T05:00:00+00:00")
    t0 = outcome.first_cohort(published)
    assert t0 == datetime.fromisoformat("2026-01-11T03:00:00+00:00")
    assert outcome.first_cohort(t0) == t0
    assert outcome.matured(t0, t0 + 2 * H - timedelta(seconds=1)) == []
    found = outcome.matured(t0, t0 + 4 * H)
    assert found == [t0 + H, t0 + 2 * H, t0 + 3 * H]
    for first, second in zip(found, found[1:], strict=False):
        assert second - first == H  # outcome windows (t, t + H] never overlap


def test_the_review_names_each_segment_its_two_rates_and_no_other_claim() -> None:
    current = {**REFERENCE, "LOYAL": Side(500, 150)}
    found = outcome.judge(LABELS, REFERENCE, current, {"LOYAL"})
    text = outcome.review_text(found)
    assert text.startswith("Customers of LOYAL repurchased within 30 days at 30.0%")
    assert "against 48.0% in the reference cohort" in text
    assert "CHAMPION" not in text
    assert len(text) <= 500


# ---------- the pass, against the seeded database ----------


@pytest.fixture(autouse=True)
def forget_outcomes(owner: psycopg.Connection) -> Iterator[None]:
    (floor,) = owner.execute(
        "SELECT coalesce(max(report_id), 0) FROM models.drift_report"
    ).fetchone()
    yield
    owner.execute("SET session_replication_role = replica")
    for table in ("drift_alert", "drift_outcome_segment", "drift_variable"):
        owner.execute(
            sql.SQL("DELETE FROM models.{} WHERE report_id > %s").format(
                sql.Identifier(table)
            ),
            (floor,),
        )
    owner.execute("DELETE FROM models.drift_report WHERE report_id > %s", (floor,))
    owner.execute("SET session_replication_role = origin")


@pytest.fixture
def t0(owner: psycopg.Connection) -> datetime:
    (published,) = owner.execute(
        "SELECT max(p.published_at) FROM models.model_publication AS p"
        " JOIN models.model_version AS v USING (version_id)"
        " WHERE v.state = 'PUBLISHED'"
    ).fetchone()
    return outcome.first_cohort(published)


@pytest.fixture
def plant(service_db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Make each cohort hold exactly these customers: (label, customers, repurchasers)
    per as-of date."""
    service_db.execute("DROP TABLE IF EXISTS pg_temp.drift_cohort")
    service_db.execute(
        "CREATE TEMP TABLE drift_cohort (as_of timestamptz, label_code text,"
        " repurchased boolean)"
    )
    monkeypatch.setattr(
        outcome,
        "_COHORT",
        "SELECT label_code, count(*)::int, count(*) FILTER (WHERE repurchased)::int"
        " FROM pg_temp.drift_cohort WHERE as_of = %(as_of)s GROUP BY label_code",
    )

    def load(as_of: datetime, sides: dict[str, Side]) -> None:
        rows = []
        for label, side in sides.items():
            rows += [(as_of, label, True)] * side.repurchasers
            rows += [(as_of, label, False)] * (side.customers - side.repurchasers)
        with service_db.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO pg_temp.drift_cohort VALUES (%s, %s, %s)", rows
            )

    return load


def _report(owner: psycopg.Connection, report_id: int) -> dict[str, Any]:
    comparison, start, end, customers, previous = owner.execute(
        "SELECT comparison, window_start, window_end, customer_count,"
        " previous_report_id FROM models.drift_report WHERE report_id = %s",
        (report_id,),
    ).fetchone()
    state = owner.execute(
        "SELECT state FROM models.drift_variable WHERE report_id = %s",
        (report_id,),
    ).fetchone()
    segments = dict(
        owner.execute(
            "SELECT label_code, result FROM models.drift_outcome_segment"
            " WHERE report_id = %s",
            (report_id,),
        ).fetchall()
    )
    alerts = owner.execute(
        "SELECT variable, claim, review_code, previous_report_id"
        " FROM models.drift_alert WHERE report_id = %s",
        (report_id,),
    ).fetchall()
    return {
        "comparison": comparison,
        "window": (start, end),
        "customers": customers,
        "previous": previous,
        "state": None if state is None else state[0],
        "segments": segments,
        "alerts": alerts,
    }


def _get(client: FlaskClient, token: Any, path: str) -> Any:
    return client.get(path, headers=token("ANALYST")).get_json()


def test_an_immature_cohort_is_never_tested(
    service_db: psycopg.Connection, t0: datetime, plant: Any
) -> None:
    plant(t0, REFERENCE)
    plant(t0 + H, REFERENCE)
    assert outcome.run_once(service_db, t0 + 2 * H - timedelta(seconds=1)) == []


def test_a_planted_change_is_claimed_on_the_second_cohort_and_served(
    client: FlaskClient,
    token: Any,
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    t0: datetime,
    plant: Any,
) -> None:
    changed = {**REFERENCE, "LOYAL": Side(500, 150)}
    plant(t0, REFERENCE)
    plant(t0 + H, changed)
    plant(t0 + 2 * H, changed)
    first, second = outcome.run_once(service_db, t0 + 3 * H + timedelta(hours=1))
    one, two = _report(owner, first), _report(owner, second)
    assert one["window"] == (t0 + H, t0 + 2 * H)
    assert one["previous"] is None  # the reference cohort has no report
    assert one["state"] == "ALARM_PENDING" and one["alerts"] == []
    assert one["segments"]["LOYAL"] == "PASSED"
    assert two["previous"] == first
    assert two["state"] == "ALARM"
    assert two["alerts"] == [
        ("REPURCHASE_RATE", "CONCEPT_DRIFT", "REVIEW_PREDICTIVE_RELATION", first)
    ]
    report = _get(client, token, f"/v1/drift-reports/{second}")
    assert report["kind"] == "OUTCOME_TEST" and report["variables"] == []
    test = report["outcome_test"]
    assert test["relation"] == "REPURCHASE_GIVEN_SEGMENT"
    assert test["horizon_days"] == 30 and test["claim"] == "CONCEPT_DRIFT"
    loyal = next(s for s in test["segments"] if s["label_code"] == "LOYAL")
    assert loyal["reference"] == {"customers": 500, "repurchasers": 240, "rate": 0.48}
    assert loyal["current"]["rate"] == 0.3 and loyal["difference_points"] == -18.0
    assert loyal["persisted"] is True
    first_view = _get(client, token, f"/v1/drift-reports/{first}")
    assert first_view["outcome_test"]["claim"] is None  # passed once claims nothing
    (alert_id,) = report["alert_ids"]
    alert = _get(client, token, f"/v1/drift-alerts/{alert_id}")
    assert alert["claim"] == "CONCEPT_DRIFT" and alert["variable"] == "REPURCHASE_RATE"
    assert [s["label_code"] for s in alert["outcome_test"]["segments"]] == ["LOYAL"]
    assert alert["suggested_review"]["code"] == "REVIEW_PREDICTIVE_RELATION"
    assert "LOYAL" in alert["suggested_review"]["text"]
    assert alert["statistics"] == [] and alert["bins"] == []
    for path in (f"/v1/drift-reports/{second}", f"/v1/drift-alerts/{alert_id}"):
        xml = client.get(
            path, headers={**token("ANALYST"), "Accept": "application/xml"}
        )
        assert xml.status_code == 200, xml.get_data(as_text=True)
        assert b"<claim>CONCEPT_DRIFT</claim>" in xml.data


def test_a_change_in_the_population_alone_is_never_concept_drift(
    client: FlaskClient,
    token: Any,
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    t0: datetime,
    plant: Any,
) -> None:
    moved = {
        "CHAMPION": Side(900, 540),
        "LOYAL": Side(100, 48),
        "LOST": Side(1200, 96),
    }
    plant(t0, REFERENCE)
    plant(t0 + H, moved)
    plant(t0 + 2 * H, moved)
    written = outcome.run_once(service_db, t0 + 3 * H + timedelta(hours=1))
    for report_id in written:
        found = _report(owner, report_id)
        assert found["state"] == "NORMAL" and found["alerts"] == []
        assert all(result != "PASSED" for result in found["segments"].values())
        assert {found["segments"][label] for label in REFERENCE} == {"NOT_PASSED"}
        view = _get(client, token, f"/v1/drift-reports/{report_id}")
        assert view["outcome_test"]["claim"] is None
        assert "concept drift" not in str(view).lower()


def test_a_cohort_with_no_segment_of_100_a_side_is_insufficient(
    client: FlaskClient,
    token: Any,
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    t0: datetime,
    plant: Any,
) -> None:
    plant(t0, {"LOYAL": Side(99, 40), "LOST": Side(500, 40)})
    plant(t0 + H, {"LOYAL": Side(400, 100), "LOST": Side(90, 9)})
    (report_id,) = outcome.run_once(service_db, t0 + 2 * H + timedelta(hours=1))
    found = _report(owner, report_id)
    assert found["comparison"] == "INSUFFICIENT_DATA"
    assert found["customers"] == 490
    assert found["state"] is None and found["segments"] == {}
    assert _get(client, token, f"/v1/drift-reports/{report_id}")["outcome_test"] is None


def test_repeating_a_pass_writes_nothing_and_a_later_pass_continues(
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    t0: datetime,
    plant: Any,
) -> None:
    changed = {**REFERENCE, "LOYAL": Side(500, 150)}
    for k in range(3):
        plant(t0 + k * H, REFERENCE if k == 0 else changed)
    (first,) = outcome.run_once(service_db, t0 + 2 * H + timedelta(hours=1))
    assert outcome.run_once(service_db, t0 + 2 * H + timedelta(hours=2)) == []
    (second,) = outcome.run_once(service_db, t0 + 3 * H + timedelta(hours=1))
    found = _report(owner, second)
    assert found["previous"] == first and found["state"] == "ALARM"
    (version_id,) = owner.execute(
        "SELECT version_id FROM models.drift_report WHERE report_id = %s", (first,)
    ).fetchone()
    ids = [
        str(uuid5(NAMESPACE_URL, f"drift:{version_id}:OUTCOME_TEST:{end.isoformat()}"))
        for end in (
            (t0 + 2 * H).astimezone(UTC),
            (t0 + 3 * H).astimezone(UTC),
        )
    ]
    (events,) = owner.execute(
        "SELECT count(*) FROM audit_events.domain_event"
        " WHERE kind = 'CALCULATION_RUN' AND event_id::text = ANY(%s)",
        (ids,),
    ).fetchone()
    assert events == 2


def test_a_publication_inside_a_cohort_is_not_compared(
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    t0: datetime,
    plant: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plant(t0, REFERENCE)
    plant(t0 + H, REFERENCE)
    # As if a publication of another version fell inside the cohort's window.
    monkeypatch.setattr(
        outcome, "_CHANGES", "SELECT 1 WHERE %s IS NOT NULL OR %s IS NOT NULL"
    )
    (report_id,) = outcome.run_once(service_db, t0 + 2 * H + timedelta(hours=1))
    found = _report(owner, report_id)
    assert found["comparison"] == "NOT_COMPARED"
    assert found["state"] is None and found["segments"] == {}


def test_the_real_cohort_read_runs_as_the_service(
    service_db: psycopg.Connection, owner: psycopg.Connection
) -> None:
    (version_id,) = owner.execute(
        "SELECT version_id FROM models.model_version WHERE state = 'PUBLISHED'"
    ).fetchone()
    (now,) = owner.execute("SELECT now()").fetchone()
    with service_db.transaction():
        cohort = outcome._cohort(service_db, version_id, now - timedelta(days=1))
    assert all(side.repurchasers <= side.customers for side in cohort.values())
    assert sum(side.customers for side in cohort.values()) <= 30


def test_the_real_cohort_keeps_an_incremental_run_under_the_version_it_links(
    owner_url: str,
) -> None:
    """ADR-0087 records the version on an incremental run, and ADR-0092 links the run
    to it in the same transaction (#1208). The member stays in the cohort, and the
    link and the recorded version agree."""
    with psycopg.connect(owner_url) as connection:
        try:
            version_id, method, window_days, rule_set_id = connection.execute(
                "SELECT version_id, method, window_days, rule_set_id"
                " FROM models.model_version WHERE state = %s",
                ("PUBLISHED",),
            ).fetchone()
            customer_id, old_label = connection.execute(
                "SELECT customer_id, label_code FROM public.customer_segment_history"
                " WHERE valid_to IS NULL AND label_code IS NOT NULL LIMIT %s",
                (1,),
            ).fetchone()
            (new_label,) = connection.execute(
                "SELECT label_code FROM public.segment_label WHERE label_code <> %s"
                " ORDER BY label_code LIMIT %s",
                (old_label, 1),
            ).fetchone()
            (before_at,) = connection.execute("SELECT clock_timestamp()").fetchone()
            before = sum(
                side.customers
                for side in outcome._cohort(connection, version_id, before_at).values()
            )
            connection.execute(
                "SELECT api_segments.apply_assignment_v1(%s, %s, %s, %s, %s, %s,"
                " %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    uuid4(),
                    customer_id,
                    new_label,
                    1,
                    1,
                    1,
                    before_at,
                    1,
                    1,
                    "SALE",
                    version_id,
                    method,
                    window_days,
                    rule_set_id,
                ),
            )
            run_id, recorded_version = connection.execute(
                "SELECT run_id, model_version_id"
                " FROM api_segments.assignment_interval_v1"
                " WHERE customer_id = %s AND valid_to IS NULL",
                (customer_id,),
            ).fetchone()
            assert recorded_version == version_id
            assert connection.execute(
                "SELECT version_id FROM models.run_version WHERE run_id = %s",
                (run_id,),
            ).fetchone() == (version_id,)
            (after_at,) = connection.execute("SELECT clock_timestamp()").fetchone()
            after = sum(
                side.customers
                for side in outcome._cohort(connection, version_id, after_at).values()
            )
            assert after == before
        finally:
            connection.rollback()
