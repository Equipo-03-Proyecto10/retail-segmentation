"""The promotions a sale may rely on today (#1104, RN-71, F25-05).

`api_campaigns.active_promotion_v1` is documented in
`contracts/db/api_campaigns.active_promotion.v1.md` and created, with the three
promotion tables behind it, in the campaigns boundary section of
`sql/01_schema.sql`. The static checks read the files; the `postgres` checks run
against a database built by the three scripts, and every row they write is rolled
back.
"""

from __future__ import annotations

import csv
import re
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "sql/01_schema.sql").read_text()
SECTION = SCHEMA.split("-- BEGIN BOUNDARY campaigns\n", 1)[1].split(
    "-- END BOUNDARY campaigns", 1
)[0]
CONTRACT = (ROOT / "contracts/db/api_campaigns.active_promotion.v1.md").read_text()
GRANTS = {
    (r["role"], r["object"], r["privilege"])
    for r in csv.DictReader((ROOT / "contracts/db/grants.csv").open())
}
COLUMNS = [
    "promotion_id",
    "campaign_id",
    "store_id",
    "channel_id",
    "discount_percent",
    "starts_on",
    "ends_on",
]
TABLES = (
    "campaigns.promotion",
    "campaigns.promotion_store",
    "campaigns.promotion_channel",
)


def test_the_contract_documents_exactly_the_views_columns() -> None:
    table = CONTRACT.split("## Columns", 1)[1].split("## Compatibility", 1)[0]
    assert re.findall(r"^\| `(\w+)` \|", table, re.M) == COLUMNS


def test_the_view_is_created_idempotently_and_closed_to_public() -> None:
    assert (
        "CREATE OR REPLACE VIEW api_campaigns.active_promotion_v1\n"
        "WITH (security_invoker = off)" in SECTION
    )
    assert "REVOKE ALL ON api_campaigns.active_promotion_v1 FROM PUBLIC;" in SECTION
    for table in TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in SECTION


def test_the_manifest_grants_the_view_to_sales_and_no_table() -> None:
    assert ("svc_sales", "api_campaigns", "USAGE") in GRANTS
    assert ("svc_sales", "api_campaigns.active_promotion_v1", "SELECT") in GRANTS
    assert not {g for g in GRANTS if g[0] == "svc_sales" and g[1] in TABLES}


@pytest.fixture
def database(owner_url: str):
    with psycopg.connect(owner_url) as connection:
        yield connection
        connection.rollback()


def _campaign(cur, campaign_id: int, status: str, starts: date, ends: date) -> None:
    cur.execute(
        "INSERT INTO public.campaign"
        " (campaign_id, name, label_code, starts_on, ends_on, status)"
        " VALUES (%s, %s, 'CHAMPION', %s, %s, %s)",
        (campaign_id, f"Promotion test {campaign_id}", starts, ends, status),
    )


def _promotion(
    cur,
    campaign_id: int,
    starts: date,
    ends: date,
    stores: list[int],
    channels: list[int],
) -> int:
    cur.execute(
        "INSERT INTO campaigns.promotion"
        " (campaign_id, name, discount_percent, starts_on, ends_on)"
        " VALUES (%s, 'Test promotion', 15.00, %s, %s) RETURNING promotion_id",
        (campaign_id, starts, ends),
    )
    promotion_id = cur.fetchone()[0]
    for store_id in stores:
        cur.execute(
            "INSERT INTO campaigns.promotion_store (promotion_id, store_id)"
            " VALUES (%s, %s)",
            (promotion_id, store_id),
        )
    for channel_id in channels:
        cur.execute(
            "INSERT INTO campaigns.promotion_channel (promotion_id, channel_id)"
            " VALUES (%s, %s)",
            (promotion_id, channel_id),
        )
    return promotion_id


def _rows(cur, promotion_id: int) -> list[tuple]:
    cur.execute(
        "SELECT store_id, channel_id, discount_percent::text"
        " FROM api_campaigns.active_promotion_v1 WHERE promotion_id = %s"
        " ORDER BY store_id, channel_id",
        (promotion_id,),
    )
    return cur.fetchall()


@pytest.mark.postgres
def test_the_view_has_exactly_the_contracts_columns(database) -> None:
    rows = database.execute(
        "SELECT a.attname FROM pg_attribute a"
        " WHERE a.attrelid = to_regclass('api_campaigns.active_promotion_v1')"
        " AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum"
    ).fetchall()
    assert [r[0] for r in rows] == COLUMNS


@pytest.mark.postgres
def test_each_promotion_table_holds_its_seed(database) -> None:
    for table in TABLES:
        assert database.execute(f"SELECT count(*) FROM {table}").fetchone()[0] >= 30


@pytest.mark.postgres
def test_an_active_promotion_is_one_row_per_store_and_channel(database) -> None:
    today = database.execute("SELECT CURRENT_DATE").fetchone()[0]
    start, end = today - timedelta(days=3), today + timedelta(days=3)
    with database.cursor() as cur:
        _campaign(cur, 9001, "ACTIVE", start, end)
        promotion = _promotion(cur, 9001, start, end, [1, 2], [1, 2])
        assert _rows(cur, promotion) == [
            (1, 1, "15.00"),
            (1, 2, "15.00"),
            (2, 1, "15.00"),
            (2, 2, "15.00"),
        ]


@pytest.mark.postgres
@pytest.mark.parametrize("status", ["DRAFT", "FINISHED", "CANCELLED"])
def test_a_campaign_that_is_not_active_publishes_nothing(database, status) -> None:
    today = database.execute("SELECT CURRENT_DATE").fetchone()[0]
    start, end = today - timedelta(days=3), today + timedelta(days=3)
    with database.cursor() as cur:
        _campaign(cur, 9002, status, start, end)
        assert _rows(cur, _promotion(cur, 9002, start, end, [1], [1])) == []


@pytest.mark.postgres
def test_a_promotion_outside_its_dates_publishes_nothing(database) -> None:
    today = database.execute("SELECT CURRENT_DATE").fetchone()[0]
    with database.cursor() as cur:
        _campaign(
            cur, 9003, "ACTIVE", today - timedelta(days=10), today + timedelta(days=10)
        )
        ended = _promotion(
            cur, 9003, today - timedelta(days=9), today - timedelta(days=1), [1], [1]
        )
        later = _promotion(
            cur, 9003, today + timedelta(days=1), today + timedelta(days=9), [1], [1]
        )
        edges = _promotion(cur, 9003, today, today, [1], [1])
        assert _rows(cur, ended) == []
        assert _rows(cur, later) == []
        assert _rows(cur, edges) == [(1, 1, "15.00")]


@pytest.mark.postgres
def test_the_tables_refuse_a_bad_percentage_or_validity(database) -> None:
    today = database.execute("SELECT CURRENT_DATE").fetchone()[0]
    with database.cursor() as cur:
        _campaign(cur, 9004, "DRAFT", today, today + timedelta(days=5))
        for percent, starts, ends, constraint in (
            ("0.00", today, today, "promotion_discount_percent_check"),
            ("100.01", today, today, "promotion_discount_percent_check"),
            ("10.00", today + timedelta(days=1), today, "promotion_validity_check"),
        ):
            cur.execute("SAVEPOINT bad")
            with pytest.raises(psycopg.errors.CheckViolation) as refused:
                cur.execute(
                    "INSERT INTO campaigns.promotion"
                    " (campaign_id, name, discount_percent, starts_on, ends_on)"
                    " VALUES (9004, 'Bad', %s, %s, %s)",
                    (percent, starts, ends),
                )
            assert refused.value.diag.constraint_name == constraint
            cur.execute("ROLLBACK TO SAVEPOINT bad")


@pytest.mark.postgres
def test_sales_reads_the_view_and_none_of_the_tables(database) -> None:
    with database.cursor() as cur:
        cur.execute("SET LOCAL ROLE svc_sales")
        cur.execute("SELECT count(*) FROM api_campaigns.active_promotion_v1")
        for table in TABLES:
            cur.execute("SAVEPOINT denied")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(f"SELECT 1 FROM {table}")
            cur.execute("ROLLBACK TO SAVEPOINT denied")
