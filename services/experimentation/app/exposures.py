"""Project real engagement campaign exposures (F25-10, RN-49, ADR-0075).

The stream carries the engagement event UUID and the exposure fact, including the
store where it happened, which api_experiments.exposure_v1 publishes. Engagement
already admits only one event per device ``client_event_id``; the UUID is the
projection's stable source key. Both notice rows and exposure facts are
append-only. A VOIDED notice is a tombstone consulted by counts, including when
it arrives before RECORDED.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection

LOGGER = logging.getLogger(__name__)
REPEAT_SECONDS = 60
SMALLINT_MAX = 32767

LOCK_SOURCE = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 841))"
NOTICE_EXISTS = """
SELECT EXISTS (
    SELECT FROM experiments.exposure_notice
     WHERE source_event_id = %s AND notice = %s
)
"""
FIND_ASSIGNMENT = """
SELECT a.assignment_id, a.experiment_id, g.kind
  FROM public.experiment_assignment AS a
  JOIN public.experiment AS e ON e.experiment_id = a.experiment_id
  JOIN public.experiment_group AS g
    ON g.group_id = a.group_id AND g.experiment_id = a.experiment_id
 WHERE a.customer_id = %(customer)s::uuid
   AND e.campaign_id = %(campaign)s
   AND %(occurred)s::date >= e.starts_on
   AND (e.ends_on IS NULL OR %(occurred)s::date <= e.ends_on)
 ORDER BY a.assigned_at DESC, a.assignment_id
 LIMIT 1
"""
LOCK_EXPERIMENT = (
    "SELECT experiment_id FROM public.experiment WHERE experiment_id = %s FOR UPDATE"
)
RECENT_EXPOSURE = """
SELECT EXISTS (
    SELECT
      FROM public.experiment_exposure AS x
     WHERE x.assignment_id = %(assignment)s
       AND abs(extract(epoch FROM (x.exposed_at - %(occurred)s::timestamptz)))
           < %(seconds)s
       AND NOT EXISTS (
           SELECT FROM experiments.exposure_notice AS v
            WHERE v.source_event_id = x.source_event_id
              AND v.notice = 'VOIDED'
       )
)
"""
INSERT_EXPOSURE = """
INSERT INTO public.experiment_exposure
       (assignment_id, exposed_at, source_event_id, store_id)
VALUES (%s, %s, %s, %s)
ON CONFLICT (source_event_id) WHERE source_event_id IS NOT NULL DO NOTHING
"""
INSERT_NOTICE = """
INSERT INTO experiments.exposure_notice
       (source_event_id, notice, assignment_id, occurred_at, disposition)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (source_event_id, notice) DO NOTHING
"""
ASSIGNMENT_COUNTS = """
SELECT count(*), min(x.exposed_at)
  FROM public.experiment_exposure AS x
 WHERE x.assignment_id = %s
   AND NOT EXISTS (
       SELECT FROM experiments.exposure_notice AS v
        WHERE v.source_event_id = x.source_event_id AND v.notice = 'VOIDED'
   )
"""
ARM_COUNTS = """
SELECT g.group_id, count(DISTINCT x.assignment_id)
  FROM public.experiment_group AS g
  LEFT JOIN public.experiment_assignment AS a ON a.group_id = g.group_id
  LEFT JOIN public.experiment_exposure AS x
    ON x.assignment_id = a.assignment_id
   AND NOT EXISTS (
       SELECT FROM experiments.exposure_notice AS v
        WHERE v.source_event_id = x.source_event_id AND v.notice = 'VOIDED'
   )
 WHERE g.experiment_id = %s
 GROUP BY g.group_id
 ORDER BY g.group_id
"""


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _source_event(value: str, notice: str) -> UUID:
    suffix = ":" + notice.lower()
    if not value.endswith(suffix):
        raise ValueError("event_id does not match notice")
    return UUID(value[: -len(suffix)])


def _store(value: str) -> int:
    """A store id as the stream encodes it: base-10, positive, a SMALLINT."""
    if not value.isascii() or not value.isdigit():
        raise ValueError("store_id is not a positive integer")
    store = int(value)
    if not 1 <= store <= SMALLINT_MAX:
        raise ValueError("store_id is out of range")
    return store


def _record_notice(
    connection: Connection,
    source: UUID,
    notice: str,
    assignment: int | None,
    occurred_at: datetime,
    disposition: str,
) -> None:
    connection.execute(
        INSERT_NOTICE, (source, notice, assignment, occurred_at, disposition)
    )


def apply(connection: Connection, fields: dict[str, str]) -> None:
    """Apply one stream entry inside the caller's transaction.

    Other engagement kinds are deliberately ignored. Malformed exposure facts
    raise so the stream helper retries them and acknowledges only after five
    failed deliveries, as the stream contract requires.
    """
    if fields.get("type") != "CAMPAIGN_EXPOSURE":
        return
    notice = fields.get("notice", "")
    if notice not in {"RECORDED", "VOIDED"}:
        raise ValueError("campaign exposure notice is invalid")
    source = _source_event(fields.get("event_id", ""), notice)
    occurred_at = _instant(fields["occurred_at"])
    connection.execute(LOCK_SOURCE, (str(source),))
    if connection.execute(NOTICE_EXISTS, (source, notice)).fetchone()[0]:
        return

    if notice == "VOIDED":
        recorded = connection.execute(
            "SELECT assignment_id FROM experiments.exposure_notice"
            " WHERE source_event_id = %s AND notice = 'RECORDED'",
            (source,),
        ).fetchone()
        _record_notice(
            connection,
            source,
            notice,
            recorded[0] if recorded else None,
            occurred_at,
            "VOIDED",
        )
        return

    customer = str(UUID(fields["customer_id"]))
    campaign_text = fields["campaign_id"]
    if not campaign_text.isascii() or not campaign_text.isdigit():
        raise ValueError("campaign_id is not a positive integer")
    campaign = int(campaign_text)
    if campaign < 1:
        raise ValueError("campaign_id is not a positive integer")
    store = _store(fields["store_id"])

    if connection.execute(NOTICE_EXISTS, (source, "VOIDED")).fetchone()[0]:
        _record_notice(connection, source, notice, None, occurred_at, "VOIDED")
        return
    row = connection.execute(
        FIND_ASSIGNMENT,
        {"customer": customer, "campaign": campaign, "occurred": occurred_at},
    ).fetchone()
    if row is None:
        _record_notice(connection, source, notice, None, occurred_at, "OUTSIDE")
        return
    assignment, experiment, kind = row
    if kind == "CONTROL":
        _record_notice(connection, source, notice, assignment, occurred_at, "CONTROL")
        return

    # This is the web's experiment-row serialization, applied to the event's
    # real occurrence time. It makes concurrent events inside 60 seconds leave
    # one fact, including two deliveries with different stream entry ids.
    connection.execute(LOCK_EXPERIMENT, (experiment,)).fetchone()
    recent = connection.execute(
        RECENT_EXPOSURE,
        {
            "assignment": assignment,
            "occurred": occurred_at,
            "seconds": REPEAT_SECONDS,
        },
    ).fetchone()[0]
    disposition = "REPEAT" if recent else "EXPOSURE"
    if not recent:
        connection.execute(INSERT_EXPOSURE, (assignment, occurred_at, source, store))
    _record_notice(connection, source, notice, assignment, occurred_at, disposition)


def assignment_count(connection: Connection, assignment_id: int) -> dict[str, Any]:
    """The contract's exposure_count and first_exposed_at for one assignment."""
    count, first = connection.execute(ASSIGNMENT_COUNTS, (assignment_id,)).fetchone()
    return {"exposure_count": count, "first_exposed_at": first}


def arm_counts(connection: Connection, experiment_id: int) -> dict[int, int]:
    """The contract's non-voided exposed-customer count for each arm."""
    return dict(connection.execute(ARM_COUNTS, (experiment_id,)).fetchall())
