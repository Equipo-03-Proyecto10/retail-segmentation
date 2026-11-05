"""The harness's pure parts run in every pytest, with or without a stack."""

import json

from tests.parity.compare import (
    CaseResult,
    Difference,
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
