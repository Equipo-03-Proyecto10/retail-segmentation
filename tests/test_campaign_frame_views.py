"""The campaign views experimentation reads to take a frame (#1051, ADR-0068 section 1).

`api_campaigns.campaign_target_v1`, `campaign_excluded_customer_v1` and
`campaign_excluded_label_v1` are documented in `contracts/db/` and created in the
campaigns boundary section of `sql/01_schema.sql`. The static checks read the files; the
`postgres` checks run against a database built by the three scripts.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "sql/01_schema.sql").read_text()
SECTION = SCHEMA.split("-- BEGIN BOUNDARY campaigns\n", 1)[1].split(
    "-- END BOUNDARY campaigns", 1
)[0]
GRANTS = list(csv.DictReader((ROOT / "contracts/db/grants.csv").open()))

VIEWS = {
    "campaign_target_v1": (
        "campaign_target",
        [
            "campaign_id",
            "label_code",
            "status",
            "registered_on_or_before",
            "min_purchases",
            "purchase_window_days",
        ],
    ),
    "campaign_excluded_customer_v1": (
        "campaign_excluded_customer",
        ["campaign_id", "customer_id"],
    ),
    "campaign_excluded_label_v1": (
        "campaign_excluded_label",
        ["campaign_id", "label_code"],
    ),
}


def _contract_columns(document: str) -> list[str]:
    table = document.split("## Columns", 1)[1].split("## Compatibility", 1)[0]
    return re.findall(r"^\| `(\w+)` \|", table, re.M)


@pytest.mark.parametrize("view", VIEWS)
def test_each_view_is_documented_with_exactly_its_columns(view: str) -> None:
    name, columns = VIEWS[view]
    document = (ROOT / f"contracts/db/api_campaigns.{name}.v1.md").read_text()
    assert f"| Object | `api_campaigns.{view}` |" in document
    assert "| Producer | campaigns-service |" in document
    assert "| Consumer | experimentation |" in document
    assert _contract_columns(document) == columns


@pytest.mark.parametrize("view", VIEWS)
def test_each_view_is_created_idempotently_and_closed_to_public(view: str) -> None:
    assert (
        f"CREATE OR REPLACE VIEW api_campaigns.{view}\nWITH (security_invoker = off)"
        in SECTION
    )
    assert f"REVOKE ALL ON api_campaigns.{view} FROM PUBLIC;" in SECTION
    assert "CREATE SCHEMA IF NOT EXISTS api_campaigns;" in SECTION


def test_the_manifest_lists_the_experimentation_grants() -> None:
    rows = {(r["role"], r["object"], r["privilege"]) for r in GRANTS}
    wanted = {("svc_experiments", "api_campaigns", "USAGE")} | {
        ("svc_experiments", f"api_campaigns.{view}", "SELECT") for view in VIEWS
    }
    assert wanted <= rows


@pytest.fixture(scope="module")
def database(owner_url: str):
    with psycopg.connect(owner_url, autocommit=True) as connection:
        yield connection


def _read_as(connection, role: str, sql: str):
    with connection.cursor() as cur:
        cur.execute(f"SET ROLE {role}")
        try:
            cur.execute(sql)
            return cur.fetchall()
        finally:
            cur.execute("RESET ROLE")


@pytest.mark.postgres
@pytest.mark.parametrize("view", VIEWS)
def test_the_view_has_exactly_the_contracts_columns(database, view: str) -> None:
    rows = database.execute(
        "SELECT a.attname FROM pg_attribute a "
        "WHERE a.attrelid = to_regclass(%s) AND a.attnum > 0 "
        "AND NOT a.attisdropped ORDER BY a.attnum",
        (f"api_campaigns.{view}",),
    ).fetchall()
    assert [r[0] for r in rows] == VIEWS[view][1]


@pytest.mark.postgres
def test_every_seeded_campaign_has_its_target_and_its_rules(database) -> None:
    campaigns = database.execute("SELECT count(*) FROM public.campaign").fetchone()[0]
    assert campaigns >= 30
    target = database.execute(
        "SELECT count(*), count(DISTINCT campaign_id) "
        "FROM api_campaigns.campaign_target_v1"
    ).fetchone()
    assert target == (campaigns, campaigns)
    # The view says what campaign and campaign_eligibility say.
    assert (
        database.execute(
            "SELECT count(*) FROM api_campaigns.campaign_target_v1 v "
            "JOIN public.campaign c USING (campaign_id) "
            "LEFT JOIN campaigns.campaign_eligibility e USING (campaign_id) "
            "WHERE v.label_code = c.label_code AND v.status = c.status "
            "AND v.registered_on_or_before "
            "IS NOT DISTINCT FROM e.registered_on_or_before "
            "AND v.min_purchases IS NOT DISTINCT FROM e.min_purchases "
            "AND v.purchase_window_days IS NOT DISTINCT FROM e.purchase_window_days"
        ).fetchone()[0]
        == campaigns
    )
    with_rules = database.execute(
        "SELECT count(*) FROM campaigns.campaign_eligibility"
    ).fetchone()[0]
    assert with_rules >= 30
    assert (
        database.execute(
            "SELECT count(*) FROM api_campaigns.campaign_target_v1 "
            "WHERE registered_on_or_before IS NOT NULL OR min_purchases IS NOT NULL"
        ).fetchone()[0]
        == with_rules
    )


@pytest.mark.postgres
def test_every_seeded_exclusion_has_its_row(database) -> None:
    for view, table in (
        ("campaign_excluded_customer_v1", "campaigns.campaign_excluded_customer"),
        ("campaign_excluded_label_v1", "campaigns.campaign_excluded_label"),
    ):
        seeded = database.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        assert seeded >= 30
        published = database.execute(
            f"SELECT count(*) FROM api_campaigns.{view}"
        ).fetchone()[0]
        assert published == seeded
        key = VIEWS[view][1][1]
        assert (
            database.execute(
                f"SELECT count(*) FROM api_campaigns.{view} v "
                f"JOIN {table} t USING (campaign_id, {key})"
            ).fetchone()[0]
            == seeded
        )


@pytest.mark.postgres
@pytest.mark.parametrize("view", VIEWS)
def test_experimentation_reads_the_view_and_no_other_service_does(
    database, view: str
) -> None:
    assert _read_as(database, "svc_experiments", f"SELECT * FROM api_campaigns.{view}")
    for role in ("svc_checkins", "svc_campaigns", "svc_sales", "retail_app"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _read_as(database, role, f"SELECT * FROM api_campaigns.{view}")


@pytest.mark.postgres
def test_experimentation_still_cannot_read_the_campaign_tables(database) -> None:
    for table in (
        "public.campaign",
        "campaigns.campaign_eligibility",
        "campaigns.campaign_excluded_customer",
        "campaigns.campaign_excluded_label",
        "campaigns.campaign_audience",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _read_as(database, "svc_experiments", f"SELECT * FROM {table}")
