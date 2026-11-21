"""Drift reports and alerts as `drift-reports` and `drift-alerts` serve them.

Reads only. What the contract shows and the tables do not store is derived
here from what they do: the reference is the version's own frozen column, the
thresholds are ADR-0065's, a bin's contribution is a function of its shares and
an alert's text a function of its bins.
"""

from __future__ import annotations

from typing import Any

from psycopg import Connection

from . import drift

THRESHOLDS = {
    "PSI": ("VALUE", drift.PSI_WATCH, drift.PSI_ALARM),
    "CHI_SQUARE": ("P_VALUE", drift.P_WATCH, drift.P_ALARM),
}
_ORDER = ("SEGMENT_SHARES", "R", "F", "M")

_REPORT = (
    "SELECT r.report_id, r.kind, r.version_id, v.name, v.method, r.comparison,"
    " r.not_compared_reason, r.window_start, r.window_end, r.customer_count,"
    " (v.reference_distribution ->> 'sample_size')::bigint,"
    " v.reference_distribution ->> 'captured_at',"
    " r.previous_report_id, r.computed_at"
    " FROM models.drift_report AS r JOIN models.model_version AS v USING (version_id)"
    " WHERE (%s::bigint IS NULL OR r.report_id = %s)"
    " AND (%s::text IS NULL OR r.kind = %s)"
    " AND (%s::bigint IS NULL OR r.version_id = %s)"
    " AND (%s::text IS NULL OR r.comparison = %s)"
    " ORDER BY r.window_end DESC, r.report_id DESC LIMIT %s OFFSET %s"
)
_REPORT_COUNT = (
    "SELECT count(*) FROM models.drift_report AS r"
    " WHERE (%s::text IS NULL OR r.kind = %s)"
    " AND (%s::bigint IS NULL OR r.version_id = %s)"
    " AND (%s::text IS NULL OR r.comparison = %s)"
)
_ALERT = (
    "SELECT a.alert_id, a.report_id, a.previous_report_id, a.variable, a.claim,"
    " a.review_code, a.raised_at"
    " FROM models.drift_alert AS a JOIN models.drift_report AS r USING (report_id)"
    " WHERE (%s::bigint IS NULL OR a.alert_id = %s)"
    " AND (%s::text IS NULL OR r.kind = %s)"
    " AND (%s::bigint IS NULL OR r.version_id = %s)"
    " AND (%s::text IS NULL OR a.variable = %s)"
    " ORDER BY a.alert_id DESC LIMIT %s OFFSET %s"
)
_ALERT_COUNT = (
    "SELECT count(*) FROM models.drift_alert AS a"
    " JOIN models.drift_report AS r USING (report_id)"
    " WHERE (%s::text IS NULL OR r.kind = %s)"
    " AND (%s::bigint IS NULL OR r.version_id = %s)"
    " AND (%s::text IS NULL OR a.variable = %s)"
)


def _statistic(row: tuple[Any, ...]) -> dict[str, Any]:
    name, value, p_value, dof, level = row
    basis, watch, alarm = THRESHOLDS[name]
    return {
        "statistic": name,
        "value": round(value, 4),
        "p_value": None if p_value is None else float(f"{p_value:.4g}"),
        "degrees_of_freedom": dof,
        "threshold_basis": basis,
        "watch_threshold": watch,
        "alarm_threshold": alarm,
        "level": level,
    }


def _bin(row: tuple[Any, ...]) -> tuple[drift.Bin, dict[str, Any]]:
    label, lower, upper, reference, current = row
    item = drift.Bin(label, lower, upper, reference, current, 0)
    return item, {
        "label": label,
        "lower": lower,
        "upper": upper,
        "reference_share": round(reference, 4),
        "current_share": round(current, 4),
        "contribution": round(item.contribution, 4),
    }


def _variable_parts(
    connection: Connection[Any], report_id: int, variable: str
) -> tuple[list[dict[str, Any]], list[drift.Bin], list[dict[str, Any]]]:
    statistics = [
        _statistic(row)
        for row in connection.execute(
            "SELECT statistic, value, p_value, degrees_of_freedom, level"
            " FROM models.drift_statistic WHERE report_id = %s AND variable = %s"
            " ORDER BY statistic DESC",
            (report_id, variable),
        ).fetchall()
    ]
    bins, shown = [], []
    for row in connection.execute(
        "SELECT label, lower_bound, upper_bound, reference_share, current_share"
        " FROM models.drift_bin WHERE report_id = %s AND variable = %s"
        " ORDER BY position",
        (report_id, variable),
    ).fetchall():
        item, view = _bin(row)
        bins.append(item)
        shown.append(view)
    return statistics, bins, shown


def _head(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "kind": row[1],
        "model_version": {"version_id": row[2], "name": row[3], "method": row[4]},
        "window": {
            "start": row[7],
            "end": row[8],
            "days": drift.WINDOW_DAYS,
            "customer_count": row[9],
        },
        "reference": {"sample_size": row[10], "captured_at": row[11]},
    }


def report_view(connection: Connection[Any], row: tuple[Any, ...]) -> dict[str, Any]:
    report_id = row[0]
    head = _head(row)
    variables = []
    states = dict(
        connection.execute(
            "SELECT variable, state FROM models.drift_variable WHERE report_id = %s",
            (report_id,),
        ).fetchall()
    )
    for name in _ORDER:
        if name in states:
            statistics, _bins, shown = _variable_parts(connection, report_id, name)
            variables.append(
                {
                    "variable": name,
                    "state": states[name],
                    "statistics": statistics,
                    "bins": shown,
                }
            )
    alert_ids = [
        alert_id
        for (alert_id,) in connection.execute(
            "SELECT alert_id FROM models.drift_alert WHERE report_id = %s"
            " ORDER BY alert_id",
            (report_id,),
        ).fetchall()
    ]
    return {
        "report_id": report_id,
        "kind": head["kind"],
        "model_version": head["model_version"],
        "comparison": row[5],
        "not_compared_reason": row[6],
        "window": head["window"],
        "reference": head["reference"],
        "previous_report_id": row[12],
        "variables": variables,
        "outcome_test": None,
        "alert_ids": alert_ids,
        "computed_at": row[13],
    }


def alert_view(connection: Connection[Any], row: tuple[Any, ...]) -> dict[str, Any]:
    alert_id, report_id, previous_id, variable, claim, code, raised_at = row
    report = connection.execute(_REPORT, (report_id, report_id) + (None,) * 6 + (1, 0))
    head_row = report.fetchone()
    head = _head(head_row)
    statistics, bins, _shown = _variable_parts(connection, report_id, variable)
    moved = sorted(bins, key=lambda item: (-item.contribution, item.label))[:3]
    return {
        "alert_id": alert_id,
        "report_id": report_id,
        "previous_report_id": previous_id,
        "kind": head["kind"],
        "claim": claim,
        "model_version": head["model_version"],
        "variable": variable,
        "window": head["window"],
        "reference": head["reference"],
        "statistics": statistics,
        "bins": [
            _bin((b.label, b.lower, b.upper, b.reference_share, b.current_share))[1]
            for b in moved
        ],
        "outcome_test": None,
        "suggested_review": {"code": code, "text": drift.review_text(variable, bins)},
        "raised_at": raised_at,
    }


def list_reports(
    connection: Connection[Any],
    kind: str | None,
    version_id: int | None,
    comparison: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    rows = connection.execute(
        _REPORT,
        (
            None,
            None,
            kind,
            kind,
            version_id,
            version_id,
            comparison,
            comparison,
            limit,
            offset,
        ),
    ).fetchall()
    (total,) = connection.execute(
        _REPORT_COUNT, (kind, kind, version_id, version_id, comparison, comparison)
    ).fetchone()
    return [report_view(connection, row) for row in rows], total


def get_report(connection: Connection[Any], report_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        _REPORT, (report_id, report_id) + (None,) * 6 + (1, 0)
    ).fetchone()
    return None if row is None else report_view(connection, row)


def list_alerts(
    connection: Connection[Any],
    kind: str | None,
    version_id: int | None,
    variable: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    rows = connection.execute(
        _ALERT,
        (
            None,
            None,
            kind,
            kind,
            version_id,
            version_id,
            variable,
            variable,
            limit,
            offset,
        ),
    ).fetchall()
    (total,) = connection.execute(
        _ALERT_COUNT, (kind, kind, version_id, version_id, variable, variable)
    ).fetchone()
    return [alert_view(connection, row) for row in rows], total


def get_alert(connection: Connection[Any], alert_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        _ALERT, (alert_id, alert_id) + (None,) * 6 + (1, 0)
    ).fetchone()
    return None if row is None else alert_view(connection, row)
