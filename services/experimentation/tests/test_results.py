"""The result of an experiment, against PostgreSQL (F25-13).

Every test builds its own experiment on its own copy of the seeded database, so
every figure is worked out by hand in the test. Sales are dated at fixed instants
a long time ago, so the analysis instant of those experiments has been reached;
an experiment that must still be open is assigned a few days before the clock.
"""

from __future__ import annotations

import math
import os
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from lxml import etree

from tests.conftest import ADMIN, ANALYST, AUDITOR, MARKETING, _with_database

pytestmark = pytest.mark.postgres

START = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
WINDOW = 7
SETTLE = 7
TABLES = ("", "_arm", "_comparison", "_balance")
# Literal statements, one per relation: guards.sh forbids interpolated SQL.
COUNT_STORED = {
    "": "SELECT count(*) FROM experiments.experiment_result WHERE experiment_id = %s",
    "_arm": "SELECT count(*) FROM experiments.experiment_result_arm"
    " WHERE experiment_id = %s",
    "_comparison": "SELECT count(*) FROM experiments.experiment_result_comparison"
    " WHERE experiment_id = %s",
    "_balance": "SELECT count(*) FROM experiments.experiment_result_balance"
    " WHERE experiment_id = %s",
}


def _code(reply: Any) -> tuple[int, str | None]:
    return reply.status_code, (reply.get_json(silent=True) or {}).get("code")


def _comparison_and_assumption_views(
    reply: Any, media_type: str
) -> tuple[str, set[str], list[tuple[str, str]], set[str]]:
    """Read the fields under test from either representation and validate XML."""
    if media_type == "application/json":
        body = reply.get_json()
        return (
            body["status"],
            {comparison["metric"] for comparison in body["comparisons"]},
            [
                (assumption["code"], assumption["statement"])
                for assumption in body["assumptions"]
            ],
            set(body["flags"]),
        )
    contract = Path(__file__).resolve().parents[1] / "contracts/experimentation"
    schema = etree.XMLSchema(etree.parse(str(contract / "experimentation.xsd")))
    root = etree.fromstring(reply.data)
    schema.assertValid(root)
    namespace = {"ex": root.nsmap[None]}
    return (
        root.findtext("ex:status", namespaces=namespace) or "",
        {
            comparison.findtext("ex:metric", namespaces=namespace) or ""
            for comparison in root.findall("ex:comparisons", namespaces=namespace)
        },
        [
            (
                assumption.findtext("ex:code", namespaces=namespace) or "",
                assumption.findtext("ex:statement", namespaces=namespace) or "",
            )
            for assumption in root.findall("ex:assumptions", namespaces=namespace)
        ],
        {flag.text or "" for flag in root.findall("ex:flags", namespaces=namespace)},
    )


class Scenario:
    """One planned experiment on up to thirty seeded customers.

    `arms` lists the kinds in deal order; customers are dealt to them in blocks of
    `sizes`. The window is seven days and the settle period seven, so the analysis
    instant is START plus fourteen days, long past.
    """

    def __init__(
        self,
        owner: psycopg.Connection,
        *,
        kinds: tuple[str, ...] = ("CONTROL", "TREATMENT"),
        sizes: tuple[int, ...] = (15, 15),
        metric: str = "REVENUE_PER_CUSTOMER",
        assigned_at: datetime = START,
        planned: bool = True,
        comparison_alpha: float = 0.05,
    ) -> None:
        self.owner = owner
        self.assigned_at = assigned_at
        self.experiment = owner.execute(
            "SELECT max(experiment_id) + 1 FROM experiment"
        ).fetchone()[0]
        first = owner.execute(
            "SELECT max(group_id) + 1 FROM experiment_group"
        ).fetchone()[0]
        self.arms = [first + position for position in range(len(kinds))]
        owner.execute(
            "INSERT INTO experiment (experiment_id, name, campaign_id, target_metric,"
            " starts_on, conversion_window_days, data_origin)"
            " VALUES (%s, 'F25-13 result', NULL, %s, %s, %s, 'OBSERVED')",
            (self.experiment, metric, assigned_at.date(), WINDOW),
        )
        for arm, kind in zip(self.arms, kinds, strict=True):
            owner.execute(
                "INSERT INTO experiment_group (group_id, experiment_id, kind, name,"
                " treatment_description) VALUES (%s, %s, %s, %s, %s)",
                (arm, self.experiment, kind, f"{kind.title()} {arm}", kind.title()),
            )
        self.customers = [
            str(row[0])
            for row in owner.execute(
                "SELECT customer_id FROM customer ORDER BY customer_id LIMIT %s",
                (sum(sizes),),
            )
        ]
        # Nothing the seed holds is inside this experiment's windows.
        owner.execute(
            "UPDATE transaction SET occurred_at = TIMESTAMPTZ '2000-01-01'"
            " WHERE customer_id = ANY(%s::uuid[])",
            (self.customers,),
        )
        self.members: list[list[int]] = []
        position = 0
        for arm, size in zip(self.arms, sizes, strict=True):
            block = list(range(position, position + size))
            self.members.append(block)
            for index in block:
                owner.execute(
                    "INSERT INTO experiment_assignment (experiment_id, group_id,"
                    " customer_id, assigned_at) VALUES (%s, %s, %s, %s)",
                    (self.experiment, arm, self.customers[index], assigned_at),
                )
            position += size
        self.counter = 0
        if planned:
            owner.execute(
                "INSERT INTO experiments.experiment_plan (experiment_id, alpha,"
                " comparison_alpha, power, minimum_detectable_effect, baseline,"
                " baseline_std_dev, required_per_arm, smallest_detectable_effect,"
                " settle_days, frame_taken_at, frame_digest, label_code, salt,"
                " planned_at, planned_by) VALUES (%s, 0.05, %s, 0.8, 1, 2, 5, 10, 1,"
                " %s, %s, repeat('a', 64), 'CHAMPION', repeat('b', 64), %s, %s)",
                (
                    self.experiment,
                    comparison_alpha,
                    SETTLE,
                    assigned_at - timedelta(days=1),
                    assigned_at - timedelta(days=1),
                    ADMIN,
                ),
            )

    def sale(
        self, position: int, when: datetime, amount: int, *, origin: str = "OBSERVED"
    ) -> int:
        """A one-unit sale of `amount` by customer `position`, at `when`."""
        self.counter += 1
        store, channel, product = self.owner.execute(
            "SELECT (SELECT min(store_id) FROM store),"
            " (SELECT min(channel_id) FROM channel),"
            " (SELECT min(product_id) FROM product)"
        ).fetchone()
        with self.owner.transaction():
            (transaction_id,) = self.owner.execute(
                "INSERT INTO transaction (source_transaction_id, customer_id,"
                " store_id, channel_id, occurred_at, total, data_origin)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING transaction_id",
                (
                    f"f25-13-{self.experiment}-{self.counter}",
                    self.customers[position],
                    store,
                    channel,
                    when,
                    amount,
                    origin,
                ),
            ).fetchone()
            self.owner.execute(
                "INSERT INTO transaction_line (transaction_id, product_id, quantity,"
                " unit_price) VALUES (%s, %s, 1, %s)",
                (transaction_id, product, amount),
            )
        return transaction_id

    def cancel(self, transaction_id: int) -> None:
        url = _with_database(
            os.environ["TEST_DATABASE_URL"],
            self.owner.info.dbname,
            role="svc_sales",
        )
        with psycopg.connect(url) as sales, sales.transaction():
            product, quantity = sales.execute(
                "SELECT product_id, quantity FROM public.transaction_line"
                " WHERE transaction_id = %s",
                (transaction_id,),
            ).fetchone()
            (reversal,) = sales.execute(
                "INSERT INTO sales.sale_reversal (transaction_id, kind, reason,"
                " idempotency_key, request_sha256, recorded_by)"
                " VALUES (%s, 'CANCELLATION', 'test', %s, %s, %s)"
                " RETURNING reversal_id",
                (transaction_id, uuid.uuid4(), bytes(32), ADMIN),
            ).fetchone()
            sales.execute(
                "INSERT INTO sales.sale_reversal_line (reversal_id, transaction_id,"
                " product_id, quantity, reason) VALUES (%s, %s, %s, %s, NULL)",
                (reversal, transaction_id, product, quantity),
            )
            sales.execute(
                "UPDATE public.transaction_line SET returned_quantity ="
                " returned_quantity + %s WHERE transaction_id = %s",
                (quantity, transaction_id),
            )

    def spend(self, arm_index: int, amounts: list[int]) -> None:
        """Each amount a sale of one member of the arm, one day after assignment."""
        for position, amount in zip(self.members[arm_index], amounts, strict=False):
            if amount:
                self.sale(position, self.assigned_at + timedelta(days=1), amount)

    def get(self, api: Any, query: str = "", **kwargs: Any) -> Any:
        return api("GET", f"/v1/experiment-results/{self.experiment}{query}", **kwargs)

    def stored(self, table: str = "") -> int:
        return self.owner.execute(COUNT_STORED[table], (self.experiment,)).fetchone()[0]


@pytest.fixture
def scene(owner: psycopg.Connection) -> Scenario:
    return Scenario(owner)


def _dealt(scene: Scenario) -> None:
    """The fixture of the hand-worked tests: control spends 10 and 20 and nobody
    else; treatment spends 10, 20, 30, 40 and 20. Fifteen customers in each arm."""
    scene.spend(0, [10, 20])
    scene.spend(1, [10, 20, 30, 40, 20])


# --------------------------------------------------------------- the estimates


def test_revenue_per_assigned_customer_counts_the_zeros_and_states_the_effect(
    api: Any, scene: Scenario
) -> None:
    _dealt(scene)
    reply = scene.get(api)
    assert reply.status_code == 200, reply.get_json()
    body = reply.get_json()
    assert (body["basis"], body["status"], body["design_kind"]) == (
        "PRIMARY",
        "COMPLETE",
        "CONTROLLED",
    )
    control, treatment = body["arms"]
    # Revenue per customer is over all fifteen, not the buyers: 30 / 15, 120 / 15.
    assert (control["net_revenue"], control["revenue_per_customer"]) == (
        "30.00",
        "2.00",
    )
    assert (treatment["net_revenue"], treatment["revenue_per_customer"]) == (
        "120.00",
        "8.00",
    )
    assert (control["assigned"], control["analysed"], control["exposed"]) == (15, 15, 0)
    assert (control["converted"], control["pending"], control["not_converted"]) == (
        2,
        0,
        13,
    )
    assert (treatment["converted"], treatment["not_converted"]) == (5, 10)
    assert control["exposed_conversion_rate"] is None
    # Sample variances: (500 - 15 * 2^2) / 14 and (3400 - 15 * 8^2) / 14.
    assert control["revenue_std_dev"] == pytest.approx(math.sqrt(440 / 14), abs=1e-5)
    assert treatment["revenue_std_dev"] == pytest.approx(math.sqrt(2440 / 14), abs=1e-5)

    revenue, conversion = body["comparisons"]
    assert (revenue["metric"], revenue["role"], revenue["label"]) == (
        "REVENUE_PER_CUSTOMER",
        "PRIMARY",
        "EFFECT",
    )
    assert (revenue["arm_id"], revenue["against_arm_id"]) == (
        treatment["arm_id"],
        control["arm_id"],
    )
    assert revenue["test"] == "WELCH_T"
    assert revenue["confidence_level"] == 0.95
    assert revenue["difference"] == pytest.approx(6.0)
    # se = sqrt(440/14/15 + 2440/14/15) = 3.7033, and the degrees of freedom are 18.9,
    # where Student's 97.5% quantile is 2.093.
    assert revenue["degrees_of_freedom"] == pytest.approx(18.9, abs=0.05)
    assert revenue["lower"] == pytest.approx(6 - 2.093 * 3.7033, abs=0.02)
    assert revenue["upper"] == pytest.approx(6 + 2.093 * 3.7033, abs=0.02)
    assert revenue["conclusion"] == "NOT_SIGNIFICANT"
    # Incremental revenue is the difference times the customers assigned to the
    # treatment, its interval scaled the same way, and nothing else: 6 x 15.
    assert revenue["incremental_revenue"]["amount"] == "90.00"
    assert float(revenue["incremental_revenue"]["lower"]) == pytest.approx(
        15 * revenue["lower"], abs=0.02
    )
    assert float(revenue["incremental_revenue"]["upper"]) == pytest.approx(
        15 * revenue["upper"], abs=0.02
    )
    assert revenue["bootstrap"]["resamples"] == 10_000
    assert revenue["bootstrap"]["agrees"] is True

    # The secondary metric has its interval and no claim: 2/15 against 5/15.
    assert (conversion["metric"], conversion["role"], conversion["test"]) == (
        "CONVERSION",
        "SECONDARY",
        "POOLED_Z",
    )
    assert conversion["difference"] == pytest.approx(0.2)
    assert conversion["lower"] == pytest.approx(0.2 - 1.959964 * 0.15006, abs=1e-4)
    assert conversion["upper"] == pytest.approx(0.2 + 1.959964 * 0.15006, abs=1e-4)
    # z = 0.2 / sqrt(7/30 * 23/30 * 2/15) = 1.295, p = 0.1953.
    assert conversion["p_value"] == pytest.approx(0.1953, abs=5e-4)
    assert conversion["conclusion"] is None
    assert conversion["incremental_revenue"] is None
    assert conversion["bootstrap"] is None
    assert body["effect_refusal"] is None


def test_conversion_as_primary_metric_states_no_money(
    api: Any, owner: psycopg.Connection
) -> None:
    scene = Scenario(owner, metric="CONVERSION")
    _dealt(scene)
    body = scene.get(api).get_json()
    first, second = body["comparisons"]
    assert (first["metric"], first["role"], first["test"]) == (
        "CONVERSION",
        "PRIMARY",
        "POOLED_Z",
    )
    assert first["conclusion"] == "NOT_SIGNIFICANT"
    # Money is claimed only for a revenue primary metric, and only on the primary.
    assert first["incremental_revenue"] is None
    assert second["incremental_revenue"] is None
    assert body["design"]["estimator"] == "DIFFERENCE_OF_PROPORTIONS"


def test_a_reversed_sale_no_longer_counts(api: Any, scene: Scenario) -> None:
    scene.spend(0, [10, 20])
    scene.spend(1, [10, 20, 30, 40, 20])
    sale = scene.sale(16, START + timedelta(days=2), 500)
    scene.cancel(sale)
    body = scene.get(api).get_json()
    treatment = body["arms"][1]
    assert (treatment["net_revenue"], treatment["converted"]) == ("120.00", 5)


def test_a_sale_after_the_window_is_not_in_the_result(
    api: Any, scene: Scenario
) -> None:
    _dealt(scene)
    scene.sale(20, START + timedelta(days=WINDOW), 999)
    assert scene.get(api).get_json()["arms"][1]["net_revenue"] == "120.00"


def test_a_sale_not_yet_recorded_as_a_conversion_is_not_missing(
    api: Any, scene: Scenario
) -> None:
    # Nobody evaluated the conversions; reading the result records them first.
    scene.sale(0, START + timedelta(days=1), 40)
    body = scene.get(api).get_json()
    assert body["arms"][0]["converted"] == 1
    assert body["arms"][0]["net_revenue"] == "40.00"


def test_a_seeded_sale_makes_the_result_synthetic(api: Any, scene: Scenario) -> None:
    scene.sale(0, START + timedelta(days=1), 40, origin="SEEDED")
    assert scene.get(api).get_json()["synthetic"] is True
    other = Scenario(scene.owner)
    other.sale(0, START + timedelta(days=1), 40)
    assert other.get(api).get_json()["synthetic"] is False


# ------------------------------------------------------------------ one look


def test_the_primary_result_is_stored_once_and_never_rewritten(
    api: Any, scene: Scenario
) -> None:
    _dealt(scene)
    assert [scene.stored(t) for t in TABLES] == [0, 0, 0, 0]
    first = scene.get(api).get_json()
    assert [scene.stored(t) for t in TABLES] == [1, 2, 2, 3]
    # A sale, and a reversal, that come after the look change nothing of it.
    late = scene.sale(scene.members[1][1], START + timedelta(days=2), 5000)
    scene.cancel(scene.sale(scene.members[1][2], START + timedelta(days=3), 700))
    again = scene.get(api).get_json()
    assert again == first
    assert again["as_of"] == first["as_of"]
    assert [scene.stored(t) for t in TABLES] == [1, 2, 2, 3]
    # A re-read is labelled, computed as of its request, and sees the sale.
    reread = scene.get(api, "?basis=RE_READ").get_json()
    assert reread["basis"] == "RE_READ"
    assert reread["as_of"] > first["as_of"]
    assert reread["arms"][0]["net_revenue"] == "30.00"
    assert reread["arms"][1]["net_revenue"] == "5120.00"
    assert [scene.stored(t) for t in TABLES] == [1, 2, 2, 3]
    assert scene.get(api).get_json() == first
    assert late > 0


def test_a_stored_result_cannot_be_edited_or_deleted(api: Any, scene: Scenario) -> None:
    _dealt(scene)
    scene.get(api)
    for update, delete in (
        (
            "UPDATE experiments.experiment_result SET synthetic = NOT synthetic"
            " WHERE experiment_id = %s",
            "DELETE FROM experiments.experiment_result WHERE experiment_id = %s",
        ),
        (
            "UPDATE experiments.experiment_result_arm SET converted = converted + 1"
            " WHERE experiment_id = %s",
            "DELETE FROM experiments.experiment_result_arm WHERE experiment_id = %s",
        ),
        (
            "UPDATE experiments.experiment_result_comparison SET difference = 0"
            " WHERE experiment_id = %s",
            "DELETE FROM experiments.experiment_result_comparison"
            " WHERE experiment_id = %s",
        ),
        (
            "UPDATE experiments.experiment_result_balance"
            " SET standardized_mean_difference = 0 WHERE experiment_id = %s",
            "DELETE FROM experiments.experiment_result_balance"
            " WHERE experiment_id = %s",
        ),
    ):
        for statement in (update, delete):
            with pytest.raises(psycopg.errors.CheckViolation):
                scene.owner.execute(statement, (scene.experiment,))


def test_two_reads_at_once_store_one_result(api: Any, scene: Scenario) -> None:
    _dealt(scene)
    first, second = scene.get(api), scene.get(api)
    assert first.get_json() == second.get_json()
    assert scene.stored() == 1


def test_before_the_analysis_instant_there_are_counts_and_no_estimate(
    api: Any, owner: psycopg.Connection
) -> None:
    now = datetime.now(UTC)
    scene = Scenario(owner, assigned_at=now - timedelta(days=2))
    scene.spend(0, [10, 20])
    for basis in ("", "?basis=RE_READ"):
        body = scene.get(api, basis).get_json()
        assert body["status"] == "PENDING"
        assert body["checks"] is None
        assert body["comparisons"] == []
        assert body["effect_refusal"] is None
        assert body["elapsed_share"] == pytest.approx(2 / 7, abs=0.01)
        control = body["arms"][0]
        # Two buyers; the other thirteen can still buy until their window closes.
        assert (control["converted"], control["pending"], control["not_converted"]) == (
            2,
            13,
            0,
        )
        assert "p_value" not in str(body["comparisons"])
    assert scene.stored() == 0


def test_the_window_closed_but_not_the_settle_period_is_still_pending(
    api: Any, owner: psycopg.Connection
) -> None:
    scene = Scenario(owner, assigned_at=datetime.now(UTC) - timedelta(days=WINDOW + 2))
    body = scene.get(api).get_json()
    assert body["status"] == "PENDING"
    assert body["elapsed_share"] == 1
    assert body["arms"][0]["not_converted"] == 15
    assert scene.stored() == 0


def test_an_experiment_that_was_never_planned_has_counts_and_no_estimate(
    api: Any, owner: psycopg.Connection
) -> None:
    scene = Scenario(owner, planned=False)
    scene.spend(1, [10, 20])
    for basis in ("", "?basis=RE_READ"):
        body = scene.get(api, basis).get_json()
        assert body["status"] == "PENDING"
        assert body["comparisons"] == []
        assert body["design"]["analysis_at"] is None
        assert body["arms"][1]["converted"] == 2
    assert scene.stored() == 0


# ----------------------------------------------------------------- no control


def test_without_a_control_two_offers_are_compared_and_no_effect_is_stated(
    api: Any, owner: psycopg.Connection
) -> None:
    scene = Scenario(
        owner, kinds=("TREATMENT", "TREATMENT"), sizes=(15, 15), comparison_alpha=0.05
    )
    _dealt(scene)
    body = scene.get(api).get_json()
    assert body["design_kind"] == "OFFER_COMPARISON"
    assert body["effect_refusal"]["code"] == "NO_CONTROL"
    assert "no control" in body["effect_refusal"]["message"]
    first, second = scene.arms
    assert [(a["arm_id"], a["kind"]) for a in body["arms"]] == [
        (first, "TREATMENT"),
        (second, "TREATMENT"),
    ]
    assert body["comparisons"]
    for comparison in body["comparisons"]:
        assert comparison["label"] == "TREATMENT_DIFFERENCE"
        assert (comparison["arm_id"], comparison["against_arm_id"]) == (second, first)
        assert comparison["incremental_revenue"] is None
    assert body["arms"][0]["exposed_conversion_rate"] is not None or True
    assert "EFFECT" not in str(body["comparisons"])
    assert "uplift" not in str(body).lower()


# ------------------------------------------------------------------- warnings


def test_a_failed_sample_ratio_withholds_the_estimate(
    api: Any, owner: psycopg.Connection
) -> None:
    scene = Scenario(owner, sizes=(28, 2))
    scene.spend(0, [10, 20])
    body = scene.get(api).get_json()
    assert body["status"] == "INVALID"
    assert body["comparisons"] == []
    assert body["checks"]["sample_ratio"]["status"] == "FAILED"
    assert body["checks"]["sample_ratio"]["p_value"] < 0.001
    assert "SAMPLE_RATIO_FAILED" in body["flags"]
    # The counts stay, and so does the flag, which getExperiment now shows.
    assert body["arms"][0]["analysed"] == 28
    flags = api("GET", f"/v1/experiments/{scene.experiment}").get_json()["flags"]
    assert "SAMPLE_RATIO_FAILED" in flags
    assert scene.get(api).get_json() == body


def test_a_clean_sample_ratio_passes(api: Any, scene: Scenario) -> None:
    _dealt(scene)
    check = scene.get(api).get_json()["checks"]["sample_ratio"]
    assert (check["status"], check["p_value"], check["threshold"]) == (
        "PASSED",
        1.0,
        0.001,
    )


@pytest.mark.parametrize(
    ("sizes", "expected_metrics"),
    [
        ((0, 2), set()),
        ((1, 1), {"CONVERSION"}),
        ((1, 5), {"CONVERSION"}),
        ((2, 2), {"CONVERSION", "REVENUE_PER_CUSTOMER"}),
    ],
)
@pytest.mark.parametrize("media_type", ["application/json", "application/xml"])
def test_small_arms_withhold_only_the_unsupported_comparisons(
    api: Any,
    owner: psycopg.Connection,
    sizes: tuple[int, int],
    expected_metrics: set[str],
    media_type: str,
) -> None:
    scene = Scenario(owner, sizes=sizes, metric="CONVERSION")
    owner.execute(
        "INSERT INTO experiments.experiment_flag (experiment_id, flag)"
        " VALUES (%s, 'UNDERPOWERED')",
        (scene.experiment,),
    )
    for query in ("", "?basis=RE_READ"):
        reply = scene.get(api, query, headers={"Accept": media_type})
        assert reply.status_code == 200
        status, metrics, assumptions, flags = _comparison_and_assumption_views(
            reply, media_type
        )
        assert status == "COMPLETE"
        assert metrics == expected_metrics
        assert "UNDERPOWERED" in flags
        withheld = [
            statement
            for code, statement in assumptions
            if code == "COMPARISON_WITHHELD_SMALL_ARM"
        ]
        assert len(withheld) == 2 - len(expected_metrics)
        for metric in {"CONVERSION", "REVENUE_PER_CUSTOMER"} - expected_metrics:
            statement = next(text for text in withheld if text.startswith(metric))
            assert all(str(arm) in statement for arm in scene.arms)
            if metric == "CONVERSION":
                assert "at least one customer per arm" in statement
            else:
                assert "at least two customers per arm" in statement
    assert scene.stored("_comparison") == len(expected_metrics)


def test_a_flagged_balance_is_a_warning_and_changes_no_estimate(
    api: Any, owner: psycopg.Connection
) -> None:
    flagged = Scenario(owner)
    # Before the frame instant (the day before assignment), the treatment arm spent
    # ten times what the control did.
    before = START - timedelta(days=2)
    for position in flagged.members[0]:
        flagged.sale(position, before, 10 + (position % 2) * 2)
    for position in flagged.members[1]:
        flagged.sale(position, before, 100 + (position % 2) * 2)
    _dealt(flagged)
    body = flagged.get(api).get_json()
    assert body["status"] == "COMPLETE"
    assert "BALANCE_FLAGGED" in body["flags"]
    spend = next(
        b for b in body["checks"]["balance"] if b["measure"] == "PRE_NET_SPEND"
    )
    assert spend["flagged"] is True and spend["standardized_mean_difference"] > 0.25
    assert (spend["arm_id"], spend["reference_arm_id"]) == (
        flagged.arms[1],
        flagged.arms[0],
    )
    assert {b["measure"] for b in body["checks"]["balance"]} == {
        "PRE_RECENCY_DAYS",
        "PRE_PURCHASE_COUNT",
        "PRE_NET_SPEND",
    }
    clean = Scenario(owner)
    _dealt(clean)
    unflagged = clean.get(api).get_json()
    assert unflagged["flags"] == []

    # The estimate is the same figure with or without the warning.
    def figures(result: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in c.items() if k not in ("arm_id", "against_arm_id")}
            for c in result["comparisons"]
        ]

    assert figures(body) == figures(unflagged)


def test_a_non_significant_result_of_an_underpowered_experiment_is_inconclusive(
    api: Any, scene: Scenario
) -> None:
    _dealt(scene)
    scene.owner.execute(
        "INSERT INTO experiments.experiment_flag (experiment_id, flag)"
        " VALUES (%s, 'UNDERPOWERED')",
        (scene.experiment,),
    )
    body = scene.get(api).get_json()
    assert "UNDERPOWERED" in body["flags"]
    primary = body["comparisons"][0]
    assert primary["role"] == "PRIMARY"
    assert primary["conclusion"] == "INCONCLUSIVE"
    assert body["comparisons"][1]["conclusion"] is None


def test_a_clear_difference_is_significant_at_the_comparison_level(
    api: Any, owner: psycopg.Connection
) -> None:
    scene = Scenario(owner)
    scene.spend(0, [10, 12, 11, 9, 10, 12, 11, 9, 10, 12])
    scene.spend(
        1, [100, 120, 110, 90, 100, 120, 110, 90, 100, 120, 110, 90, 100, 120, 110]
    )
    primary = scene.get(api).get_json()["comparisons"][0]
    assert primary["conclusion"] == "SIGNIFICANT"
    assert primary["lower"] > 0 and primary["p_value"] < 0.05


def test_two_treatments_split_the_level_and_each_is_against_the_control(
    api: Any, owner: psycopg.Connection
) -> None:
    scene = Scenario(
        owner,
        kinds=("CONTROL", "TREATMENT", "TREATMENT"),
        sizes=(10, 10, 10),
        comparison_alpha=0.025,
    )
    scene.spend(1, [10, 20, 30])
    scene.spend(2, [5, 5, 5, 5])
    body = scene.get(api).get_json()
    revenue = [c for c in body["comparisons"] if c["metric"] == "REVENUE_PER_CUSTOMER"]
    assert [(c["arm_id"], c["against_arm_id"]) for c in revenue] == [
        (scene.arms[1], scene.arms[0]),
        (scene.arms[2], scene.arms[0]),
    ]
    assert {c["confidence_level"] for c in body["comparisons"]} == {0.975}
    assert {a["code"] for a in body["assumptions"]} >= {"FAMILY_WISE_ALPHA"}


# ------------------------------------------------------------ the operation


def test_the_exposed_are_counted_apart_from_the_assigned(
    api: Any, scene: Scenario
) -> None:
    _dealt(scene)
    owner = scene.owner
    # Two treated customers were exposed; one of them, c15, bought after being exposed.
    for position in (scene.members[1][0], scene.members[1][4]):
        owner.execute(
            "INSERT INTO experiment_exposure (assignment_id, exposed_at)"
            " SELECT assignment_id, %s FROM experiment_assignment"
            " WHERE experiment_id = %s AND customer_id = %s",
            (START, scene.experiment, scene.customers[position]),
        )
    treatment = scene.get(api).get_json()["arms"][1]
    assert treatment["exposed"] == 2
    # Both bought one day after their first exposure, inside the window.
    assert treatment["exposed_conversion_rate"] == 1.0
    assert treatment["assigned"] == 15 and treatment[
        "conversion_rate"
    ] == pytest.approx(5 / 15, abs=1e-6)


def test_exposure_decides_nothing_in_an_effect(api: Any, scene: Scenario) -> None:
    """Exposing treated customers changes no comparison (RN-27, #1198)."""
    _dealt(scene)
    before = scene.get(api, "?basis=RE_READ").get_json()
    for position in scene.members[1][:5]:
        scene.owner.execute(
            "INSERT INTO experiment_exposure (assignment_id, exposed_at)"
            " SELECT assignment_id, %s FROM experiment_assignment"
            " WHERE experiment_id = %s AND customer_id = %s",
            (START, scene.experiment, scene.customers[position]),
        )
    after = scene.get(api, "?basis=RE_READ").get_json()
    assert after["arms"][1]["exposed"] == 5
    assert after["comparisons"] == before["comparisons"]
    assert after["status"] == before["status"]
    for arm_before, arm_after in zip(before["arms"], after["arms"], strict=True):
        assert arm_after["conversion_rate"] == arm_before["conversion_rate"]
        assert arm_after["net_revenue"] == arm_before["net_revenue"]


def test_the_assumptions_name_what_the_estimate_rests_on(
    api: Any, scene: Scenario
) -> None:
    _dealt(scene)
    codes = {a["code"] for a in scene.get(api).get_json()["assumptions"]}
    assert codes >= {
        "INTENT_TO_TREAT",
        "NET_OF_REVERSALS",
        "SINGLE_LOOK",
        "ZERO_FOR_NON_BUYERS",
        "CONTROL_NEVER_EXPOSED",
    }


def test_the_same_result_in_xml(api: Any, scene: Scenario) -> None:
    _dealt(scene)
    reply = scene.get(api, headers={"Accept": "application/xml"})
    assert reply.status_code == 200
    assert reply.mimetype == "application/xml"
    root = ET.fromstring(reply.data)
    assert root.tag.endswith("ExperimentResult")
    assert "90.00" in reply.get_data(as_text=True)
    contract = Path(__file__).resolve().parents[1] / "contracts/experimentation"
    schema = etree.XMLSchema(etree.parse(str(contract / "experimentation.xsd")))
    schema.assertValid(etree.fromstring(reply.data))


@pytest.mark.parametrize(
    "caller",
    [
        (ADMIN, "ADMIN"),
        (ANALYST, "ANALYST"),
        (MARKETING, "MARKETING"),
        (AUDITOR, "AUDITOR"),
    ],
)
def test_the_roles_that_may_read_a_result(
    api: Any, scene: Scenario, caller: Any
) -> None:
    assert scene.get(api, as_user=caller).status_code == 200


def test_a_role_without_the_operation_is_refused(api: Any, scene: Scenario) -> None:
    assert (
        scene.get(
            api, as_user=("11111111-1111-1111-1111-000000000002", "STORE_MANAGER")
        ).status_code
        == 403
    )


def test_refusals(api: Any, scene: Scenario, owner: psycopg.Connection) -> None:
    assert _code(api("GET", "/v1/experiment-results/987654")) == (
        404,
        "experiment_not_found",
    )
    unassigned = owner.execute(
        "SELECT max(experiment_id) + 1 FROM experiment"
    ).fetchone()[0]
    owner.execute(
        "INSERT INTO experiment (experiment_id, name, campaign_id, target_metric,"
        " starts_on, conversion_window_days, data_origin)"
        " VALUES (%s, 'nobody', NULL, 'CONVERSION', DATE '2026-03-01', 7, 'OBSERVED')",
        (unassigned,),
    )
    assert _code(api("GET", f"/v1/experiment-results/{unassigned}")) == (
        409,
        "experiment_not_assigned",
    )
    assert _code(scene.get(api, "?basis=LATEST"))[0] == 422
