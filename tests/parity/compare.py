"""Pure parts of the parity harness (F20-07): reading /catalog/stock, diffing
two views of the same stock levels, and writing the results file.

Nothing here touches the network or the database, so it is tested on its own
(tests/parity/test_compare.py). It never imports web/ (ADR-0045): the web is
read as the browser reads it, through its HTML.
"""

from __future__ import annotations

import json
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


@dataclass
class CaseResult:
    case: str
    status: str  # passed | failed | skipped
    detail: str = ""
    differences: list[Difference] = field(default_factory=list)


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
