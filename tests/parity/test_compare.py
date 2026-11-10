"""The harness's pure parts run in every pytest, with or without a stack."""

import json

from tests.parity.compare import (
    AuditShape,
    CaseResult,
    Difference,
    audit_shape,
    credentials_in,
    diff_audit,
    diff_levels,
    parse_stock_page,
    write_results,
)

PAGE = """<table><tbody>
<tr><td>Centro</td><td>SKU-1</td><td>Soap</td><td>7 <span>Low</span></td><td>x</td></tr>
<tr><td>Norte</td><td>SKU-2</td><td>Tea</td><td>40</td><td>x</td></tr>
</tbody></table>"""


def test_the_stock_page_is_read_row_by_row() -> None:
    assert parse_stock_page(PAGE) == [("Centro", "SKU-1", 7), ("Norte", "SKU-2", 40)]


def test_equal_views_have_no_difference() -> None:
    assert diff_levels({(1, 2): 5}, {(1, 2): 5}) == []


def test_every_difference_is_reported() -> None:
    found = diff_levels({(1, 2): 5, (1, 3): 1}, {(1, 2): 4, (2, 2): 9})
    assert [(d.store_id, d.product_id, d.what) for d in found] == [
        (1, 2, "quantity_on_hand"),
        (1, 3, "missing on one side"),
        (2, 2, "missing on one side"),
    ]
    assert "web=5" in str(found[0])


def test_results_file_counts_cases(tmp_path) -> None:
    out = tmp_path / "parity.json"
    write_results(
        [
            CaseResult("a", "passed"),
            CaseResult("b", "failed", differences=[Difference(1, 2, "q", 1, 2)]),
            CaseResult("c", "skipped", "no stack"),
        ],
        out,
    )
    data = json.loads(out.read_text())
    assert (data["passed"], data["failed"], data["skipped"]) == (1, 1, 1)
    assert data["cases"][1]["differences"][0]["web"] == 1


# ---------- audit (F20-08) ----------

ACTOR = "11111111-1111-1111-1111-000000000001"


def _row(action, before, after, user=ACTOR, entity="category") -> dict:
    return {
        "entity": entity,
        "action": action,
        "user_id": user,
        "data_before": before,
        "data_after": after,
    }


def test_an_audit_shape_keeps_columns_and_changes_not_values() -> None:
    shape = audit_shape(
        _row(
            "UPDATE", {"category_id": 7, "name": "A"}, {"category_id": 7, "name": "B"}
        ),
        ACTOR,
    )
    assert shape == AuditShape(
        "category",
        "UPDATE",
        ("category_id", "name"),
        ("category_id", "name"),
        ("name",),
        True,
    )
    assert audit_shape(_row("INSERT", None, {"category_id": 8}), ACTOR).before is None


def test_the_same_change_by_two_writers_has_no_difference() -> None:
    web = [audit_shape(_row("UPDATE", {"id": 1, "n": "a"}, {"id": 1, "n": "b"}), ACTOR)]
    service = [
        audit_shape(_row("UPDATE", {"id": 2, "n": "c"}, {"id": 2, "n": "d"}), ACTOR)
    ]
    assert diff_audit("category UPDATE", web, service) == []


def test_an_unattributed_or_differently_shaped_row_is_reported() -> None:
    web = [audit_shape(_row("UPDATE", {"id": 1, "n": "a"}, {"id": 1, "n": "b"}), ACTOR)]
    service = [
        audit_shape(
            _row("UPDATE", {"id": 2, "n": "c"}, {"id": 2, "n": "c"}, user=None), ACTOR
        ),
        audit_shape(_row("INSERT", None, {"id": 2}), ACTOR),
    ]
    found = diff_audit("category UPDATE", web, service)
    assert [d.what for d in found] == [
        "number of audit rows",
        "changed",
        "row 0 attributed to the actor",
    ]
    assert "category UPDATE: changed" in str(found[1])
    assert diff_audit("x", [], [])[0].what == "no audit row on either side"


def test_credentials_are_found_by_key_and_by_hash_shape() -> None:
    clean = {"audit_entries": [{"data_after": {"email": "a@b.co", "name": "A"}}]}
    assert credentials_in(clean) == []
    leaked = {
        "audit_entries": [
            {"data_after": {"password_hash": "x"}},
            {"data_before": {"note": "$argon2id$v=19$m=65536"}},
        ]
    }
    assert credentials_in(leaked) == [
        "audit_entries[0].data_after.password_hash",
        "audit_entries[1].data_before.note",
    ]
