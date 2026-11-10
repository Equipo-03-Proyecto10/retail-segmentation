"""Pure parts of the parity harness (F20-07, F20-08): reading /catalog/stock,
diffing two views of the same stock levels, comparing the audit entries two
writers leave, and writing the results file.

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


@dataclass(frozen=True)
class AuditDifference:
    """One way the audit evidence of a change differs between the web and a
    service (F20-08). `change` names the change, such as "category UPDATE"."""

    change: str
    what: str
    web: object
    service: object

    def __str__(self) -> str:
        return f"{self.change}: {self.what}: web={self.web!r} service={self.service!r}"


@dataclass
class CaseResult:
    case: str
    status: str  # passed | failed | skipped
    detail: str = ""
    differences: list[Difference | AuditDifference] = field(default_factory=list)


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


# ---------- audit (F20-08) ----------


@dataclass(frozen=True)
class AuditShape:
    """What an audit_log row says about a change, without its values: which
    table and action, which columns each payload holds, which columns the
    change altered, and whether the acting user is the one recorded."""

    entity: str
    action: str
    before: tuple[str, ...] | None
    after: tuple[str, ...] | None
    changed: tuple[str, ...]
    attributed: bool


def audit_shape(row: dict, actor: str) -> AuditShape:
    """`row` is one audit_log row as a dict, as the database or GET
    /v1/audit-entries gives it; `actor` is the user who made the change."""

    before, after = row.get("data_before"), row.get("data_after")
    changed: tuple[str, ...] = ()
    if before is not None and after is not None:
        changed = tuple(
            sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        )
    return AuditShape(
        row["entity"],
        row["action"],
        None if before is None else tuple(sorted(before)),
        None if after is None else tuple(sorted(after)),
        changed,
        row.get("user_id") is not None and str(row["user_id"]) == str(actor),
    )


def diff_audit(
    change: str, web: list[AuditShape], service: list[AuditShape]
) -> list[AuditDifference]:
    """Both writers must leave the same rows, in the same order, each with the
    acting user attributed (RN-28)."""

    found = []
    if len(web) != len(service):
        found.append(
            AuditDifference(change, "number of audit rows", len(web), len(service))
        )
    # A length difference is reported above; the rows both have are compared.
    for index, (w, s) in enumerate(zip(web, service, strict=False)):
        for name in ("entity", "action", "before", "after", "changed"):
            if getattr(w, name) != getattr(s, name):
                found.append(
                    AuditDifference(change, name, getattr(w, name), getattr(s, name))
                )
        # Equal is not enough: both must name the user who acted.
        if not (w.attributed and s.attributed):
            found.append(
                AuditDifference(
                    change,
                    f"row {index} attributed to the actor",
                    w.attributed,
                    s.attributed,
                )
            )
    if not web and not service:
        found.append(AuditDifference(change, "no audit row on either side", 0, 0))
    return found


_CREDENTIAL_KEYS = ("password", "secret", "token")
_HASH_PREFIXES = ("$argon2", "$2a$", "$2b$", "$2y$", "pbkdf2", "scrypt")


def credentials_in(value, path: str = "") -> list[str]:
    """Where a document carries something that looks like a credential: a key
    naming one, or a string shaped like a password hash (RN-29)."""

    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}" if path else str(key)
            if any(word in str(key).lower() for word in _CREDENTIAL_KEYS):
                found.append(here)
            found.extend(credentials_in(item, here))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(credentials_in(item, f"{path}[{index}]"))
    elif isinstance(value, str) and value.lower().startswith(_HASH_PREFIXES):
        found.append(path)
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
