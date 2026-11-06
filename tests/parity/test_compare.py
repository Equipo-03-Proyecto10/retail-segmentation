"""The harness's pure parts run in every pytest, with or without a stack."""

import json

from tests.parity.compare import (
    ASSIGNED,
    NO_OPEN_ASSIGNMENT,
    UNASSIGNED_RESULT,
    Assignment,
    CaseResult,
    Difference,
    SegmentDifference,
    assignment_from_service,
    diff_assignments,
    diff_levels,
    diff_members,
    parse_customer_current,
    parse_report_open_members,
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


# ---------- segments (F20-09) ----------

PILL = (
    '<span class="mq-pill mq-pill--{tone}">'
    '<span class="mq-pill__dot"></span>{text}</span>'
)
LOYAL = PILL.format(tone="info", text="Loyal")
SYNTHETIC = PILL.format(tone="warning", text="Synthetic")


def _timeline(*rows: str) -> str:
    """The shape of catalog/customer_detail.html: a comparison table first,
    then the timeline, whose header the reader looks for."""

    return f"""
<table class="mq-table"><thead><tr><th>Measure</th><th>Previous</th></tr></thead>
<tbody><tr><td>Label</td><td>{LOYAL}</td></tr></tbody></table>
<table class="mq-table"><thead><tr>
<th scope="col">Run</th><th scope="col">Label</th><th scope="col">Valid from</th>
<th scope="col">Valid to</th><th>R</th><th>F</th><th>M</th><th>Recency</th>
</tr></thead><tbody>{"".join(rows)}</tbody></table>"""


def _row(run: int, label: str, valid_to: str, scores=("4", "5", "3")) -> str:
    r, f, m = scores
    return (
        f'<tr><td><a href="/run-history/{run}">#{run}</a> {SYNTHETIC} '
        f'<span class="mq-caption">2026-10-01 · 90-day window</span></td>'
        f'<td>{PILL.format(tone="info", text=label)} '
        f'<span class="mq-caption">since 2026-09-01</span></td>'
        f"<td>2026-10-01 03:00</td><td>{valid_to}</td>"
        f"<td>{r}</td><td>{f}</td><td>{m}</td><td>12 days</td></tr>"
    )


def test_the_open_timeline_row_is_the_current_assignment() -> None:
    page = _timeline(_row(9, "Loyal", "Open"), _row(4, "At risk", "2026-10-01 03:00"))
    assert parse_customer_current(page) == (9, "Loyal", (4, 5, 3))


def test_an_unassigned_open_row_has_no_label_and_no_scores() -> None:
    page = _timeline(_row(9, "Unassigned", "Open", ("—", "—", "—")))
    assert parse_customer_current(page) == (9, None, (None, None, None))


def test_a_customer_without_an_open_row_has_no_current_assignment() -> None:
    assert parse_customer_current(_timeline(_row(4, "Loyal", "2026-10-01"))) is None
    assert parse_customer_current("<p>No segment history</p>") is None


def _report(rows: str, page: int = 1, pages: int = 2) -> str:
    return f"""
<table class="mq-table"><thead><tr>
<th>Customer</th><th>Run</th><th>Label</th><th>Valid from</th><th>Valid to</th><th></th>
</tr></thead><tbody>{rows}</tbody></table>
<nav>Page {page} of {pages} · 30 rows</nav>"""


def _report_row(customer: str, valid_to: str) -> str:
    return (
        f'<tr><td><a href="/catalog/customers/{customer}">Ana</a></td>'
        f"<td>#9 · 2026-10-01 · RFM_RULES</td>"
        f"<td>{PILL.format(tone='info', text='Loyal')}</td><td>2026-10-01</td>"
        f"<td>{valid_to}</td><td><details><summary>Why?</summary>"
        "<table><thead><tr><th>Component</th></tr></thead>"
        "<tbody><tr><td>Recency</td></tr></tbody></table></details></td></tr>"
    )


def test_the_report_keeps_only_open_rows_and_reads_its_page_count() -> None:
    a, b = (
        "7b3ad781-c5de-4f40-ae50-1fa825ce2b25",
        "0c9f6a3e-5d2b-4c8e-9a41-7e3b2d1f8a66",
    )
    page = _report(
        _report_row(a, '<span class="mq-caption">still open</span>')
        + _report_row(b, "2026-10-01")
    )
    assert parse_report_open_members(page) == ({a}, 1, 2)


def test_service_states_map_to_assignments() -> None:
    assert assignment_from_service(
        {
            "customer_id": "c",
            "state": ASSIGNED,
            "label_code": "LOYAL",
            "r_score": 4,
            "f_score": 5,
            "m_score": 4,
            "since": "x",
            "run_id": 42,
        }
    ) == Assignment(ASSIGNED, "LOYAL", 4, 5, 4, 42)
    assert assignment_from_service(
        {"customer_id": "c", "state": UNASSIGNED_RESULT, "since": "x", "run_id": 42}
    ) == Assignment(UNASSIGNED_RESULT, run_id=42)
    assert assignment_from_service(
        {"customer_id": "c", "state": NO_OPEN_ASSIGNMENT}
    ) == Assignment(NO_OPEN_ASSIGNMENT)


def test_every_assignment_field_that_differs_is_reported() -> None:
    web = {
        "a": Assignment(ASSIGNED, "LOYAL", 4, 5, 4, 42),
        "b": Assignment(UNASSIGNED_RESULT, run_id=42),
    }
    service = {
        "a": Assignment(ASSIGNED, "LOYAL", 4, 5, 3, 41),
        "c": Assignment(NO_OPEN_ASSIGNMENT),
    }
    found = diff_assignments(web, service)
    assert [(d.subject, d.what) for d in found] == [
        ("a", "m_score"),
        ("a", "run_id"),
        ("b", "missing on one side"),
        ("c", "missing on one side"),
    ]
    assert diff_assignments(web, dict(web)) == []


def test_members_on_one_side_only_are_reported() -> None:
    found = diff_members("LOYAL", {"a", "b"}, {"b", "c"})
    assert found == [
        SegmentDifference("LOYAL", "member on the web only", "a", None),
        SegmentDifference("LOYAL", "member on the service only", None, "c"),
    ]
    assert "LOYAL: member on the web only" in str(found[0])
