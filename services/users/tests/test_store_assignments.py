"""Store assignment reads and atomic replacements (F19-06, #756)."""

from __future__ import annotations

import threading
import time
from typing import Any

import psycopg
import pytest
from lxml import etree

from tests.conftest import ANALYST, STORE_MANAGER, Api

pytestmark = pytest.mark.postgres
SALES_ASSOCIATE = "11111111-1111-1111-1111-000000000031"
REVOKE_AUDIT = (
    "REVOKE EXECUTE ON FUNCTION api_audit.record_event_v1("
    "uuid, text, text, timestamptz, text, uuid, text,"
    " smallint, text, text, bigint, bigint, text, jsonb) FROM svc_users"
)


def _code(reply: Any) -> tuple[int, str | None]:
    body = reply.get_json(silent=True) or {}
    return reply.status_code, body.get("code")


def _stored(owner: psycopg.Connection, user_id: str) -> list[int]:
    return [
        row[0]
        for row in owner.execute(
            "SELECT store_id FROM users.store_assignment"
            " WHERE user_id = %s ORDER BY store_id",
            (user_id,),
        ).fetchall()
    ]


def test_admin_replaces_and_reads_a_sorted_set_in_json(
    api: Api, owner: psycopg.Connection
) -> None:
    replaced = api(
        "PUT",
        f"/v1/store-assignments/{STORE_MANAGER}",
        json_body={"store_ids": [9, 2, 5]},
    )
    assert replaced.status_code == 200
    assert replaced.get_json() == {
        "user_id": STORE_MANAGER,
        "store_ids": [2, 5, 9],
    }

    read = api("GET", f"/v1/store-assignments/{STORE_MANAGER}")
    assert read.status_code == 200
    assert read.get_json() == replaced.get_json()
    actor = owner.execute(
        "SELECT user_id::text FROM audit_log"
        " WHERE entity = 'store_assignment' AND entity_pk = %s"
        " ORDER BY audit_id DESC LIMIT 1",
        (STORE_MANAGER,),
    ).fetchone()
    assert actor == ("11111111-1111-1111-1111-000000000001",)


def test_xml_replace_and_read_validate_against_the_xsd(api: Api, service: Any) -> None:
    namespace = "https://mosaiq.maxthecoder.online/xml/users/v1"
    request_body = (
        f'<ReplaceStoreAssignmentRequest xmlns="{namespace}">'
        "<store_ids>7</store_ids><store_ids>3</store_ids>"
        "</ReplaceStoreAssignmentRequest>"
    )
    replaced = api(
        "PUT",
        f"/v1/store-assignments/{SALES_ASSOCIATE}",
        data=request_body,
        headers={"Content-Type": "application/xml", "Accept": "application/xml"},
    )
    assert replaced.status_code == 200, replaced.data
    schema = service.extensions["mosaiq_kit"].contract.xml_schema
    document = etree.fromstring(replaced.data)
    assert schema.validate(document), schema.error_log
    assert [int(node.text) for node in document.findall("{*}store_ids")] == [3, 7]

    read = api(
        "GET",
        f"/v1/store-assignments/{SALES_ASSOCIATE}",
        headers={"Accept": "application/xml"},
    )
    document = etree.fromstring(read.data)
    assert read.status_code == 200 and schema.validate(document), schema.error_log
    assert [int(node.text) for node in document.findall("{*}store_ids")] == [3, 7]


def test_only_admin_writes_and_floor_roles_read_only_themselves(api: Api) -> None:
    refused = api(
        "PUT",
        f"/v1/store-assignments/{STORE_MANAGER}",
        as_user=(ANALYST, "AUDITOR"),
        json_body={"store_ids": []},
    )
    assert _code(refused) == (403, "forbidden_role")

    own = api(
        "GET",
        f"/v1/store-assignments/{STORE_MANAGER}",
        as_user=(STORE_MANAGER, "STORE_MANAGER"),
    )
    assert own.status_code == 200
    other = api(
        "GET",
        f"/v1/store-assignments/{SALES_ASSOCIATE}",
        as_user=(STORE_MANAGER, "STORE_MANAGER"),
    )
    assert _code(other) == (403, "forbidden_user")
    hidden_missing = api(
        "GET",
        "/v1/store-assignments/99999999-9999-9999-9999-999999999999",
        as_user=(STORE_MANAGER, "STORE_MANAGER"),
    )
    assert _code(hidden_missing) == (403, "forbidden_user")


def test_non_store_bound_role_accepts_only_an_empty_set(api: Api) -> None:
    refused = api(
        "PUT",
        f"/v1/store-assignments/{ANALYST}",
        json_body={"store_ids": [1]},
    )
    assert _code(refused) == (409, "role_not_store_bound")
    cleared = api(
        "PUT",
        f"/v1/store-assignments/{ANALYST}",
        json_body={"store_ids": []},
    )
    assert cleared.status_code == 200
    assert cleared.get_json()["store_ids"] == []


@pytest.mark.parametrize(
    "store_ids", [[1, 1], [0], [32768], [-1]], ids=["duplicate", "0", "32768", "-1"]
)
def test_a_duplicate_or_out_of_range_id_is_invalid_body_and_changes_nothing(
    api: Api, owner: psycopg.Connection, store_ids: list[int]
) -> None:
    before = _stored(owner, STORE_MANAGER)
    refused = api(
        "PUT",
        f"/v1/store-assignments/{STORE_MANAGER}",
        json_body={"store_ids": store_ids},
    )
    assert _code(refused) == (422, "invalid_body")
    assert _stored(owner, STORE_MANAGER) == before


def test_unknown_store_rolls_back_the_complete_replacement(
    api: Api, owner: psycopg.Connection
) -> None:
    before = _stored(owner, STORE_MANAGER)
    audited_before = owner.execute(
        "SELECT count(*) FROM audit_log"
        " WHERE entity = 'store_assignment' AND entity_pk = %s",
        (STORE_MANAGER,),
    ).fetchone()[0]
    refused = api(
        "PUT",
        f"/v1/store-assignments/{STORE_MANAGER}",
        json_body={"store_ids": [2, 32767]},
    )
    assert _code(refused) == (422, "unknown_store")
    assert _stored(owner, STORE_MANAGER) == before
    audited_after = owner.execute(
        "SELECT count(*) FROM audit_log"
        " WHERE entity = 'store_assignment' AND entity_pk = %s",
        (STORE_MANAGER,),
    ).fetchone()[0]
    assert audited_after == audited_before


@pytest.mark.parametrize(
    ("path", "as_user", "body", "expected"),
    [
        (
            f"/v1/store-assignments/{STORE_MANAGER}",
            (ANALYST, "AUDITOR"),
            "<store_ids>2</store_ids>",
            "forbidden_role",
        ),
        (
            f"/v1/store-assignments/{ANALYST}",
            None,
            "<store_ids>2</store_ids>",
            "role_not_store_bound",
        ),
        (
            f"/v1/store-assignments/{STORE_MANAGER}",
            None,
            "<store_ids>32767</store_ids>",
            "unknown_store",
        ),
    ],
)
def test_write_refusals_are_xml(
    api: Api,
    path: str,
    as_user: tuple[str, str] | None,
    body: str,
    expected: str,
) -> None:
    namespace = "https://mosaiq.maxthecoder.online/xml/users/v1"
    kwargs: dict[str, Any] = {}
    if as_user is not None:
        kwargs["as_user"] = as_user
    request_xml = (
        f'<ReplaceStoreAssignmentRequest xmlns="{namespace}">'
        f"{body}</ReplaceStoreAssignmentRequest>"
    )
    reply = api(
        "PUT",
        path,
        data=request_xml,
        headers={"Content-Type": "application/xml", "Accept": "application/xml"},
        **kwargs,
    )
    assert reply.status_code in {403, 409, 422}
    document = etree.fromstring(reply.data)
    assert document.findtext("{*}code") == expected


def test_deactivation_keeps_history_but_the_get_and_view_grant_nothing(
    api: Api, owner: psycopg.Connection
) -> None:
    replaced = api(
        "PUT",
        f"/v1/store-assignments/{STORE_MANAGER}",
        json_body={"store_ids": [4, 8]},
    )
    assert replaced.status_code == 200
    assert api("POST", f"/v1/users/{STORE_MANAGER}/deactivation").status_code == 200

    assert _stored(owner, STORE_MANAGER) == [4, 8]
    assert api("GET", f"/v1/store-assignments/{STORE_MANAGER}").get_json() == {
        "user_id": STORE_MANAGER,
        "store_ids": [],
    }
    visible = owner.execute(
        "SELECT store_id FROM api_users.store_assignment_v1 WHERE user_id = %s",
        (STORE_MANAGER,),
    ).fetchall()
    assert visible == []


def test_replace_records_at_most_one_event_per_direction(
    api: Api, owner: psycopg.Connection
) -> None:
    assert (
        api(
            "PUT",
            f"/v1/store-assignments/{STORE_MANAGER}",
            json_body={"store_ids": [1, 2, 9]},
            headers={"X-Correlation-ID": "assignment-replace-1"},
        ).status_code
        == 200
    )
    events = owner.execute(
        "SELECT details FROM audit_events.domain_event"
        " WHERE correlation_id = %s ORDER BY seq",
        ("assignment-replace-1",),
    ).fetchall()
    assert events == [
        ({"change": "STORES_ASSIGNED", "store_ids": [2]},),
        ({"change": "STORES_UNASSIGNED", "store_ids": [17, 25]},),
    ]


def test_replace_rolls_back_when_its_permission_event_cannot_be_recorded(
    api: Api, owner: psycopg.Connection
) -> None:
    before = _stored(owner, STORE_MANAGER)
    owner.execute(REVOKE_AUDIT)

    refused = api(
        "PUT",
        f"/v1/store-assignments/{STORE_MANAGER}",
        json_body={"store_ids": [2, 5]},
    )

    assert _code(refused) == (503, "audit_unavailable")
    assert _stored(owner, STORE_MANAGER) == before


def test_two_concurrent_replaces_leave_one_complete_submitted_set(
    api: Api,
    service: Any,
    owner: psycopg.Connection,
    database: dict[str, str],
) -> None:
    from app import create_app

    second_app = create_app(start_relay=False)
    second_app.extensions["mosaiq_kit"].auth.redis = service.extensions[
        "mosaiq_kit"
    ].auth.redis
    second = Api(second_app.test_client(), api.sign)
    sets = ([2, 4, 6], [3, 5, 7])
    replies: list[Any] = []

    lock = psycopg.connect(database["service"])
    try:
        lock.execute(
            "SELECT 1 FROM app_user WHERE user_id = %s FOR UPDATE",
            (STORE_MANAGER,),
        )
        threads = [
            threading.Thread(
                target=lambda client=client, values=values: replies.append(
                    client(
                        "PUT",
                        f"/v1/store-assignments/{STORE_MANAGER}",
                        json_body={"store_ids": values},
                    )
                )
            )
            for client, values in ((api, sets[0]), (second, sets[1]))
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            waiting = owner.execute(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database() AND wait_event_type = 'Lock'"
            ).fetchone()[0]
            if waiting >= 2:
                break
            time.sleep(0.05)
        assert waiting >= 2
        lock.commit()
        for thread in threads:
            thread.join(timeout=10)
    finally:
        lock.close()
        second_app.extensions["mosaiq_kit"].db.close()

    assert len(replies) == 2
    assert all(reply.status_code == 200 for reply in replies)
    assert _stored(owner, STORE_MANAGER) in [list(values) for values in sets]
