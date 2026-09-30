"""#340: the KPIs the retrospective lists beside the dashboard's charts.

The database is mocked. What is checked is that each figure is the aggregate the
issue names, computed from the rows it is given, and that the page prints it.
The SQL itself is checked against the statement text; the PR gives the queries to
compare a live run against.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask

from tests.test_experiment_assignment import _experiment
from tests.test_segmentation_dashboard_route import (
    _dashboard,
    _sign_in,
)
from web.app import create_app
from web.config import Config
from web.db import experiment_report as report_db
from web.db import segmentation_dashboard as db
from web.db.experiment_report import ReportGroup
from web.db.segmentation_dashboard import LabelMeans
from web.services import segmentation_dashboard as service
from web.services.consumption_profile import UnknownCustomer
from web.services.recommendations import Recommendation, RecommendationResult, Status

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
TODAY = date(2026, 10, 20)
ORDINALS = {"CHAMPION": 1, "LOYAL": 2, "LOST": 3}


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


# ---------- average R, F and M ----------


def test_the_means_are_read_per_label_for_labelled_customers_only() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        ("CHAMPION", 2, Decimal("5"), Decimal("4.5"), Decimal("4"), 10, 12, 900)
    ]

    rows = db.list_run_label_means(connection, 31, NOW)

    statement, parameters = _cursor(connection).execute.call_args.args
    assert parameters == (NOW, 31)
    for average in ("avg(r_score)", "avg(f_score)", "avg(m_score)"):
        assert average in statement
    assert "avg(frequency_count)" in statement and "avg(monetary_total)" in statement
    assert "label_code IS NOT NULL" in statement
    assert "GROUP BY label_code" in statement
    assert rows[0].label_code == "CHAMPION" and rows[0].customers == 2


def test_every_label_has_a_row_best_to_worst_even_with_no_customers() -> None:
    rows = service.build_label_means(
        [
            LabelMeans(
                "LOST", 3, None, None, None, Decimal("90"), Decimal("1"), Decimal("5")
            )
        ],
        ORDINALS,
    )

    assert [r.label for r in rows] == ["CHAMPION", "LOYAL", "LOST"]
    assert [r.customers for r in rows] == [0, 0, 3]
    assert rows[0].mean_r is None
    assert rows[2].mean_monetary == Decimal("5")


def test_a_run_without_quintile_scores_is_reported_as_not_scored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """K-means records raw values and no scores (ADR-0018): dashes, not zeros."""
    _wire_kpis(
        monkeypatch,
        means=[LabelMeans("LOST", 3, None, None, None, Decimal("90"), 1, 5)],
    )

    kpis = service.build_kpis(MagicMock(), _dashboard().run, TODAY, NOW)

    assert kpis.scored is False
    assert kpis.label_means[2].mean_r is None


# ---------- active experiments ----------


def _running(monkeypatch, experiments, groups) -> None:
    monkeypatch.setattr(
        report_db,
        "list_report_experiments",
        Mock(return_value=(experiments, len(experiments))),
    )
    monkeypatch.setattr(report_db, "list_report_groups", Mock(return_value=groups))


def test_only_experiments_running_today_with_assignments_are_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import timedelta

    started = _experiment(experiment_id=1, starts_on=TODAY, assignments=10)
    open_ended = _experiment(
        experiment_id=2, starts_on=TODAY - timedelta(days=9), assignments=10
    )
    finished = _experiment(
        experiment_id=3,
        starts_on=TODAY - timedelta(days=30),
        ends_on=TODAY - timedelta(days=1),
        assignments=10,
    )
    future = _experiment(
        experiment_id=4, starts_on=TODAY + timedelta(days=1), assignments=10
    )
    empty = _experiment(experiment_id=5, starts_on=TODAY, assignments=0)
    ends_today = _experiment(
        experiment_id=6,
        starts_on=TODAY - timedelta(days=5),
        ends_on=TODAY,
        assignments=10,
    )
    _running(
        monkeypatch, [started, open_ended, finished, future, empty, ends_today], []
    )

    active = service.build_active_experiments(MagicMock(), TODAY, NOW)

    assert [a.experiment.experiment_id for a in active] == [1, 2, 6]


def test_each_arm_carries_its_intent_to_treat_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _running(
        monkeypatch,
        [_experiment(experiment_id=31, starts_on=TODAY, assignments=2000)],
        [
            ReportGroup(31, 61, "CONTROL", 1000, 0, 100, 0),
            ReportGroup(31, 62, "TREATMENT", 1000, 700, 150, 0),
            ReportGroup(99, 90, "TREATMENT", 10, 5, 5, 0),
        ],
    )

    (active,) = service.build_active_experiments(MagicMock(), TODAY, NOW)

    assert [(g.kind, g.rate) for g in active.arms] == [
        ("CONTROL", 0.10),
        ("TREATMENT", 0.15),
    ], "another experiment's arms are not shown under this one"


def test_a_synthetic_experiment_is_labelled(monkeypatch: pytest.MonkeyPatch) -> None:
    _running(
        monkeypatch,
        [_experiment(starts_on=TODAY, assignments=5, data_origin="SEEDED")],
        [],
    )

    (active,) = service.build_active_experiments(MagicMock(), TODAY, NOW)

    assert active.label == "Synthetic"


# ---------- most recommended products ----------


def _result(
    store_id: int, store: str, *items: tuple[int, str, int]
) -> RecommendationResult:
    return RecommendationResult(
        customer_id="c",
        customer_name="C",
        status=Status.RECOMMENDED,
        message="",
        window_days=180,
        window_start=NOW,
        window_end=NOW,
        store_id=store_id,
        store_name=store,
        recommendations=tuple(
            Recommendation(pid, name, 1, "Dairy", stock, ())
            for pid, name, stock in items
        ),
    )


def test_products_are_ranked_by_how_many_customers_they_are_recommended_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = {
        "a": _result(1, "Centro", (10, "Milk", 8), (11, "Cheese", 3)),
        "b": _result(1, "Centro", (10, "Milk", 8)),
        "c": _result(2, "Norte", (10, "Milk", 20), (11, "Cheese", 1)),
    }
    monkeypatch.setattr(service, "recommend", lambda _c, cid: answers[cid])

    top = service.build_top_recommended(MagicMock(), ["a", "b", "c"], 3)

    assert [(p.product_id, p.customers) for p in top.products] == [(10, 3), (11, 2)]


def test_stock_is_listed_per_store_once_however_many_customers_share_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = {
        "a": _result(1, "Centro", (10, "Milk", 8)),
        "b": _result(1, "Centro", (10, "Milk", 8)),
        "c": _result(2, "Norte", (10, "Milk", 20)),
    }
    monkeypatch.setattr(service, "recommend", lambda _c, cid: answers[cid])

    (milk,) = service.build_top_recommended(MagicMock(), ["a", "b", "c"], 3).products

    assert milk.stock == (("Centro", 8), ("Norte", 20))


def test_a_tie_is_broken_by_product_id_and_the_list_is_cut_at_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "recommend",
        lambda _c, _cid: _result(1, "Centro", *[(p, f"P{p}", 1) for p in (7, 3, 9, 1)]),
    )

    top = service.build_top_recommended(MagicMock(), ["a"], 1, limit=3)

    assert [p.product_id for p in top.products] == [1, 3, 7]


def test_a_customer_the_recommender_cannot_serve_adds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def answer(_c, customer_id):
        if customer_id == "gone":
            raise UnknownCustomer(customer_id)
        return _result(1, "Centro", (10, "Milk", 8))

    monkeypatch.setattr(service, "recommend", answer)

    top = service.build_top_recommended(MagicMock(), ["a", "gone"], 2)

    assert [p.customers for p in top.products] == [1]


def test_a_run_larger_than_the_cap_says_it_stopped_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "recommend", lambda _c, _cid: _result(1, "Centro"))

    top = service.build_top_recommended(MagicMock(), ["a"] * 100, 250)

    assert top.capped is True and (top.considered, top.total) == (100, 250)
    assert service.build_top_recommended(MagicMock(), ["a"], 1).capped is False


def test_the_customer_read_is_bounded_and_counts_all_of_them() -> None:
    connection = MagicMock()
    cursor = _cursor(connection)
    cursor.fetchall.return_value = [("c1",), ("c2",)]
    cursor.fetchone.return_value = (250,)

    ids, total = db.list_run_labelled_customers(connection, 31, 100)

    first, second = (call.args for call in cursor.execute.call_args_list)
    assert first[1] == (31, 100) and "LIMIT %s" in first[0]
    assert second[1] == (31,)
    assert (ids, total) == (["c1", "c2"], 250)


# ---------- the page ----------


def _wire_kpis(monkeypatch, means=None, ids=None, total=0) -> None:
    monkeypatch.setattr(service, "get_label_ordinals", lambda _c: ORDINALS)
    monkeypatch.setattr(service, "list_run_label_means", lambda *_a: means or [])
    monkeypatch.setattr(
        service, "list_run_labelled_customers", lambda *_a: (ids or [], total)
    )
    monkeypatch.setattr(
        report_db, "list_report_experiments", Mock(return_value=([], 0))
    )
    monkeypatch.setattr(report_db, "list_report_groups", Mock(return_value=[]))


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


def _page(app: Flask, monkeypatch: pytest.MonkeyPatch, kpis: service.Kpis) -> str:
    monkeypatch.setattr(
        "web.routes.segmentation_dashboard.build_dashboard",
        Mock(return_value=_dashboard()),
    )
    monkeypatch.setattr(
        "web.routes.segmentation_dashboard.build_kpis", Mock(return_value=kpis)
    )
    monkeypatch.setattr(
        "web.routes.segmentation_dashboard.list_runs",
        Mock(return_value=([_dashboard().run], 1)),
    )
    monkeypatch.setattr(
        "web.routes.segmentation_dashboard.get_run", lambda _c, _i: _dashboard().run
    )
    client = app.test_client()
    _sign_in(client)
    return " ".join(
        client.get("/segmentation-dashboard/").get_data(as_text=True).split()
    )


def test_the_page_prints_the_means_the_experiments_and_the_products(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = service.ActiveExperiment(
        _experiment(experiment_id=31, name="Win-back offer", data_origin="SEEDED"),
        (service.ArmRate(62, "TREATMENT", 1000, 150),),
    )
    kpis = service.Kpis(
        label_means=(
            service.MeansRow(
                "CHAMPION",
                2,
                Decimal("4.5"),
                Decimal("4.0"),
                Decimal("3.5"),
                Decimal("12.5"),
                Decimal("7.0"),
                Decimal("910.25"),
            ),
            service.MeansRow("LOST", 0),
        ),
        active_experiments=(active,),
        recommended=service.TopRecommended(
            (
                service.RecommendedProduct(
                    10, "Milk", "Dairy", 7, (("Centro", 8), ("Norte", 20))
                ),
            ),
            100,
            250,
        ),
        scored=True,
    )

    body = _page(app, monkeypatch, kpis)

    assert "Average R, F and M by label" in body
    assert "4.50" in body and "910.25" in body and "12.5" in body
    assert "Active experiments" in body and "1 running today" in body
    assert "15.00%" in body and "Synthetic" in body
    assert "Most recommended products" in body and "Milk" in body
    assert 'Centro: <span class="mq-num">8</span>' in body
    assert 'Norte: <span class="mq-num">20</span>' in body
    assert "first 100 of 250 labelled customers" in body


def test_a_product_stocked_at_many_stores_lists_five_and_totals_the_rest(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = tuple((f"Store {n:02d}", n) for n in range(1, 13))
    kpis = service.Kpis(
        label_means=(service.MeansRow("CHAMPION", 1),),
        active_experiments=(),
        recommended=service.TopRecommended(
            (service.RecommendedProduct(10, "Milk", "Dairy", 12, stores),), 12, 12
        ),
        scored=True,
    )

    body = _page(app, monkeypatch, kpis)

    assert "12 stores," in body
    assert '<span class="mq-num">78</span> units in stock' in body, "1 + 2 + ... + 12"
    assert "Store 05" in body and "Store 06" not in body
    assert "and 7 more" in body


def test_an_empty_run_says_so_instead_of_showing_empty_tables(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    kpis = service.Kpis(
        label_means=(service.MeansRow("CHAMPION", 0),),
        active_experiments=(),
        recommended=service.TopRecommended((), 0, 0),
        scored=False,
    )

    body = _page(app, monkeypatch, kpis)

    assert "No experiment with assigned customers is running today." in body
    assert "No customer of this run has a recommendation" in body
    assert "records no quintile scores" in body
