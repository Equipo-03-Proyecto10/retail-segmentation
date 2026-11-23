"""The monitor and the drift endpoints, against the seeded database."""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from xml.etree import ElementTree

import psycopg
import pytest
from flask.testing import FlaskClient

from app import drift, monitor

NS = "{https://mosaiq.maxthecoder.online/xml/model-registry/v1}"


@pytest.fixture(autouse=True)
def forget_drift(owner: psycopg.Connection) -> Iterator[None]:
    (floor,) = owner.execute(
        "SELECT coalesce(max(report_id), 0) FROM models.drift_report"
    ).fetchone()
    (alert_floor,) = owner.execute(
        "SELECT coalesce(max(alert_id), 0) FROM models.drift_alert"
    ).fetchone()
    yield
    owner.execute("SET session_replication_role = replica")
    owner.execute("DELETE FROM models.drift_alert WHERE alert_id > %s", (alert_floor,))
    owner.execute("DELETE FROM models.drift_bin WHERE report_id > %s", (floor,))
    owner.execute("DELETE FROM models.drift_statistic WHERE report_id > %s", (floor,))
    owner.execute("DELETE FROM models.drift_variable WHERE report_id > %s", (floor,))
    owner.execute("DELETE FROM models.drift_report WHERE report_id > %s", (floor,))
    owner.execute("SET session_replication_role = origin")


@pytest.fixture
def reference(owner: psycopg.Connection) -> dict[str, Any]:
    (value,) = owner.execute(
        "SELECT reference_distribution FROM models.model_version"
        " WHERE state = 'PUBLISHED'"
    ).fetchone()
    return value


@pytest.fixture
def after_publication(owner: psycopg.Connection) -> datetime:
    """A clock at which the seeded publication is out of every window."""
    (published,) = owner.execute(
        "SELECT max(published_at) FROM models.model_publication"
    ).fetchone()
    return monitor.boundary(published + timedelta(days=45))


def profiles(
    reference: dict[str, Any], n: int, shift: bool = False
) -> list[tuple[str, int, int, str]]:
    """n scored customers who match the reference, or who sit in its worst bins."""
    labels = reference["labels"]
    codes: list[str] = []
    for label in labels:
        codes += [label["label_code"]] * round(label["share"] * n)
    codes += [labels[-1]["label_code"]] * (n - len(codes))
    if shift:
        codes = [labels[-1]["label_code"]] * n
    columns = []
    for feature in reference["features"]:
        edges, shares = feature["bin_edges"], feature["proportions"]
        values: list[float] = []
        for index, share in enumerate(shares):
            mid = (edges[index] + edges[index + 1]) / 2
            values += [mid] * round(share * n)
        values += [values[-1]] * (n - len(values))
        columns.append([values[-1]] * n if shift else values[:n])
    return list(zip(codes[:n], *columns, strict=True))  # type: ignore[arg-type]


@pytest.fixture
def feed(service_db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Make the window hold exactly these profiles, as the view would."""

    service_db.execute("DROP TABLE IF EXISTS pg_temp.drift_feed")
    service_db.execute(
        "CREATE TEMP TABLE drift_feed (label_code text, r_days int, f_count int,"
        " m_total numeric, computed_at timestamptz)"
    )

    def load(rows: list[tuple[Any, ...]]) -> None:
        with service_db.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO pg_temp.drift_feed (label_code, r_days, f_count,"
                " m_total) VALUES (%s, %s, %s, %s)",
                rows,
            )
        monkeypatch.setattr(
            monitor,
            "_PROFILES",
            "SELECT label_code, r_days, f_count, m_total, computed_at"
            " FROM pg_temp.drift_feed",
        )

    return load


def one_pass(db: psycopg.Connection, now: datetime) -> int | None:
    """A pass owns its transactions: it reads in one snapshot and writes in
    another, so it is called on a connection with none open."""
    return monitor.run_once(db, now)


def report(owner: psycopg.Connection, report_id: int) -> dict[str, Any]:
    row = owner.execute(
        "SELECT comparison, not_compared_reason, customer_count, previous_report_id"
        " FROM models.drift_report WHERE report_id = %s",
        (report_id,),
    ).fetchone()
    states = dict(
        owner.execute(
            "SELECT variable, state FROM models.drift_variable WHERE report_id = %s",
            (report_id,),
        ).fetchall()
    )
    return {
        "comparison": row[0],
        "reason": row[1],
        "customers": row[2],
        "previous": row[3],
        "states": states,
    }


def test_a_window_that_matches_the_reference_is_compared_and_normal(
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
) -> None:
    feed(profiles(reference, 1000))
    result = report(owner, one_pass(service_db, after_publication))
    assert result["comparison"] == "COMPARED"
    assert result["customers"] == 1000
    assert result["states"] == {
        "SEGMENT_SHARES": "NORMAL",
        "R": "NORMAL",
        "F": "NORMAL",
        "M": "NORMAL",
    }


def test_every_statistic_is_recorded_over_each_variable(
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
) -> None:
    feed(profiles(reference, 1000, shift=True))
    report_id = one_pass(service_db, after_publication)
    rows = owner.execute(
        "SELECT variable, statistic FROM models.drift_statistic WHERE report_id = %s"
        " ORDER BY 1, 2",
        (report_id,),
    ).fetchall()
    assert rows == [
        ("F", "PSI"),
        ("M", "PSI"),
        ("R", "PSI"),
        ("SEGMENT_SHARES", "CHI_SQUARE"),
        ("SEGMENT_SHARES", "PSI"),
    ]
    (bins,) = owner.execute(
        "SELECT count(*) FROM models.drift_bin WHERE report_id = %s AND variable = 'R'",
        (report_id,),
    ).fetchone()
    assert bins == len(reference["features"][0]["proportions"]) + 1


def test_an_alarm_held_two_windows_alerts_and_alerts_again_while_it_continues(
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
) -> None:
    feed(profiles(reference, 1000, shift=True))
    first = one_pass(service_db, after_publication)
    assert report(owner, first)["states"]["SEGMENT_SHARES"] == "ALARM_PENDING"
    assert (
        owner.execute(
            "SELECT count(*) FROM models.drift_alert WHERE report_id = %s", (first,)
        ).fetchone()[0]
        == 0
    )
    second = one_pass(service_db, after_publication + timedelta(days=1))
    assert report(owner, second)["states"]["SEGMENT_SHARES"] == "ALARM"
    assert report(owner, second)["previous"] == first
    alerts = owner.execute(
        "SELECT variable, claim, review_code, previous_report_id"
        " FROM models.drift_alert"
        " WHERE report_id = %s ORDER BY variable",
        (second,),
    ).fetchall()
    assert [a[0] for a in alerts] == ["F", "M", "R", "SEGMENT_SHARES"]
    assert all(a[1] == "DISTRIBUTION_CHANGE" and a[3] == first for a in alerts)
    third = one_pass(service_db, after_publication + timedelta(days=2))
    # listDriftAlerts: an alarm that continues raises a further alert in each
    # report whose variable is still at ALARM.
    again = owner.execute(
        "SELECT variable, previous_report_id FROM models.drift_alert"
        " WHERE report_id = %s ORDER BY variable",
        (third,),
    ).fetchall()
    assert again == [(v, second) for v in ("F", "M", "R", "SEGMENT_SHARES")]


def test_repeating_a_pass_on_the_same_data_changes_nothing(
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
) -> None:
    feed(profiles(reference, 1000, shift=True))
    one = one_pass(service_db, after_publication)
    counts = owner.execute(
        "SELECT (SELECT count(*) FROM models.drift_report),"
        " (SELECT count(*) FROM models.drift_bin),"
        " (SELECT count(*) FROM audit_events.domain_event)"
    ).fetchone()
    two = one_pass(service_db, after_publication + timedelta(hours=5))
    assert two == one
    assert (
        owner.execute(
            "SELECT (SELECT count(*) FROM models.drift_report),"
            " (SELECT count(*) FROM models.drift_bin),"
            " (SELECT count(*) FROM audit_events.domain_event)"
        ).fetchone()
        == counts
    )


def test_two_databases_fed_the_same_data_agree_to_the_digit(
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
) -> None:
    feed(profiles(reference, 600, shift=True))
    one = one_pass(service_db, after_publication)
    two = one_pass(service_db, after_publication + timedelta(days=1))
    query = (
        "SELECT variable, statistic, value, p_value, level FROM models.drift_statistic"
        " WHERE report_id = %s ORDER BY 1, 2"
    )
    assert (
        owner.execute(query, (one,)).fetchall()
        == owner.execute(query, (two,)).fetchall()
    )


def test_fewer_than_200_customers_is_insufficient_data_with_no_statistic(
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
) -> None:
    feed(profiles(reference, drift.MIN_CUSTOMERS - 1, shift=True))
    result = report(owner, one_pass(service_db, after_publication))
    assert result["comparison"] == "INSUFFICIENT_DATA"
    assert result["customers"] == drift.MIN_CUSTOMERS - 1
    assert result["states"] == {}


def test_exactly_200_customers_is_compared(
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
) -> None:
    feed(profiles(reference, drift.MIN_CUSTOMERS))
    assert (
        report(owner, one_pass(service_db, after_publication))["comparison"]
        == "COMPARED"
    )


def test_a_version_change_inside_the_window_is_not_compared(
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    reference: dict[str, Any],
    feed: Any,
) -> None:
    (published,) = owner.execute(
        "SELECT max(published_at) FROM models.model_publication"
    ).fetchone()
    feed(profiles(reference, 1000, shift=True))
    result = report(owner, one_pass(service_db, published + timedelta(days=3)))
    assert result["comparison"] == "NOT_COMPARED"
    assert result["reason"] == "VERSION_CHANGE"
    assert result["states"] == {}


def test_every_pass_is_audited_with_the_window_it_covered(
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
) -> None:
    # Audit events are never removed: each run of the suite audits its own window.
    window = after_publication + timedelta(days=100 + secrets.randbelow(20000))
    feed(profiles(reference, 300))
    report_id = one_pass(service_db, window)
    (version_id,) = owner.execute(
        "SELECT version_id FROM models.drift_report WHERE report_id = %s", (report_id,)
    ).fetchone()
    rows = owner.execute(
        "SELECT kind, source, actor_type, model_version_id, details"
        " FROM audit_events.domain_event WHERE subject_type = 'calculation'"
        " AND subject_id = %s",
        (f"drift-{version_id}-{window:%Y%m%d}",),
    ).fetchall()
    assert len(rows) == 1
    kind, source, actor, model_version, details = rows[0]
    assert (kind, source, actor, model_version) == (
        "CALCULATION_RUN",
        "svc_models",
        "SYSTEM",
        version_id,
    )
    assert details["calculation"] == "DRIFT"
    assert details["status"] == "COMPLETED"
    assert details["input_count"] == 300
    assert details["window_to"].startswith(f"{window:%Y-%m-%d}")
    assert len(details["parameters_digest"]) == 64


def test_a_pass_with_the_audit_down_commits_no_report(
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed(profiles(reference, 300))
    monkeypatch.setattr(
        monitor.audit, "DRIFT_RECORD", "SELECT * FROM api_audit.nothing()"
    )
    (before,) = owner.execute("SELECT count(*) FROM models.drift_report").fetchone()
    with pytest.raises(psycopg.Error):
        one_pass(service_db, after_publication)
    (after,) = owner.execute("SELECT count(*) FROM models.drift_report").fetchone()
    assert after == before


def test_the_tables_refuse_a_change_and_a_claim_only_an_outcome_test_may_make(
    owner: psycopg.Connection,
) -> None:
    with pytest.raises(psycopg.errors.Error):
        owner.execute("UPDATE models.drift_report SET customer_count = 1")
    with pytest.raises(psycopg.errors.Error):
        owner.execute("DELETE FROM models.drift_report")
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute(
            "INSERT INTO models.drift_alert (report_id, variable, previous_report_id,"
            " claim, review_code) VALUES (1, 'R', 1, 'CONCEPT_DRIFT',"
            " 'REVIEW_FEATURE_DISTRIBUTION')"
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute(
            "INSERT INTO models.drift_report (version_id, kind, comparison,"
            " window_start,"
            " window_end, customer_count) VALUES (1, 'DATA_DRIFT', 'COMPARED',"
            " now() - interval '30 days', now(), 199)"
        )


def get(
    client: FlaskClient, token: Any, path: str, role: str = "ANALYST", **headers: str
) -> Any:
    return client.get(path, headers={**token(role), **headers})


def seed_alerts(
    service_db: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
) -> int:
    feed(profiles(reference, 1000, shift=True))
    one_pass(service_db, after_publication)
    return one_pass(service_db, after_publication + timedelta(days=1))  # type: ignore[return-value]


def test_reports_and_alerts_are_served_with_the_version_the_reference_and_the_review(
    client: FlaskClient,
    token: Any,
    service_db: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
) -> None:
    report_id = seed_alerts(service_db, reference, after_publication, feed)
    body = get(client, token, f"/v1/drift-reports/{report_id}").get_json()
    assert body["kind"] == "DATA_DRIFT"
    assert body["comparison"] == "COMPARED"
    assert body["model_version"]["method"] == "RFM_RULES"
    assert body["reference"]["sample_size"] == reference["sample_size"]
    assert [v["variable"] for v in body["variables"]] == [
        "SEGMENT_SHARES",
        "R",
        "F",
        "M",
    ]
    assert body["outcome_test"] is None
    assert body["alert_ids"]
    segment = body["variables"][0]
    assert [s["statistic"] for s in segment["statistics"]] == ["PSI", "CHI_SQUARE"]
    assert segment["statistics"][0]["threshold_basis"] == "VALUE"
    assert segment["statistics"][1]["threshold_basis"] == "P_VALUE"

    alert = get(client, token, f"/v1/drift-alerts/{body['alert_ids'][0]}").get_json()
    assert alert["claim"] == "DISTRIBUTION_CHANGE"
    assert alert["outcome_test"] is None
    assert alert["suggested_review"]["code"] in (
        "REVIEW_SEGMENT_MIX",
        "REVIEW_FEATURE_DISTRIBUTION",
    )
    assert (
        alert["variable"] in alert["suggested_review"]["text"]
        or alert["variable"] == "SEGMENT_SHARES"
    )
    assert alert["model_version"] == body["model_version"]
    assert len(alert["bins"]) <= 3
    contributions = [b["contribution"] for b in alert["bins"]]
    assert contributions == sorted(contributions, reverse=True)
    assert alert["previous_report_id"] == body["previous_report_id"]


def test_a_report_read_never_says_concept_drift(
    client: FlaskClient,
    token: Any,
    service_db: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
) -> None:
    seed_alerts(service_db, reference, after_publication, feed)
    for path in ("/v1/drift-reports", "/v1/drift-alerts"):
        for accept in ("application/json", "application/xml"):
            response = client.get(path, headers={**token("ANALYST"), "Accept": accept})
            assert response.status_code == 200
            assert "concept drift" not in response.get_data(as_text=True).lower()
            assert "CONCEPT_DRIFT" not in response.get_data(as_text=True)


def test_lists_filter_and_page_and_answer_in_xml(
    client: FlaskClient,
    token: Any,
    service_db: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
) -> None:
    seed_alerts(service_db, reference, after_publication, feed)
    page = get(
        client, token, "/v1/drift-reports?comparison=INSUFFICIENT_DATA"
    ).get_json()
    assert all(r["comparison"] == "INSUFFICIENT_DATA" for r in page["reports"])
    alerts = get(client, token, "/v1/drift-alerts?variable=R&size=1").get_json()
    assert [a["variable"] for a in alerts["alerts"]] == ["R"]
    assert get(client, token, "/v1/drift-alerts?variable=NOPE").status_code == 422
    assert (
        get(client, token, "/v1/drift-reports?model_version_id=abc").status_code == 422
    )
    response = client.get(
        "/v1/drift-alerts?variable=R",
        headers={**token("ANALYST"), "Accept": "application/xml"},
    )
    assert response.status_code == 200
    root = ElementTree.fromstring(response.data)
    assert root.find(f".//{NS}claim").text == "DISTRIBUTION_CHANGE"


def test_unknown_ids_roles_and_read_only(client: FlaskClient, token: Any) -> None:
    assert get(client, token, "/v1/drift-reports/999999999").status_code == 404
    assert get(client, token, "/v1/drift-alerts/999999999").status_code == 404
    assert (
        get(client, token, "/v1/drift-reports", role="STORE_MANAGER").status_code == 403
    )
    for role in ("ADMIN", "ANALYST", "MARKETING", "AUDITOR"):
        assert get(client, token, "/v1/drift-reports", role=role).status_code == 200
    assert client.post(
        "/v1/drift-reports", headers=token("ADMIN"), json={}
    ).status_code in (404, 405)


def test_the_seeded_report_is_what_the_monitor_would_write_at_the_seed(
    client: FlaskClient, token: Any, owner: psycopg.Connection
) -> None:
    """The version is published as the seed runs, inside the latest window, so the
    one report the seed can state honestly is not compared, with no customer."""
    (row,) = owner.execute(
        "SELECT array[r.comparison, r.not_compared_reason, r.customer_count::text,"
        " (r.computed_at >= r.window_end)::text,"
        " (p.published_at > r.window_start)::text]"
        " FROM models.drift_report AS r JOIN models.model_publication AS p"
        " USING (version_id) ORDER BY r.report_id LIMIT 1"
    ).fetchone()
    assert row == ["NOT_COMPARED", "VERSION_CHANGE", "0", "true", "true"]
    page = get(client, token, "/v1/drift-reports").get_json()
    assert any(
        r["comparison"] == "NOT_COMPARED" and r["variables"] == []
        for r in page["reports"]
    )
    assert json.dumps(page).count("concept") == 0


# ------------------------------------------------------ what the review of #1125 found


def test_an_older_report_than_the_window_before_does_not_count_for_persistence(
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
) -> None:
    """After days without a report, an alarm starts over: one week-old pending
    alarm and today's are not two consecutive windows."""
    feed(profiles(reference, 1000, shift=True))
    first = one_pass(service_db, after_publication)
    later = one_pass(service_db, after_publication + timedelta(days=4))
    result = report(owner, later)
    assert result["previous"] == first
    assert set(result["states"].values()) == {"ALARM_PENDING"}
    (alerts,) = owner.execute(
        "SELECT count(*) FROM models.drift_alert WHERE report_id = %s", (later,)
    ).fetchone()
    assert alerts == 0


def test_a_version_published_after_the_window_end_is_not_compared_against_it(
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    reference: dict[str, Any],
    feed: Any,
) -> None:
    """Published after today's 03:00: the profiles were scored under the version
    before it, so today's window is not compared (ADR-0064)."""
    (published,) = owner.execute(
        "SELECT max(published_at) FROM models.model_publication"
    ).fetchone()
    now = published + timedelta(seconds=1)
    if monitor.boundary(now) >= published:
        now = published + timedelta(days=1, seconds=-1)  # still before tomorrow's end
    assert monitor.boundary(now) < published
    feed(profiles(reference, 1000))
    result = report(owner, one_pass(service_db, now))
    assert (result["comparison"], result["reason"]) == (
        "NOT_COMPARED",
        "VERSION_CHANGE",
    )


def test_a_label_the_reference_never_saw_is_counted_and_judged(
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
) -> None:
    rows = profiles(reference, 1000)
    code = "OUTSIDE_THE_REFERENCE"
    assert code not in {label["label_code"] for label in reference["labels"]}
    feed(rows + [(code, *rows[0][1:])] * 300)
    report_id = one_pass(service_db, after_publication)
    result = report(owner, report_id)
    assert result["customers"] == 1300
    assert result["states"]["SEGMENT_SHARES"] == "ALARM_PENDING"
    (share,) = owner.execute(
        "SELECT reference_share FROM models.drift_bin WHERE report_id = %s"
        " AND variable = 'SEGMENT_SHARES' AND label = %s",
        (report_id, code),
    ).fetchone()
    assert share == 0.0


def test_r_is_aged_to_the_end_of_the_window() -> None:
    end = datetime(2026, 11, 21, 3, 0, tzinfo=UTC)
    assert (
        monitor._aged(("LOYAL", 5, 2, 10, end - timedelta(days=10, hours=3)), end)[1]
        == 15
    )
    assert monitor._aged(("LOYAL", 5, 2, 10, end + timedelta(hours=2)), end)[1] == 5
    assert monitor._aged(("LOYAL", 5, 2, 10, None), end)[1] == 5


def test_an_event_another_service_audits_during_a_pass_does_not_break_it(
    service_db: psycopg.Connection,
    service_url: str,
    owner: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The write is read committed, so the trail's head is the one committed last,
    not the one of the pass's read snapshot."""
    from uuid import uuid4

    from app import audit

    window = after_publication + timedelta(days=200 + secrets.randbelow(20000))
    feed(profiles(reference, 300))
    original = monitor.drift.segment_bins

    def meanwhile(*args: Any) -> Any:
        with psycopg.connect(service_url, autocommit=True) as other:
            audit.drift_run(
                other,
                event_id=uuid4(),
                version_id=1,
                window=(window - timedelta(days=30), window),
                input_count=0,
                output_count=0,
                digest=None,
                status="FAILED",
                correlation_id=None,
            )
        return original(*args)

    monkeypatch.setattr(monitor.drift, "segment_bins", meanwhile)
    report_id = one_pass(service_db, window)
    assert report_id is not None
    assert report(owner, report_id)["comparison"] == "COMPARED"


def test_an_outcome_test_may_be_insufficient_with_many_customers(
    owner: psycopg.Connection,
) -> None:
    """No segment with 100 on both sides can hold with far more than 200 customers:
    the 200 rule is the data-drift one (ADR-0065)."""
    owner.execute(
        "INSERT INTO models.drift_report (version_id, kind, comparison,"
        " window_start, window_end, customer_count)"
        " SELECT version_id, 'OUTCOME_TEST', 'INSUFFICIENT_DATA',"
        " now() - interval '60 days', now() - interval '30 days', 500"
        " FROM models.model_version WHERE state = 'PUBLISHED'"
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute(
            "INSERT INTO models.drift_report (version_id, kind, comparison,"
            " window_start, window_end, customer_count)"
            " SELECT version_id, 'DATA_DRIFT', 'INSUFFICIENT_DATA',"
            " now() - interval '31 days', now() - interval '1 day', 500"
            " FROM models.model_version WHERE state = 'PUBLISHED'"
        )


@pytest.mark.parametrize(
    ("variable", "plant", "moved"),
    [
        # F23-10: spend planted four times higher, every purchase count unchanged.
        ("M", lambda row: (row[0], row[1], row[2], float(row[3]) * 4), "rose"),
        # F23-10: two purchases in three removed, recency and spend unchanged.
        ("F", lambda row: (row[0], row[1], max(1, row[2] // 3), row[3]), "fell"),
    ],
)
def test_a_planted_shift_alerts_on_its_variable_and_the_alert_names_it(
    client: FlaskClient,
    token: Any,
    service_db: psycopg.Connection,
    owner: psycopg.Connection,
    reference: dict[str, Any],
    after_publication: datetime,
    feed: Any,
    variable: str,
    plant: Any,
    moved: str,
) -> None:
    feed([plant(row) for row in profiles(reference, 1000)])
    first = one_pass(service_db, after_publication)
    second = one_pass(service_db, after_publication + timedelta(days=1))
    assert report(owner, first)["states"][variable] == "ALARM_PENDING"
    states = report(owner, second)["states"]
    assert states[variable] == "ALARM"
    assert {name for name, state in states.items() if state == "ALARM"} == {variable}
    (alert_id,) = owner.execute(
        "SELECT alert_id FROM models.drift_alert WHERE report_id = %s", (second,)
    ).fetchone()
    alert = get(client, token, f"/v1/drift-alerts/{alert_id}").get_json()
    assert alert["variable"] == variable
    assert alert["claim"] == "DISTRIBUTION_CHANGE"
    assert alert["suggested_review"]["code"] == "REVIEW_FEATURE_DISTRIBUTION"
    assert f"The {variable} values moved" in alert["suggested_review"]["text"]
    assert f" {moved} from " in alert["suggested_review"]["text"]
