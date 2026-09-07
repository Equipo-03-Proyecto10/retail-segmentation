"""Reading the audit log (#103, F3-11).

The log itself is written by triggers in sql/01_schema.sql and read back
against a real database in docs/evidence/f3-11-audit-log-view.md. What these
tests cover is the application's side: who may open it, what a page of it is,
how an entry is compared, and the things it must never show.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.audit import AuditEntry, AuditEntryDetail
from web.services.audit import PAGE_SIZE, EntryDetail, Page, read_page
from web.services.audit import _compare as compare_payloads


def _entry(audit_id: int, *, actor: str | None = "MOSAIQ Administrator") -> AuditEntry:
    return AuditEntry(
        audit_id=audit_id,
        entity="product",
        entity_pk=str(audit_id),
        action="UPDATE",
        actor_name=actor,
        executed_at=datetime(2026, 9, 6, 10, 30),
    )


def _detail(
    *,
    before: dict | None,
    after: dict | None,
    entity: str = "product",
) -> AuditEntryDetail:
    return AuditEntryDetail(
        audit_id=7,
        entity=entity,
        entity_pk="7",
        action="UPDATE",
        actor_name="MOSAIQ Administrator",
        executed_at=datetime(2026, 9, 6, 10, 30),
        data_before=before,
        data_after=after,
    )


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


def _sign_in(client: FlaskClient, role_code: str) -> None:
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "11111111-1111-1111-1111-000000000001"
        flask_session["role_code"] = role_code
        flask_session["name"] = f"{role_code.title()} user"


def _page(entries: tuple[AuditEntry, ...] = (), **overrides) -> Page:
    defaults = dict(
        entries=entries,
        entities=("app_user", "product", "store"),
        total=len(entries),
        page=1,
        page_count=1,
    )
    return Page(**{**defaults, **overrides})


# ---------- who may open it ----------


@pytest.mark.parametrize("role_code", ["ADMIN", "AUDITOR"])
def test_the_administrator_and_the_auditor_may_read_it(
    app: Flask, monkeypatch: pytest.MonkeyPatch, role_code: str
) -> None:
    monkeypatch.setattr(
        "web.routes.audit.read_page", lambda _c, **_f: _page((_entry(1),))
    )
    client = app.test_client()
    _sign_in(client, role_code)

    response = client.get("/audit/")

    assert response.status_code == 200
    assert "Audit log" in response.get_data(as_text=True)


@pytest.mark.parametrize(
    "role_code",
    ["ANALYST", "MARKETING", "STORE_MANAGER", "INVENTORY_PLANNER", "CUSTOMER"],
)
def test_every_other_role_is_refused(app: Flask, role_code: str) -> None:
    client = app.test_client()
    _sign_in(client, role_code)

    assert client.get("/audit/").status_code == 403
    assert client.get("/audit/1").status_code == 403


def test_signed_out_it_sends_you_to_sign_in(app: Flask) -> None:
    response = app.test_client().get("/audit/")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


# ---------- what the list shows ----------


def test_entries_carry_entity_key_action_actor_and_time(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.audit.read_page", lambda _c, **_f: _page((_entry(42),))
    )
    client = app.test_client()
    _sign_in(client, "AUDITOR")

    body = client.get("/audit/").get_data(as_text=True)

    assert "product" in body
    assert "42" in body
    assert "UPDATE" in body
    assert "MOSAIQ Administrator" in body
    assert "2026-09-06 10:30" in body


def test_an_entry_without_an_actor_is_unattributed_not_hidden(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.audit.read_page",
        lambda _c, **_f: _page((_entry(1, actor=None),)),
    )
    client = app.test_client()
    _sign_in(client, "AUDITOR")

    body = client.get("/audit/").get_data(as_text=True)

    assert "unattributed" in body
    assert "audit/1" in body, "the row is still there and still openable"


def test_the_filters_reach_the_service(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    def capture(_connection, **filters):
        seen.update(filters)
        return _page()

    monkeypatch.setattr("web.routes.audit.read_page", capture)
    client = app.test_client()
    _sign_in(client, "AUDITOR")

    client.get("/audit/?entity=store&from=2026-01-01&to=2026-06-30&page=3")

    assert seen["entity"] == "store"
    assert seen["date_from"] == date(2026, 1, 1)
    assert seen["date_to"] == date(2026, 6, 30)
    assert seen["page"] == 3


@pytest.mark.parametrize("query", ["page=abc", "page=-4"])
def test_an_unreadable_filter_is_dropped_rather_than_refused(
    app: Flask, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    """The value came from a query string a person may have edited by hand."""
    monkeypatch.setattr("web.routes.audit.read_page", lambda _c, **_f: _page())
    client = app.test_client()
    _sign_in(client, "AUDITOR")

    assert client.get(f"/audit/?{query}").status_code == 200


def test_the_pager_names_the_page_and_the_total(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.audit.read_page",
        lambda _c, **_f: _page((_entry(1),), total=140, page=2, page_count=6),
    )
    client = app.test_client()
    _sign_in(client, "AUDITOR")

    body = client.get("/audit/?page=2").get_data(as_text=True)

    assert "Page 2 of 6" in body
    assert "140 entries" in body
    assert "page=1" in body and "page=3" in body


# ---------- what a page of the log is ----------


def test_read_page_asks_for_one_page_and_clamps_the_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: dict = {}
    monkeypatch.setattr("web.services.audit.count_entries", lambda _c, **_f: 60)
    monkeypatch.setattr("web.services.audit.audited_entities", lambda _c: ["product"])

    def capture(_connection, **arguments):
        asked.update(arguments)
        return []

    monkeypatch.setattr("web.services.audit.search_entries", capture)

    page = read_page(MagicMock(), page=99)

    assert page.page_count == 3  # 60 entries at 25 to a page
    assert page.page == 3, "a page past the end shows the last one"
    assert asked["limit"] == PAGE_SIZE
    assert asked["offset"] == 50


def test_an_empty_log_still_has_one_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web.services.audit.count_entries", lambda _c, **_f: 0)
    monkeypatch.setattr("web.services.audit.audited_entities", lambda _c: [])
    monkeypatch.setattr("web.services.audit.search_entries", lambda _c, **_f: [])

    page = read_page(MagicMock())

    assert (page.page, page.page_count, page.total) == (1, 1, 0)
    assert not page.has_previous and not page.has_next


# ---------- before and after ----------


def _compare(before: dict | None, after: dict | None, **kwargs) -> EntryDetail:
    """Pair up one entry's payloads, the way `read_entry` does after fetching."""
    entry = _detail(before=before, after=after, **kwargs)
    return EntryDetail(entry=entry, fields=tuple(compare_payloads(entry)))


def test_a_changed_field_is_distinguishable_from_an_unchanged_one() -> None:
    detail = _compare(
        {"name": "Store 1", "city": "Monterrey"},
        {"name": "Store One", "city": "Monterrey"},
    )

    changed = {field.name: field.changed for field in detail.fields}

    assert changed == {"name": True, "city": False}
    assert detail.changed_fields == 1


def test_an_insert_has_no_before_and_counts_as_wholly_new() -> None:
    detail = _compare(None, {"name": "Store 9"})

    assert [(f.name, f.before, f.after) for f in detail.fields] == [
        ("name", None, "Store 9")
    ]
    assert detail.changed_fields == 1


def test_a_delete_has_no_after() -> None:
    detail = _compare({"name": "Store 9"}, None)

    assert detail.fields[0].after is None


def test_a_field_added_by_one_side_still_appears() -> None:
    detail = _compare({"name": "x"}, {"name": "x", "image_path": "/uploads/a.png"})

    assert [field.name for field in detail.fields] == ["image_path", "name"]


def test_non_string_values_are_rendered_readably() -> None:
    detail = _compare(
        {"list_price": 10, "is_active": True}, {"list_price": 12.5, "is_active": True}
    )

    values = {field.name: (field.before, field.after) for field in detail.fields}

    assert values["list_price"] == ("10", "12.5")
    assert values["is_active"] == ("true", "true")


# ---------- what it must never show ----------


def test_a_password_hash_is_never_rendered_even_if_a_payload_carries_one() -> None:
    """`fn_audit()` strips it; the view refuses to render it regardless."""
    detail = _compare(
        {"email": "admin@mosaiq-demo.com", "password_hash": "$argon2id$v=19$leaked"},
        {"email": "new@mosaiq-demo.com", "password_hash": "$argon2id$v=19$also"},
        entity="app_user",
    )

    assert [field.name for field in detail.fields] == ["email"]
    assert "argon2id" not in str(detail.fields)


def test_the_view_offers_nothing_that_would_write_to_the_log(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RNF-17: append-only. The page carries no form but signing out."""
    monkeypatch.setattr(
        "web.routes.audit.read_page", lambda _c, **_f: _page((_entry(1),))
    )
    monkeypatch.setattr(
        "web.routes.audit.read_entry",
        lambda _c, _id: EntryDetail(
            entry=_detail(before={"a": 1}, after={"a": 2}), fields=()
        ),
    )
    client = app.test_client()
    _sign_in(client, "ADMIN")

    for path in ("/audit/", "/audit/7"):
        body = client.get(path).get_data(as_text=True)
        # The filter is a GET form, which reads. The only form that submits a
        # write is signing out, and it belongs to the shell rather than to this
        # page.
        writing_forms = re.findall(r'<form[^>]*method="post"[^>]*>', body)
        assert len(writing_forms) == 1
        assert 'action="/logout"' in body

    # And there is no route that could accept a write.
    assert client.post("/audit/").status_code == 405
    assert client.post("/audit/7").status_code == 405


def test_an_entry_that_does_not_exist_is_a_404(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.audit.read_entry", lambda _c, _id: None)
    client = app.test_client()
    _sign_in(client, "ADMIN")

    assert client.get("/audit/999999").status_code == 404


@pytest.mark.parametrize(
    "query", ["from=not-a-date", "to=2026-13-45", "from=2026-09-06&to=2026-01-01"]
)
def test_invalid_audit_dates_explain_the_refusal_without_reading_entries(
    app, monkeypatch, query
):
    read = Mock()
    monkeypatch.setattr("web.routes.audit.read_page", read)
    client = app.test_client()
    _sign_in(client, "AUDITOR")
    response = client.get(f"/audit/?{query}")
    assert response.status_code == 400
    assert b'role="alert"' in response.data
    assert b"Clear" in response.data
    read.assert_not_called()
