"""Pure parts of the parity harness (F20-07, F20-09): reading the web's pages,
diffing two views of the same stock levels or segment assignments, and
writing the results file.

Nothing here touches the network or the database, so it is tested on its own
(tests/parity/test_compare.py). It never imports web/ (ADR-0045): the web is
read as the browser reads it, through its HTML.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path

Pair = tuple[int, int]  # (store_id, product_id)


@dataclass(frozen=True)
class Difference:
    store_id: int
    product_id: int
    what: str
    web: object
    service: object

    def __str__(self) -> str:
        return (
            f"store {self.store_id} product {self.product_id}: {self.what}: "
            f"web={self.web!r} service={self.service!r}"
        )


@dataclass(frozen=True)
class SegmentDifference:
    """One way the web and segments-service disagree about a customer or a
    label (F20-09). `subject` is the customer id or the label code."""

    subject: str
    what: str
    web: object
    service: object

    def __str__(self) -> str:
        return f"{self.subject}: {self.what}: web={self.web!r} service={self.service!r}"


@dataclass
class CaseResult:
    case: str
    status: str  # passed | failed | skipped
    detail: str = ""
    differences: list[Difference | SegmentDifference] = field(default_factory=list)


class _StockTable(HTMLParser):
    """Collects the rows of catalog/stock.html: store, SKU, name, on hand."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs) -> None:
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []

    def handle_data(self, data) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag) -> None:
        if tag == "td" and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def parse_stock_page(html: str) -> list[tuple[str, str, int]]:
    """(store name, SKU, quantity on hand) for each row of the page."""

    parser = _StockTable()
    parser.feed(html)
    rows = []
    for cells in parser.rows:
        if len(cells) < 4:
            continue
        # The cell may carry a "Low" pill after the number.
        rows.append((cells[0], cells[1], int(cells[3].split()[0])))
    return rows


def diff_levels(
    web: dict[Pair, int | None], service: dict[Pair, int | None]
) -> list[Difference]:
    """Every difference between the two readers, over the union of pairs."""

    found = []
    for store_id, product_id in sorted(set(web) | set(service)):
        pair = (store_id, product_id)
        w, s = web.get(pair), service.get(pair)
        if w is None or s is None:
            if w != s:
                found.append(
                    Difference(store_id, product_id, "missing on one side", w, s)
                )
        elif w != s:
            found.append(Difference(store_id, product_id, "quantity_on_hand", w, s))
    return found


# ---------- segments (F20-09) ----------

ASSIGNED = "assigned"
UNASSIGNED_RESULT = "unassigned_result"
NO_OPEN_ASSIGNMENT = "no_open_assignment"


@dataclass(frozen=True)
class Assignment:
    """A customer's current state as GET /v1/segment-assignments/current
    states it. `since` is left out: the web prints only the day."""

    state: str
    label_code: str | None = None
    r_score: int | None = None
    f_score: int | None = None
    m_score: int | None = None
    run_id: int | None = None


@dataclass
class Cell:
    text: str = ""
    pills: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    is_header: bool = False


@dataclass
class Table:
    headers: list[str] = field(default_factory=list)
    rows: list[list[Cell]] = field(default_factory=list)


class _Tables(HTMLParser):
    """Every table of a page, nested ones apart: a cell's text, the status
    pills inside it, and the links it holds. The pill's dot is not text."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[Table] = []
        # Per open table: the table, the row being read, the cell being read.
        self._open: list[tuple[Table, list[Cell] | None, Cell | None]] = []
        self._pill: list[str] | None = None
        self._pill_depth = 0

    def _top(self):
        return self._open[-1] if self._open else None

    def handle_starttag(self, tag, attrs) -> None:
        attrs = dict(attrs)
        if tag == "table":
            table = Table()
            self.tables.append(table)
            self._open.append((table, None, None))
            return
        if not self._open:
            return
        table, row, cell = self._open[-1]
        if tag == "tr":
            self._open[-1] = (table, [], None)
        elif tag in ("td", "th") and row is not None:
            self._open[-1] = (table, row, Cell(is_header=tag == "th"))
        elif cell is not None:
            classes = (attrs.get("class") or "").split()
            if tag == "a" and attrs.get("href"):
                cell.links.append(attrs["href"])
            if tag == "span":
                if self._pill is not None:
                    self._pill_depth += 1
                elif "mq-pill" in classes:
                    self._pill, self._pill_depth = [], 0

    def handle_data(self, data) -> None:
        top = self._top()
        if top is None or top[2] is None:
            return
        top[2].text += data
        if self._pill is not None:
            self._pill.append(data)

    def handle_endtag(self, tag) -> None:
        if not self._open:
            return
        table, row, cell = self._open[-1]
        if tag == "table":
            self._open.pop()
        elif tag == "span" and self._pill is not None and cell is not None:
            if self._pill_depth:
                self._pill_depth -= 1
            else:
                cell.pills.append(" ".join("".join(self._pill).split()))
                self._pill = None
        elif tag in ("td", "th") and cell is not None and row is not None:
            cell.text = " ".join(cell.text.split())
            row.append(cell)
            self._open[-1] = (table, row, None)
        elif tag == "tr" and row is not None:
            if row and all(c.is_header for c in row):
                table.headers.extend(c.text for c in row)
            elif row:
                table.rows.append(row)
            self._open[-1] = (table, None, None)


def parse_tables(html: str) -> list[Table]:
    parser = _Tables()
    parser.feed(html)
    return parser.tables


_RUN = re.compile(r"#(\d+)")
_CUSTOMER_LINK = re.compile(r"/catalog/customers/([0-9a-f-]{36})")
_PAGES = re.compile(r"Page (\d+) of (\d+)")
_TIMELINE = ["Run", "Label", "Valid from", "Valid to", "R", "F", "M"]


def _label(cell: Cell) -> str | None:
    """The cell's label pill, None for Unassigned; the Synthetic pill is a
    mark on the row, not a label."""

    names = [p for p in cell.pills if p != "Synthetic"]
    if len(names) != 1:
        raise ValueError(f"expected one label pill, found {cell.pills!r}")
    return None if names[0] == "Unassigned" else names[0]


def _score(text: str) -> int | None:
    return None if text in ("", "—") else int(text)


def parse_customer_current(html: str) -> tuple[int, str | None, tuple] | None:
    """The open row of /catalog/customers/<id>'s segment timeline: (run_id,
    label name or None, (R, F, M)). None when the customer has no open row,
    which is also what the page says when no run has scored them."""

    for table in parse_tables(html):
        if table.headers[: len(_TIMELINE)] != _TIMELINE:
            continue
        open_rows = [row for row in table.rows if row[3].text == "Open"]
        if len(open_rows) > 1:
            raise ValueError(f"{len(open_rows)} open rows in the timeline")
        if not open_rows:
            return None
        row = open_rows[0]
        run = _RUN.search(row[0].text)
        if run is None:
            raise ValueError(f"no run id in {row[0].text!r}")
        scores = tuple(_score(row[i].text) for i in (4, 5, 6))
        return int(run.group(1)), _label(row[1]), scores
    return None


def parse_report_open_members(html: str) -> tuple[set[str], int, int]:
    """The customers holding an open row on one page of
    /segment-history-report/, and that page's (page, page count)."""

    members: set[str] = set()
    for table in parse_tables(html):
        if table.headers[:5] != ["Customer", "Run", "Label", "Valid from", "Valid to"]:
            continue
        for row in table.rows:
            if row[4].text != "still open":
                continue
            [link] = [m for h in row[0].links if (m := _CUSTOMER_LINK.search(h))]
            members.add(link.group(1))
    pages = _PAGES.search(" ".join(html.split()))
    page, count = (int(pages.group(1)), int(pages.group(2))) if pages else (1, 1)
    return members, page, count


def assignment_from_service(body: dict) -> Assignment:
    """The comparable part of a CurrentAssignment answer."""

    return Assignment(
        body["state"],
        body.get("label_code"),
        body.get("r_score"),
        body.get("f_score"),
        body.get("m_score"),
        body.get("run_id"),
    )


_FIELDS = ("state", "label_code", "r_score", "f_score", "m_score", "run_id")


def diff_assignments(
    web: dict[str, Assignment], service: dict[str, Assignment]
) -> list[SegmentDifference]:
    """Every field that differs, per customer, over the union of customers."""

    found = []
    for customer in sorted(set(web) | set(service)):
        w, s = web.get(customer), service.get(customer)
        if w is None or s is None:
            found.append(SegmentDifference(customer, "missing on one side", w, s))
            continue
        for name in _FIELDS:
            if getattr(w, name) != getattr(s, name):
                found.append(
                    SegmentDifference(
                        customer, name, getattr(w, name), getattr(s, name)
                    )
                )
    return found


def diff_members(
    label_code: str, web: set[str], service: set[str]
) -> list[SegmentDifference]:
    """The customers one side lists under a label and the other does not."""

    return [
        SegmentDifference(label_code, "member on the web only", c, None)
        for c in sorted(web - service)
    ] + [
        SegmentDifference(label_code, "member on the service only", None, c)
        for c in sorted(service - web)
    ]


def write_results(results: list[CaseResult], path: Path) -> None:
    """The file the consolidated report (F20-03) reads: one entry per case."""

    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "suite": "parity",
        "passed": sum(r.status == "passed" for r in results),
        "failed": sum(r.status == "failed" for r in results),
        "skipped": sum(r.status == "skipped" for r in results),
        "cases": [asdict(r) for r in results],
    }
    path.write_text(json.dumps(document, indent=2, default=str) + "\n")
