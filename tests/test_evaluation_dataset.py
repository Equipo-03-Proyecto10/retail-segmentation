"""The evaluation dataset is reproducible and holds what it says (F23-01)."""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import Connection

from analysis import dataset as evaluation
from analysis.load import is_local, load
from web.db.segments import read_rfm_inputs, score_rfm_rules
from web.services.kmeans import KMeansParams, fit_customers

SMALL = 600


@pytest.fixture(scope="module")
def small() -> evaluation.Dataset:
    return evaluation.generate(customers=SMALL)


def test_a_seed_always_gives_the_same_rows(small: evaluation.Dataset) -> None:
    again = evaluation.generate(customers=SMALL)
    assert again.fingerprint() == small.fingerprint()
    assert evaluation.generate(seed=7, customers=SMALL).fingerprint() != (
        small.fingerprint()
    )


def test_the_rows_of_the_default_seed_do_not_move(small: evaluation.Dataset) -> None:
    """A study names this fingerprint, so changing the rows must be deliberate."""
    assert small.fingerprint() == (
        "3952c606851849570804f7849070626fea7b97f9dd6769699e89f5e6b06a15e0"
    )


def test_only_the_method_python_guarantees_draws_numbers() -> None:
    """`Random.random()` is the one sequence stable across Python versions."""
    tree = ast.parse(Path(evaluation.__file__).read_text(encoding="utf-8"))
    drawn = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "rng"
    }
    assert drawn == {"random"}


def test_each_group_holds_its_share(small: evaluation.Dataset) -> None:
    sizes = Counter(customer.group for customer in small.customers)
    assert sum(sizes.values()) == SMALL
    for group in evaluation.GROUPS:
        assert sizes[group.code] == pytest.approx(group.share * SMALL, abs=1)


def test_the_shares_add_up_to_everyone() -> None:
    assert sum(group.share for group in evaluation.GROUPS) == pytest.approx(1)


def test_every_customer_bought_at_least_once(small: evaluation.Dataset) -> None:
    assert {sale.customer_id for sale in small.sales} == {
        customer.customer_id for customer in small.customers
    }


def test_every_sale_is_a_legal_row(small: evaluation.Dataset) -> None:
    for sale in small.sales:
        assert sale.amount > 0
        assert sale.amount == sale.amount.quantize(Decimal("0.01"))
        assert 0 <= sale.seconds_ago <= evaluation.HISTORY_DAYS * 86400
        assert 1 <= sale.store <= evaluation.STORES
        assert 1 <= sale.channel <= 5
        assert 1 <= sale.category <= 10


def test_extreme_spenders_really_are_extreme() -> None:
    data = evaluation.generate(customers=2000)
    extreme = {c.customer_id for c in data.customers if c.extreme}
    assert extreme
    top = sorted(data.sales, key=lambda sale: sale.amount, reverse=True)[:20]
    assert all(sale.customer_id in extreme for sale in top)


def test_a_customer_outside_the_window_has_no_row(small: evaluation.Dataset) -> None:
    inside = {row.customer_id for row in evaluation.rfm_rows(small)}
    limit = evaluation.DEFAULT_WINDOW_DAYS * 86400
    recent = {s.customer_id for s in small.sales if s.seconds_ago <= limit}
    assert inside == recent
    assert len(inside) < SMALL


def test_rfm_rows_add_up_to_the_window_s_sales(small: evaluation.Dataset) -> None:
    rows = evaluation.rfm_rows(small)
    limit = evaluation.DEFAULT_WINDOW_DAYS * 86400
    window = [sale for sale in small.sales if sale.seconds_ago <= limit]
    assert sum(row.frequency for row in rows) == len(window)
    assert sum(row.monetary for row in rows) == sum(sale.amount for sale in window)


def test_the_existing_kmeans_runs_on_it(small: evaluation.Dataset) -> None:
    fit = fit_customers(evaluation.rfm_rows(small), KMeansParams(k=6, seed=11))
    assert len(set(fit.assignments)) == 6
    assert fit.silhouette is not None


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("postgresql://postgres@localhost:5432/retail", True),
        ("postgresql://postgres@127.0.0.1/retail", True),
        ("postgresql:///retail", True),
        ("postgresql://postgres@34.51.123.31:5432/retail", False),
        ("postgresql://postgres@mosaiq.maxthecoder.online/retail", False),
    ],
)
def test_only_a_local_database_is_accepted(url: str, local: bool) -> None:
    assert is_local(url) is local


@pytest.fixture
def owner_connection(owner_url: str) -> Iterator[Connection[Any]]:
    connection = psycopg.connect(owner_url)
    yield connection
    connection.rollback()
    connection.close()


@pytest.mark.postgres
def test_it_loads_and_the_web_s_reads_score_it(
    owner_connection: Connection[Any],
) -> None:
    """It loads under every constraint, and the web's RFM reads see it. Nothing is
    committed, so the database the other tests share keeps its rows."""
    data = evaluation.generate(seed=99, customers=120)
    load(owner_connection, data)
    with owner_connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute(
            "SELECT count(*), count(*) FILTER (WHERE data_origin = 'SEEDED') "
            "FROM transaction WHERE source_transaction_id LIKE %s",
            ("EVAL-99-%",),
        )
        total, seeded = cursor.fetchone()
    assert total == seeded == len(data.sales)
    identifiers = {customer.customer_id for customer in data.customers}
    window = evaluation.DEFAULT_WINDOW_DAYS
    inputs = [
        row
        for row in read_rfm_inputs(owner_connection, window)
        if str(row.customer_id) in identifiers
    ]
    assert len(inputs) == len(identifiers)
    scored = [
        row
        for row in score_rfm_rules(owner_connection, window)
        if str(row.customer_id) in identifiers
    ]
    assert len(scored) == len(identifiers)
