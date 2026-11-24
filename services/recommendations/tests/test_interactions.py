"""Interactions kept from the engagement stream and what they earn (#1197, ADR-0090).

What is tested: a recorded impression or click becomes one row; a repeat is the same
row; a void hides the row whichever notice arrived first and a later record never
brings it back; an anonymous interaction is kept; `CAMPAIGN_EXPOSURE` and malformed
entries are not stored; a purchase counts only after a click on that product by that
customer, within seven days, on a net line; and analytics-kpis reads the views and
no table. Real PostgreSQL (the seeded database).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import psycopg
import pytest

from app import interactions

NOW = "2026-11-20T12:00:00.000Z"


def _entry(
    kind: str = "RECO_CLICK", notice: str = "RECORDED", **over: str
) -> dict[str, str]:
    event = over.pop("event", str(uuid.uuid4()))
    fields = {
        "event_id": f"{event}:{notice.lower()}",
        "v": "1",
        "notice": notice,
        "type": kind,
        "store_id": "1",
        "product_id": "1",
        "customer_id": str(uuid.uuid4()),
        "label_code": "A",
        "rank": "1",
        "occurred_at": NOW,
    }
    fields.update(over)
    return fields


def _event(fields: dict[str, str]) -> str:
    return fields["event_id"].rsplit(":", 1)[0]


@pytest.fixture
def service(service_url: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(service_url, autocommit=True) as connection:
        yield connection


@pytest.fixture
def cleanup(owner: psycopg.Connection[Any]) -> Iterator[list[str]]:
    events: list[str] = []
    yield events
    owner.execute(
        "DELETE FROM recommendations.recommendation_interaction"
        " WHERE event_id = ANY(%s::uuid[])",
        (events,),
    )


def _row(owner: psycopg.Connection[Any], event: str) -> tuple[Any, ...] | None:
    return owner.execute(
        "SELECT kind, voided, customer_id FROM"
        " recommendations.recommendation_interaction WHERE event_id = %s",
        (event,),
    ).fetchone()


def _published(owner: psycopg.Connection[Any], event: str) -> int:
    return owner.execute(
        "SELECT count(*) FROM api_recommendations.interaction_v1 WHERE event_id = %s",
        (event,),
    ).fetchone()[0]


def test_a_recorded_interaction_is_one_row_and_a_repeat_changes_nothing(
    service: psycopg.Connection[Any],
    owner: psycopg.Connection[Any],
    cleanup: list[str],
) -> None:
    fields = _entry("RECO_IMPRESSION")
    cleanup.append(_event(fields))
    interactions.apply(service, fields)
    interactions.apply(service, fields)
    assert _row(owner, _event(fields))[:2] == ("RECO_IMPRESSION", False)
    assert _published(owner, _event(fields)) == 1


def test_a_void_hides_the_row_and_a_later_record_does_not_bring_it_back(
    service: psycopg.Connection[Any],
    owner: psycopg.Connection[Any],
    cleanup: list[str],
) -> None:
    recorded = _entry()
    cleanup.append(_event(recorded))
    interactions.apply(service, recorded)
    interactions.apply(service, _entry(notice="VOIDED", event=_event(recorded)))
    assert _published(owner, _event(recorded)) == 0
    interactions.apply(service, recorded)
    assert _row(owner, _event(recorded))[1] is True
    assert _published(owner, _event(recorded)) == 0


def test_a_void_that_arrives_first_is_kept(
    service: psycopg.Connection[Any],
    owner: psycopg.Connection[Any],
    cleanup: list[str],
) -> None:
    voided = _entry(notice="VOIDED")
    cleanup.append(_event(voided))
    interactions.apply(service, voided)
    interactions.apply(service, _entry(event=_event(voided)))
    assert _row(owner, _event(voided))[1] is True
    assert _published(owner, _event(voided)) == 0


def test_an_anonymous_interaction_is_kept_without_a_customer(
    service: psycopg.Connection[Any],
    owner: psycopg.Connection[Any],
    cleanup: list[str],
) -> None:
    fields = _entry()
    del fields["customer_id"]
    cleanup.append(_event(fields))
    interactions.apply(service, fields)
    assert _row(owner, _event(fields)) == ("RECO_CLICK", False, None)


def test_an_exposure_and_a_malformed_entry_store_nothing(
    service: psycopg.Connection[Any],
    owner: psycopg.Connection[Any],
    cleanup: list[str],
) -> None:
    exposure = _entry("CAMPAIGN_EXPOSURE")
    cleanup.append(_event(exposure))
    interactions.apply(service, exposure)
    assert _row(owner, _event(exposure)) is None
    for field, value in (
        ("notice", "REPLAYED"),
        ("store_id", "0"),
        ("rank", "-1"),
        ("product_id", "x"),
        ("occurred_at", "2026-11-20T12:00:00"),
        ("event_id", "not-a-uuid:recorded"),
    ):
        bad = _entry(**{field: value})
        cleanup.append(_event(bad)) if field != "event_id" else None
        with pytest.raises(ValueError):
            interactions.apply(service, bad)


@pytest.fixture
def line(owner: psycopg.Connection[Any]) -> dict[str, Any]:
    """A net sale line whose customer has no interaction on that product."""
    row = owner.execute(
        "SELECT n.transaction_id, n.customer_id, n.product_id, n.store_id,"
        " n.occurred_at FROM api_sales.net_purchase_line_v1 AS n"
        " WHERE NOT EXISTS (SELECT FROM recommendations.recommendation_interaction AS i"
        "  WHERE i.customer_id = n.customer_id AND i.product_id = n.product_id)"
        " ORDER BY n.transaction_id, n.product_id LIMIT 1"
    ).fetchone()
    assert row is not None
    keys = ("transaction_id", "customer_id", "product_id", "store_id", "occurred_at")
    return dict(zip(keys, row, strict=True))


def _click(
    service: psycopg.Connection[Any],
    cleanup: list[str],
    line: dict[str, Any],
    before: timedelta,
    kind: str = "RECO_CLICK",
    **over: str,
) -> str:
    fields = _entry(
        kind,
        store_id=str(line["store_id"]),
        product_id=str(line["product_id"]),
        customer_id=str(line["customer_id"]),
        occurred_at=(line["occurred_at"] - before).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        **over,
    )
    cleanup.append(_event(fields))
    interactions.apply(service, fields)
    return _event(fields)


def _purchases(owner: psycopg.Connection[Any], line: dict[str, Any]) -> list[Any]:
    return owner.execute(
        "SELECT click_event_id, net_quantity FROM"
        " api_recommendations.recommended_purchase_v1"
        " WHERE transaction_id = %s AND product_id = %s",
        (line["transaction_id"], line["product_id"]),
    ).fetchall()


def test_a_click_within_seven_days_earns_the_purchase_and_an_older_one_does_not(
    service: psycopg.Connection[Any],
    owner: psycopg.Connection[Any],
    cleanup: list[str],
    line: dict[str, Any],
) -> None:
    assert _purchases(owner, line) == []
    _click(service, cleanup, line, timedelta(days=8))
    assert _purchases(owner, line) == []
    near = _click(service, cleanup, line, timedelta(days=6))
    assert [str(r[0]) for r in _purchases(owner, line)] == [near]


def test_several_clicks_give_one_row_against_the_earliest(
    service: psycopg.Connection[Any],
    owner: psycopg.Connection[Any],
    cleanup: list[str],
    line: dict[str, Any],
) -> None:
    _click(service, cleanup, line, timedelta(days=1))
    earliest = _click(service, cleanup, line, timedelta(days=5))
    rows = _purchases(owner, line)
    assert [str(r[0]) for r in rows] == [earliest]


def test_an_impression_a_void_or_another_product_earns_nothing(
    service: psycopg.Connection[Any],
    owner: psycopg.Connection[Any],
    cleanup: list[str],
    line: dict[str, Any],
) -> None:
    _click(service, cleanup, line, timedelta(days=1), kind="RECO_IMPRESSION")
    voided = _click(service, cleanup, line, timedelta(days=2))
    interactions.apply(service, _entry(notice="VOIDED", event=voided))
    other = dict(line, product_id=line["product_id"] + 1_000_000)
    _click(service, cleanup, other, timedelta(days=1))
    after = _entry(
        store_id=str(line["store_id"]),
        product_id=str(line["product_id"]),
        customer_id=str(line["customer_id"]),
        occurred_at=(line["occurred_at"] + timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        ),
    )
    cleanup.append(_event(after))
    interactions.apply(service, after)
    assert _purchases(owner, line) == []


def test_analytics_kpis_reads_the_views_and_no_table(owner_url: str) -> None:
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(owner_url)
    netloc = f"svc_kpis:svc_kpis@{parts.hostname}" + (
        f":{parts.port}" if parts.port else ""
    )
    url = urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
    with psycopg.connect(url, autocommit=True) as kpis:
        assert (
            kpis.execute(
                "SELECT count(*) FROM api_recommendations.interaction_v1"
            ).fetchone()[0]
            >= 60
        )
        kpis.execute("SELECT count(*) FROM api_recommendations.recommended_purchase_v1")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            kpis.execute("SELECT 1 FROM recommendations.recommendation_interaction")


def test_the_web_role_has_no_access(retail_url: str) -> None:
    with psycopg.connect(retail_url, autocommit=True) as retail:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            retail.execute("SELECT 1 FROM recommendations.recommendation_interaction")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            retail.execute("SELECT 1 FROM api_recommendations.interaction_v1")
