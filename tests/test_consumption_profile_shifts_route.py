"""Before and now on the consumption profile page (#341), one test or more per
acceptance criterion.

The profile itself is replaced, as in tests/test_customer_profile_route.py, and
so is the single totals read; `build_profile_shifts` runs for real, so each test
states exactly which purchases each half of the window held. The rules are
tested without a page in tests/test_consumption_shift.py.
"""

from __future__ import annotations

import re
from decimal import Decimal
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask

from tests.test_customer_profile_route import _END, _open, _profile
from web.app import create_app
from web.config import Config
from web.db.consumption import CategoryTotal, GroupTotal

_ADA = "00000000-0000-0000-0000-000000000001"
_URL_BASE = f"/catalog/customers/{_ADA}/profile"


@pytest.fixture
def app() -> Flask:
    application = create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused-by-test",
        ),
        database_connector=Mock(return_value=MagicMock()),
    )
    application.config["PROPAGATE_EXCEPTIONS"] = False
    return application


def _g(item_id: int, name: str, purchases: int) -> GroupTotal:
    return GroupTotal(item_id, name, purchases, Decimal("100.00"))


def _c(category_id: int, name: str, purchases: int) -> CategoryTotal:
    return CategoryTotal(category_id, name, purchases, purchases, Decimal("50.00"))


def _half(channels, stores, categories, lined):
    return ({_ADA: channels}, {_ADA: stores}, {_ADA: categories}, {_ADA: lined})


_SHIFTED = (
    _half(
        [_g(1, "physical_store", 4), _g(2, "web", 2)],
        [_g(10, "San Pedro", 5), _g(11, "Valle Oriente", 1)],
        [_c(100, "Electronics", 3), _c(200, "Home", 2)],
        5,
    ),
    _half(
        [_g(2, "web", 3), _g(1, "physical_store", 1)],
        [_g(11, "Valle Oriente", 3), _g(10, "San Pedro", 1)],
        [_c(200, "Home", 2), _c(100, "Electronics", 1), _c(300, "Garden", 1)],
        4,
    ),
)


def _page(app: Flask, monkeypatch: pytest.MonkeyPatch, halves) -> tuple[int, str]:
    read = Mock(return_value=halves)
    monkeypatch.setattr("web.services.consumption_shift.list_totals_for_periods", read)
    response, _ = _open(app, monkeypatch, _profile())
    return response.status_code, " ".join(response.get_data(as_text=True).split())


def _panel(body: str) -> str:
    start = body.index('id="shifts-title"')
    return body[start : body.index("</section>", start)]


def _row(panel: str, name: str) -> str:
    return re.search(rf"<td>{name}</td>(.*?)</tr>", panel).group(1)


# ---------- AC 1: dominant channel and store, before and now, with a shift ----------


def test_channel_and_store_show_before_and_now_with_shares_and_a_shift(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, body = _page(app, monkeypatch, _SHIFTED)
    panel = _panel(body)

    assert status == 200
    assert "Before · % of purchases" in panel and "Now · % of purchases" in panel
    channel = _row(panel, "Channel")
    assert channel.index("physical_store") < channel.index("web")
    assert "67 %" in channel and "(4 of 6)" in channel
    assert "75 %" in channel and "(3 of 4)" in channel
    assert "Shifted" in channel
    store = _row(panel, "Store")
    assert "San Pedro" in store and "83 %" in store
    assert "Valle Oriente" in store and "75 %" in store
    assert "Shifted" in store


def test_the_periods_are_the_two_halves_of_the_window(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, body = _page(app, monkeypatch, _SHIFTED)
    panel = _panel(body)

    assert "90 days each" in panel
    assert f"to {_END.strftime('%Y-%m-%d')} · 90 days each" in panel
    assert 'href="/consumption-reports/?window_days=90"' in panel


def test_an_unchanged_leader_says_no_shift(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    same = _half([_g(1, "web", 4)], [_g(10, "San Pedro", 4)], [], 0)
    _, body = _page(app, monkeypatch, (same, same))

    assert "No shift" in _row(_panel(body), "Channel")
    assert "Shifted" not in _panel(body)


# ---------- AC 2: top categories with shares, before and now ----------


def test_top_categories_show_their_share_before_and_now(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, body = _page(app, monkeypatch, _SHIFTED)
    categories = _row(_panel(body), "Top categories")

    before, now = categories.split("</td>", 1)
    assert before.index("Electronics") < before.index("Home")
    assert "60 %" in before and "40 %" in before
    assert now.index("Home") < now.index("Electronics") < now.index("Garden")
    assert "50 %" in now and "25 %" in now
    assert "Shifted" in _row(_panel(body), "Category")


def test_the_page_says_category_shares_can_add_up_to_more_than_100(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, body = _page(app, monkeypatch, _SHIFTED)

    assert "several categories can add up to more than 100 %" in _panel(body)


# ---------- AC 3: too few purchases or a tie claims no shift ----------


def test_too_few_purchases_in_a_half_claims_no_shift_and_says_why(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    earlier = _half([_g(1, "physical_store", 2)], [_g(10, "San Pedro", 2)], [], 0)
    later = _half([_g(2, "web", 3)], [_g(11, "Valle Oriente", 3)], [], 0)
    _, body = _page(app, monkeypatch, (earlier, later))
    channel = _row(_panel(body), "Channel")

    assert "No shift claimed" in channel
    assert "Not enough purchases: 2 in the earlier period, 3 needed." in channel
    assert "Shifted" not in _panel(body)
    assert "at least 3 purchases in each period" in _panel(body)


def test_a_one_against_one_tie_claims_no_shift(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    earlier = _half(
        [_g(1, "physical_store", 2), _g(2, "web", 2)], [_g(10, "San Pedro", 4)], [], 0
    )
    later = _half([_g(2, "web", 4)], [_g(10, "San Pedro", 4)], [], 0)
    _, body = _page(app, monkeypatch, (earlier, later))
    channel = _row(_panel(body), "Channel")

    assert "No shift claimed" in channel
    assert "Tied in the earlier period: 2 purchases each for the top two." in channel


def test_a_half_with_no_purchases_is_not_compared(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    later = _half([_g(2, "web", 3)], [_g(11, "Valle Oriente", 3)], [], 0)
    _, body = _page(app, monkeypatch, (({}, {}, {}, {}), later))
    channel = _row(_panel(body), "Channel")

    assert "Not compared" in channel
    assert "No purchases in the earlier period." in channel


def test_a_one_day_window_has_no_before_and_now(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    read = Mock()
    monkeypatch.setattr("web.services.consumption_shift.list_totals_for_periods", read)
    response, _ = _open(
        app, monkeypatch, _profile(window_days=1), url=f"{_URL_BASE}?window=1"
    )

    assert response.status_code == 200
    assert 'id="shifts-title"' not in response.get_data(as_text=True)
    read.assert_not_called()
