"""trg_campaign_activation_guard, the grants and the audit trail (F14-28).

The guard fires only for a session whose session_user is svc_campaigns, so
these tests sign in as that role for real, not with SET ROLE.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import psycopg
import pytest

from tests.conftest import ADMIN, MARKETING

pytestmark = pytest.mark.postgres
SCHEMA = Path(__file__).resolve().parents[3] / "sql/01_schema.sql"
BODY = {
    "name": "Audited",
    "label_code": "LOYAL",
    "starts_on": "2099-01-01",
    "ends_on": "2099-12-31",
}


def _unready_campaign(owner: psycopg.Connection) -> int:
    campaign_id = owner.execute(
        "INSERT INTO campaign (campaign_id, name, label_code, starts_on, ends_on,"
        " status) SELECT max(campaign_id) + 1, 'Unready', 'LOYAL', CURRENT_DATE,"
        " CURRENT_DATE + 30, 'DRAFT' FROM campaign RETURNING campaign_id"
    ).fetchone()[0]
    experiment_id = owner.execute(
        "INSERT INTO experiment (experiment_id, name, campaign_id, target_metric,"
        " starts_on, ends_on, conversion_window_days, data_origin)"
        " SELECT coalesce(max(experiment_id), 0) + 1, 'Pending', %s, 'CONVERSION',"
        " DATE '2099-01-01', DATE '2099-12-31', 7, 'OBSERVED' FROM experiment"
        " RETURNING experiment_id",
        (campaign_id,),
    ).fetchone()[0]
    owner.execute(
        "INSERT INTO experiment_group (group_id, experiment_id, kind, name,"
        " treatment_description) SELECT coalesce(max(group_id), 0) + 1, %s,"
        " 'CONTROL', 'Holdout', 'None.' FROM experiment_group",
        (experiment_id,),
    )
    return campaign_id


def test_the_guard_fires_for_the_service_session_as_check_violation(
    database: dict[str, str], owner: psycopg.Connection
) -> None:
    campaign_id = _unready_campaign(owner)
    with psycopg.connect(database["service"]) as service:
        with pytest.raises(psycopg.errors.CheckViolation) as refused:
            service.execute(
                "UPDATE campaign SET status = 'ACTIVE' WHERE campaign_id = %s",
                (campaign_id,),
            )
        assert refused.value.diag.sqlstate == "23514"
        assert refused.value.diag.constraint_name == "experiment_not_ready"
        service.rollback()
        # Only a move to ACTIVE is judged.
        service.execute(
            "UPDATE campaign SET status = 'CANCELLED' WHERE campaign_id = %s",
            (campaign_id,),
        )
        service.rollback()


def test_the_guard_does_not_fire_for_the_web_or_the_owner(
    database: dict[str, str], owner: psycopg.Connection
) -> None:
    campaign_id = _unready_campaign(owner)
    with psycopg.connect(database["web"]) as web:
        web.execute(
            "UPDATE campaign SET status = 'ACTIVE' WHERE campaign_id = %s",
            (campaign_id,),
        )
        web.rollback()
    owner.execute("BEGIN")
    owner.execute(
        "UPDATE campaign SET status = 'ACTIVE' WHERE campaign_id = %s", (campaign_id,)
    )
    owner.execute("ROLLBACK")
    assert (
        owner.execute(
            "SELECT status FROM campaign WHERE campaign_id = %s", (campaign_id,)
        ).fetchone()[0]
        == "DRAFT"
    )


def test_the_function_is_a_pinned_definer_guard_nobody_may_execute(
    owner: psycopg.Connection,
) -> None:
    row = owner.execute(
        "SELECT pg_get_userbyid(proowner), prosecdef, proconfig,"
        " coalesce((SELECT bool_or(a.grantee = 0) FROM aclexplode(proacl) a), false)"
        " FROM pg_proc WHERE proname = 'fn_campaign_activation_guard'"
    ).fetchone()
    owner_name, definer, config, public_execute = row
    assert owner_name == "definer_guard" and definer
    assert config == ["search_path=pg_catalog, pg_temp"]
    assert public_execute is False


def test_the_trigger_is_before_update_of_status_with_its_when_clause(
    owner: psycopg.Connection,
) -> None:
    definition = owner.execute(
        "SELECT pg_get_triggerdef(oid) FROM pg_trigger"
        " WHERE tgname = 'trg_campaign_activation_guard'"
    ).fetchone()[0]
    assert "BEFORE UPDATE OF status ON public.campaign" in definition
    assert "FOR EACH ROW" in definition
    assert "new.status" in definition and "SESSION_USER = 'svc_campaigns'" in definition


def test_the_grants_are_exactly_select_insert_update_on_campaign(
    owner: psycopg.Connection,
) -> None:
    def has(role: str, table: str, privilege: str) -> bool:
        return owner.execute(
            "SELECT has_table_privilege(%s, %s, %s)", (role, table, privilege)
        ).fetchone()[0]

    for privilege in ("SELECT", "INSERT", "UPDATE"):
        assert has("svc_campaigns", "public.campaign", privilege)
    for privilege in ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert not has("svc_campaigns", "public.campaign", privilege)
    for table in ("public.experiment", "public.experiment_group"):
        assert has("definer_guard", table, "SELECT")
        assert not has("svc_campaigns", table, "SELECT")
    others = [
        row[0]
        for row in owner.execute(
            "SELECT rolname FROM pg_roles WHERE rolname LIKE 'svc\\_%' AND"
            " rolname <> 'svc_campaigns'"
        )
    ]
    for role in others:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert not has(role, "public.campaign", privilege), role
        for table in ("public.experiment", "public.experiment_group"):
            assert not has(role, table, "SELECT"), role


def test_the_service_cannot_read_the_experiment_tables(
    database: dict[str, str],
) -> None:
    with psycopg.connect(database["service"]) as service:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            service.execute("SELECT 1 FROM experiment")


def test_reapplying_the_section_is_clean(owner: psycopg.Connection) -> None:
    text = SCHEMA.read_text()
    section = re.search(
        r"^-- BEGIN BOUNDARY campaigns$.*?^-- END BOUNDARY campaigns$",
        text,
        re.M | re.S,
    )
    assert section is not None
    owner.execute(section.group(0))
    owner.execute(section.group(0))


def _audit(owner: psycopg.Connection, campaign_id: int) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT action, user_id::text FROM audit_log WHERE entity = 'campaign'"
        " AND entity_pk = %s ORDER BY audit_id",
        (str(campaign_id),),
    ).fetchall()


def test_each_write_leaves_one_audit_row_naming_the_caller(
    api: Any, owner: psycopg.Connection
) -> None:
    caller = (MARKETING, "MARKETING")
    created = api("POST", "/v1/campaigns", as_user=caller, json_body=BODY)
    campaign_id = created.get_json()["campaign_id"]
    assert _audit(owner, campaign_id) == [("INSERT", MARKETING)]

    api(
        "PATCH",
        f"/v1/campaigns/{campaign_id}",
        as_user=caller,
        json_body={"name": "Edited"},
    )
    assert _audit(owner, campaign_id)[1:] == [("UPDATE", MARKETING)]

    api(
        "POST",
        f"/v1/campaigns/{campaign_id}/transitions",
        as_user=(ADMIN, "ADMIN"),
        json_body={"to": "ACTIVE", "expected_status": "DRAFT"},
    )
    assert _audit(owner, campaign_id)[2:] == [("UPDATE", ADMIN)]

    # A refused move writes nothing.
    api(
        "POST",
        f"/v1/campaigns/{campaign_id}/transitions",
        json_body={"to": "DRAFT", "expected_status": "ACTIVE"},
    )
    assert len(_audit(owner, campaign_id)) == 3
