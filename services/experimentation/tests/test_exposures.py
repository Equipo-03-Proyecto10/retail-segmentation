"""Real campaign exposures projected from the real Redis stream and PostgreSQL."""

from __future__ import annotations

import os
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
import redis
from mosaiq_kit import StreamConsumer

from app import exposures
from app.config import ENGAGEMENT_STREAM

pytestmark = pytest.mark.postgres

START = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


class Scenario:
    """An active campaign with one control, one treatment and one outsider."""

    def __init__(self, owner: psycopg.Connection) -> None:
        self.owner = owner
        self.campaign = owner.execute(
            "SELECT max(campaign_id) + 1 FROM campaign"
        ).fetchone()[0]
        self.experiment = owner.execute(
            "SELECT max(experiment_id) + 1 FROM experiment"
        ).fetchone()[0]
        first_group = owner.execute(
            "SELECT max(group_id) + 1 FROM experiment_group"
        ).fetchone()[0]
        self.control, self.treatment = first_group, first_group + 1
        self.customers = [
            str(row[0])
            for row in owner.execute(
                "SELECT customer_id FROM customer ORDER BY customer_id LIMIT 3"
            )
        ]
        owner.execute(
            "INSERT INTO campaign"
            " (campaign_id, name, label_code, starts_on, ends_on, status)"
            " VALUES (%s, 'F25-10 floor',"
            " (SELECT min(label_code) FROM segment_label), %s, %s, 'ACTIVE')",
            (self.campaign, START.date(), (START + timedelta(days=10)).date()),
        )
        owner.execute(
            "INSERT INTO experiment"
            " (experiment_id, name, campaign_id, target_metric, starts_on, ends_on,"
            " conversion_window_days, data_origin)"
            " VALUES (%s, 'F25-10 exposure', %s, 'CONVERSION', %s, %s, 7,"
            " 'OBSERVED')",
            (
                self.experiment,
                self.campaign,
                START.date(),
                (START + timedelta(days=10)).date(),
            ),
        )
        for group, kind in ((self.control, "CONTROL"), (self.treatment, "TREATMENT")):
            owner.execute(
                "INSERT INTO experiment_group"
                " (group_id, experiment_id, kind, name, treatment_description)"
                " VALUES (%s, %s, %s, %s, %s)",
                (group, self.experiment, kind, kind.title(), kind.title()),
            )
        for customer, group in zip(
            self.customers[:2], (self.control, self.treatment), strict=True
        ):
            owner.execute(
                "INSERT INTO experiment_assignment"
                " (experiment_id, group_id, customer_id, assigned_at)"
                " VALUES (%s, %s, %s, %s)",
                (self.experiment, group, customer, START - timedelta(hours=1)),
            )
        self.assignments = dict(
            owner.execute(
                "SELECT customer_id::text, assignment_id FROM experiment_assignment"
                " WHERE experiment_id = %s",
                (self.experiment,),
            ).fetchall()
        )

    def fields(
        self,
        customer: str,
        at: datetime,
        *,
        source: uuid.UUID | None = None,
        notice: str = "RECORDED",
        kind: str = "CAMPAIGN_EXPOSURE",
    ) -> dict[str, str]:
        source = source or uuid.uuid4()
        return {
            "event_id": f"{source}:{notice.lower()}",
            "v": "1",
            "notice": notice,
            "type": kind,
            "store_id": "1",
            "customer_id": customer,
            "campaign_id": str(self.campaign),
            "occurred_at": at.isoformat().replace("+00:00", "Z"),
        }

    def published(self) -> list[tuple[Any, ...]]:
        """This campaign's rows of api_experiments.exposure_v1, read as svc_kpis."""
        with self.owner.transaction():
            self.owner.execute("SET LOCAL ROLE svc_kpis")
            return self.owner.execute(
                "SELECT campaign_id, experiment_id, customer_id::text, store_id,"
                " exposed_at, data_origin FROM api_experiments.exposure_v1"
                " WHERE campaign_id = %s ORDER BY exposed_at",
                (self.campaign,),
            ).fetchall()

    def exposure_rows(self) -> list[tuple[Any, ...]]:
        return self.owner.execute(
            "SELECT assignment_id, exposed_at, source_event_id"
            " FROM experiment_exposure WHERE assignment_id = ANY(%s)"
            " ORDER BY exposed_at",
            (list(self.assignments.values()),),
        ).fetchall()


@pytest.fixture
def scene(owner: psycopg.Connection) -> Scenario:
    return Scenario(owner)


def test_duplicate_and_concurrent_duplicate_leave_one_exposure(
    service: Any, scene: Scenario
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    event = scene.fields(scene.customers[1], START)
    handler(event)
    handler(event)
    concurrent = scene.fields(scene.customers[1], START + timedelta(seconds=60))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(handler, (concurrent, concurrent)))
    rows = scene.exposure_rows()
    assert len(rows) == 2
    assert {str(row[2]) for row in rows} == {
        item["event_id"].removesuffix(":recorded") for item in (event, concurrent)
    }
    notices = scene.owner.execute(
        "SELECT notice, disposition FROM experiments.exposure_notice"
        " WHERE source_event_id = ANY(%s::uuid[]) ORDER BY occurred_at",
        ([item["event_id"].removesuffix(":recorded") for item in (event, concurrent)],),
    ).fetchall()
    assert notices == [
        ("RECORDED", "EXPOSURE"),
        ("RECORDED", "EXPOSURE"),
    ]


def test_the_webs_sixty_second_rule_uses_the_real_occurrence_time(
    service: Any, scene: Scenario
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    for offset in (0, 59, 60):
        handler(scene.fields(scene.customers[1], START + timedelta(seconds=offset)))
    rows = scene.exposure_rows()
    assert [row[1] for row in rows] == [START, START + timedelta(seconds=60)]
    dispositions = scene.owner.execute(
        "SELECT disposition FROM experiments.exposure_notice"
        " WHERE assignment_id = %s AND notice = 'RECORDED'"
        " ORDER BY occurred_at",
        (scene.assignments[scene.customers[1]],),
    ).fetchall()
    assert dispositions == [("EXPOSURE",), ("REPEAT",), ("EXPOSURE",)]
    assignment = scene.assignments[scene.customers[1]]
    assert exposures.assignment_count(scene.owner, assignment)["exposure_count"] == 2
    assert exposures.arm_counts(scene.owner, scene.experiment)[scene.treatment] == 1


def test_outside_and_control_customers_never_become_exposed(
    service: Any, scene: Scenario
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    handler(scene.fields(scene.customers[0], START))
    handler(scene.fields(scene.customers[2], START))
    assert scene.exposure_rows() == []
    dispositions = scene.owner.execute(
        "SELECT disposition, assignment_id FROM experiments.exposure_notice"
        " ORDER BY disposition",
    ).fetchall()
    assert dispositions == [
        ("CONTROL", scene.assignments[scene.customers[0]]),
        ("OUTSIDE", None),
    ]
    assert exposures.arm_counts(scene.owner, scene.experiment) == {
        scene.control: 0,
        scene.treatment: 0,
    }
    assert scene.published() == []


@pytest.mark.parametrize("void_first", [False, True])
def test_voided_notice_removes_or_prevents_the_exposure_count(
    service: Any, scene: Scenario, void_first: bool
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    source = uuid.uuid4()
    recorded = scene.fields(scene.customers[1], START, source=source)
    voided = scene.fields(scene.customers[1], START, source=source, notice="VOIDED")
    for event in ((voided, recorded) if void_first else (recorded, voided)):
        handler(event)
    assignment = scene.assignments[scene.customers[1]]
    assert exposures.assignment_count(scene.owner, assignment) == {
        "exposure_count": 0,
        "first_exposed_at": None,
    }
    assert exposures.arm_counts(scene.owner, scene.experiment)[scene.treatment] == 0
    # The accepted fact is never rewritten; a void is a separate notice.
    assert len(scene.exposure_rows()) == (0 if void_first else 1)
    assert scene.published() == []


class Crash(BaseException):
    """A process death is outside the kit's Exception retry boundary."""


def test_crash_after_effect_before_xack_reapplies_to_the_same_state(
    service: Any,
    scene: Scenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = os.environ["TEST_REDIS_URL"]
    client = redis.Redis.from_url(url, decode_responses=True)
    group = "experimentation-crash-" + uuid.uuid4().hex
    monkeypatch.setenv("MOSAIQ_SERVICE_NAME", group)
    production = service.extensions["experimentation_handle_exposure"]
    first = StreamConsumer(client, ENGAGEMENT_STREAM, production)
    first.consumer = socket.gethostname() + "-crash"
    first.run_once(1)  # Create this test's group at the current tail.
    fields = scene.fields(scene.customers[1], START)
    client.xadd(ENGAGEMENT_STREAM, fields)

    def crash_after_commit(entry: dict[str, str]) -> None:
        production(entry)
        raise Crash

    crashing = StreamConsumer(client, ENGAGEMENT_STREAM, crash_after_commit)
    crashing.consumer = first.consumer
    with pytest.raises(Crash):
        crashing.run_once(10)
    assert len(scene.exposure_rows()) == 1
    assert client.xpending(ENGAGEMENT_STREAM, group)["pending"] == 1

    restarted = StreamConsumer(client, ENGAGEMENT_STREAM, production)
    restarted.consumer = first.consumer
    assert restarted.run_once(10) == 1
    assert len(scene.exposure_rows()) == 1
    assert client.xpending(ENGAGEMENT_STREAM, group)["pending"] == 0
    client.xgroup_destroy(ENGAGEMENT_STREAM, group)
    client.close()


def test_assignment_exposure_and_sale_stay_three_separate_facts(
    service: Any, scene: Scenario
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    assignment = scene.assignments[scene.customers[1]]
    before = scene.owner.execute(
        "SELECT experiment_id, group_id, customer_id, assigned_at"
        " FROM experiment_assignment WHERE assignment_id = %s",
        (assignment,),
    ).fetchone()
    handler(scene.fields(scene.customers[1], START))
    after = scene.owner.execute(
        "SELECT experiment_id, group_id, customer_id, assigned_at"
        " FROM experiment_assignment WHERE assignment_id = %s",
        (assignment,),
    ).fetchone()
    conversions = scene.owner.execute(
        "SELECT count(*) FROM experiment_conversion WHERE assignment_id = %s",
        (assignment,),
    ).fetchone()[0]
    assert after == before
    assert len(scene.exposure_rows()) == 1
    assert conversions == 0


def test_other_engagement_kinds_are_ignored(service: Any, scene: Scenario) -> None:
    service.extensions["experimentation_handle_exposure"](
        scene.fields(scene.customers[1], START, kind="RECO_CLICK")
    )
    assert scene.exposure_rows() == []
    assert (
        scene.owner.execute(
            "SELECT count(*) FROM experiments.exposure_notice"
        ).fetchone()[0]
        == 0
    )


def test_exposure_view_publishes_each_exposure_once_with_its_store(
    service: Any, scene: Scenario
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    customer = scene.customers[1]
    event = scene.fields(customer, START)
    event["store_id"] = "7"
    for _ in range(3):  # A relay duplicate and a redelivery after a crash.
        handler(event)
    without_store = scene.fields(customer, START + timedelta(minutes=5))
    del without_store["store_id"]
    handler(without_store)
    assert scene.published() == [
        (scene.campaign, scene.experiment, customer, 7, START, "OBSERVED"),
        (
            scene.campaign,
            scene.experiment,
            customer,
            None,
            START + timedelta(minutes=5),
            "OBSERVED",
        ),
    ]


def test_exposure_view_drops_an_exposure_once_voided(
    service: Any, scene: Scenario
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    source = uuid.uuid4()
    handler(scene.fields(scene.customers[1], START, source=source))
    assert len(scene.published()) == 1
    voided = scene.fields(scene.customers[1], START, source=source, notice="VOIDED")
    handler(voided)
    handler(voided)
    assert scene.published() == []
    assert len(scene.exposure_rows()) == 1


@pytest.mark.parametrize("store", ["0", "x", "40000", "-1"])
def test_a_malformed_store_is_refused_and_writes_nothing(
    service: Any, scene: Scenario, store: str
) -> None:
    event = scene.fields(scene.customers[1], START)
    event["store_id"] = store
    with pytest.raises(ValueError, match="store_id"):
        service.extensions["experimentation_handle_exposure"](event)
    assert scene.exposure_rows() == []


def test_the_schema_refuses_a_control_exposure_from_any_writer(
    scene: Scenario,
) -> None:
    control = scene.assignments[scene.customers[0]]
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        with scene.owner.transaction():
            scene.owner.execute(
                "INSERT INTO experiment_exposure (assignment_id, exposed_at, store_id)"
                " VALUES (%s, %s, 1)",
                (control, START),
            )
    assert refused.value.diag.constraint_name == "experiment_exposure_treatment_only"
    assert scene.published() == []


def test_svc_kpis_reads_the_view_and_no_table_beneath_it(scene: Scenario) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with scene.owner.transaction():
            scene.owner.execute("SET LOCAL ROLE svc_kpis")
            scene.owner.execute("SELECT count(*) FROM public.experiment_exposure")
    assert scene.published() == []
