"""Conversions in their window, net of reversals, against PostgreSQL (F25-11).

Every test builds its own experiment on its own copy of the seeded database, so
every count is written out by hand. Times are fixed instants, never the clock.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from app import conversions
from tests.conftest import ADMIN, ANALYST, AUDITOR, MARKETING, _with_database

pytestmark = pytest.mark.postgres

MARKETING_CALLER = (MARKETING, "MARKETING")
START = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
WINDOW = 7


def _code(reply: Any) -> tuple[int, str | None]:
    return reply.status_code, (reply.get_json(silent=True) or {}).get("code")


class Scenario:
    """One experiment, one control and one treatment arm, six customers.

    Customers c0 to c2 are assigned to the control arm and c3 to c5 to the
    treatment arm, all at START. The window is seven days.
    """

    def __init__(self, owner: psycopg.Connection) -> None:
        self.owner = owner
        self.experiment = owner.execute(
            "SELECT max(experiment_id) + 1 FROM experiment"
        ).fetchone()[0]
        first = owner.execute(
            "SELECT max(group_id) + 1 FROM experiment_group"
        ).fetchone()[0]
        self.control, self.treatment = first, first + 1
        owner.execute(
            "INSERT INTO experiment (experiment_id, name, campaign_id, target_metric,"
            " starts_on, conversion_window_days, data_origin)"
            " VALUES (%s, 'F25-11 window', NULL, 'CONVERSION', DATE '2026-03-01',"
            " %s, 'OBSERVED')",
            (self.experiment, WINDOW),
        )
        for group, kind in ((self.control, "CONTROL"), (self.treatment, "TREATMENT")):
            owner.execute(
                "INSERT INTO experiment_group (group_id, experiment_id, kind, name,"
                " treatment_description) VALUES (%s, %s, %s, %s, %s)",
                (group, self.experiment, kind, kind.title(), kind.title()),
            )
        self.customers = [
            str(row[0])
            for row in owner.execute(
                "SELECT customer_id FROM customer ORDER BY customer_id LIMIT 6"
            )
        ]
        # Nothing the seed holds is inside this experiment's windows.
        owner.execute(
            "UPDATE transaction SET occurred_at = TIMESTAMPTZ '2000-01-01'"
            " WHERE customer_id = ANY(%s::uuid[])",
            (self.customers,),
        )
        for position, customer in enumerate(self.customers):
            owner.execute(
                "INSERT INTO experiment_assignment (experiment_id, group_id,"
                " customer_id, assigned_at) VALUES (%s, %s, %s, %s)",
                (
                    self.experiment,
                    self.control if position < 3 else self.treatment,
                    customer,
                    START,
                ),
            )
        self.counter = 0

    def sale(self, position: int, when: datetime, *, quantity: int = 2) -> int:
        """A sale of ten per unit by customer `position`, at `when`."""
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
                " VALUES (%s, %s, %s, %s, %s, %s, 'SEEDED') RETURNING transaction_id",
                (
                    f"f25-11-{self.experiment}-{self.counter}",
                    self.customers[position],
                    store,
                    channel,
                    when,
                    quantity * 10,
                ),
            ).fetchone()
            self.owner.execute(
                "INSERT INTO transaction_line (transaction_id, product_id, quantity,"
                " unit_price) VALUES (%s, %s, %s, 10.00)",
                (transaction_id, product, quantity),
            )
        return transaction_id

    def reverse(self, transaction_id: int, *, units: int | None = None) -> None:
        """A cancellation (`units` None) or a return of `units`, as svc_sales."""
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
            taken = quantity if units is None else units
            (reversal,) = sales.execute(
                "INSERT INTO sales.sale_reversal (transaction_id, kind, reason,"
                " idempotency_key, request_sha256, recorded_by)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING reversal_id",
                (
                    transaction_id,
                    "CANCELLATION" if units is None else "RETURN",
                    "test" if units is None else None,
                    uuid.uuid4(),
                    bytes(32),
                    ADMIN,
                ),
            ).fetchone()
            sales.execute(
                "INSERT INTO sales.sale_reversal_line (reversal_id, transaction_id,"
                " product_id, quantity, reason) VALUES (%s, %s, %s, %s, %s)",
                (
                    reversal,
                    transaction_id,
                    product,
                    taken,
                    None if units is None else "test",
                ),
            )
            sales.execute(
                "UPDATE public.transaction_line SET returned_quantity ="
                " returned_quantity + %s WHERE transaction_id = %s",
                (taken, transaction_id),
            )

    def rows(self, api: Any, query: str = "") -> list[dict[str, Any]]:
        reply = api("GET", f"/v1/experiments/{self.experiment}/conversions{query}")
        assert reply.status_code == 200, reply.get_json()
        return reply.get_json()["conversions"]

    def evaluate(self, api: Any, **kwargs: Any) -> dict[str, Any]:
        reply = api(
            "POST",
            f"/v1/experiments/{self.experiment}/conversions",
            as_user=kwargs.pop("as_user", MARKETING_CALLER),
        )
        assert reply.status_code == 200, reply.get_json()
        return reply.get_json()


@pytest.fixture
def scene(owner: psycopg.Connection) -> Scenario:
    return Scenario(owner)


def test_a_sale_is_a_conversion_inside_the_window_only(
    api: Any, scene: Scenario
) -> None:
    inside = scene.sale(0, START)  # the assignment instant opens the window
    scene.sale(1, START - timedelta(seconds=1))  # before the assignment
    last = scene.sale(2, START + timedelta(days=WINDOW) - timedelta(seconds=1))
    scene.sale(3, START + timedelta(days=WINDOW))  # the window is open on the right
    scene.sale(4, START + timedelta(days=WINDOW + 3))
    outcome = scene.evaluate(api)
    assert outcome["recorded"] == 2
    assert outcome["already_recorded"] == 0
    assert sorted(row["sale_id"] for row in scene.rows(api)) == sorted([inside, last])


def test_a_sale_of_someone_who_is_not_assigned_is_never_recorded(
    api: Any, scene: Scenario, owner: psycopg.Connection
) -> None:
    stranger = str(
        owner.execute(
            "SELECT customer_id FROM customer WHERE customer_id <> ALL(%s::uuid[])"
            " ORDER BY customer_id LIMIT 1",
            (scene.customers,),
        ).fetchone()[0]
    )
    scene.customers.append(stranger)
    scene.sale(6, START + timedelta(days=1))
    assert scene.evaluate(api)["recorded"] == 0


def test_evaluating_twice_records_the_same_conversions(
    api: Any, scene: Scenario
) -> None:
    for position in range(6):
        scene.sale(position, START + timedelta(days=position))
    first = scene.evaluate(api)
    rows = scene.rows(api)
    second = scene.evaluate(api)
    assert (first["recorded"], first["already_recorded"]) == (6, 0)
    assert (second["recorded"], second["already_recorded"]) == (0, 6)
    # Not one recorded row moved, converted_at included.
    again = scene.rows(api)
    for before, after in zip(rows, again, strict=True):
        assert {k: v for k, v in before.items() if k != "as_of"} == {
            k: v for k, v in after.items() if k != "as_of"
        }
    assert len({row["converted_at"] for row in rows}) == 1


def test_a_later_evaluation_adds_only_the_sale_it_had_not_seen(
    api: Any, scene: Scenario
) -> None:
    scene.sale(0, START + timedelta(days=1))
    scene.evaluate(api)
    before = scene.rows(api)
    scene.sale(0, START + timedelta(days=2))  # recorded late, dated inside the window
    outcome = scene.evaluate(api)
    after = scene.rows(api)
    assert (outcome["recorded"], outcome["already_recorded"]) == (1, 1)
    assert [r["conversion_id"] for r in after][:1] == [before[0]["conversion_id"]]
    assert after[0] == {**before[0], "as_of": after[0]["as_of"]}


def test_a_sale_with_several_lines_is_one_conversion(
    api: Any, scene: Scenario, owner: psycopg.Connection
) -> None:
    transaction = scene.sale(0, START + timedelta(days=1))
    other = owner.execute(
        "SELECT min(product_id) FROM product WHERE product_id NOT IN"
        " (SELECT product_id FROM transaction_line WHERE transaction_id = %s)",
        (transaction,),
    ).fetchone()[0]
    with owner.transaction():
        owner.execute(
            "INSERT INTO transaction_line (transaction_id, product_id, quantity,"
            " unit_price) VALUES (%s, %s, 1, 10.00)",
            (transaction, other),
        )
        owner.execute(
            "UPDATE transaction SET total = 30.00 WHERE transaction_id = %s",
            (transaction,),
        )
    assert scene.evaluate(api)["recorded"] == 1
    (row,) = scene.rows(api)
    assert row["net_amount"] == "30.00"


def test_a_sale_cancelled_before_the_evaluation_is_not_a_conversion(
    api: Any, scene: Scenario
) -> None:
    gone = scene.sale(0, START + timedelta(days=1))
    scene.reverse(gone)
    assert scene.evaluate(api)["recorded"] == 0


def test_a_sale_reversed_after_it_converted_stays_and_stops_counting(
    api: Any, scene: Scenario, owner: psycopg.Connection
) -> None:
    cancelled = scene.sale(0, START + timedelta(days=1))
    returned = scene.sale(1, START + timedelta(days=2))
    kept = scene.sale(2, START + timedelta(days=3))
    scene.evaluate(api)
    recorded = owner.execute(
        "SELECT conversion_id, assignment_id, transaction_id, converted_at"
        " FROM experiment_conversion ORDER BY conversion_id"
    ).fetchall()
    scene.reverse(cancelled)
    scene.reverse(returned, units=1)
    by_sale = {row["sale_id"]: row for row in scene.rows(api)}
    assert (by_sale[cancelled]["counts"], by_sale[cancelled]["net_amount"]) == (
        False,
        "0.00",
    )
    assert (by_sale[returned]["counts"], by_sale[returned]["net_amount"]) == (
        True,
        "10.00",
    )
    assert (by_sale[kept]["counts"], by_sale[kept]["net_amount"]) == (True, "20.00")
    # The cancelled sale still has its date and its origin, though the net view lost it.
    assert by_sale[cancelled]["sold_at"] == "2026-03-02T12:00:00Z"
    assert by_sale[cancelled]["sale_data_origin"] == "SEEDED"
    # Not a row was added, changed or removed, however often it runs again.
    outcome = scene.evaluate(api)
    assert (outcome["recorded"], outcome["already_recorded"]) == (0, 3)
    assert (
        owner.execute(
            "SELECT conversion_id, assignment_id, transaction_id, converted_at"
            " FROM experiment_conversion ORDER BY conversion_id"
        ).fetchall()
        == recorded
    )
    # counts=false lists the cancelled one, counts=true the other two.
    assert [r["sale_id"] for r in scene.rows(api, "?counts=false")] == [cancelled]
    assert {r["sale_id"] for r in scene.rows(api, "?counts=true")} == {returned, kept}


def test_the_application_roles_can_neither_rewrite_nor_remove_a_conversion(
    api: Any, scene: Scenario, database: dict[str, str]
) -> None:
    scene.sale(0, START + timedelta(days=1))
    scene.evaluate(api)
    with psycopg.connect(database["service"], autocommit=True) as service:
        for statement in (
            "UPDATE public.experiment_conversion SET converted_at = now()",
            "DELETE FROM public.experiment_conversion",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                service.execute(statement)


def test_the_filters_narrow_the_list(api: Any, scene: Scenario) -> None:
    scene.sale(0, START + timedelta(days=1))
    scene.sale(3, START + timedelta(days=1))
    scene.sale(3, START + timedelta(days=2))
    scene.evaluate(api)
    assert len(scene.rows(api)) == 3
    assert [r["arm_id"] for r in scene.rows(api, f"?arm_id={scene.control}")] == [
        scene.control
    ]
    assert len(scene.rows(api, f"?arm_id={scene.treatment}")) == 2
    customer = scene.customers[3]
    assert len(scene.rows(api, f"?customer_id={customer}")) == 2
    rows = scene.rows(api, "?page=2&size=2")
    assert len(rows) == 1
    reply = api("GET", f"/v1/experiments/{scene.experiment}/conversions?size=2")
    assert reply.get_json()["page_info"]["total"] == 3


@pytest.mark.parametrize(
    "query",
    ["?customer_id=nope", "?arm_id=0", "?arm_id=x", "?counts=maybe"],
)
def test_a_bad_filter_is_invalid_query(api: Any, scene: Scenario, query: str) -> None:
    reply = api("GET", f"/v1/experiments/{scene.experiment}/conversions{query}")
    assert _code(reply) == (422, "invalid_query")


def test_an_unknown_experiment_is_404_and_an_unassigned_one_is_409(
    api: Any, owner: psycopg.Connection
) -> None:
    assert _code(api("GET", "/v1/experiments/999999/conversions")) == (
        404,
        "experiment_not_found",
    )
    assert _code(api("POST", "/v1/experiments/999999/conversions")) == (
        404,
        "experiment_not_found",
    )
    empty = owner.execute(
        "INSERT INTO experiment (experiment_id, name, target_metric, starts_on,"
        " conversion_window_days, data_origin) SELECT max(experiment_id) + 1,"
        " 'Unassigned', 'CONVERSION', DATE '2026-03-01', 7, 'OBSERVED'"
        " FROM experiment RETURNING experiment_id"
    ).fetchone()[0]
    reply = api("POST", f"/v1/experiments/{empty}/conversions")
    assert _code(reply) == (409, "experiment_not_assigned")


def test_roles(api: Any, scene: Scenario) -> None:
    path = f"/v1/experiments/{scene.experiment}/conversions"
    for caller in ((ANALYST, "ANALYST"), (AUDITOR, "AUDITOR")):
        assert api("POST", path, as_user=caller).status_code == 403
        assert api("GET", path, as_user=caller).status_code == 200
    assert api("POST", path, as_user=(ADMIN, "ADMIN")).status_code == 200
    assert api("POST", path, as_user=MARKETING_CALLER).status_code == 200


def test_open_windows_are_pending_and_closed_ones_are_not_converted(
    scene: Scenario, owner: psycopg.Connection
) -> None:
    # c0 and c3 bought; c1 and c4 bought and had it cancelled; c2 and c5 never did.
    keep0 = scene.sale(0, START + timedelta(days=1))
    gone1 = scene.sale(1, START + timedelta(days=1))
    scene.sale(3, START + timedelta(days=1))
    gone4 = scene.sale(4, START + timedelta(days=1))
    owner.execute(
        "INSERT INTO experiment_conversion (assignment_id, transaction_id)"
        " SELECT a.assignment_id, t.transaction_id FROM experiment_assignment a"
        " JOIN transaction t ON t.customer_id = a.customer_id"
        " WHERE a.experiment_id = %s AND t.source_transaction_id LIKE 'f25-11-%%'",
        (scene.experiment,),
    )
    scene.reverse(gone1)
    scene.reverse(gone4)
    assert keep0
    url = _with_database(os.environ["TEST_DATABASE_URL"], owner.info.dbname)
    with psycopg.connect(url) as connection:
        window_open = START + timedelta(days=3)
        window_closed = START + timedelta(days=WINDOW + 1)
        open_arms = {
            a["kind"]: a
            for a in conversions.arm_outcomes(connection, scene.experiment, window_open)
        }
        closed_arms = {
            a["kind"]: a
            for a in conversions.arm_outcomes(
                connection, scene.experiment, window_closed
            )
        }
    for kind, converted, rest in (("CONTROL", 1, 2), ("TREATMENT", 1, 2)):
        opened, closed = open_arms[kind], closed_arms[kind]
        assert opened["assigned"] == opened["analysed"] == 3
        assert (opened["converted"], opened["pending"], opened["not_converted"]) == (
            converted,
            rest,
            0,
        )
        assert (closed["converted"], closed["pending"], closed["not_converted"]) == (
            converted,
            0,
            rest,
        )
        for arm in (opened, closed):
            assert arm["converted"] + arm["pending"] + arm["not_converted"] == 3


def test_the_new_view_column_is_the_origin_of_the_sale(
    scene: Scenario, owner: psycopg.Connection
) -> None:
    sale = scene.sale(0, START)
    (origin,) = owner.execute(
        "SELECT DISTINCT data_origin FROM api_sales.purchase_line_v1"
        " WHERE transaction_id = %s",
        (sale,),
    ).fetchone()
    assert origin == "SEEDED"


def test_both_operations_answer_in_xml_too(api: Any, scene: Scenario) -> None:
    scene.sale(0, START + timedelta(days=1))
    path = f"/v1/experiments/{scene.experiment}/conversions"
    evaluated = api(
        "POST", path, as_user=MARKETING_CALLER, headers={"Accept": "application/xml"}
    )
    listed = api("GET", path, headers={"Accept": "application/xml"})
    assert evaluated.status_code == listed.status_code == 200
    assert b"<ConversionEvaluation" in evaluated.data
    assert b"<Conversions" in listed.data and b"<sale_id>" in listed.data
