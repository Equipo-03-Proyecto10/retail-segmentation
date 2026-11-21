"""An experiment's result: the difference against control, with its interval (F25-13).

ADR-0019, ADR-0053, ADR-0062, ADR-0068, ADR-0069, ADR-0085. A result is read over
every assigned customer, net of reversals, in two parts that are never merged:
the counts of each arm, and the comparisons between arms, each with its interval
and test. Only a treatment's difference against a control is an effect, and the
money an effect stands for is derived from that difference and nothing else. An
experiment with no control compares offers: it states differences between
treatments, no effect, and says why.

There is one primary look, at the analysis instant `A` (the last assignment plus
the window plus the settle days). Before `A` the result holds counts and the
elapsed share of the window, and no interval, p-value or direction. The first
read of the primary result at or after `A` computes it, stores it (four frozen
relations, written together under an advisory lock) and returns it; every later
read returns what was stored. `basis=RE_READ` computes the figures again as of
the request, writes nothing of its own and never replaces the primary result.

Reading a result first records the qualifying sales that are not recorded yet
(conversions.evaluate), so a sale the evaluation had not seen is never missing
from a figure. That records facts and changes none. Statements are literal
(guards.sh).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from psycopg import Connection

from app import conversions, experiments, plan, stats

PRIMARY = "PRIMARY"
RE_READ = "RE_READ"
BASES = (PRIMARY, RE_READ)
CONTROLLED = "CONTROLLED"
EFFECT = "EFFECT"
TREATMENT_DIFFERENCE = "TREATMENT_DIFFERENCE"
MEASURES = ("PRE_RECENCY_DAYS", "PRE_PURCHASE_COUNT", "PRE_NET_SPEND")
SAMPLE_RATIO_FAILED = "SAMPLE_RATIO_FAILED"
BALANCE_FLAGGED = "BALANCE_FLAGGED"
COMPARISON_WITHHELD_SMALL_ARM = "COMPARISON_WITHHELD_SMALL_ARM"

# contracts/db/advisory-locks.md: two keys, this and the experiment. One reader at a
# time computes and stores the primary look, so it is stored exactly once.
LOCK_KEY = 298_001
LOCK = "SELECT pg_advisory_xact_lock(%s, %s)"
NOW = conversions.NOW

# One row per assigned customer. A customer is converted while a recorded
# conversion of theirs has a sale with a unit left; their revenue is the net
# amount of those sales, zero when there are none (ADR-0053, ADR-0062). The
# window closes at their own assignment instant plus the window's 24-hour days.
CUSTOMERS = """
SELECT a.group_id,
       seen.converted,
       seen.revenue,
       seen.seeded,
       a.assigned_at + make_interval(hours => 24 * e.conversion_window_days)
  FROM public.experiment AS e
  JOIN public.experiment_assignment AS a ON a.experiment_id = e.experiment_id
  CROSS JOIN LATERAL (
        SELECT count(n.transaction_id) > 0 AS converted,
               COALESCE(sum(n.net_amount), 0)::numeric(14,2) AS revenue,
               COALESCE(bool_or(n.data_origin = 'SEEDED'), false) AS seeded
          FROM public.experiment_conversion AS c
          JOIN api_sales.net_purchase_line_v1 AS n
            ON n.transaction_id = c.transaction_id
         WHERE c.assignment_id = a.assignment_id) AS seen
 WHERE e.experiment_id = %s
 ORDER BY a.assignment_id
"""

# Descriptive and non-causal (RN-48): of the customers with a non-voided
# exposure, how many bought inside the window counted from their first one.
EXPOSURE = """
SELECT a.group_id,
       count(first.at),
       count(*) FILTER (WHERE first.at IS NOT NULL AND EXISTS (
           SELECT FROM api_sales.net_purchase_line_v1 AS n
            WHERE n.customer_id = a.customer_id
              AND n.occurred_at >= first.at
              AND n.occurred_at < first.at
                  + make_interval(hours => 24 * e.conversion_window_days)))
  FROM public.experiment AS e
  JOIN public.experiment_assignment AS a ON a.experiment_id = e.experiment_id
  CROSS JOIN LATERAL (
        SELECT min(x.exposed_at) AS at
          FROM public.experiment_exposure AS x
         WHERE x.assignment_id = a.assignment_id
           AND NOT EXISTS (
               SELECT FROM experiments.exposure_notice AS v
                WHERE v.source_event_id = x.source_event_id
                  AND v.notice = 'VOIDED')) AS first
 WHERE e.experiment_id = %s
 GROUP BY a.group_id
"""

# The pre-assignment measures: over the period of one window before the frame
# instant, the net purchases of each assigned customer.
BALANCE = """
SELECT a.group_id,
       count(DISTINCT n.transaction_id),
       COALESCE(sum(n.net_amount), 0)::float8,
       max(n.occurred_at)
  FROM public.experiment AS e
  JOIN public.experiment_assignment AS a ON a.experiment_id = e.experiment_id
  LEFT JOIN api_sales.net_purchase_line_v1 AS n
    ON n.customer_id = a.customer_id
   AND n.occurred_at < %(taken)s
   AND n.occurred_at >= %(taken)s
                        - make_interval(hours => 24 * e.conversion_window_days)
 WHERE e.experiment_id = %(experiment)s
 GROUP BY a.assignment_id, a.group_id
 ORDER BY a.assignment_id
"""

STORED = """
SELECT computed_at, sample_ratio_p_value, synthetic
  FROM experiments.experiment_result
 WHERE experiment_id = %s
"""
STORED_ARMS = """
SELECT group_id, analysed, exposed, exposed_converted, converted, pending,
       not_converted, net_revenue, revenue_std_dev
  FROM experiments.experiment_result_arm
 WHERE experiment_id = %s
 ORDER BY group_id
"""
STORED_COMPARISONS = """
SELECT group_id, against_group_id, metric, difference, lower, upper, p_value,
       degrees_of_freedom, bootstrap_lower, bootstrap_upper
  FROM experiments.experiment_result_comparison
 WHERE experiment_id = %s
 ORDER BY group_id, against_group_id, metric
"""
STORED_BALANCE = """
SELECT group_id, reference_group_id, measure, standardized_mean_difference
  FROM experiments.experiment_result_balance
 WHERE experiment_id = %s
 ORDER BY group_id, measure
"""
INSERT_RESULT = """
INSERT INTO experiments.experiment_result
       (experiment_id, computed_at, sample_ratio_p_value, synthetic)
VALUES (%s, %s, %s, %s)
ON CONFLICT (experiment_id) DO NOTHING
"""
INSERT_ARM = """
INSERT INTO experiments.experiment_result_arm
       (experiment_id, group_id, analysed, exposed, exposed_converted, converted,
        pending, not_converted, net_revenue, revenue_std_dev)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
INSERT_COMPARISON = """
INSERT INTO experiments.experiment_result_comparison
       (experiment_id, group_id, against_group_id, metric, difference, lower, upper,
        p_value, degrees_of_freedom, bootstrap_lower, bootstrap_upper)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
INSERT_BALANCE = """
INSERT INTO experiments.experiment_result_balance
       (experiment_id, group_id, reference_group_id, measure,
        standardized_mean_difference)
VALUES (%s, %s, %s, %s, %s)
"""
INSERT_FLAG = """
INSERT INTO experiments.experiment_flag (experiment_id, flag)
VALUES (%s, %s)
ON CONFLICT (experiment_id, flag) DO NOTHING
"""

LATEST_ASSIGNMENT = (
    "SELECT max(assigned_at) FROM public.experiment_assignment WHERE experiment_id = %s"
)

NO_CONTROL_MESSAGE = (
    "This experiment has no control arm, so it holds no untreated population: it "
    "compares two offers and states no effect of the campaign against no treatment."
)
STATEMENTS = {
    "INTENT_TO_TREAT": (
        "Every assigned customer counts in the arm they were assigned to, "
        "exposed or not."
    ),
    "NET_OF_REVERSALS": (
        "Conversions and revenue count sales net of reversals, as known at " "`as_of`."
    ),
    "SINGLE_LOOK": (
        "The result is read once, at the analysis instant; stopping early or "
        "adding a look needs a new experiment."
    ),
    "FAMILY_WISE_ALPHA": (
        "Each treatment is compared with the control at the experiment's alpha "
        "divided by the number of treatments."
    ),
    "LARGE_SAMPLE_APPROXIMATION": (
        "The intervals rest on the normal approximation, which the planned "
        "customers per arm make adequate."
    ),
    "NO_CONTAMINATION": (
        "A customer is assigned in at most one experiment whose period "
        "overlaps this one's."
    ),
    "CONTROL_NEVER_EXPOSED": (
        "The control is never exposed, so exposure is a secondary, descriptive "
        "measure and not an effect on the exposed."
    ),
    "ZERO_FOR_NON_BUYERS": (
        "Revenue per customer counts zero for a customer who bought nothing in "
        "the window."
    ),
}


def parse_basis(args: Any) -> str:
    """`basis`: PRIMARY when absent, 422 `invalid_query` when it is neither value."""
    basis = args.get("basis", PRIMARY)
    if basis not in BASES:
        raise experiments.invalid_query("basis", "enum")
    return basis


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _money(value: Decimal | float) -> str:
    exact = value if isinstance(value, Decimal) else Decimal(repr(float(value)))
    return f"{exact.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def _round(value: float) -> float:
    return round(value, 6)


def _significant(value: float) -> float:
    """Six significant figures: a tiny p-value stays a tiny number, never 0.0."""
    return float(f"{value:.6g}")


def _seed(experiment_id: int, arm_id: int) -> str:
    return f"experiment-result:{experiment_id}:{arm_id}"


# ------------------------------------------------------------------ computing


def _measure(
    connection: Connection, experiment: dict[str, Any], now: datetime
) -> tuple[dict[int, list[tuple[bool, Decimal, datetime]]], bool]:
    """Each arm's customers: converted, revenue and window end; and whether any
    counted sale is a seeded one."""
    arms: dict[int, list[tuple[bool, Decimal, datetime]]] = {
        arm["arm_id"]: [] for arm in experiment["arms"]
    }
    seeded = False
    with connection.cursor() as cursor:
        cursor.execute(CUSTOMERS, (experiment["experiment_id"],))
        for group_id, converted, revenue, has_seeded, window_end in cursor.fetchall():
            arms[group_id].append((converted, revenue, window_end))
            seeded = seeded or has_seeded
    return arms, seeded


def _revenue_values(rows: list[tuple[bool, Decimal, datetime]]) -> list[float]:
    return [float(revenue) for _, revenue, _ in rows]


def _arm_core(
    rows: list[tuple[bool, Decimal, datetime]],
    now: datetime,
    exposed: int,
    exposed_converted: int | None,
) -> dict[str, Any]:
    converted = sum(1 for done, _, _ in rows if done)
    pending = sum(1 for done, _, end in rows if not done and end > now)
    values = _revenue_values(rows)
    _, variance = stats.mean_and_variance(values) if values else (0.0, 0.0)
    return {
        "analysed": len(rows),
        "exposed": exposed,
        "exposed_converted": exposed_converted,
        "converted": converted,
        "pending": pending,
        "not_converted": len(rows) - converted - pending,
        "net_revenue": sum((revenue for _, revenue, _ in rows), Decimal("0.00")),
        "revenue_std_dev": math.sqrt(variance),
    }


def _pairs(experiment: dict[str, Any]) -> list[tuple[int, int]]:
    """`(arm, against)`: each treatment against the control, or each later offer
    against the first when there is no control."""
    arms = experiment["arms"]
    reference = arms[0]
    return [(arm["arm_id"], reference["arm_id"]) for arm in arms[1:]]


def _withheld_comparisons(
    experiment: dict[str, Any], core_arms: dict[int, dict[str, Any]]
) -> list[tuple[str, int, int, int]]:
    """Metric, arm, reference and minimum size for unsupported comparisons."""
    design = experiment["design"]
    metrics = [design["primary_metric"], design["secondary_metric"]]
    return [
        (metric, arm_id, against, minimum)
        for metric in metrics
        for arm_id, against in _pairs(experiment)
        for minimum in (1 if metric == plan.CONVERSION else 2,)
        if any(core_arms[member]["analysed"] < minimum for member in (arm_id, against))
    ]


def _comparisons(
    experiment: dict[str, Any],
    core_arms: dict[int, dict[str, Any]],
    values: dict[int, list[float]],
) -> list[dict[str, Any]]:
    design = experiment["design"]
    alpha = design["comparison_alpha"]
    pairs = _pairs(experiment)
    primary = design["primary_metric"]
    metrics = [primary, design["secondary_metric"]]
    withheld = {
        (metric, arm_id, against)
        for metric, arm_id, against, _ in _withheld_comparisons(experiment, core_arms)
    }
    summary = {
        arm_id: (len(v), *stats.mean_and_variance(v)) if v else (0, 0.0, 0.0)
        for arm_id, v in values.items()
    }
    means: dict[int, list[float]] = {}
    out: list[dict[str, Any]] = []
    for metric in metrics:
        for arm_id, against in pairs:
            if (metric, arm_id, against) in withheld:
                continue
            row: dict[str, Any] = {
                "group_id": arm_id,
                "against_group_id": against,
                "metric": metric,
                "bootstrap_lower": None,
                "bootstrap_upper": None,
            }
            if metric == plan.CONVERSION:
                difference = stats.compare_proportions(
                    core_arms[against]["analysed"],
                    core_arms[against]["converted"],
                    core_arms[arm_id]["analysed"],
                    core_arms[arm_id]["converted"],
                    alpha,
                )
            else:
                difference = stats.welch(summary[against], summary[arm_id], alpha)
                for member in (arm_id, against):
                    if member not in means:
                        means[member] = stats.bootstrap_means(
                            values[member],
                            _seed(experiment["experiment_id"], member),
                            stats.RESAMPLES,
                        )
                low, high = stats.bootstrap_interval(
                    means[against], means[arm_id], alpha
                )
                row["bootstrap_lower"], row["bootstrap_upper"] = low, high
            row.update(
                difference=difference.difference,
                lower=difference.lower,
                upper=difference.upper,
                p_value=difference.p_value,
                degrees_of_freedom=difference.degrees_of_freedom,
            )
            out.append(row)
    return out


def _balance(
    connection: Connection,
    experiment: dict[str, Any],
    frame_taken_at: datetime,
) -> list[dict[str, Any]]:
    window = experiment["design"]["conversion_window_days"]
    measures: dict[int, dict[str, list[float]]] = {
        arm["arm_id"]: {name: [] for name in MEASURES} for arm in experiment["arms"]
    }
    with connection.cursor() as cursor:
        cursor.execute(
            BALANCE,
            {"experiment": experiment["experiment_id"], "taken": frame_taken_at},
        )
        for group_id, purchases, spend, last in cursor.fetchall():
            recency = (
                float(window)
                if last is None
                else (frame_taken_at - last).total_seconds() / 86400.0
            )
            measures[group_id]["PRE_RECENCY_DAYS"].append(recency)
            measures[group_id]["PRE_PURCHASE_COUNT"].append(float(purchases))
            measures[group_id]["PRE_NET_SPEND"].append(spend)
    reference = experiment["arms"][0]["arm_id"]
    return [
        {
            "group_id": arm["arm_id"],
            "reference_group_id": reference,
            "measure": name,
            "standardized_mean_difference": stats.standardized_mean_difference(
                measures[reference][name], measures[arm["arm_id"]][name]
            ),
        }
        for arm in experiment["arms"][1:]
        for name in MEASURES
    ]


def _compute(
    connection: Connection,
    experiment: dict[str, Any],
    now: datetime,
    *,
    estimate: bool,
) -> dict[str, Any]:
    """The figures as of `now`: always the counts, and the checks and comparisons
    when `estimate` (the analysis instant has been reached)."""
    experiment_id = experiment["experiment_id"]
    rows, seeded = _measure(connection, experiment, now)
    with connection.cursor() as cursor:
        cursor.execute(EXPOSURE, (experiment_id,))
        exposure = {group: (seen, bought) for group, seen, bought in cursor.fetchall()}
    controls = {a["arm_id"] for a in experiment["arms"] if a["kind"] == plan.CONTROL}
    arms: dict[int, dict[str, Any]] = {}
    for arm in experiment["arms"]:
        arm_id = arm["arm_id"]
        seen, bought = exposure.get(arm_id, (0, 0))
        arms[arm_id] = _arm_core(
            rows[arm_id],
            now,
            0 if arm_id in controls else seen,
            None if arm_id in controls else bought,
        )
    core: dict[str, Any] = {
        "computed_at": now,
        "synthetic": seeded or experiment["data_origin"] != "OBSERVED",
        "arms": arms,
        "sample_ratio_p_value": None,
        "comparisons": [],
        "balance": [],
    }
    if not estimate:
        return core
    core["sample_ratio_p_value"] = stats.sample_ratio_p(
        [arm["analysed"] for arm in arms.values()]
    )
    core["balance"] = _balance(
        connection, experiment, _parse(experiment["frame"]["taken_at"])
    )
    if core["sample_ratio_p_value"] >= stats.SAMPLE_RATIO_THRESHOLD:
        values = {arm_id: _revenue_values(r) for arm_id, r in rows.items()}
        core["comparisons"] = _comparisons(experiment, arms, values)
    return core


# -------------------------------------------------------------------- storing


def _store(
    connection: Connection, experiment: dict[str, Any], core: dict[str, Any]
) -> None:
    experiment_id = experiment["experiment_id"]
    connection.execute(
        INSERT_RESULT,
        (
            experiment_id,
            core["computed_at"],
            core["sample_ratio_p_value"],
            core["synthetic"],
        ),
    )
    for arm_id, arm in core["arms"].items():
        connection.execute(
            INSERT_ARM,
            (
                experiment_id,
                arm_id,
                arm["analysed"],
                arm["exposed"],
                arm["exposed_converted"],
                arm["converted"],
                arm["pending"],
                arm["not_converted"],
                arm["net_revenue"],
                arm["revenue_std_dev"],
            ),
        )
    for row in core["comparisons"]:
        connection.execute(
            INSERT_COMPARISON,
            (
                experiment_id,
                row["group_id"],
                row["against_group_id"],
                row["metric"],
                row["difference"],
                row["lower"],
                row["upper"],
                row["p_value"],
                row["degrees_of_freedom"],
                row["bootstrap_lower"],
                row["bootstrap_upper"],
            ),
        )
    for row in core["balance"]:
        connection.execute(
            INSERT_BALANCE,
            (
                experiment_id,
                row["group_id"],
                row["reference_group_id"],
                row["measure"],
                row["standardized_mean_difference"],
            ),
        )
    for flag in _computed_flags(core):
        connection.execute(INSERT_FLAG, (experiment_id, flag))


def _stored(connection: Connection, experiment_id: int) -> dict[str, Any] | None:
    head = connection.execute(STORED, (experiment_id,)).fetchone()
    if head is None:
        return None
    computed_at, p_value, synthetic = head
    with connection.cursor() as cursor:
        cursor.execute(STORED_ARMS, (experiment_id,))
        arms = {
            row[0]: {
                "analysed": row[1],
                "exposed": row[2],
                "exposed_converted": row[3],
                "converted": row[4],
                "pending": row[5],
                "not_converted": row[6],
                "net_revenue": row[7],
                "revenue_std_dev": row[8],
            }
            for row in cursor.fetchall()
        }
        cursor.execute(STORED_COMPARISONS, (experiment_id,))
        comparisons = [
            dict(
                zip(
                    (
                        "group_id",
                        "against_group_id",
                        "metric",
                        "difference",
                        "lower",
                        "upper",
                        "p_value",
                        "degrees_of_freedom",
                        "bootstrap_lower",
                        "bootstrap_upper",
                    ),
                    row,
                    strict=True,
                )
            )
            for row in cursor.fetchall()
        ]
        cursor.execute(STORED_BALANCE, (experiment_id,))
        balance = [
            {
                "group_id": row[0],
                "reference_group_id": row[1],
                "measure": row[2],
                "standardized_mean_difference": row[3],
            }
            for row in cursor.fetchall()
        ]
    return {
        "computed_at": computed_at,
        "synthetic": synthetic,
        "arms": arms,
        "sample_ratio_p_value": p_value,
        "comparisons": comparisons,
        "balance": balance,
    }


# ------------------------------------------------------------------ rendering


def _computed_flags(core: dict[str, Any]) -> list[str]:
    flags = []
    p_value = core["sample_ratio_p_value"]
    if p_value is not None and p_value < stats.SAMPLE_RATIO_THRESHOLD:
        flags.append(SAMPLE_RATIO_FAILED)
    if any(
        abs(row["standardized_mean_difference"]) > stats.BALANCE_THRESHOLD
        for row in core["balance"]
    ):
        flags.append(BALANCE_FLAGGED)
    return flags


def _arm_view(arm: dict[str, Any], shown: dict[str, Any], kind: str) -> dict[str, Any]:
    analysed = shown["analysed"]
    exposed = shown["exposed"]
    revenue = shown["net_revenue"]
    return {
        "arm_id": arm["arm_id"],
        "kind": kind,
        "name": arm["name"],
        "assigned": analysed,
        "analysed": analysed,
        "exposed": exposed,
        "converted": shown["converted"],
        "pending": shown["pending"],
        "not_converted": shown["not_converted"],
        "conversion_rate": _round(shown["converted"] / analysed) if analysed else 0.0,
        "exposed_conversion_rate": (
            None
            if kind == plan.CONTROL or shown["exposed_converted"] is None
            else (_round(shown["exposed_converted"] / exposed) if exposed else 0.0)
        ),
        "net_revenue": _money(revenue),
        "revenue_per_customer": _money(revenue / analysed if analysed else Decimal(0)),
        "revenue_std_dev": _round(shown["revenue_std_dev"]),
    }


def _comparison_view(
    row: dict[str, Any],
    experiment: dict[str, Any],
    core: dict[str, Any],
    flags: set[str],
) -> dict[str, Any]:
    design = experiment["design"]
    alpha = design["comparison_alpha"]
    controlled = experiment["design_kind"] == CONTROLLED
    primary = row["metric"] == design["primary_metric"]
    revenue = row["metric"] == plan.REVENUE_PER_CUSTOMER
    conclusion = None
    if primary:
        if row["p_value"] < alpha:
            conclusion = "SIGNIFICANT"
        else:
            conclusion = (
                "INCONCLUSIVE" if plan.UNDERPOWERED in flags else "NOT_SIGNIFICANT"
            )
    incremental = None
    if controlled and primary and revenue:
        customers = core["arms"][row["group_id"]]["analysed"]
        incremental = {
            "amount": _money(Decimal(repr(row["difference"])) * customers),
            "lower": _money(Decimal(repr(row["lower"])) * customers),
            "upper": _money(Decimal(repr(row["upper"])) * customers),
        }
    bootstrap = None
    if revenue:
        low, high = row["bootstrap_lower"], row["bootstrap_upper"]
        interval = stats.Difference(
            row["difference"],
            row["lower"],
            row["upper"],
            row["p_value"],
            row["degrees_of_freedom"],
        )
        bootstrap = {
            "resamples": stats.RESAMPLES,
            "lower": _round(low),
            "upper": _round(high),
            "agrees": stats.bootstrap_agrees(interval, low, high),
        }
    return {
        "label": EFFECT if controlled else TREATMENT_DIFFERENCE,
        "metric": row["metric"],
        "role": "PRIMARY" if primary else "SECONDARY",
        "arm_id": row["group_id"],
        "against_arm_id": row["against_group_id"],
        "difference": _round(row["difference"]),
        "lower": _round(row["lower"]),
        "upper": _round(row["upper"]),
        "confidence_level": _round(1.0 - alpha),
        "test": "WELCH_T" if revenue else "POOLED_Z",
        "p_value": _significant(row["p_value"]),
        "degrees_of_freedom": (
            None
            if row["degrees_of_freedom"] is None
            else round(row["degrees_of_freedom"], 1)
        ),
        "conclusion": conclusion,
        "incremental_revenue": incremental,
        "bootstrap": bootstrap,
    }


def _small_arm_statement(
    metric: str,
    arm_id: int,
    against: int,
    minimum: int,
    core_arms: dict[int, dict[str, Any]],
) -> str:
    deficient = []
    for member in (arm_id, against):
        count = core_arms[member]["analysed"]
        if count < minimum:
            quantity = "no customers" if count == 0 else "one customer"
            deficient.append(f"arm {member} has {quantity}")
    requirement = (
        "a proportion test needs at least one customer per arm"
        if metric == plan.CONVERSION
        else "Welch's interval needs at least two customers per arm"
    )
    return (
        f"{metric} comparison between arm {arm_id} and arm {against} is withheld "
        f"because {requirement}; {' and '.join(deficient)}."
    )


def _assumptions(
    experiment: dict[str, Any], core: dict[str, Any], estimate: bool
) -> list[dict[str, str]]:
    codes = ["INTENT_TO_TREAT", "NET_OF_REVERSALS", "SINGLE_LOOK"]
    if estimate:
        controlled = experiment["design_kind"] == CONTROLLED
        treatments = sum(1 for a in experiment["arms"] if a["kind"] == plan.TREATMENT)
        if controlled and treatments > 1:
            codes.append("FAMILY_WISE_ALPHA")
        codes += ["LARGE_SAMPLE_APPROXIMATION", "NO_CONTAMINATION"]
        if controlled:
            codes.append("CONTROL_NEVER_EXPOSED")
        codes.append("ZERO_FOR_NON_BUYERS")
    assumptions = [{"code": code, "statement": STATEMENTS[code]} for code in codes]
    if estimate:
        assumptions += [
            {
                "code": COMPARISON_WITHHELD_SMALL_ARM,
                "statement": _small_arm_statement(
                    metric, arm_id, against, minimum, core["arms"]
                ),
            }
            for metric, arm_id, against, minimum in _withheld_comparisons(
                experiment, core["arms"]
            )
        ]
    return assumptions


def render(
    experiment: dict[str, Any],
    core: dict[str, Any],
    basis: str,
    *,
    estimate: bool,
    elapsed: float,
) -> dict[str, Any]:
    """The ExperimentResult of `core`, whether it was just computed or stored."""
    flags = set(experiment["flags"]) | set(_computed_flags(core))
    position = {arm["arm_id"]: index for index, arm in enumerate(experiment["arms"])}
    metrics = [
        experiment["design"]["primary_metric"],
        experiment["design"]["secondary_metric"],
    ]
    # Stored rows come back in key order; a result lists the primary metric's
    # comparisons first, then the secondary's, each in the deal order of the arms.
    comparisons = sorted(
        core["comparisons"],
        key=lambda row: (metrics.index(row["metric"]), position[row["group_id"]]),
    )
    balance = sorted(
        core["balance"],
        key=lambda row: (position[row["group_id"]], MEASURES.index(row["measure"])),
    )
    invalid = SAMPLE_RATIO_FAILED in flags and estimate
    controlled = experiment["design_kind"] == CONTROLLED
    kinds = {arm["arm_id"]: arm["kind"] for arm in experiment["arms"]}
    checks = None
    if estimate:
        p_value = core["sample_ratio_p_value"]
        checks = {
            "sample_ratio": {
                "status": (
                    "FAILED" if p_value < stats.SAMPLE_RATIO_THRESHOLD else "PASSED"
                ),
                "p_value": _significant(p_value),
                "threshold": stats.SAMPLE_RATIO_THRESHOLD,
            },
            "balance": [
                {
                    "measure": row["measure"],
                    "arm_id": row["group_id"],
                    "reference_arm_id": row["reference_group_id"],
                    "standardized_mean_difference": _round(
                        row["standardized_mean_difference"]
                    ),
                    "flagged": abs(row["standardized_mean_difference"])
                    > stats.BALANCE_THRESHOLD,
                    "threshold": stats.BALANCE_THRESHOLD,
                }
                for row in balance
            ],
        }
    return {
        "experiment_id": experiment["experiment_id"],
        "campaign_id": experiment["campaign_id"],
        "basis": basis,
        "status": (
            "PENDING" if not estimate else ("INVALID" if invalid else "COMPLETE")
        ),
        "as_of": _instant(core["computed_at"]),
        "elapsed_share": 1 if estimate else elapsed,
        "data_origin": experiment["data_origin"],
        "synthetic": core["synthetic"],
        "design_kind": experiment["design_kind"],
        "design": experiment["design"],
        "arms": [
            _arm_view(arm, core["arms"][arm["arm_id"]], kinds[arm["arm_id"]])
            for arm in experiment["arms"]
        ],
        "checks": checks,
        "comparisons": [
            _comparison_view(row, experiment, core, flags) for row in comparisons
        ],
        "effect_refusal": (
            None
            if controlled
            else {"code": "NO_CONTROL", "message": NO_CONTROL_MESSAGE}
        ),
        "attribution": None,
        "assumptions": _assumptions(experiment, core, estimate),
        "flags": sorted(flags),
    }


# ------------------------------------------------------------------ operation


def get_result(
    connection: Connection, experiment_id: int, basis: str
) -> dict[str, Any]:
    """The result of one experiment, in a transaction the caller opened.

    404 when there is no such experiment, 409 `experiment_not_assigned` when
    nobody was assigned. Before the analysis instant, or for an experiment that
    was never planned and so has none, the counts alone.
    """
    experiment = experiments.get_experiment(connection, experiment_id)
    assignment = experiment["assignment"]
    if assignment is None:
        raise conversions.not_assigned()
    analysis_at = experiment["design"]["analysis_at"]
    window = experiment["design"]["conversion_window_days"]
    connection.execute(LOCK, (LOCK_KEY, experiment_id))
    now = connection.execute(NOW).fetchone()[0]
    reached = analysis_at is not None and now >= _parse(analysis_at)
    if basis == PRIMARY and reached:
        stored = _stored(connection, experiment_id)
        if stored is not None:
            return render(experiment, stored, basis, estimate=True, elapsed=1.0)
    conversions.evaluate(connection, experiment_id)
    core = _compute(connection, experiment, now, estimate=reached)
    if basis == PRIMARY and reached:
        _store(connection, experiment, core)
    last = connection.execute(LATEST_ASSIGNMENT, (experiment_id,)).fetchone()[0]
    elapsed = _elapsed(now, last, window)
    return render(experiment, core, basis, estimate=reached, elapsed=elapsed)


def _elapsed(now: datetime, last: datetime, window: int) -> float:
    share = (now - last).total_seconds() / (window * 86400.0)
    return _round(min(1.0, max(0.0, share)))
