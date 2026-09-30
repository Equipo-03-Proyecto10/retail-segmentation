"""The customer segment timeline on the customer's page (#337), one test or more
per acceptance criterion.

The history and sales reads are replaced with plain rows and the rest of the
page's reads are stubbed, so `build_timeline` and `build_change` run for real
and each test states exactly what history the page was given. The connection's
session time zone is UTC, so an "as of" date resolves predictably.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.consumption import HistoryRow
from web.db.customer_timeline import SaleRow
from web.db.customers import Customer

_ID = "00000000-0000-0000-0000-000000000001"
_URL = f"/catalog/customers/{_ID}"
_JULY = datetime(2026, 7, 1, 3, 0, tzinfo=UTC)
_AUGUST = datetime(2026, 8, 1, 3, 0, tzinfo=UTC)
_SEPTEMBER = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)


@pytest.fixture
def app() -> Flask:
    connection = MagicMock()
    connection.info.timezone = UTC
    application = create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused-by-test",
        ),
        database_connector=Mock(return_value=connection),
    )
    application.config["PROPAGATE_EXCEPTIONS"] = False
    return application


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def _sign_in(client: FlaskClient, role_code: str = "ANALYST") -> None:
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "11111111-1111-1111-1111-000000000001"
        flask_session["role_code"] = role_code
        flask_session["name"] = f"{role_code.title()} user"


def _customer() -> Customer:
    return Customer(
        customer_id=_ID,
        user_id=None,
        name="Ada Lovelace",
        email="ada@example.test",
        phone="5500000001",
        registration_channel_id=2,
        registered_on=date(2026, 1, 15),
    )


def _row(
    run_id: int,
    label: str | None,
    valid_from: datetime,
    valid_to: datetime | None,
    *,
    recency: int = 12,
    frequency: int = 4,
    monetary: str = "1234.50",
    scores: tuple[int, int, int] = (3, 3, 3),
) -> HistoryRow:
    unassigned = label is None
    return HistoryRow(
        run_id=run_id,
        label_code=label,
        label_name=None if unassigned else label.replace("_", " ").title(),
        last_purchase_at=None if unassigned else valid_from - timedelta(days=recency),
        frequency_count=None if unassigned else frequency,
        monetary_total=None if unassigned else Decimal(monetary),
        r_score=None if unassigned else scores[0],
        f_score=None if unassigned else scores[1],
        m_score=None if unassigned else scores[2],
        valid_from=valid_from,
        valid_to=valid_to,
        run_at=valid_from,
        window_days=180,
    )


def _history() -> list[HistoryRow]:
    """Loyal on runs 1 and 2, at risk from run 3: newest first, contiguous."""
    return [
        _row(
            3,
            "AT_RISK",
            _SEPTEMBER,
            None,
            recency=12,
            frequency=2,
            monetary="480.00",
            scores=(2, 2, 3),
        ),
        _row(
            2,
            "LOYAL",
            _AUGUST,
            _SEPTEMBER,
            recency=1,
            frequency=5,
            monetary="1234.50",
            scores=(5, 4, 4),
        ),
        _row(1, "LOYAL", _JULY, _AUGUST),
    ]


@pytest.fixture
def page(monkeypatch: pytest.MonkeyPatch):
    """Stub every read on the customer pages except the timeline's own, and let
    the test say which history rows those return."""
    monkeypatch.setattr("web.routes.catalog.get_customer", lambda _c, _id: _customer())
    monkeypatch.setattr("web.routes.catalog.get_channel", lambda _c, _id: None)
    monkeypatch.setattr(
        "web.routes.catalog.get_current_assignment", lambda _c, _id: None
    )
    monkeypatch.setattr("web.routes.catalog.list_interest_categories", lambda *a: [])
    monkeypatch.setattr("web.routes.catalog.list_preferred_channels", lambda *a: [])

    def use(rows: list[HistoryRow]) -> None:
        monkeypatch.setattr(
            "web.services.customer_timeline.list_history_rows", lambda *_: rows
        )

    use(_history())
    return use


def _text(response) -> str:
    return re.sub(r"\s+", " ", response.get_data(as_text=True))


def _section(html: str, heading_id: str) -> str:
    start = html.index(f'id="{heading_id}"')
    return html[start : html.index("</section>", start)]


# ---------- AC 1: every assignment, with run, validity and R/F/M ----------


def test_the_page_lists_every_assignment_with_its_run_validity_and_rfm(
    client: FlaskClient, page
) -> None:
    _sign_in(client)
    response = client.get(_URL)
    html = _text(response)
    timeline = _section(html, "timeline-title")

    assert response.status_code == 200
    assert "3 assignments, newest first" in timeline
    for run_id in (1, 2, 3):
        assert f'href="/run-history/{run_id}">#{run_id}</a>' in timeline
    assert "Loyal" in timeline and "At Risk" in timeline
    assert "2026-07-01 03:00" in timeline  # valid_from of run 1
    assert "2026-08-01 03:00" in timeline  # valid_to of run 1, valid_from of run 2
    assert "Open" in timeline  # valid_to of the current assignment
    # Scores and raw measures of run 2.
    assert '<td class="mq-table__cell--num">5</td>' in timeline
    assert "1,234.50" in timeline
    assert "480.00" in timeline


def test_an_unassigned_run_is_listed_as_unassigned_not_left_out(
    client: FlaskClient, page
) -> None:
    page(
        [
            _row(2, None, _AUGUST, None),
            _row(1, "LOYAL", _JULY, _AUGUST),
        ]
    )
    _sign_in(client)
    timeline = _section(_text(client.get(_URL)), "timeline-title")

    assert "2 assignments" in timeline
    assert "Unassigned" in timeline


def test_a_customer_never_scored_is_told_so(client: FlaskClient, page) -> None:
    page([])
    _sign_in(client)
    html = _text(client.get(_URL))

    assert "No segment history" in html
    assert 'id="timeline-title"' not in html
    assert 'id="comparison-title"' not in html


# ---------- AC 2: as of a date ----------


def test_as_of_shows_the_assignment_open_at_the_end_of_that_day(
    client: FlaskClient, page
) -> None:
    _sign_in(client)
    response = client.get(f"{_URL}?as_of=2026-08-15")
    as_of = _section(_text(response), "as-of-title")

    assert response.status_code == 200
    assert "On 2026-08-15:" in as_of
    assert "Loyal" in as_of
    assert "run #2" in as_of
    assert "assignment 2026-08-01 to 2026-09-01" in as_of
    assert 'value="2026-08-15"' in as_of


def test_as_of_today_shows_the_open_assignment(client: FlaskClient, page) -> None:
    _sign_in(client)
    as_of = _section(_text(client.get(f"{_URL}?as_of=2026-09-29")), "as-of-title")

    assert "At Risk" in as_of
    assert "run #3" in as_of
    assert "to open" in as_of


def test_as_of_a_date_before_the_first_run_says_nothing_had_scored_the_customer(
    client: FlaskClient, page
) -> None:
    _sign_in(client)
    as_of = _section(_text(client.get(f"{_URL}?as_of=2026-06-01")), "as-of-title")

    assert "On 2026-06-01 no segmentation run had scored this customer." in as_of


@pytest.mark.parametrize("raw", ["yesterday", "2026-02-30", "20260815", "0001-01-01"])
def test_an_as_of_that_is_not_a_date_is_refused_and_the_page_still_shows(
    client: FlaskClient, page, raw: str
) -> None:
    _sign_in(client)
    response = client.get(_URL, query_string={"as_of": raw})
    html = _text(response)

    assert response.status_code == 400
    assert "Enter a date as YYYY-MM-DD" in html
    assert 'id="timeline-title"' in html


def test_without_as_of_the_page_asks_no_question(client: FlaskClient, page) -> None:
    _sign_in(client)
    as_of = _section(_text(client.get(_URL)), "as-of-title")

    assert 'role="status"' not in as_of
    assert 'role="alert"' not in as_of


# ---------- AC 3: recency in days, previous and current side by side ----------


def test_recency_is_shown_in_days(client: FlaskClient, page) -> None:
    _sign_in(client)
    html = _text(client.get(_URL))
    comparison = _section(html, "comparison-title")

    assert "12 days" in comparison
    assert "1 day " in comparison  # singular, the previous run's recency
    assert "1 days" not in html


def test_previous_and_current_rfm_sit_side_by_side(client: FlaskClient, page) -> None:
    _sign_in(client)
    comparison = _section(_text(client.get(_URL)), "comparison-title")

    assert "Previous · run #2" in comparison
    assert "Current · run #3" in comparison
    # Each measure's row holds the previous value and score, then the current.
    frequency = re.search(r"<td>Frequency</td>(.*?)</tr>", comparison).group(1)
    assert frequency.index("5 purchases") < frequency.index("2 purchases")
    assert "-2" in frequency
    monetary = re.search(r"<td>Monetary</td>(.*?)</tr>", comparison).group(1)
    assert monetary.index("1,234.50 MXN") < monetary.index("480.00 MXN")
    recency = re.search(r"<td>Recency</td>(.*?)</tr>", comparison).group(1)
    assert recency.index("1 day") < recency.index("12 days")
    assert "-3" in recency


def test_a_single_assignment_has_no_comparison(client: FlaskClient, page) -> None:
    page([_row(1, "LOYAL", _JULY, None)])
    _sign_in(client)
    html = _text(client.get(_URL))

    assert 'id="comparison-title"' not in html
    assert "First assignment" in _section(html, "timeline-title")


# ---------- AC 4: the sales between two runs ----------


def _sale(transaction_id: int, total: str, occurred_at: datetime) -> SaleRow:
    return SaleRow(
        transaction_id=transaction_id,
        source_transaction_id=f"T-{transaction_id:04d}",
        occurred_at=occurred_at,
        total=Decimal(total),
        store_name="Centro",
        channel_name="Online",
        units=2,
    )


@pytest.fixture
def sales(monkeypatch: pytest.MonkeyPatch):
    def read(_connection, _customer, *, run_id, excluding_run_id):
        if (run_id, excluding_run_id) == (3, 2):
            return [_sale(10, "150.00", datetime(2026, 8, 20, 17, 5, tzinfo=UTC))]
        return [
            _sale(7, "80.50", datetime(2026, 2, 3, 11, 0, tzinfo=UTC)),
            _sale(8, "19.50", datetime(2026, 2, 20, 9, 30, tzinfo=UTC)),
        ]

    monkeypatch.setattr(
        "web.services.customer_timeline.list_sales_in_window_only", read
    )


def test_only_a_label_change_links_to_what_changed(client: FlaskClient, page) -> None:
    _sign_in(client)
    timeline = _section(_text(client.get(_URL)), "timeline-title")

    assert f'href="/catalog/customers/{_ID}/segment-changes/3">What changed' in (
        timeline
    )
    assert "segment-changes/2" not in timeline
    assert "segment-changes/1" not in timeline
    assert "Unchanged" in timeline


def test_opening_a_change_lists_the_sales_that_entered_with_amount_and_date(
    client: FlaskClient, page, sales
) -> None:
    _sign_in(client)
    response = client.get(f"{_URL}/segment-changes/3")
    html = _text(response)
    entered = _section(html, "entered-title")

    assert response.status_code == 200
    assert "Loyal" in html and "At Risk" in html
    assert "Label changed" in html
    assert "run #3's window (2026-03-05 to 2026-09-01)" in entered
    assert "run #2's (2026-02-02 to 2026-08-01)" in entered
    assert "2026-08-20 17:05" in entered
    assert "T-0010" in entered
    assert "150.00" in entered
    assert "1 sale" in entered


def test_opening_a_change_lists_the_sales_that_left_the_window(
    client: FlaskClient, page, sales
) -> None:
    _sign_in(client)
    left = _section(_text(client.get(f"{_URL}/segment-changes/3")), "left-title")

    assert "2026-02-03 11:00" in left and "80.50" in left
    assert "2026-02-20 09:30" in left and "19.50" in left
    assert "2 sales" in left and "100.00" in left


def test_a_change_with_no_sales_either_way_says_so(
    client: FlaskClient, page, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.services.customer_timeline.list_sales_in_window_only",
        lambda *_a, **_k: [],
    )
    _sign_in(client)
    html = _text(client.get(f"{_URL}/segment-changes/3"))

    assert "No accepted sale entered run #3's window" in html
    assert "Every sale run #2 counted was still inside run #3's window." in html


def test_the_first_assignment_has_no_change_to_explain(
    client: FlaskClient, page
) -> None:
    _sign_in(client)
    response = client.get(f"{_URL}/segment-changes/1")

    assert response.status_code == 200
    assert "is the first run that scored this customer" in _text(response)


def test_a_run_that_did_not_assign_the_customer_is_a_404(
    client: FlaskClient, page
) -> None:
    _sign_in(client)
    assert client.get(f"{_URL}/segment-changes/99").status_code == 404


def test_a_change_for_an_unknown_customer_is_a_404(
    client: FlaskClient, page, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.catalog.get_customer", lambda _c, _id: None)
    _sign_in(client)
    assert client.get(f"{_URL}/segment-changes/3").status_code == 404


def test_a_change_is_refused_to_a_role_without_segment_read(
    client: FlaskClient, page
) -> None:
    _sign_in(client, "INVENTORY_PLANNER")
    assert client.get(f"{_URL}/segment-changes/3").status_code == 403


# ---------- AC 5: since is when the label was entered ----------


def test_since_on_an_unchanged_rerun_is_the_date_the_label_was_entered(
    client: FlaskClient, page
) -> None:
    page(
        [
            _row(3, "LOYAL", _SEPTEMBER, None),
            _row(2, "LOYAL", _AUGUST, _SEPTEMBER),
            _row(1, "AT_RISK", _JULY, _AUGUST),
        ]
    )
    _sign_in(client)
    html = _text(client.get(_URL))

    # Run 3 reconfirmed Loyal on 1 September; the customer entered it on 1 August.
    assert "since 2026-08-01" in _section(html, "comparison-title")
    assert "since 2026-09-01" not in html
    current_row = re.search(r'href="/run-history/3">#3</a>(.*?)</tr>', html).group(1)
    assert "since 2026-08-01" in current_row


def test_the_current_label_shows_since_when_it_was_entered(
    client: FlaskClient, page, monkeypatch: pytest.MonkeyPatch
) -> None:
    page(
        [
            _row(2, "LOYAL", _AUGUST, None),
            _row(1, "LOYAL", _JULY, _AUGUST),
        ]
    )
    assignment = MagicMock(label_code="LOYAL", segment_id=None)
    monkeypatch.setattr(
        "web.routes.catalog.get_current_assignment", lambda _c, _id: assignment
    )
    monkeypatch.setattr("web.routes.catalog.get_label_name", lambda _c, _code: "Loyal")
    _sign_in(client)
    html = _text(client.get(_URL))

    current_label = re.search(r"<dt>Current label</dt>(.*?)</dd>", html).group(1)
    assert "since 2026-07-01" in current_label


# ---------- #338: the timeline links each migration to its explanation ----------

_EXPLANATION = f"/migration-explanation/?run_a=2&amp;run_b=3&amp;customer_id={_ID}"


def test_a_label_change_on_the_timeline_links_to_its_explanation(
    client: FlaskClient, page
) -> None:
    _sign_in(client)
    timeline = _section(_text(client.get(_URL)), "timeline-title")

    assert f'href="{_EXPLANATION}">Explanation</a>' in timeline
    # Only the change links to one: unchanged reruns and the first assignment do not.
    assert timeline.count("/migration-explanation/") == 1


def test_the_current_and_previous_panel_links_to_its_explanation_and_summarises_it(
    client: FlaskClient, page
) -> None:
    _sign_in(client)
    comparison = _section(_text(client.get(_URL)), "comparison-title")

    assert f'href="{_EXPLANATION}">Explanation</a>' in comparison
    assert (
        "Recency went from 1 to 12 days, frequency dropped from 5 to 2, "
        "monetary fell from 1,234.50 to 480.00 MXN." in comparison
    )


def test_the_segment_change_page_reads_in_plain_language_and_links_to_the_explanation(
    client: FlaskClient, page, sales
) -> None:
    _sign_in(client)
    explanation = _section(
        _text(client.get(f"{_URL}/segment-changes/3")), "explanation-title"
    )

    assert "Recency went from 1 to 12 days — changed" in explanation
    assert "Frequency dropped from 5 to 2 purchases — changed." in explanation
    assert f'href="{_EXPLANATION}">Migration explanation</a>' in explanation


def test_an_unchanged_score_on_the_timeline_reads_stable_not_plus_zero(
    client: FlaskClient, page
) -> None:
    page(
        [
            _row(2, "AT_RISK", _AUGUST, None, scores=(2, 3, 3)),
            _row(1, "HIBERNATING", _JULY, _AUGUST, scores=(2, 2, 3)),
        ]
    )
    _sign_in(client)
    comparison = _section(_text(client.get(_URL)), "comparison-title")

    assert "+0" not in comparison
    assert '<td class="mq-table__cell--num">stable</td>' in comparison
    # Every value stayed put and the label still moved: the rank moved it.
    assert "other customers moved the quintile cut points" in comparison
