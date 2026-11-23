"""F29-03 (#888): the transaction path's evidence, around one recorded sale.

Two steps, each writing an evidence directory (docs/evidence/
f29-03-transaction-path.md is the runbook):

    python -m tests.acceptance.f29_03 snapshot --out DIR/before \\
        --store 3 --product 1007 --product 1020 --customer <uuid>

before the sale is recorded on the phone ("Venta Android"), then

    python -m tests.acceptance.f29_03 sale --out DIR/after \\
        --before DIR/before --sale-id 4812 --idempotency-key <key>

after it. The second step reads the sale back from sales-service in JSON and
in XML (the XML validated against contracts/sales/ with xmllint, as Electron
validates it), reads its rows with clients/android/evidence/sale_evidence.sql,
and takes the same snapshot again. "Paridad web" then holds when, for every
line, the portal's /catalog/stock, stock-service's stock level and the
`inventory` row agree, and each fell by the quantity sold; and the
customer's profile page counts one more purchase.

It reads only: every request is a GET except the sign-ins and sign-outs, and
every query runs in a read-only transaction. Environment:

    MOSAIQ_BASE_URL       the gateway's origin (https://<host>, or
                          http://127.0.0.1:<port> for a local stack)
    MOSAIQ_HOST, MOSAIQ_INSECURE_TLS
                          as for tests/negative, for a loopback hop
    MOSAIQ_WEB_URL        the portal's origin (default: MOSAIQ_BASE_URL)
    MOSAIQ_DB_EXEC        the psql prefix (evidence.Database)
    MOSAIQ_ACCEPTANCE_EMAIL / _PASSWORD
                          a service reader of sales and stock (prompted)
    MOSAIQ_WEB_EMAIL / _PASSWORD
                          a portal account holding catalog.read and
                          segment.read (prompted; may be the same account)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from tests.acceptance.evidence import (
    Database,
    Portal,
    Recorder,
    credential,
    edge_from_environment,
    sign_in,
    sign_out,
    versions,
)
from tests.conformance.harness import load_contract
from tests.negative.stack import ROOT, env_bool
from tests.parity.compare import parse_stock_page

SALE_EVIDENCE_SQL = ROOT / "clients/android/evidence/sale_evidence.sql"
SERVICES = ["auth", "users", "stores", "products", "stock", "customers", "sales"]
PROFILE_WINDOW_DAYS = 30

_PURCHASES = re.compile(
    r"Purchase frequency</span>\s*<span class=\"mq-metric__value\">\s*(\d+)"
)
_SPEND = re.compile(
    r"Total spend.*?</span>\s*<span class=\"mq-metric__value\">\s*([0-9,]+\.\d+)",
    re.S,
)


def parse_profile(html: str) -> dict[str, str]:
    """Purchases and spend the profile page shows; a customer with no sales
    in the window shows neither, which is zero."""
    purchases = _PURCHASES.search(html)
    spend = _SPEND.search(html)
    return {
        "purchases": purchases.group(1) if purchases else "0",
        "spend": spend.group(1).replace(",", "") if spend else "0.00",
    }


def stock_on_page(html: str, store_name: str, sku: str) -> int | None:
    return next(
        (q for name, s, q in parse_stock_page(html) if s == sku and name == store_name),
        None,
    )


def xml_validation(document: bytes) -> tuple[bool, str]:
    """xmllint against the XSD the document's namespace names (ADR-0041)."""
    contract = load_contract(ROOT / "contracts/sales")
    namespace = subprocess.run(
        ["xmllint", "--nonet", "--xpath", "namespace-uri(/*)", "-"],
        input=document,
        capture_output=True,
    ).stdout.decode()
    xsd = contract.xsd_for(namespace.strip())
    result = subprocess.run(
        ["xmllint", "--noout", "--nonet", "--schema", str(xsd), "-"],
        input=document,
        capture_output=True,
    )
    detail = (result.stderr or result.stdout).decode().strip()
    return result.returncode == 0, f"{xsd.relative_to(ROOT)}: {detail}"


class Session:
    """The edge, the portal and the database, signed in once per step."""

    def __init__(self, rec: Recorder) -> None:
        self.rec = rec
        self.edge = edge_from_environment()
        self.db = Database.from_environment()
        email = credential("MOSAIQ_ACCEPTANCE_EMAIL", "Service reader email: ", False)
        password = credential(
            "MOSAIQ_ACCEPTANCE_PASSWORD", "Service reader password: ", True
        )
        self.token = sign_in(rec, self.edge, email, password, "service reader")
        rec.facts["service reader"] = email
        web_email = os.environ.get("MOSAIQ_WEB_EMAIL") or email
        web_password = (
            password
            if web_email == email
            else credential("MOSAIQ_WEB_PASSWORD", "Portal password: ", True)
        )
        web_url = os.environ.get("MOSAIQ_WEB_URL") or self.edge.base.geturl()
        self.portal = Portal(web_url, rec, insecure=env_bool("MOSAIQ_INSECURE_TLS"))
        self.portal.sign_in(web_email, web_password)
        rec.facts["portal reader"] = web_email
        rec.facts["portal"] = web_url
        rec.facts["gateway"] = self.edge.base.geturl()

    def close(self) -> None:
        sign_out(self.rec, self.edge, self.token, "service reader")


def snapshot(s: Session, store: int, products: list[int], customer: str | None) -> dict:
    """What the three readers say for each (store, product), and the
    customer's purchases on the portal and in PostgreSQL."""
    rec, db = s.rec, s.db
    taken: dict = {"store_id": store, "lines": {}, "customer": None}
    [[store_name]] = db.rows("SELECT name FROM store WHERE store_id = %s", (store,))
    for product in products:
        [[sku]] = db.rows("SELECT sku FROM product WHERE product_id = %s", (product,))
        path = f"/api/stock/v1/stock-levels/{store}/{product}"
        reply, number = rec.call(s.edge, "GET", path, note="stock level", token=s.token)
        service = reply.json()["quantity_on_hand"] if reply.status == 200 else None
        query = f"/catalog/stock?store={store}&q={sku}"
        _status, html, page = s.portal.get(query, f"portal stock, {sku}")
        sql = (
            "SELECT store_id, product_id, quantity_on_hand, updated_at "
            "FROM inventory WHERE store_id = %s AND product_id = %s"
        )
        rows = db.rows(sql, (store, product))
        anchor = rec.add_rows(
            f"inventory, store {store}, product {product}",
            db.table(sql, (store, product)),
        )
        taken["lines"][str(product)] = {
            "sku": sku,
            "store_name": store_name,
            "web": stock_on_page(html, store_name, sku),
            "service": service,
            "database": int(rows[0][2]) if rows else None,
            "evidence": [rec.link(page), rec.link(number), anchor],
        }
    if customer:
        path = f"/catalog/customers/{customer}/profile?window={PROFILE_WINDOW_DAYS}"
        _status, html, page = s.portal.get(path, "customer profile")
        sql = (
            "SELECT count(*), coalesce(sum(total), 0) FROM transaction "
            "WHERE customer_id = %s"
        )
        [[count, total]] = db.rows(sql, (customer,))
        anchor = rec.add_rows(
            f"transactions of customer {customer}", db.table(sql, (customer,))
        )
        taken["customer"] = {
            "customer_id": customer,
            "web": parse_profile(html),
            "database": {"purchases": count, "spend": total},
            "evidence": [rec.link(page), anchor],
        }
    return taken


def judge_agreement(rec: Recorder, taken: dict, label: str) -> None:
    for product, line in taken["lines"].items():
        rec.check(
            "Paridad web",
            f"{label}: product {product} ({line['sku']}), portal = service = row",
            f"{line['database']} = {line['database']} = {line['database']}",
            f"{line['web']} = {line['service']} = {line['database']}",
            line["web"] == line["service"] == line["database"] is not None,
            line["evidence"],
        )


def run_snapshot(args: argparse.Namespace) -> int:
    rec = Recorder(Path(args.out), "F29-03 snapshot before the sale", "f29-03")
    s = Session(rec)
    try:
        versions(rec, s.edge, SERVICES)
        taken = snapshot(s, args.store, args.product, args.customer)
        judge_agreement(rec, taken, "before")
        (rec.out / "snapshot.json").write_text(json.dumps(taken, indent=2))
    finally:
        s.close()
    return 0 if rec.write() else 1


def run_sale(args: argparse.Namespace) -> int:
    before = json.loads((Path(args.before) / "snapshot.json").read_text())
    # The before step's links, as seen from this step's directory.
    up = os.path.relpath(Path(args.before), Path(args.out))
    for item in [*before["lines"].values(), before.get("customer") or {}]:
        item["evidence"] = [f"{up}/{ref}" for ref in item.get("evidence", [])]
    rec = Recorder(Path(args.out), "F29-03 the sale and the parity after it", "f29-03")
    rec.facts["sale_id"] = args.sale_id
    rec.facts["Idempotency-Key"] = args.idempotency_key
    s = Session(rec)
    try:
        versions(rec, s.edge, SERVICES)
        path = f"/api/sales/v1/sales/{args.sale_id}"
        reply, json_number = rec.call(
            s.edge, "GET", path, note="the sale", token=s.token
        )
        rec.check(
            "Venta Android",
            "the sale reads back in JSON",
            200,
            reply.status,
            links=[rec.link(json_number)],
        )
        if reply.status != 200:
            rec.write()
            return 1
        sale = reply.json()

        xml, xml_number = rec.call(
            s.edge,
            "GET",
            path,
            note="the sale in XML",
            token=s.token,
            accept="application/xml",
        )
        valid, detail = (False, "no XML body")
        if xml.status == 200 and xml.media_type == "application/xml":
            valid, detail = xml_validation(xml.body)
            (rec.out / "sale.xml").write_bytes(xml.body)
        rec.check(
            "Consulta escritorio (protocol)",
            "GET in XML validates against the sales XSD",
            "200 application/xml, valid",
            f"{xml.status} {xml.media_type}, {'valid' if valid else 'invalid'}",
            xml.status == 200 and valid,
            [rec.link(xml_number)],
        )
        rec.facts["xmllint"] = detail

        text = s.db.script(
            SALE_EVIDENCE_SQL,
            {"sale_id": str(args.sale_id), "idempotency_key": args.idempotency_key},
        )
        rows_anchor = rec.add_rows("sale_evidence.sql for the sale", text)
        [[key_matches, total, customer, store]] = s.db.rows(
            "SELECT (source_transaction_id = 'svc-sales:' || lower(%s))::text, "
            "total::text, customer_id::text, store_id::text "
            "FROM transaction WHERE transaction_id = %s",
            (args.idempotency_key, args.sale_id),
        )
        rec.check(
            "Venta Android",
            "PostgreSQL holds the sale under its Idempotency-Key",
            "true",
            key_matches,
            links=[rows_anchor, rec.link(json_number)],
        )
        rec.check(
            "Venta Android",
            "the row's total is the response's total",
            sale["total"],
            total,
            Decimal(sale["total"]) == Decimal(total),
            [rows_anchor, rec.link(json_number)],
        )
        [[captures]] = s.db.rows(
            "SELECT count(*) FROM sales.sale_capture WHERE transaction_id = %s",
            (args.sale_id,),
        )
        rec.check(
            "Venta Android",
            "exactly one capture row",
            "1",
            captures,
            links=[rows_anchor],
        )

        sold = {str(line["product_id"]): line["quantity"] for line in sale["lines"]}
        if str(before["store_id"]) != store or set(sold) - set(before["lines"]):
            rec.check(
                "Paridad web",
                "the before snapshot names the sale's store and products",
                f"store {store}, products {sorted(sold)}",
                f"store {before['store_id']}, products {sorted(before['lines'])}",
                False,
            )
        after = snapshot(s, int(store), [int(p) for p in sold], customer)
        (rec.out / "snapshot.json").write_text(json.dumps(after, indent=2))
        judge_agreement(rec, after, "after")
        for product, quantity in sold.items():
            was = before["lines"].get(product, {})
            now = after["lines"][product]
            for reader in ("web", "service", "database"):
                if was.get(reader) is None or now[reader] is None:
                    continue
                rec.check(
                    "Paridad web",
                    f"product {product}: {reader} fell by the {quantity} sold",
                    was[reader] - quantity,
                    now[reader],
                    links=was.get("evidence", []) + now["evidence"],
                )
        if before.get("customer") and after.get("customer"):
            was, now = before["customer"]["web"], after["customer"]["web"]
            rec.check(
                "Paridad web",
                "the portal's profile counts one more purchase",
                int(was["purchases"]) + 1,
                int(now["purchases"]),
                links=before["customer"]["evidence"] + after["customer"]["evidence"],
            )
            rec.check(
                "Paridad web",
                "the portal's spend grew by the sale's total",
                Decimal(was["spend"]) + Decimal(sale["total"]),
                Decimal(now["spend"]),
                links=after["customer"]["evidence"],
            )
    finally:
        s.close()
    return 0 if rec.write() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.acceptance.f29_03")
    commands = parser.add_subparsers(dest="command", required=True)
    snap = commands.add_parser("snapshot", help="the three readers before the sale")
    snap.add_argument("--out", required=True)
    snap.add_argument("--store", type=int, required=True)
    snap.add_argument("--product", type=int, action="append", required=True)
    snap.add_argument("--customer")
    sale = commands.add_parser("sale", help="the sale, its rows and the parity after")
    sale.add_argument("--out", required=True)
    sale.add_argument("--before", required=True, help="the snapshot step's --out")
    sale.add_argument("--sale-id", type=int, required=True)
    sale.add_argument("--idempotency-key", required=True)
    args = parser.parse_args(argv)
    code = run_snapshot(args) if args.command == "snapshot" else run_sale(args)
    print(Path(args.out) / "summary.md")
    return code


if __name__ == "__main__":
    sys.exit(main())
