"""api_experiments.exposure_v1 shows the campaign exposures that still count (#1198).

What is tested: a projected exposure appears with its campaign, experiment,
customer and time; a VOIDED notice removes it whichever arrived first; the
control, an outsider and an experiment without a campaign have no row;
analytics-kpis reads the view and no table; and the web has no privilege on it.
Real PostgreSQL (a private copy of the seeded database).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import psycopg
import pytest

from tests.conftest import _with_database  # noqa: PLC2701
from tests.test_exposures import START, Scenario

pytestmark = pytest.mark.postgres

VIEW = (
    "SELECT campaign_id, experiment_id, customer_id::text, exposed_at, data_origin"
    " FROM api_experiments.exposure_v1 WHERE experiment_id = %s"
)


@pytest.fixture
def scene(owner: psycopg.Connection) -> Scenario:
    return Scenario(owner)


def _rows(scene: Scenario) -> list[tuple[Any, ...]]:
    return scene.owner.execute(VIEW, (scene.experiment,)).fetchall()


def test_a_projected_exposure_appears_with_its_campaign_customer_and_time(
    service: Any, scene: Scenario
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    assert _rows(scene) == []
    handler(scene.fields(scene.customers[1], START))
    assert _rows(scene) == [
        (scene.campaign, scene.experiment, scene.customers[1], START, "OBSERVED")
    ]


def test_a_repeat_inside_sixty_seconds_is_one_row(
    service: Any, scene: Scenario
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    handler(scene.fields(scene.customers[1], START))
    handler(scene.fields(scene.customers[1], START + timedelta(seconds=30)))
    assert len(_rows(scene)) == 1


@pytest.mark.parametrize("void_first", [False, True])
def test_a_voided_exposure_has_no_row_whichever_notice_came_first(
    service: Any, scene: Scenario, void_first: bool
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    source = uuid.uuid4()
    recorded = scene.fields(scene.customers[1], START, source=source)
    voided = scene.fields(scene.customers[1], START, source=source, notice="VOIDED")
    for event in (voided, recorded) if void_first else (recorded, voided):
        handler(event)
    assert _rows(scene) == []


def test_the_control_an_outsider_and_a_campaignless_experiment_have_no_row(
    service: Any, scene: Scenario
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    handler(scene.fields(scene.customers[0], START))
    handler(scene.fields(scene.customers[2], START))
    assert _rows(scene) == []
    scene.owner.execute(
        "UPDATE experiment SET campaign_id = NULL WHERE experiment_id = %s",
        (scene.experiment,),
    )
    handler(scene.fields(scene.customers[1], START + timedelta(hours=1)))
    assert _rows(scene) == []


def test_an_exposure_the_web_wrote_without_a_source_event_counts(
    scene: Scenario,
) -> None:
    scene.owner.execute(
        "INSERT INTO experiment_exposure (assignment_id, exposed_at)"
        " VALUES (%s, %s)",
        (scene.assignments[scene.customers[1]], START),
    )
    assert len(_rows(scene)) == 1


def test_analytics_kpis_reads_the_view_and_no_table(
    database: dict[str, str], service: Any, scene: Scenario
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    handler(scene.fields(scene.customers[1], START))
    name = database["owner"].rsplit("/", 1)[1]
    with psycopg.connect(
        _with_database(database["owner"], name, role="svc_kpis"), autocommit=True
    ) as kpis:
        assert kpis.execute(VIEW, (scene.experiment,)).fetchall()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            kpis.execute("SELECT 1 FROM public.experiment_exposure")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            kpis.execute("SELECT 1 FROM experiments.exposure_notice")


def test_the_web_has_no_privilege_on_the_view(database: dict[str, str]) -> None:
    with psycopg.connect(database["web"], autocommit=True) as web:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            web.execute("SELECT 1 FROM api_experiments.exposure_v1")
