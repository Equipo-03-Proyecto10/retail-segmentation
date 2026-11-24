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


def _published(scene: Scenario) -> list[tuple[Any, ...]]:
    """This scenario's rows of api_experiments.exposure_v1."""
    return scene.owner.execute(
        "SELECT source_event_id, campaign_id, experiment_id, group_id,"
        " customer_id::text, store_id, exposed_at, data_origin"
        " FROM api_experiments.exposure_v1 WHERE experiment_id = %s"
        " ORDER BY exposed_at",
        (scene.experiment,),
    ).fetchall()


def test_exposure_view_publishes_one_row_per_redelivered_event(
    service: Any, scene: Scenario
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    source = uuid.uuid4()
    event = scene.fields(scene.customers[1], START, source=source)
    handler(event)
    handler(event)
    handler(dict(event))
    assert _published(scene) == [
        (
            source,
            scene.campaign,
            scene.experiment,
            scene.treatment,
            scene.customers[1],
            1,
            START,
            "OBSERVED",
        )
    ]


def test_the_events_store_is_kept_and_published(service: Any, scene: Scenario) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    source = uuid.uuid4()
    event = scene.fields(scene.customers[1], START, source=source)
    event["store_id"] = "7"
    handler(event)
    stored = scene.owner.execute(
        "SELECT store_id FROM experiment_exposure WHERE source_event_id = %s",
        (source,),
    ).fetchall()
    assert stored == [(7,)]
    assert [(row[0], row[5]) for row in _published(scene)] == [(source, 7)]


@pytest.mark.parametrize("store", ["0", "-1", "x", "32768", "", "\u0661"])
def test_a_malformed_store_records_nothing(
    service: Any, scene: Scenario, store: str
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    event = scene.fields(scene.customers[1], START)
    event["store_id"] = store
    with pytest.raises(ValueError, match="store_id"):
        handler(event)
    assert scene.exposure_rows() == []


def test_a_web_written_exposure_is_published_without_a_store(
    service: Any, scene: Scenario
) -> None:
    assignment = scene.assignments[scene.customers[1]]
    scene.owner.execute(
        "INSERT INTO experiment_exposure (assignment_id, exposed_at) VALUES (%s, %s)",
        (assignment, START),
    )
    assert [(row[0], row[5]) for row in _published(scene)] == [(None, None)]


@pytest.mark.parametrize("void_first", [False, True])
def test_exposure_view_leaves_out_a_voided_exposure(
    service: Any, scene: Scenario, void_first: bool
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    voided_source, kept_source = uuid.uuid4(), uuid.uuid4()
    recorded = scene.fields(scene.customers[1], START, source=voided_source)
    voided = scene.fields(
        scene.customers[1], START, source=voided_source, notice="VOIDED"
    )
    for event in (voided, recorded) if void_first else (recorded, voided):
        handler(event)
    later = START + timedelta(minutes=5)
    handler(scene.fields(scene.customers[1], later, source=kept_source))
    assert [row[0] for row in _published(scene)] == [kept_source]


def test_exposure_view_never_names_the_control_arm(
    service: Any, scene: Scenario
) -> None:
    handler = service.extensions["experimentation_handle_exposure"]
    handler(scene.fields(scene.customers[0], START))
    handler(scene.fields(scene.customers[2], START))
    assert _published(scene) == []
    control_rows = scene.owner.execute(
        "SELECT count(*) FROM api_experiments.exposure_v1 AS v"
        " JOIN experiment_group AS g"
        " ON g.group_id = v.group_id AND g.experiment_id = v.experiment_id"
        " WHERE g.kind <> 'TREATMENT' OR v.campaign_id IS NULL"
    ).fetchone()[0]
    assert control_rows == 0


def test_exposure_view_has_unique_exposures_and_source_events(
    owner: psycopg.Connection,
) -> None:
    total, exposures_, sources, with_source = owner.execute(
        "SELECT count(*), count(DISTINCT exposure_id),"
        " count(DISTINCT source_event_id), count(source_event_id)"
        " FROM api_experiments.exposure_v1"
    ).fetchone()
    assert total > 0  # The seeded exposures are published.
    assert exposures_ == total
    assert sources == with_source


def test_kpis_reads_the_exposure_view_and_not_the_tables(
    owner: psycopg.Connection,
) -> None:
    def can(relation: str) -> bool:
        return owner.execute(
            "SELECT has_table_privilege('svc_kpis', %s, 'SELECT')", (relation,)
        ).fetchone()[0]

    assert can("api_experiments.exposure_v1")
    assert not can("public.experiment_exposure")
    assert not can("experiments.exposure_notice")
    assert not owner.execute(
        "SELECT has_table_privilege('retail_app', 'api_experiments.exposure_v1',"
        " 'SELECT')"
    ).fetchone()[0]
    with owner.transaction():
        owner.execute("SET LOCAL ROLE svc_kpis")
        assert (
            owner.execute(
                "SELECT count(*) FROM api_experiments.exposure_v1"
            ).fetchone()[0]
            > 0
        )
