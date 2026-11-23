"""The harness's pure parts run in every pytest, with or without a stack."""

import http.client
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.conformance.harness import Reply
from tests.parity.compare import (
    ASSIGNED,
    NO_OPEN_ASSIGNMENT,
    UNASSIGNED_RESULT,
    Assignment,
    AuditShape,
    CaseResult,
    Difference,
    EngineRfm,
    Recommendation,
    RecommendationDifference,
    ReportDifference,
    RfmDifference,
    SegmentDifference,
    WebRfm,
    assignment_from_service,
    audit_shape,
    credentials_in,
    diff_assignments,
    diff_audit,
    diff_levels,
    diff_members,
    diff_recommendations,
    diff_rfm,
    parse_customer_current,
    parse_propagation_bound,
    parse_recommendations_page,
    parse_report_open_members,
    parse_reports_revenue,
    parse_stock_page,
    recommendation_from_service,
    write_results,
)
from tests.parity.stack import ParityClient

PAGE = """<table><tbody>
<tr><td>Centro</td><td>SKU-1</td><td>Soap</td><td>7 <span>Low</span></td><td>x</td></tr>
<tr><td>Norte</td><td>SKU-2</td><td>Tea</td><td>40</td><td>x</td></tr>
</tbody></table>"""


class FakeTransport:
    def __init__(self, replies) -> None:
        self.replies = iter(replies)
        self.calls = 0

    def __call__(self, method, path, **kwargs):
        self.calls += 1
        return next(self.replies)


def _reply(status: int, retry_after: str | None = None) -> Reply:
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return Reply(status, headers, b"")


def _parity_client(replies, sleeps: list[int], retries=3) -> tuple:
    transport = FakeTransport(replies)
    client = ParityClient(
        "http://parity.invalid",
        contract=None,
        sleep=sleeps.append,
        transport=transport,
        rate_limit_retries=retries,
    )
    return client, transport


def test_a_parity_read_waits_for_retry_after_before_retrying() -> None:
    sleeps = []
    client, transport = _parity_client([_reply(429, "7"), _reply(200)], sleeps)

    assert client.call("GET", "/api/segments/v1/segment-assignments").status == 200
    assert sleeps == [7]
    assert transport.calls == 2


def test_a_parity_read_caps_retry_after_at_sixty_seconds() -> None:
    sleeps = []
    client, _ = _parity_client([_reply(429, "90"), _reply(200)], sleeps)

    assert client.call("GET", "/api/segments/v1/segment-assignments").status == 200
    assert sleeps == [60]


def test_a_parity_read_gives_up_after_the_retry_bound() -> None:
    sleeps = []
    client, transport = _parity_client([_reply(429, "1")] * 3, sleeps, retries=2)

    with pytest.raises(AssertionError, match="still rate limited after 2 retries"):
        client.call("GET", "/api/segments/v1/segment-assignments")

    assert sleeps == [1, 1]
    assert transport.calls == 3


def test_a_parity_read_does_not_retry_another_error() -> None:
    sleeps = []
    unavailable = _reply(503)
    client, transport = _parity_client([unavailable], sleeps)

    assert client.call("GET", "/api/segments/v1/segment-assignments") is unavailable
    assert sleeps == []
    assert transport.calls == 1


def test_a_rate_limited_write_is_returned_not_repeated() -> None:
    sleeps = []
    limited = _reply(429, "5")
    client, transport = _parity_client([limited], sleeps)

    assert client.call("POST", "/api/sales/v1/sales") is limited
    assert sleeps == []
    assert transport.calls == 1


def test_retry_after_is_read_from_real_headers_whatever_their_case() -> None:
    # The shared client hands back http.client's headers, which match any case.
    headers = http.client.HTTPMessage()
    headers["retry-after"] = "4"
    sleeps = []
    client, _ = _parity_client([Reply(429, headers, b""), _reply(200)], sleeps)

    assert client.call("GET", "/api/segments/v1/segment-assignments").status == 200
    assert sleeps == [4]


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


# ---------- audit (F20-08) ----------

ACTOR = "11111111-1111-1111-1111-000000000001"


def _audit_row(action, before, after, user=ACTOR, entity="category") -> dict:
    return {
        "entity": entity,
        "action": action,
        "user_id": user,
        "data_before": before,
        "data_after": after,
    }


def test_an_audit_shape_keeps_columns_and_changes_not_values() -> None:
    shape = audit_shape(
        _audit_row(
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
    assert (
        audit_shape(_audit_row("INSERT", None, {"category_id": 8}), ACTOR).before
        is None
    )


def test_the_same_change_by_two_writers_has_no_difference() -> None:
    web = [
        audit_shape(
            _audit_row("UPDATE", {"id": 1, "n": "a"}, {"id": 1, "n": "b"}), ACTOR
        )
    ]
    service = [
        audit_shape(
            _audit_row("UPDATE", {"id": 2, "n": "c"}, {"id": 2, "n": "d"}), ACTOR
        )
    ]
    assert diff_audit("category UPDATE", web, service) == []


def test_an_unattributed_or_differently_shaped_row_is_reported() -> None:
    web = [
        audit_shape(
            _audit_row("UPDATE", {"id": 1, "n": "a"}, {"id": 1, "n": "b"}), ACTOR
        )
    ]
    service = [
        audit_shape(
            _audit_row("UPDATE", {"id": 2, "n": "c"}, {"id": 2, "n": "c"}, user=None),
            ACTOR,
        ),
        audit_shape(_audit_row("INSERT", None, {"id": 2}), ACTOR),
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


# ---------- stream bounds (F20-09) ----------

CONTRACT = """# `stream:segments.v1.run_recorded`

## Propagation bound

| Group | p95 (ms) | Max (ms) |
|---|---:|---:|
| `segments` | 3000 | 5000 |
| `recommendations` | 2500 | 4000 |

## Failure and compatibility
"""


def test_a_group_bound_is_read_from_its_stream_contract() -> None:
    assert parse_propagation_bound(CONTRACT, "segments") == (3000, 5000)
    assert parse_propagation_bound(CONTRACT, "recommendations") == (2500, 4000)


def test_a_contract_without_a_bound_states_none() -> None:
    with pytest.raises(ValueError, match="no '## Propagation bound' section"):
        parse_propagation_bound("# `stream:x`\n\n## Consumers\n", "segments")
    with pytest.raises(ValueError, match="no row for 'campaigns'"):
        parse_propagation_bound(CONTRACT, "campaigns")


REPORTS = """<div class="mq-grid mq-grid--3">
<article class="mq-metric">
  <span class="mq-metric__label">Revenue <span class="mq-pill">Synthetic</span></span>
  <span class="mq-metric__value mq-num">12,345.60</span>
  <div class="mq-metric__footer"><span>30 days to 2026-11-01</span></div>
</article>
<article class="mq-metric">
  <span class="mq-metric__label">Sales</span>
  <span class="mq-metric__value mq-num">1,204</span>
</article>
</div>
<section><span class="mq-table__count">Revenue by channel</span>
<table><tr><td>Web</td><td class="mq-num">9</td></tr></table></section>"""


def test_the_reports_index_is_read_as_revenue_and_sales() -> None:
    from decimal import Decimal

    assert parse_reports_revenue(REPORTS) == (Decimal("12345.60"), 1204)


def test_an_index_without_sales_has_no_figures() -> None:
    assert parse_reports_revenue("<h2>No sales yet</h2>") is None


def test_a_report_difference_names_the_figure() -> None:
    found = ReportDifference("reports index", "revenue", "10.00", "20.00")
    assert "reports index: revenue: web='10.00' expected='20.00'" == str(found)


# ---------- RFM (F20-11) ----------

BOUGHT = datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
COMPUTED = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
CUSTOMER = "11111111-1111-1111-1111-111111111111"


def _web(frequency=3, monetary="120.50", at=BOUGHT) -> WebRfm:
    return WebRfm(at, frequency, Decimal(monetary))


def _engine(r_days=20, f_count=3, m_total="120.50", state="scored") -> EngineRfm:
    return EngineRfm(state, r_days, f_count, Decimal(m_total), COMPUTED)


def test_the_same_raw_values_are_no_difference() -> None:
    # 20 days and 2 hours 30 minutes from the last purchase to computed_at.
    assert diff_rfm({CUSTOMER: _web()}, {CUSTOMER: _engine()}) == []


def test_recency_is_the_whole_days_to_the_profiles_own_clock() -> None:
    just_under = BOUGHT - timedelta(hours=21, minutes=30)  # 21 days to COMPUTED
    assert (
        diff_rfm({CUSTOMER: _web(at=just_under)}, {CUSTOMER: _engine(r_days=21)}) == []
    )
    found = diff_rfm({CUSTOMER: _web()}, {CUSTOMER: _engine(r_days=21)})
    assert found == [RfmDifference(CUSTOMER, "recency_days", 20, 21)]


def test_each_difference_names_the_customer_and_both_values() -> None:
    found = diff_rfm(
        {CUSTOMER: _web(frequency=3, monetary="120.50")},
        {CUSTOMER: _engine(f_count=4, m_total="150.00")},
    )
    assert [str(d) for d in found] == [
        f"{CUSTOMER}: frequency: web=3 rfm-engine=4",
        f"{CUSTOMER}: monetary: web=Decimal('120.50') rfm-engine=Decimal('150.00')",
    ]


def test_money_is_compared_as_a_decimal_not_as_text() -> None:
    assert (
        diff_rfm(
            {CUSTOMER: _web(monetary="120.5")}, {CUSTOMER: _engine(m_total="120.50")}
        )
        == []
    )


def test_a_customer_with_no_purchase_agrees_with_an_unassigned_profile_or_none() -> (
    None
):
    nothing = WebRfm(None, None, None)
    unassigned = EngineRfm("unassigned", None, 0, Decimal("0.00"), COMPUTED)
    assert diff_rfm({CUSTOMER: nothing}, {CUSTOMER: unassigned}) == []
    assert diff_rfm({CUSTOMER: nothing}, {}) == []


def test_a_purchase_with_no_scored_profile_is_a_difference() -> None:
    found = diff_rfm({CUSTOMER: _web()}, {})
    assert found == [
        RfmDifference(CUSTOMER, "profile", (BOUGHT, 3, Decimal("120.50")), None)
    ]
    pending = EngineRfm("pending", None, None, None, COMPUTED)
    assert diff_rfm({CUSTOMER: _web()}, {CUSTOMER: pending})[0].engine == "pending"


def test_a_scored_profile_with_no_web_purchase_is_a_difference() -> None:
    found = diff_rfm({CUSTOMER: WebRfm(None, None, None)}, {CUSTOMER: _engine()})
    assert found == [
        RfmDifference(CUSTOMER, "profile", None, (20, 3, Decimal("120.50")))
    ]


def test_a_customer_only_one_side_knows_is_found() -> None:
    other = "22222222-2222-2222-2222-222222222222"
    found = diff_rfm({CUSTOMER: _web()}, {other: _engine()})
    assert [d.customer_id for d in found] == sorted([CUSTOMER, other])


def test_differences_are_listed_in_customer_order() -> None:
    low, high = (
        "00000000-0000-0000-0000-000000000001",
        "ffffffff-ffff-ffff-ffff-ffffffffffff",
    )
    found = diff_rfm(
        {high: _web(frequency=1), low: _web(frequency=1)},
        {high: _engine(f_count=2), low: _engine(f_count=2)},
    )
    assert [d.customer_id for d in found] == [low, high]


def test_an_rfm_difference_is_written_to_the_results_file(tmp_path) -> None:
    path = tmp_path / "parity.json"
    found = RfmDifference(CUSTOMER, "frequency", 3, 4)
    write_results([CaseResult("rfm", "failed", "x", [found])], path)
    entry = json.loads(path.read_text())["cases"][0]["differences"][0]
    assert entry == {
        "customer_id": CUSTOMER,
        "what": "frequency",
        "web": 3,
        "engine": 4,
    }


# ---------- recommendations (F20-10) ----------

_ITEM_HTML = """
<article class="mq-panel" data-recommendation="{product}">
  <dl class="mq-description-list">
    <dt>Store</dt><dd>Centro</dd>
    <dt>Available stock</dt><dd class="mq-num">{units}</dd>
  </dl>
</article>"""


def test_the_recommendations_page_lists_products_in_order_with_their_stock() -> None:
    html = _ITEM_HTML.format(product=245, units="8 units") + _ITEM_HTML.format(
        product=120, units="1 unit"
    )
    assert parse_recommendations_page(html) == Recommendation(
        "recommended", ((245, 8), (120, 1))
    )


@pytest.mark.parametrize(
    ("title", "status"),
    [
        ("No segment available", "no_segment"),
        ("No usual store", "no_usual_store"),
        ("No eligible products", "none_match"),
    ],
)
def test_an_empty_recommendations_page_says_why(title: str, status: str) -> None:
    html = f'<span class="mq-state__title">{title}</span>'
    assert parse_recommendations_page(html) == Recommendation(status)


def test_a_page_with_neither_items_nor_a_reason_is_refused() -> None:
    with pytest.raises(ValueError):
        parse_recommendations_page("<main></main>")


def test_an_item_without_its_stock_line_is_refused_not_paired_with_the_next() -> None:
    first = '<article data-recommendation="245"><dt>Store</dt><dd>Centro</dd></article>'
    html = first + _ITEM_HTML.format(product=120, units="1 unit")
    with pytest.raises(ValueError, match="2 recommendation items, 1 with"):
        parse_recommendations_page(html)


def test_the_services_segment_statuses_are_the_webs_no_segment() -> None:
    for status in ("no_open_assignment", "unassigned_result"):
        assert recommendation_from_service({"status": status, "items": []}) == (
            Recommendation("no_segment")
        )
    body = {
        "status": "recommended",
        "items": [
            {"product_id": 245, "quantity_on_hand": 8},
            {"product_id": 120, "quantity_on_hand": 14},
        ],
    }
    assert recommendation_from_service(body).products == (245, 120)


def test_recommendations_differ_by_status_then_order_then_stock() -> None:
    web = Recommendation("recommended", ((1, 5), (2, 3)))
    assert diff_recommendations("sale", web, web) == []
    assert diff_recommendations("sale", web, Recommendation("none_match")) == [
        RecommendationDifference("sale", "status", "recommended", "none_match")
    ]
    reordered = Recommendation("recommended", ((2, 3), (1, 5)))
    assert diff_recommendations("sale", web, reordered)[0].what == "products"
    restocked = Recommendation("recommended", ((1, 5), (2, 4)))
    assert diff_recommendations("stock", web, restocked) == [
        RecommendationDifference("stock", "product 2 on hand", 3, 4)
    ]
