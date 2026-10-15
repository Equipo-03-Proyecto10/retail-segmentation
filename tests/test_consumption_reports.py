"""Filtered consumption-shift and recommendation reports (F12-03).

Two independent reports share one filter bar: store, channel, category and a
period (as_of + window_days). Each is a pure function of plain rows here, and
`build_shift_report`/`build_recommendation_report` are exercised with the
reads replaced by fakes.

The shift report reuses F8-05's `detect_shifts` unchanged; the recommendation
report reuses F10-01's `recommend` unchanged, once per customer, flattened one
row per recommended product. Neither recomputes anything the two already do.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock

import pytest

from web.db.customers import Customer
from web.services.consumption_reports import (
    build_recommendation_report,
    build_shift_report,
    filter_recommendation_rows,
    filter_shift_rows,
)
from web.services.consumption_shift import (
    CustomerShift,
    DimensionShift,
    Leader,
    Period,
    ShiftReport,
)
from web.services.recommendations import (
    Reason,
    Recommendation,
    RecommendationResult,
    Signal,
    Status,
)

_NOW = datetime(2026, 9, 28, tzinfo=UTC)
_EARLIER = Period(datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC))
_LATER = Period(datetime(2026, 9, 1, tzinfo=UTC), _NOW)


def _leader(item_id: int, name: str = "X") -> Leader:
    return Leader(item_id, name)


def _shift(customer_id: str, **overrides) -> CustomerShift:
    return CustomerShift(customer_id=customer_id, **overrides)


def _dim(before_id: int, after_id: int) -> DimensionShift:
    return DimensionShift(_leader(before_id, "Before"), _leader(after_id, "After"))


# ---------- shift rows: filtering ----------


def test_no_filters_keeps_every_shift() -> None:
    shifts = [_shift("a", store=_dim(1, 2)), _shift("b", channel=_dim(3, 4))]

    assert filter_shift_rows(shifts) == tuple(shifts)


def test_a_store_filter_keeps_a_shift_whose_store_touches_it_either_side() -> None:
    shifts = [
        _shift("a", store=_dim(1, 2)),  # store 2 is the "after"
        _shift("b", store=_dim(3, 2)),  # store 2 is the "before"
        _shift("c", store=_dim(3, 4)),  # neither
    ]

    kept = filter_shift_rows(shifts, store_id=2)

    assert {s.customer_id for s in kept} == {"a", "b"}


def test_a_shift_with_no_store_dimension_never_matches_a_store_filter() -> None:
    shifts = [_shift("a", channel=_dim(1, 2))]

    assert filter_shift_rows(shifts, store_id=1) == ()


def test_a_channel_filter_and_a_category_filter_work_the_same_way() -> None:
    shifts = [_shift("a", channel=_dim(5, 6)), _shift("b", category=_dim(7, 8))]

    assert {s.customer_id for s in filter_shift_rows(shifts, channel_id=6)} == {"a"}
    assert {s.customer_id for s in filter_shift_rows(shifts, category_id=7)} == {"b"}


def test_every_applied_filter_must_match_the_same_shift() -> None:
    matches_store_only = _shift("a", store=_dim(1, 2), channel=_dim(9, 9))
    matches_both = _shift("b", store=_dim(1, 2), channel=_dim(5, 6))

    kept = filter_shift_rows(
        [matches_store_only, matches_both], store_id=2, channel_id=6
    )

    assert {s.customer_id for s in kept} == {"b"}


def test_a_filter_combination_that_matches_nothing_is_an_empty_tuple() -> None:
    assert filter_shift_rows([_shift("a", store=_dim(1, 2))], store_id=999) == ()


# ---------- the shift report: orchestration ----------


def _wire_shifts(monkeypatch: pytest.MonkeyPatch, *, shifts=None, names=None) -> Mock:
    manager = Mock()
    manager.detect_shifts = Mock(
        return_value=ShiftReport(
            earlier=_EARLIER,
            later=_LATER,
            shifts=tuple(
                shifts if shifts is not None else [_shift("a", store=_dim(1, 2))]
            ),
            absences=(),
            compared=1,
            unchanged=0,
        )
    )
    manager.list_customer_names = Mock(return_value=names or {"a": "Ada Lovelace"})
    import web.services.consumption_reports as service

    monkeypatch.setattr(service, "detect_shifts", manager.detect_shifts)
    monkeypatch.setattr(service, "list_customer_names", manager.list_customer_names)
    return manager


def test_the_shift_report_derives_two_consecutive_periods_from_as_of_and_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire_shifts(monkeypatch)

    build_shift_report(MagicMock(), as_of=_NOW, window_days=90)

    earlier, later = manager.detect_shifts.call_args.args[1:]
    assert later.end == _NOW
    assert (later.end - later.start).days == 90
    assert earlier.end == later.start


def test_the_shift_report_carries_the_two_periods_it_compared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_shifts(monkeypatch)

    report = build_shift_report(MagicMock(), as_of=_NOW, window_days=90)

    assert report.earlier == _EARLIER and report.later == _LATER


def test_shift_rows_carry_the_customers_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_shifts(monkeypatch, names={"a": "Ada Lovelace"})

    report = build_shift_report(MagicMock(), as_of=_NOW, window_days=90)

    assert report.rows[0].customer_name == "Ada Lovelace"


def test_names_are_looked_up_only_for_the_rows_a_filter_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire_shifts(
        monkeypatch,
        shifts=[_shift("a", store=_dim(1, 2)), _shift("b", channel=_dim(3, 4))],
    )

    build_shift_report(MagicMock(), as_of=_NOW, window_days=90, store_id=2)

    (ids,) = manager.list_customer_names.call_args.args[1:]
    assert set(ids) == {"a"}


# ---------- recommendation rows: filtering ----------


def _reason() -> Reason:
    return Reason(Signal.SEGMENT, "2 other Loyal customers bought it")


def _recommendation(
    product_id: int, category_id: int = 3, stock: int = 5
) -> Recommendation:
    return Recommendation(
        product_id, f"Product {product_id}", category_id, "Cat", stock, (_reason(),)
    )


def _result(**overrides) -> RecommendationResult:
    defaults = dict(
        customer_id="a",
        customer_name="Ada Lovelace",
        status=Status.RECOMMENDED,
        message="ok",
        window_days=180,
        window_start=_EARLIER.start,
        window_end=_NOW,
        store_id=8,
        store_name="Store 8",
        channel_id=4,
        channel_name="Marketplace",
        recommendations=(_recommendation(1),),
    )
    return RecommendationResult(**{**defaults, **overrides})


def test_no_filters_keeps_every_recommended_product() -> None:
    result = _result(recommendations=(_recommendation(1), _recommendation(2)))

    rows = filter_recommendation_rows([result])

    assert {row.product_id for row in rows} == {1, 2}


def test_a_customer_not_recommended_to_contributes_no_rows() -> None:
    for status in (Status.NO_SEGMENT, Status.NO_USUAL_STORE, Status.NONE_MATCH):
        result = _result(status=status, recommendations=())
        assert filter_recommendation_rows([result]) == ()


def test_the_status_guard_holds_even_if_a_non_recommended_result_carried_rows() -> None:
    """F10-01 never actually populates `recommendations` for a status other
    than RECOMMENDED, but the guard checks the status itself rather than
    trusting that invariant to hold everywhere it might be constructed."""
    malformed = _result(status=Status.NO_SEGMENT, recommendations=(_recommendation(1),))

    assert filter_recommendation_rows([malformed]) == ()


def test_a_store_filter_keeps_only_that_stores_rows() -> None:
    results = [_result(store_id=8), _result(customer_id="b", store_id=9)]

    rows = filter_recommendation_rows(results, store_id=8)

    assert {row.customer_id for row in rows} == {"a"}


def test_a_channel_filter_keeps_only_that_channels_rows() -> None:
    results = [_result(channel_id=4), _result(customer_id="b", channel_id=5)]

    rows = filter_recommendation_rows(results, channel_id=5)

    assert {row.customer_id for row in rows} == {"b"}


def test_a_category_filter_applies_to_the_product_not_the_whole_customer() -> None:
    result = _result(
        recommendations=(
            _recommendation(1, category_id=3),
            _recommendation(2, category_id=9),
        )
    )

    rows = filter_recommendation_rows([result], category_id=9)

    assert [row.product_id for row in rows] == [2]


def test_filters_combine_across_store_channel_and_category() -> None:
    matches = _result(
        customer_id="a",
        store_id=8,
        channel_id=4,
        recommendations=(_recommendation(1, category_id=3),),
    )
    wrong_store = _result(
        customer_id="b",
        store_id=9,
        channel_id=4,
        recommendations=(_recommendation(2, category_id=3),),
    )

    rows = filter_recommendation_rows(
        [matches, wrong_store], store_id=8, channel_id=4, category_id=3
    )

    assert [row.customer_id for row in rows] == ["a"]


def test_every_row_carries_its_reason_and_its_stock() -> None:
    result = _result(recommendations=(_recommendation(1, stock=17),))

    (row,) = filter_recommendation_rows([result])

    assert row.in_stock == 17
    assert row.reasons == (_reason(),)


# ---------- the recommendation report: orchestration ----------


def _wire_recommend(
    monkeypatch: pytest.MonkeyPatch, *, customers=None, results=None
) -> Mock:
    manager = Mock()
    manager.list_customers = Mock(
        return_value=(
            (
                customers
                if customers is not None
                else [Customer("a", None, "Ada Lovelace", None, None, 1, None)]
            ),
            1,
        )
    )
    by_id = {r.customer_id: r for r in (results or [_result()])}
    manager.recommend = Mock(side_effect=lambda _c, cid, **kw: by_id[cid])
    import web.services.consumption_reports as service

    monkeypatch.setattr(service, "list_customers", manager.list_customers)
    monkeypatch.setattr(service, "recommend", manager.recommend)
    return manager


def test_the_recommendation_report_asks_every_customer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customers = [
        Customer("a", None, "Ada", None, None, 1, None),
        Customer("b", None, "Bob", None, None, 1, None),
    ]
    manager = _wire_recommend(
        monkeypatch,
        customers=customers,
        results=[_result(customer_id="a"), _result(customer_id="b")],
    )

    build_recommendation_report(MagicMock(), as_of=_NOW, window_days=180)

    assert {call.args[1] for call in manager.recommend.call_args_list} == {"a", "b"}


def test_the_recommendation_report_consumes_customers_after_the_first_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customers = [
        Customer(str(n), None, f"C{n}", None, None, 1, None) for n in range(501)
    ]
    results = [
        _result(customer_id=str(n), recommendations=(_recommendation(n),))
        for n in range(501)
    ]
    manager = _wire_recommend(monkeypatch, customers=customers, results=results)
    manager.list_customers.side_effect = [
        (customers[:500], 501),
        (customers[500:], 501),
    ]

    report = build_recommendation_report(MagicMock(), as_of=_NOW, window_days=180)

    assert report.total == 501
    assert len(manager.recommend.call_args_list) == 501
    assert [call.kwargs["page"] for call in manager.list_customers.call_args_list] == [
        1,
        2,
    ]


def test_the_shift_report_pages_after_filtering_and_names_only_that_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shifts = [_shift(str(n), store=_dim(1, 2)) for n in range(30)]
    names = {str(n): f"Customer {n}" for n in range(30)}
    manager = _wire_shifts(monkeypatch, shifts=shifts, names=names)

    report = build_shift_report(
        MagicMock(), as_of=_NOW, window_days=90, page=2, page_size=25
    )

    assert report.total == 30
    assert report.page == 2
    assert report.page_count == 2
    assert [row.customer_id for row in report.rows] == [str(n) for n in range(25, 30)]
    (looked_up,) = manager.list_customer_names.call_args.args[1:]
    assert looked_up == [str(n) for n in range(25, 30)]


def test_the_recommendation_report_passes_the_window_to_each_customer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire_recommend(monkeypatch)

    build_recommendation_report(MagicMock(), as_of=_NOW, window_days=45)

    assert manager.recommend.call_args.kwargs["window_days"] == 45
    assert manager.recommend.call_args.kwargs["as_of"] == _NOW


def test_the_recommendation_report_is_paged(monkeypatch: pytest.MonkeyPatch) -> None:
    customers = [
        Customer(str(n), None, f"C{n}", None, None, 1, None) for n in range(30)
    ]
    results = [
        _result(customer_id=str(n), recommendations=(_recommendation(n),))
        for n in range(30)
    ]
    _wire_recommend(monkeypatch, customers=customers, results=results)

    report = build_recommendation_report(
        MagicMock(), as_of=_NOW, window_days=180, page=1
    )

    assert report.total == 30
    assert len(report.rows) == report.page_size
    assert report.page_count == -(-30 // report.page_size)


# ---------- method independence (ADR-0018) ----------


def test_neither_report_reads_or_is_told_a_method() -> None:
    for builder in (build_shift_report, build_recommendation_report):
        names = " ".join(inspect.signature(builder).parameters)
        assert "method" not in names and "cluster" not in names
