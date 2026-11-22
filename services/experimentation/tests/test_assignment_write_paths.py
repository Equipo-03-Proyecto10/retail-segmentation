"""The write paths test_assignment.py did not try (F25-08, RN-42, #839)."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from tests.test_assignment import Scenario


@pytest.fixture
def scene(owner: psycopg.Connection) -> Scenario:
    return Scenario(owner)


def test_a_truncate_is_refused_for_the_owner_and_by_a_cascade(
    api: Any, scene: Scenario
) -> None:
    assert scene.assign(api).status_code == 201
    before = scene.rows()
    for statement in (
        "TRUNCATE public.experiment_assignment CASCADE",
        "TRUNCATE public.experiment_group CASCADE",
        "TRUNCATE public.experiment CASCADE",
    ):
        with pytest.raises(psycopg.errors.CheckViolation) as refused:
            scene.owner.execute(statement)
        assert (
            refused.value.diag.constraint_name
            == "experiment_assignment_never_rewritten"
        )
    assert scene.rows() == before


def test_deleting_the_experiment_or_its_arm_cannot_cascade_the_assignment_away(
    api: Any, scene: Scenario
) -> None:
    assert scene.assign(api).status_code == 201
    before = scene.rows()
    for statement, params in (
        ("DELETE FROM public.experiment_group WHERE group_id = %s", (scene.arms[0],)),
        (
            "DELETE FROM public.experiment WHERE experiment_id = %s",
            (scene.experiment,),
        ),
        (
            "DELETE FROM public.customer WHERE customer_id = %s",
            (scene.customers[0],),
        ),
    ):
        with pytest.raises(psycopg.errors.Error):
            scene.owner.execute(statement, params)
    assert scene.rows() == before


def test_a_customer_cannot_be_moved_to_another_customer_or_experiment(
    api: Any, scene: Scenario
) -> None:
    assert scene.assign(api).status_code == 201
    before = scene.rows()
    for statement, params in (
        (
            "UPDATE public.experiment_assignment SET customer_id = %s"
            " WHERE customer_id = %s",
            (scene.customers[1], scene.customers[0]),
        ),
        (
            "UPDATE public.experiment_assignment SET assigned_at = now()"
            " WHERE experiment_id = %s",
            (scene.experiment,),
        ),
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            scene.owner.execute(statement, params)
    assert scene.rows() == before
