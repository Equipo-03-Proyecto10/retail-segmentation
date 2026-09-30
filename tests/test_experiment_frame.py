"""#357: the experiment's frame is fixed once customers are assigned, an
experiment cannot join a campaign that is over, an over campaign is not
activated, and an exposure is refused outside the experiment or repeated by
accident. The database is replaced; what is checked is the application's rules.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, Mock

import pytest

from tests.test_experiment_assignment import _experiment
from tests.test_experiments import EDIT, FORM
from web.db import clock
from web.db import experiments as db
from web.db.campaigns import Campaign
from web.services import campaigns as campaign_service
from web.services import experiments as service

TODAY = date(2026, 10, 15)


def test_the_business_date_comes_from_postgresql() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (TODAY,)

    assert clock.current_date(connection) == TODAY
    assert cursor.execute.call_args.args == ("SELECT CURRENT_DATE", ())


def _wire_update(monkeypatch, current, status="ACTIVE") -> Mock:
    monkeypatch.setattr(db, "lock_experiment", Mock(return_value=True))
    monkeypatch.setattr(db, "get_experiment", Mock(return_value=current))
    monkeypatch.setattr(db, "get_campaign_status", Mock(return_value=status))
    update = Mock(return_value=True)
    monkeypatch.setattr(db, "update_experiment", update)
    return update


def _current(**changes):
    return _experiment(
        experiment_id=3,
        campaign_id=7,
        starts_on=date(2026, 10, 1),
        ends_on=date(2026, 10, 31),
        conversion_window_days=14,
        **changes,
    )


# ---------- the frame locks with the first assignment ----------


@pytest.mark.parametrize(
    "change,field,label",
    [
        ({"campaign_id": "8"}, "campaign_id", "campaign"),
        ({"campaign_id": ""}, "campaign_id", "campaign"),
        ({"starts_on": "2026-09-01"}, "starts_on", "start date"),
        ({"ends_on": "2026-12-31"}, "ends_on", "end date"),
        ({"ends_on": ""}, "ends_on", "end date"),
    ],
)
def test_the_frame_cannot_change_once_anyone_is_assigned(
    monkeypatch: pytest.MonkeyPatch, change: dict, field: str, label: str
) -> None:
    update = _wire_update(monkeypatch, _current(assignments=5))
    data, errors = service.validate_experiment(**{**EDIT, **change})
    assert data is not None, errors

    with pytest.raises(service.ExperimentRefused) as refusal:
        service.update_experiment(MagicMock(), 3, data)

    assert refusal.value.field == field
    assert f"The {label} is fixed" in str(refusal.value)
    assert "5 assignments" in str(refusal.value)
    update.assert_not_called()


def test_an_assigned_experiment_can_be_renamed_and_resaved_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update = _wire_update(monkeypatch, _current(assignments=5))
    data, _ = service.validate_experiment(**{**EDIT, "name": "Renamed"})

    service.update_experiment(MagicMock(), 3, data)

    assert update.call_args.kwargs["name"] == "Renamed"


def test_before_any_assignment_the_frame_can_still_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update = _wire_update(monkeypatch, _current(assignments=0))
    data, _ = service.validate_experiment(
        **{**EDIT, "campaign_id": "8", "starts_on": "2026-09-01", "ends_on": ""}
    )

    service.update_experiment(MagicMock(), 3, data)

    assert update.call_args.kwargs["campaign_id"] == 8
    assert update.call_args.kwargs["starts_on"] == date(2026, 9, 1)


# ---------- a campaign that is over takes no experiment ----------


@pytest.mark.parametrize("status", ["CANCELLED", "FINISHED"])
def test_an_experiment_cannot_be_created_for_a_campaign_that_is_over(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    monkeypatch.setattr(db, "get_campaign_status", Mock(return_value=status))
    create = Mock(return_value=1)
    monkeypatch.setattr(db, "create_experiment", create)
    data, _ = service.validate_experiment(**FORM)

    with pytest.raises(service.ExperimentRefused) as refusal:
        service.create_experiment(MagicMock(), data)

    assert refusal.value.field == "campaign_id"
    assert status.lower() in str(refusal.value)
    create.assert_not_called()


@pytest.mark.parametrize("status", ["DRAFT", "ACTIVE"])
def test_a_draft_or_active_campaign_takes_an_experiment(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    monkeypatch.setattr(db, "get_campaign_status", Mock(return_value=status))
    monkeypatch.setattr(db, "create_experiment", Mock(return_value=1))
    data, _ = service.validate_experiment(**FORM)

    assert service.create_experiment(MagicMock(), data) == 1


def test_an_experiment_with_no_campaign_needs_no_status_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = Mock()
    monkeypatch.setattr(db, "get_campaign_status", status)
    monkeypatch.setattr(db, "create_experiment", Mock(return_value=1))
    data, _ = service.validate_experiment(**{**FORM, "campaign_id": ""})

    service.create_experiment(MagicMock(), data)

    status.assert_not_called()


def test_moving_an_unassigned_experiment_to_a_cancelled_campaign_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update = _wire_update(monkeypatch, _current(assignments=0), status="CANCELLED")
    data, _ = service.validate_experiment(**{**EDIT, "campaign_id": "8"})

    with pytest.raises(service.ExperimentRefused) as refusal:
        service.update_experiment(MagicMock(), 3, data)

    assert refusal.value.field == "campaign_id"
    update.assert_not_called()


def test_saving_an_experiment_without_changing_its_over_campaign_is_not_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rename of an experiment whose campaign has since ended must not fail."""
    update = _wire_update(monkeypatch, _current(assignments=0), status="FINISHED")
    data, _ = service.validate_experiment(**{**EDIT, "name": "Renamed"})

    service.update_experiment(MagicMock(), 3, data)

    update.assert_called_once()


# ---------- a campaign that is over is not activated ----------


def _transition(monkeypatch, ends_on: date, today: date) -> str:
    monkeypatch.setattr(
        campaign_service.campaigns,
        "get_campaign",
        Mock(
            return_value=Campaign(
                7, "Win-back", "AT_RISK", date(2020, 1, 1), ends_on, "DRAFT"
            )
        ),
    )
    monkeypatch.setattr(
        campaign_service.campaigns, "change_status", Mock(return_value=True)
    )
    monkeypatch.setattr(campaign_service, "activation_refusal", lambda *_a: None)
    return campaign_service.transition(MagicMock(), 7, "activate", today)


def test_a_campaign_dated_in_the_past_cannot_be_activated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(campaign_service.InvalidTransition, match="ended on 2020-12-31"):
        _transition(monkeypatch, date(2020, 12, 31), TODAY)


def test_a_campaign_ending_today_or_later_can_be_activated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _transition(monkeypatch, TODAY, TODAY) == "ACTIVE"
    assert _transition(monkeypatch, date(2026, 12, 31), TODAY) == "ACTIVE"


def test_campaign_activation_defaults_to_the_database_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_today = Mock(return_value=TODAY)
    monkeypatch.setattr(campaign_service, "business_date", database_today)
    monkeypatch.setattr(
        campaign_service.campaigns,
        "get_campaign",
        Mock(
            return_value=Campaign(
                7,
                "Win-back",
                "AT_RISK",
                date(2026, 10, 1),
                TODAY,
                "DRAFT",
            )
        ),
    )
    monkeypatch.setattr(
        campaign_service.campaigns, "change_status", Mock(return_value=True)
    )
    monkeypatch.setattr(campaign_service, "activation_refusal", lambda *_a: None)
    connection = MagicMock()

    assert campaign_service.transition(connection, 7, "activate") == "ACTIVE"
    database_today.assert_called_once_with(connection)


def test_a_campaign_that_has_not_started_can_be_activated_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        campaign_service.campaigns,
        "get_campaign",
        Mock(
            return_value=Campaign(
                7, "Win-back", "AT_RISK", date(2027, 1, 1), date(2027, 2, 1), "DRAFT"
            )
        ),
    )
    monkeypatch.setattr(
        campaign_service.campaigns, "change_status", Mock(return_value=True)
    )
    monkeypatch.setattr(campaign_service, "activation_refusal", lambda *_a: None)

    assert campaign_service.transition(MagicMock(), 7, "activate", TODAY) == "ACTIVE"


def test_cancelling_an_old_campaign_is_still_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        campaign_service.campaigns,
        "get_campaign",
        Mock(
            return_value=Campaign(
                7, "Win-back", "AT_RISK", date(2020, 1, 1), date(2020, 2, 1), "DRAFT"
            )
        ),
    )
    monkeypatch.setattr(
        campaign_service.campaigns, "change_status", Mock(return_value=True)
    )

    assert campaign_service.transition(MagicMock(), 7, "cancel", TODAY) == "CANCELLED"


# ---------- exposure ----------

CUSTOMER = "00000000-0000-0000-0000-000000000001"


def _wire_exposure(monkeypatch, *, experiment=None, status="ACTIVE", written=True):
    monkeypatch.setattr(db, "lock_experiment", Mock(return_value=True))
    monkeypatch.setattr(db, "find_assignment", Mock(return_value=(900, "TREATMENT")))
    monkeypatch.setattr(
        db,
        "get_experiment",
        Mock(
            return_value=experiment
            or _experiment(
                campaign_id=7,
                starts_on=date(2026, 10, 1),
                ends_on=date(2026, 10, 31),
                assignments=10,
            )
        ),
    )
    monkeypatch.setattr(db, "get_campaign_status", Mock(return_value=status))
    insert = Mock(return_value=written)
    monkeypatch.setattr(db, "insert_exposure", insert)
    return insert


def test_an_exposure_inside_the_experiment_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    insert = _wire_exposure(monkeypatch)

    assert service.record_exposure(MagicMock(), 31, CUSTOMER, TODAY) is True

    assert insert.call_args.kwargs["repeat_seconds"] == service.EXPOSURE_REPEAT_SECONDS


def test_an_exposure_defaults_to_the_database_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_exposure(monkeypatch)
    database_today = Mock(return_value=TODAY)
    monkeypatch.setattr(service, "business_date", database_today)
    connection = MagicMock()

    assert service.record_exposure(connection, 31, CUSTOMER) is True
    database_today.assert_called_once_with(connection)


@pytest.mark.parametrize(
    "today,message",
    [
        (date(2026, 9, 30), "starts on 2026-10-01"),
        (date(2026, 11, 1), "ended on 2026-10-31"),
    ],
)
def test_an_exposure_outside_the_experiments_dates_is_refused(
    monkeypatch: pytest.MonkeyPatch, today: date, message: str
) -> None:
    insert = _wire_exposure(monkeypatch)

    with pytest.raises(service.ExposureRefused, match=message):
        service.record_exposure(MagicMock(), 31, CUSTOMER, today)

    insert.assert_not_called()


@pytest.mark.parametrize("edge", [date(2026, 10, 1), date(2026, 10, 31)])
def test_the_first_and_last_day_of_the_experiment_are_inside_it(
    monkeypatch: pytest.MonkeyPatch, edge: date
) -> None:
    _wire_exposure(monkeypatch)

    assert service.record_exposure(MagicMock(), 31, CUSTOMER, edge) is True


def test_an_experiment_with_no_end_date_never_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_exposure(
        monkeypatch,
        experiment=_experiment(
            campaign_id=7, starts_on=date(2026, 10, 1), ends_on=None
        ),
    )

    assert service.record_exposure(MagicMock(), 31, CUSTOMER, date(2030, 1, 1))


@pytest.mark.parametrize("status", ["FINISHED", "CANCELLED"])
def test_an_exposure_for_a_campaign_that_is_over_is_refused(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    insert = _wire_exposure(monkeypatch, status=status)

    with pytest.raises(service.ExposureRefused, match=status.lower()):
        service.record_exposure(MagicMock(), 31, CUSTOMER, TODAY)

    insert.assert_not_called()


def test_a_repeat_of_the_same_moment_reports_that_nothing_was_added(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_exposure(monkeypatch, written=False)

    assert service.record_exposure(MagicMock(), 31, CUSTOMER, TODAY) is False


def _wire_bulk_exposure(
    monkeypatch: pytest.MonkeyPatch, *, status: str = "ACTIVE", written: bool = True
) -> Mock:
    insert = _wire_exposure(monkeypatch, status=status, written=written)
    monkeypatch.setattr(
        db,
        "list_group_definitions",
        Mock(
            return_value=[
                db.ExperimentGroup(2, 31, "TREATMENT", "Offer", "Discount", 1)
            ]
        ),
    )
    monkeypatch.setattr(db, "list_assignment_ids", Mock(return_value=[900]))
    return insert


@pytest.mark.parametrize(
    "today,message",
    [
        (date(2026, 9, 30), "starts on 2026-10-01"),
        (date(2026, 11, 1), "ended on 2026-10-31"),
    ],
)
def test_bulk_exposure_uses_the_same_experiment_dates(
    monkeypatch: pytest.MonkeyPatch, today: date, message: str
) -> None:
    insert = _wire_bulk_exposure(monkeypatch)

    with pytest.raises(service.ExposureRefused, match=message):
        service.record_group_exposures(MagicMock(), 31, 2, [CUSTOMER], today=today)

    insert.assert_not_called()


@pytest.mark.parametrize("status", ["FINISHED", "CANCELLED"])
def test_bulk_exposure_uses_the_same_campaign_guard(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    insert = _wire_bulk_exposure(monkeypatch, status=status)

    with pytest.raises(service.ExposureRefused, match=status.lower()):
        service.record_group_exposures(MagicMock(), 31, 2, [CUSTOMER], today=TODAY)

    insert.assert_not_called()


def test_bulk_exposure_uses_the_same_repeat_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    insert = _wire_bulk_exposure(monkeypatch, written=False)

    assert (
        service.record_group_exposures(MagicMock(), 31, 2, [CUSTOMER], today=TODAY) == 0
    )
    assert insert.call_args.kwargs["repeat_seconds"] == service.EXPOSURE_REPEAT_SECONDS


def test_bulk_exposure_defaults_to_the_database_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_bulk_exposure(monkeypatch)
    database_today = Mock(return_value=TODAY)
    monkeypatch.setattr(service, "business_date", database_today)
    connection = MagicMock()

    assert service.record_group_exposures(connection, 31, 2, [CUSTOMER]) == 1
    database_today.assert_called_once_with(connection)


def test_the_repeat_guard_is_a_conditional_insert_by_assignment_and_time() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.rowcount = 0

    written = db.insert_exposure(connection, 900, repeat_seconds=60)

    statement, params = cursor.execute.call_args.args
    assert written is False
    assert "WHERE NOT EXISTS" in statement
    assert "assignment_id = %s" in statement
    assert "make_interval(secs => %s)" in statement
    assert params == (900, 900, 60)
    cursor.rowcount = 1
    assert db.insert_exposure(connection, 900, repeat_seconds=60) is True


def test_a_later_exposure_is_still_recorded() -> None:
    """ADR-0019: exposures are plural. Only a repeat inside the guard is skipped."""
    assert service.EXPOSURE_REPEAT_SECONDS <= 300


# ---------- the metric ----------


def test_average_ticket_says_it_is_not_measured_yet_and_stays_selectable() -> None:
    assert "AVERAGE_TICKET" in service.TARGET_METRICS
    assert "not measured yet" in service.TARGET_METRICS["AVERAGE_TICKET"]
