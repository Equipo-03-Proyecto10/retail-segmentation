"""Audiences, eligibility, exclusions and budgets against PostgreSQL (F25-04).

Campaign 1 is a seeded DRAFT. Each scenario moves every other holder of its
label away and gives eight chosen customers a known history, so every count is
written out by hand rather than read back from the query under test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from tests.conftest import ADMIN, ANALYST, AUDITOR, MARKETING

pytestmark = pytest.mark.postgres

MARKETING_CALLER = (MARKETING, "MARKETING")
ANALYST_CALLER = (ANALYST, "ANALYST")
AUDITOR_CALLER = (AUDITOR, "AUDITOR")
REASONS = [
    "not_in_customer_reference",
    "personalization_withdrawn",
    "marketing_contact_withdrawn",
    "excluded_customer",
    "excluded_label",
    "registered_after_cutoff",
    "too_few_purchases",
    "over_max_customers",
]
DRAFT = 1
ACTIVE = 2


def _code(reply: Any) -> tuple[int, str | None]:
    return reply.status_code, (reply.get_json(silent=True) or {}).get("code")


def _counts(reply: Any) -> dict[str, int]:
    assert reply.status_code == 200, reply.get_json()
    return {entry["code"]: entry["excluded"] for entry in reply.get_json()["reasons"]}


def _decide(
    owner: psycopg.Connection, customer: str, purpose: str, decision: str
) -> None:
    owner.execute(
        "INSERT INTO preferences.consent_decision (customer_id, purpose, decision,"
        " recorded_by, source, basis) VALUES (%s, %s, %s, %s, 'STAFF_ON_BEHALF',"
        " 'EXPLICIT')",
        (customer, purpose, decision, ADMIN),
    )


def _sale(owner: psycopg.Connection, customer: str, tag: str) -> None:
    store, channel, product = owner.execute(
        "SELECT (SELECT min(store_id) FROM store),"
        " (SELECT min(channel_id) FROM channel),"
        " (SELECT min(product_id) FROM product)"
    ).fetchone()
    with owner.transaction():
        (transaction_id,) = owner.execute(
            "INSERT INTO transaction (source_transaction_id, customer_id, store_id,"
            " channel_id, occurred_at, total, data_origin)"
            " VALUES (%s, %s, %s, %s, now() - interval '3 days', 10.00, 'SEEDED')"
            " RETURNING transaction_id",
            (f"f25-04-{tag}-{customer}", customer, store, channel),
        ).fetchone()
        owner.execute(
            "INSERT INTO transaction_line (transaction_id, product_id, quantity,"
            " unit_price) VALUES (%s, %s, 1, 10.00)",
            (transaction_id, product),
        )


def _clear_limits(owner: psycopg.Connection) -> None:
    """The seed gives campaign 1 every limit; these tests start with none."""
    owner.execute("DELETE FROM campaigns.campaign_audience WHERE campaign_id = 1")
    owner.execute("DELETE FROM campaigns.campaign_budget WHERE campaign_id = 1")
    owner.execute("DELETE FROM campaigns.campaign_eligibility WHERE campaign_id = 1")
    owner.execute(
        "DELETE FROM campaigns.campaign_excluded_customer WHERE campaign_id = 1"
    )
    owner.execute("DELETE FROM campaigns.campaign_excluded_label WHERE campaign_id = 1")


def _scenario(owner: psycopg.Connection, *, silent: int | None = None) -> list[str]:
    """The first eight customers, c0 to c7, made the only holders of campaign 1's label.

    All eight grant both purposes (but `silent`, a new customer with no consent
    row), were registered in 2020 and bought three days ago, except c0 and c5,
    who never bought (their seeded sales are moved to 2000). Nobody else holds
    the label.
    """
    _clear_limits(owner)
    label = owner.execute(
        "SELECT label_code FROM campaign WHERE campaign_id = %s", (DRAFT,)
    ).fetchone()[0]
    chosen = [
        str(row[0])
        for row in owner.execute(
            "SELECT customer_id FROM customer ORDER BY customer_id LIMIT 8"
        )
    ]
    assert len(chosen) == 8
    if silent is not None:
        # The log is append-only, so a customer with no consent row is a new one.
        (newcomer,) = owner.execute(
            "INSERT INTO customer (name, registration_channel_id)"
            " VALUES ('No consent yet', 1) RETURNING customer_id"
        ).fetchone()
        chosen[silent] = str(newcomer)
    owner.execute(
        "UPDATE customer_segment_history SET valid_to = now()"
        " WHERE valid_to IS NULL"
        " AND (label_code = %s OR customer_id = ANY(%s::uuid[]))",
        (label, chosen),
    )
    (run_id,) = owner.execute(
        "INSERT INTO segmentation_run (method, window_days, customer_count,"
        " data_origin) VALUES ('RFM_RULES', 365, 8, 'SEEDED') RETURNING run_id"
    ).fetchone()
    for customer in chosen:
        owner.execute(
            "INSERT INTO customer_segment_history (customer_id, run_id, label_code)"
            " VALUES (%s, %s, %s)",
            (customer, run_id, label),
        )
    owner.execute(
        "UPDATE customer SET registered_on = DATE '2020-01-01'"
        " WHERE customer_id = ANY(%s::uuid[])",
        (chosen,),
    )
    owner.execute(
        "UPDATE transaction SET occurred_at = TIMESTAMPTZ '2000-01-01'"
        " WHERE customer_id = ANY(%s::uuid[])",
        (chosen,),
    )
    for position, customer in enumerate(chosen):
        if position != silent:
            _decide(owner, customer, "PERSONALIZATION", "GRANTED")
            _decide(owner, customer, "MARKETING_CONTACT", "GRANTED")
    for position, customer in enumerate(chosen):
        if position not in (0, 5):
            _sale(owner, customer, "a")
    return chosen


def _put(api: Any, resource: str, body: Any, campaign: int = DRAFT, **kw: Any) -> Any:
    return api(
        "PUT",
        f"/v1/campaigns/{campaign}/{resource}",
        json_body=body,
        as_user=kw.pop("as_user", MARKETING_CALLER),
    )


def _get(api: Any, resource: str, campaign: int = DRAFT, **kw: Any) -> Any:
    return api("GET", f"/v1/campaigns/{campaign}/{resource}", **kw)


def test_a_campaign_without_limits_reports_none(
    api: Any, owner: psycopg.Connection
) -> None:
    _clear_limits(owner)
    audience = _get(api, "audience").get_json()
    assert "max_customers" not in audience
    assert _get(api, "budget").get_json() == {"campaign_id": DRAFT}
    assert _get(api, "eligibility-rules").get_json() == {"campaign_id": DRAFT}
    assert _get(api, "exclusions").get_json() == {
        "campaign_id": DRAFT,
        "customer_ids": [],
        "label_codes": [],
    }


def test_the_breakdown_always_lists_eight_reasons_that_add_up(
    api: Any, owner: psycopg.Connection
) -> None:
    _scenario(owner)
    body = _get(api, "audience/breakdown").get_json()
    assert [entry["code"] for entry in body["reasons"]] == REASONS
    assert body["label_holders"] == 8
    assert body["audience_size"] == 8
    assert (
        sum(entry["excluded"] for entry in body["reasons"]) + body["audience_size"]
        == body["label_holders"]
    )
    assert _get(api, "audience").get_json()["audience_size"] == 8


def test_each_customer_is_counted_once_under_the_first_reason(
    api: Any, owner: psycopg.Connection
) -> None:
    c = _scenario(owner)
    # c0 withdrew both purposes and never bought: counted under the first only.
    _decide(owner, c[0], "PERSONALIZATION", "WITHDRAWN")
    _decide(owner, c[0], "MARKETING_CONTACT", "WITHDRAWN")
    _decide(owner, c[1], "MARKETING_CONTACT", "WITHDRAWN")
    owner.execute(
        "UPDATE customer SET registered_on = DATE '2025-06-01' WHERE customer_id = %s",
        (c[4],),
    )
    assert (
        _put(api, "exclusions", {"customer_ids": [c[2]], "label_codes": []}).status_code
        == 200
    )
    assert (
        _put(
            api,
            "eligibility-rules",
            {
                "registered_on_or_before": "2025-01-01",
                "min_purchases": 1,
                "purchase_window_days": 30,
            },
        ).status_code
        == 200
    )
    counts = _counts(_get(api, "audience/breakdown"))
    assert counts == {
        "not_in_customer_reference": 0,
        "personalization_withdrawn": 1,
        "marketing_contact_withdrawn": 1,
        "excluded_customer": 1,
        "excluded_label": 0,
        "registered_after_cutoff": 1,
        "too_few_purchases": 1,
        "over_max_customers": 0,
    }
    assert _get(api, "audience").get_json()["audience_size"] == 3


def test_an_anonymized_customer_leaves_the_audience_and_its_count(
    api: Any, owner: psycopg.Connection
) -> None:
    c = _scenario(owner)
    assert _get(api, "audience").get_json()["audience_size"] == 8
    before = _counts(_get(api, "audience/breakdown"))
    assert before["not_in_customer_reference"] == 0
    # c3 still holds both consents and a purchase, as anonymization keeps them
    # (ADR-0066 section 3): only the identity goes and the reference flags it.
    owner.execute(
        "UPDATE customer SET name = 'Anonymized customer', email = NULL,"
        " phone = NULL, user_id = NULL, anonymized_at = now()"
        " WHERE customer_id = %s",
        (c[3],),
    )
    assert owner.execute(
        "SELECT anonymized FROM api_customers.customer_ref_v1 WHERE customer_id = %s",
        (c[3],),
    ).fetchone() == (True,)
    body = _get(api, "audience/breakdown").get_json()
    counts = _counts(_get(api, "audience/breakdown"))
    assert counts["not_in_customer_reference"] == 1
    assert counts["personalization_withdrawn"] == 0
    assert body["audience_size"] == 7
    assert _get(api, "audience").get_json()["audience_size"] == 7
    assert (
        sum(entry["excluded"] for entry in body["reasons"]) + body["audience_size"]
        == body["label_holders"]
    )
    # A cap counts the remaining customers only: the anonymized one never fills a place.
    assert _put(api, "audience", {"max_customers": 7}).get_json()["audience_size"] == 7
    assert _counts(_get(api, "audience/breakdown"))["over_max_customers"] == 0


def test_a_customer_with_no_consent_row_is_not_granted(
    api: Any, owner: psycopg.Connection
) -> None:
    _scenario(owner, silent=7)
    counts = _counts(_get(api, "audience/breakdown"))
    assert counts["personalization_withdrawn"] == 1
    assert _get(api, "audience").get_json()["audience_size"] == 7


def test_excluding_the_campaign_label_empties_the_audience(
    api: Any, owner: psycopg.Connection
) -> None:
    _scenario(owner)
    label = _get(api, "audience").get_json()["label_code"]
    reply = _put(api, "exclusions", {"customer_ids": [], "label_codes": [label]})
    assert reply.status_code == 200
    body = _get(api, "audience/breakdown").get_json()
    assert body["audience_size"] == 0
    assert _counts(_get(api, "audience/breakdown"))["excluded_label"] == 8


def test_the_cap_removes_the_last_customers_by_id(
    api: Any, owner: psycopg.Connection
) -> None:
    _scenario(owner)
    reply = _put(api, "audience", {"max_customers": 3})
    assert reply.status_code == 200
    assert reply.get_json()["max_customers"] == 3
    assert reply.get_json()["audience_size"] == 3
    assert _counts(_get(api, "audience/breakdown"))["over_max_customers"] == 5
    # An empty body removes the cap.
    reply = _put(api, "audience", {})
    assert "max_customers" not in reply.get_json()
    assert reply.get_json()["audience_size"] == 8


def test_purchases_are_counted_inside_the_window_and_only_when_net(
    api: Any, owner: psycopg.Connection
) -> None:
    c = _scenario(owner)
    # c0 and c5 never bought; the other six bought three days ago.
    rules = {"min_purchases": 1, "purchase_window_days": 30}
    assert _put(api, "eligibility-rules", rules).status_code == 200
    assert _counts(_get(api, "audience/breakdown"))["too_few_purchases"] == 2
    rules = {"min_purchases": 2, "purchase_window_days": 30}
    assert _put(api, "eligibility-rules", rules).status_code == 200
    assert _counts(_get(api, "audience/breakdown"))["too_few_purchases"] == 8
    _sale(owner, c[1], "b")
    assert _counts(_get(api, "audience/breakdown"))["too_few_purchases"] == 7
    rules = {"min_purchases": 1, "purchase_window_days": 1}
    assert _put(api, "eligibility-rules", rules).status_code == 200
    assert _counts(_get(api, "audience/breakdown"))["too_few_purchases"] == 8


def test_budget_round_trips_and_an_empty_body_removes_it(api: Any) -> None:
    reply = _put(api, "budget", {"total_amount": "1500.00", "per_customer_cap": "9.50"})
    assert reply.status_code == 200
    assert reply.get_json() == {
        "campaign_id": DRAFT,
        "total_amount": "1500.00",
        "per_customer_cap": "9.50",
    }
    assert _get(api, "budget").get_json() == reply.get_json()
    only_total = _put(api, "budget", {"total_amount": "20.00"}).get_json()
    assert only_total == {"campaign_id": DRAFT, "total_amount": "20.00"}
    assert _put(api, "budget", {}).get_json() == {"campaign_id": DRAFT}
    assert _get(api, "budget").get_json() == {"campaign_id": DRAFT}


@pytest.mark.parametrize(
    "resource, body",
    [
        ("budget", {"total_amount": "10.00", "per_customer_cap": "10.01"}),
        ("budget", {"total_amount": "10"}),
        ("budget", {"total_amount": "-1.00"}),
        ("eligibility-rules", {"min_purchases": 2}),
        ("eligibility-rules", {"purchase_window_days": 30}),
        ("eligibility-rules", {"min_purchases": 0, "purchase_window_days": 30}),
        ("eligibility-rules", {"min_purchases": 1, "purchase_window_days": 732}),
        ("eligibility-rules", {"registered_on_or_before": "2025-02-30"}),
        ("audience", {"max_customers": 0}),
        ("audience", {"label_code": "LOYAL"}),
        ("audience", {"audience_size": 3}),
        ("exclusions", {"customer_ids": [], "label_codes": ["a", "a"]}),
    ],
)
def test_bad_bodies_are_422_and_change_nothing(
    api: Any, resource: str, body: dict[str, Any]
) -> None:
    before = _get(api, resource).get_json()
    reply = _put(api, resource, body)
    assert reply.status_code == 422, reply.get_json()
    after = _get(api, resource).get_json()
    before.pop("as_of", None)
    after.pop("as_of", None)
    assert after == before


def test_a_cap_above_the_total_is_invalid_body(api: Any) -> None:
    reply = _put(api, "budget", {"total_amount": "10.00", "per_customer_cap": "11.00"})
    assert _code(reply) == (422, "invalid_body")
    equal = _put(api, "budget", {"total_amount": "10.00", "per_customer_cap": "10.00"})
    assert equal.status_code == 200


def test_unknown_customers_and_labels_are_named(
    api: Any, owner: psycopg.Connection
) -> None:
    missing = "00000000-0000-4000-8000-000000000000"
    before = _get(api, "exclusions").get_json()
    reply = _put(api, "exclusions", {"customer_ids": [missing], "label_codes": []})
    assert _code(reply) == (422, "unknown_customer")
    reply = _put(api, "exclusions", {"customer_ids": [], "label_codes": ["NOPE"]})
    assert _code(reply) == (422, "unknown_label")
    assert _get(api, "exclusions").get_json() == before


def test_exclusions_are_replaced_as_a_whole(
    api: Any, owner: psycopg.Connection
) -> None:
    c = _scenario(owner)
    labels = [
        row[0]
        for row in owner.execute("SELECT label_code FROM segment_label ORDER BY 1")
    ]
    first = _put(
        api,
        "exclusions",
        {"customer_ids": [c[1], c[0]], "label_codes": [labels[1], labels[0]]},
    ).get_json()
    assert first["customer_ids"] == sorted([c[0], c[1]])
    assert first["label_codes"] == sorted([labels[0], labels[1]])
    second = _put(
        api, "exclusions", {"customer_ids": [c[2]], "label_codes": []}
    ).get_json()
    assert second["customer_ids"] == [c[2]]
    assert second["label_codes"] == []
    duplicated = _put(
        api, "exclusions", {"customer_ids": [c[2], c[2]], "label_codes": []}
    )
    assert duplicated.status_code == 422


def test_eligibility_round_trips(api: Any) -> None:
    body = {
        "registered_on_or_before": "2024-12-31",
        "min_purchases": 3,
        "purchase_window_days": 90,
    }
    reply = _put(api, "eligibility-rules", body)
    assert reply.get_json() == {"campaign_id": DRAFT, **body}
    assert _get(api, "eligibility-rules").get_json() == reply.get_json()
    date_only = _put(
        api, "eligibility-rules", {"registered_on_or_before": "2024-01-01"}
    )
    assert date_only.get_json() == {
        "campaign_id": DRAFT,
        "registered_on_or_before": "2024-01-01",
    }
    assert _put(api, "eligibility-rules", {}).get_json() == {"campaign_id": DRAFT}


@pytest.mark.parametrize(
    "resource, body",
    [
        ("audience", {"max_customers": 5}),
        ("budget", {"total_amount": "5.00"}),
        ("eligibility-rules", {"min_purchases": 1, "purchase_window_days": 5}),
        ("exclusions", {"customer_ids": [], "label_codes": []}),
    ],
)
def test_only_a_draft_can_be_written(
    api: Any, resource: str, body: dict[str, Any]
) -> None:
    for campaign in (ACTIVE, 3, 4):
        reply = _put(api, resource, body, campaign)
        assert _code(reply) == (409, "campaign_not_draft")
        assert _get(api, resource, campaign).status_code == 200
    assert _code(_put(api, resource, body, 99999)) == (404, "campaign_not_found")


def test_roles(api: Any) -> None:
    for resource, body in (
        ("audience", {"max_customers": 5}),
        ("budget", {"total_amount": "5.00"}),
    ):
        assert _put(api, resource, body, as_user=ANALYST_CALLER).status_code == 403
        assert _put(api, resource, body, as_user=AUDITOR_CALLER).status_code == 403
        assert _put(api, resource, body, as_user=(ADMIN, "ADMIN")).status_code == 200
        for caller in (ANALYST_CALLER, AUDITOR_CALLER):
            assert _get(api, resource, as_user=caller).status_code == 200


def test_the_web_can_still_write_the_campaign_without_losing_the_limits(
    api: Any, database: dict[str, str]
) -> None:
    assert _put(api, "audience", {"max_customers": 7}).status_code == 200
    assert _put(api, "budget", {"total_amount": "50.00"}).status_code == 200
    with psycopg.connect(database["web"], autocommit=True) as web:
        web.execute(
            "UPDATE campaign SET name = 'Renamed by the web' WHERE campaign_id = %s",
            (DRAFT,),
        )
    assert _get(api, "audience").get_json()["max_customers"] == 7
    assert _get(api, "budget").get_json()["total_amount"] == "50.00"


def test_a_web_activation_in_flight_makes_the_put_refuse(
    api: Any, database: dict[str, str]
) -> None:
    with psycopg.connect(database["web"]) as web:
        web.execute(
            "SELECT status FROM campaign WHERE campaign_id = %s FOR UPDATE", (DRAFT,)
        )
        # The service waits for the row; release it once the web has activated.
        import threading

        outcome: dict[str, Any] = {}

        def put() -> None:
            outcome["reply"] = _put(api, "audience", {"max_customers": 1})

        thread = threading.Thread(target=put)
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive()
        web.execute(
            "UPDATE campaign SET status = 'ACTIVE' WHERE campaign_id = %s", (DRAFT,)
        )
        web.commit()
        thread.join(timeout=10)
    assert _code(outcome["reply"]) == (409, "campaign_not_draft")


def test_the_seed_gives_every_new_table_thirty_rows(owner: psycopg.Connection) -> None:
    counts = owner.execute(
        "SELECT (SELECT count(*) FROM campaigns.campaign_audience),"
        " (SELECT count(*) FROM campaigns.campaign_budget),"
        " (SELECT count(*) FROM campaigns.campaign_eligibility),"
        " (SELECT count(*) FROM campaigns.campaign_excluded_customer),"
        " (SELECT count(*) FROM campaigns.campaign_excluded_label)"
    ).fetchone()
    assert all(count >= 30 for count in counts), counts


def test_the_instant_is_reported_in_utc(api: Any) -> None:
    stamp = _get(api, "audience").get_json()["as_of"]
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert abs(datetime.now(UTC) - moment) < timedelta(minutes=5)
