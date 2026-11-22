"""Reading experiments: the stored plan, shown (F25-09).

An experiment planned through the service has a row in `experiments.experiment_plan`
(assignment rule V1: a frame, a salt, a design). One the web created has none (rule V0):
it is shown with the design values it never had as null, no frame and no salt. Nothing
derivable is stored (docs/data-model.md): the design kind, an arm's position, the
estimator, the secondary metric, the frame's size, the state and the analysis instant
are read off what is stored. There is no stored result yet, so none is ANALYZED.
Statements are literal (guards.sh).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from mosaiq_kit import ApiError
from psycopg import Connection

from app import plan, stats

MEASURES = ("PRE_RECENCY_DAYS", "PRE_PURCHASE_COUNT", "PRE_NET_SPEND")

STATES = ("PLANNED", "ASSIGNED", "ANALYZED")
ORIGINS = ("OBSERVED", "SEEDED", "INJECTED")

PAGE = """
SELECT e.experiment_id, e.name, e.campaign_id, e.target_metric, e.starts_on,
       e.conversion_window_days, e.data_origin,
       p.experiment_id IS NOT NULL AS planned,
       p.alpha, p.comparison_alpha, p.power, p.minimum_detectable_effect,
       p.baseline, p.baseline_std_dev, p.required_per_arm,
       p.smallest_detectable_effect, p.settle_days, p.frame_taken_at, p.frame_digest,
       p.label_code, p.model_version_id, p.salt, p.planned_at, p.planned_by
  FROM public.experiment AS e
  LEFT JOIN experiments.experiment_plan AS p ON p.experiment_id = e.experiment_id
 WHERE (%(campaign)s::integer IS NULL OR e.campaign_id = %(campaign)s::integer)
   AND (%(origin)s::text IS NULL OR e.data_origin = %(origin)s::text)
   AND (%(state)s::text IS NULL OR %(state)s::text =
        CASE WHEN EXISTS (SELECT FROM public.experiment_assignment AS a
                           WHERE a.experiment_id = e.experiment_id)
             THEN 'ASSIGNED' ELSE 'PLANNED' END)
 ORDER BY e.experiment_id DESC
 LIMIT %(limit)s OFFSET %(offset)s
"""
COUNT = """
SELECT count(*)
  FROM public.experiment AS e
 WHERE (%(campaign)s::integer IS NULL OR e.campaign_id = %(campaign)s::integer)
   AND (%(origin)s::text IS NULL OR e.data_origin = %(origin)s::text)
   AND (%(state)s::text IS NULL OR %(state)s::text =
        CASE WHEN EXISTS (SELECT FROM public.experiment_assignment AS a
                           WHERE a.experiment_id = e.experiment_id)
             THEN 'ASSIGNED' ELSE 'PLANNED' END)
"""
ONE = """
SELECT e.experiment_id, e.name, e.campaign_id, e.target_metric, e.starts_on,
       e.conversion_window_days, e.data_origin,
       p.experiment_id IS NOT NULL AS planned,
       p.alpha, p.comparison_alpha, p.power, p.minimum_detectable_effect,
       p.baseline, p.baseline_std_dev, p.required_per_arm,
       p.smallest_detectable_effect, p.settle_days, p.frame_taken_at, p.frame_digest,
       p.label_code, p.model_version_id, p.salt, p.planned_at, p.planned_by
  FROM public.experiment AS e
  LEFT JOIN experiments.experiment_plan AS p ON p.experiment_id = e.experiment_id
 WHERE e.experiment_id = %s
"""

ARMS = """
SELECT g.experiment_id, g.group_id, g.kind, g.name, g.treatment_description,
       (SELECT count(*) FROM public.experiment_assignment AS a
         WHERE a.group_id = g.group_id) AS assigned
  FROM public.experiment_group AS g
 WHERE g.experiment_id = ANY(%s)
 ORDER BY g.experiment_id, (g.kind = 'CONTROL') DESC, g.group_id
"""
ASSIGNMENTS = """
SELECT experiment_id, min(assigned_at), max(assigned_at)
  FROM public.experiment_assignment
 WHERE experiment_id = ANY(%s)
 GROUP BY experiment_id
"""
FRAME_SIZES = """
SELECT experiment_id, count(*)
  FROM experiments.experiment_frame_member
 WHERE experiment_id = ANY(%s)
 GROUP BY experiment_id
"""
EXCLUSIONS = """
SELECT experiment_id, reason, removed
  FROM experiments.experiment_frame_exclusion
 WHERE experiment_id = ANY(%s)
 ORDER BY experiment_id, reason
"""
RECORDS = """
SELECT experiment_id, assigned_by, acknowledged_underpowered,
       recomputation_matches, sample_ratio_p_value
  FROM experiments.experiment_assignment_record
 WHERE experiment_id = ANY(%s)
"""
BALANCES = """
SELECT experiment_id, group_id, measure, standardized_mean_difference
  FROM experiments.experiment_assignment_balance
 WHERE experiment_id = ANY(%s)
"""
FLAGS = """
SELECT experiment_id, flag
  FROM experiments.experiment_flag
 WHERE experiment_id = ANY(%s)
 ORDER BY experiment_id, flag
"""


def experiment_not_found() -> ApiError:
    return ApiError(404, "experiment_not_found", "The experiment does not exist.")


def invalid_query(field: str, code: str) -> ApiError:
    return ApiError(
        422,
        "invalid_query",
        "A query parameter is outside what the operation accepts.",
        [{"field": field, "code": code}],
    )


def parse_filters(args: Any) -> dict[str, Any]:
    """`campaign_id`, `state`, `data_origin`: None when absent, 422 when invalid."""
    campaign = args.get("campaign_id")
    if campaign is not None:
        if (
            not campaign.isascii()
            or not campaign.isdigit()
            or not 1 <= int(campaign) <= 2**31 - 1
        ):
            raise invalid_query("campaign_id", "out_of_range")
        campaign = int(campaign)
    state = args.get("state")
    if state is not None and state not in STATES:
        raise invalid_query("state", "enum")
    origin = args.get("data_origin")
    if origin is not None and origin not in ORIGINS:
        raise invalid_query("data_origin", "enum")
    return {"campaign": campaign, "state": state, "origin": origin}


def _number(value: Any) -> float | None:
    return None if value is None else float(value)


def _instant(value: datetime | None) -> str | None:
    return None if value is None else plan.instant(value)


def _assigned_by(record: tuple[Any, ...] | None) -> str | None:
    return None if record is None or record[0] is None else str(record[0])


def _checks(
    record: tuple[Any, ...] | None,
    balance: list[tuple[Any, ...]],
    arm_ids: list[int],
) -> dict[str, Any] | None:
    """What was checked at assignment; None for an assignment the web recorded (V0)."""
    if record is None:
        return None
    _by, _acknowledged, matches, p_value = record
    position = {arm_id: index for index, arm_id in enumerate(arm_ids)}
    return {
        "recomputation_matches": matches,
        "sample_ratio": {
            "status": "FAILED" if p_value < stats.SAMPLE_RATIO_THRESHOLD else "PASSED",
            "p_value": p_value,
            "threshold": stats.SAMPLE_RATIO_THRESHOLD,
        },
        "balance": [
            {
                "measure": measure,
                "arm_id": group_id,
                "reference_arm_id": arm_ids[0],
                "standardized_mean_difference": round(difference, 6),
                "flagged": abs(difference) > stats.BALANCE_THRESHOLD,
                "threshold": stats.BALANCE_THRESHOLD,
            }
            for group_id, measure, difference in sorted(
                balance, key=lambda r: (position[r[0]], MEASURES.index(r[1]))
            )
        ],
    }


def _design(row: tuple[Any, ...], assigned_last: datetime | None) -> dict[str, Any]:
    (
        _id,
        _name,
        _campaign,
        metric,
        starts_on,
        window,
        _origin,
        planned,
        alpha,
        comparison_alpha,
        power,
        effect,
        baseline,
        std_dev,
        required,
        smallest,
        settle,
        *_rest,
    ) = row
    design: dict[str, Any] = {
        "primary_metric": metric,
        "secondary_metric": plan.secondary_of(metric) if planned else None,
        "estimator": plan.estimator_of(metric) if planned else None,
        "alpha": _number(alpha),
        "comparison_alpha": _number(comparison_alpha),
        "power": _number(power),
        "minimum_detectable_effect": _number(effect),
        "baseline": _number(baseline),
        "baseline_std_dev": _number(std_dev),
        "required_per_arm": required,
        "smallest_detectable_effect": _number(smallest),
        "allocation": "EQUAL" if planned else None,
        "conversion_window_days": window,
        "settle_days": settle,
        "starts_on": starts_on.isoformat(),
        "analysis_at": None,
    }
    if planned and assigned_last is not None:
        # A: the assignment instant plus the window and the settle period.
        design["analysis_at"] = _instant(
            assigned_last + timedelta(hours=24 * window + 24 * settle)
        )
    return design


def load(connection: Connection, rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    """The Experiment representations of `rows`, read from the stored plans."""
    ids = [row[0] for row in rows]
    with connection.cursor() as cursor:
        cursor.execute(ARMS, (ids,))
        arm_rows = cursor.fetchall()
        cursor.execute(ASSIGNMENTS, (ids,))
        assignment_rows = {r[0]: (r[1], r[2]) for r in cursor.fetchall()}
        cursor.execute(FRAME_SIZES, (ids,))
        sizes = dict(cursor.fetchall())
        cursor.execute(EXCLUSIONS, (ids,))
        exclusion_rows = cursor.fetchall()
        cursor.execute(FLAGS, (ids,))
        flag_rows = cursor.fetchall()
        cursor.execute(RECORDS, (ids,))
        records = {r[0]: r[1:] for r in cursor.fetchall()}
        cursor.execute(BALANCES, (ids,))
        balance_rows = cursor.fetchall()
    balances: dict[int, list[tuple[Any, ...]]] = {}
    for experiment_id, group_id, measure, difference in balance_rows:
        balances.setdefault(experiment_id, []).append((group_id, measure, difference))
    arms: dict[int, list[tuple[Any, ...]]] = {}
    for arm in arm_rows:
        arms.setdefault(arm[0], []).append(arm)
    exclusions: dict[int, list[dict[str, Any]]] = {}
    for experiment_id, reason, removed in exclusion_rows:
        exclusions.setdefault(experiment_id, []).append(
            {"reason": reason, "count": removed}
        )
    flags: dict[int, list[str]] = {}
    for experiment_id, flag in flag_rows:
        flags.setdefault(experiment_id, []).append(flag)

    experiments = []
    for row in rows:
        experiment_id, name, campaign_id, metric = row[0:4]
        origin, planned = row[6], row[7]
        assigned_first, assigned_last = assignment_rows.get(experiment_id, (None, None))
        mine = arms.get(experiment_id, [])
        shown_arms = [
            {
                "arm_id": arm[1],
                "position": position,
                "kind": arm[2],
                "name": arm[3],
                "treatment_description": arm[4],
            }
            for position, arm in enumerate(mine)
        ]
        assignment = None
        if assigned_first is not None:
            assignment = {
                "experiment_id": experiment_id,
                "assigned_at": _instant(assigned_first),
                "assigned_by": _assigned_by(records.get(experiment_id)),
                "assigned_total": sum(arm[5] for arm in mine),
                "acknowledged_underpowered": bool(
                    records.get(experiment_id, (None, False))[1]
                ),
                "arms": [
                    {"arm_id": arm[1], "kind": arm[2], "assigned": arm[5]}
                    for arm in mine
                ],
                "checks": _checks(
                    records.get(experiment_id),
                    balances.get(experiment_id, []),
                    [arm[1] for arm in mine],
                ),
            }
        frame = None
        if planned:
            frame = {
                "taken_at": _instant(row[17]),
                "size": sizes.get(experiment_id, 0),
                "digest": row[18],
                "label_code": row[19],
                "model_version_id": row[20],
                "exclusions": exclusions.get(experiment_id, []),
            }
        experiments.append(
            {
                "experiment_id": experiment_id,
                "name": name,
                "campaign_id": campaign_id,
                "state": "ASSIGNED" if assigned_first is not None else "PLANNED",
                "design_kind": (
                    "CONTROLLED"
                    if any(arm[2] == plan.CONTROL for arm in mine)
                    else "OFFER_COMPARISON"
                ),
                "assignment_rule": "V1" if planned else "V0",
                "data_origin": origin,
                "arms": shown_arms,
                "design": _design(row, assigned_last),
                "frame": frame,
                "salt": row[21] if planned else None,
                "assignment": assignment,
                "flags": flags.get(experiment_id, []),
                "planned_at": _instant(row[22]) if planned else None,
                "planned_by": str(row[23]) if planned else None,
            }
        )
    return experiments


def get_experiment(connection: Connection, experiment_id: int) -> dict[str, Any]:
    row = connection.execute(ONE, (experiment_id,)).fetchone()
    if row is None:
        raise experiment_not_found()
    return load(connection, [row])[0]


def list_experiments(
    connection: Connection, filters: dict[str, Any], *, page: int, size: int
) -> tuple[list[dict[str, Any]], int]:
    """One page, newest first by `experiment_id`, and the filtered count."""
    with connection.cursor() as cursor:
        cursor.execute(PAGE, filters | {"limit": size, "offset": (page - 1) * size})
        rows = cursor.fetchall()
        cursor.execute(COUNT, filters)
        total = cursor.fetchone()[0]
    return (load(connection, rows) if rows else []), total
