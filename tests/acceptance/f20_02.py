"""F20-02 (#760): the desktop client's sale read and draft campaign, checked.

The desktop client makes the calls (clients/desktop, `npm run evidence:f20-02`,
through its own main-process code); this collector checks what it recorded,
trusting none of the client's own conclusions:

    python -m tests.acceptance.f20_02 --electron DIR/electron --out DIR/checked

- each XML body the client sent or received is validated again with `xmllint`
  against the committed XSD: `contracts/sales/sales.xsd`,
  `contracts/campaigns/campaigns.xsd`, and `contracts/common/v1/common.xsd`
  for the 403 envelope;
- each account's role is checked against the operation's `x-mosaiq-roles`;
- the sale's `transaction` row and the new `campaign` row are read with bind
  parameters in a read-only transaction, and compared with the XML;
- the versions: the checkout's commit, each service's contract as served, and
  the desktop build the client recorded.

docs/evidence/f20-02-electron-sale-campaign.md is the runbook. Environment:

    MOSAIQ_BASE_URL       the gateway's origin, for the served contract versions
    MOSAIQ_HOST, MOSAIQ_INSECURE_TLS
                          as for tests/negative
    MOSAIQ_DB_EXEC        the psql prefix (evidence.Database)

It needs no account: the only requests it makes are the unauthenticated
`GET /api/<name>/openapi.yaml` of `versions`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from tests.acceptance.evidence import (
    JWT,
    Database,
    Recorder,
    edge_from_environment,
    versions,
)
from tests.negative.stack import ROOT

SERVICES = ["auth", "sales", "campaigns"]
COMMON_XSD = ROOT / "contracts/common/v1/common.xsd"
COMMON_NS = "https://mosaiq.maxthecoder.online/xml/common/v1"
_ELEMENT = "<(?:[\\w-]+:)?{0}>([^<]*)</(?:[\\w-]+:)?{0}>"


def element(xml: str, name: str) -> str | None:
    """The text of the first element of that local name, or None."""
    match = re.search(_ELEMENT.format(re.escape(name)), xml)
    return match.group(1) if match else None


def roles(service: str, method: str, path: str) -> list[str]:
    """An operation's `x-mosaiq-roles`, from the committed contract."""
    import yaml

    document = yaml.safe_load((ROOT / f"contracts/{service}/openapi.yaml").read_text())
    return list(document["paths"][path][method.lower()].get("x-mosaiq-roles", []))


def xsd_for(document: bytes, service: str) -> Path:
    """common.xsd for an `Error`, the service's own XSD otherwise."""
    namespace = subprocess.run(
        ["xmllint", "--nonet", "--xpath", "namespace-uri(/*)", "-"],
        input=document,
        capture_output=True,
    ).stdout.decode()
    if namespace.strip() == COMMON_NS:
        return COMMON_XSD
    return ROOT / f"contracts/{service}/{service}.xsd"


def xmllint(path: Path, service: str) -> tuple[bool, str]:
    """`xmllint --schema` on the exact bytes; the detail names the XSD."""
    document = path.read_bytes()
    xsd = xsd_for(document, service)
    result = subprocess.run(
        ["xmllint", "--noout", "--nonet", "--schema", str(xsd), str(path)],
        capture_output=True,
    )
    detail = (result.stderr or result.stdout).decode().strip()
    detail = detail.replace(str(path.parent) + "/", "")
    return result.returncode == 0, f"{xsd.relative_to(ROOT)}: {detail}"


class Checked:
    """The client's directory, re-read and checked into a Recorder."""

    def __init__(self, electron: Path, rec: Recorder, db: Database) -> None:
        self.dir = electron
        self.rec = rec
        self.db = db
        self.result = json.loads((electron / "result.json").read_text())
        self.exchanges = json.loads((electron / "exchanges.json").read_text())
        for x in self.exchanges:
            # Already masked by the client; the Recorder masks again and refuses tokens.
            rec.add(
                note=f"desktop client, {x['step']} account",
                method=x["method"],
                url=x["url"],
                correlation=x["correlation_id"],
                request_headers=x["request_headers"],
                request_body=x["request_body"],
                status=x["status"],
                response_headers=x["response_headers"],
                response_body=x["response_body"],
            )
        build = self.result.get("build", {})
        for key, value in build.items():
            rec.facts[f"desktop {key}" if not key.startswith("desktop") else key] = (
                value
            )
        rec.facts["desktop base URL"] = self.result["base_url"]
        rec.facts["desktop run"] = (
            f"{self.result['started_at']} – {self.result['finished_at']}"
        )

    def exchange(self, step: str) -> dict | None:
        number = self.result["steps"][step].get("exchange")
        return self.exchanges[number - 1] if number else None

    def validate(
        self, criterion: str, step: str, part: str, service: str, expected_xsd: str
    ) -> tuple[bool, str | None]:
        x = self.exchange(step)
        name = x and x.get(f"{part}_file")
        if not name:
            self.rec.check(criterion, f"the {part} body was recorded", "a file", None)
            return False, None
        valid, detail = xmllint(self.dir / name, service)
        self.rec.check(
            criterion,
            f"xmllint: the {part} validates against {expected_xsd}",
            f"{expected_xsd}: valid",
            detail,
            valid and detail.startswith(expected_xsd),
            [Recorder.link(x["number"])],
        )
        self.rec.facts[f"xmllint, {step} {part}"] = detail
        return valid, (self.dir / name).read_text(encoding="utf-8")

    def role(self, criterion: str, step: str, allowed: list[str], inside: bool) -> None:
        s = self.result["steps"][step]
        self.rec.facts[f"{step} account"] = f"{s['account']} ({s['role']})"
        self.rec.check(
            criterion,
            f"the {step} account's role is {'in' if inside else 'outside'} "
            f"x-mosaiq-roles {allowed}",
            "in" if inside else "outside",
            "in" if s["role"] in allowed else "outside",
        )

    # ---------- the three criteria ----------

    def sale(self) -> None:
        c = "1. Electron reads the sale in XML"
        s = self.result["steps"]["staff"]
        sale_id = self.result["sale_id"]
        self.role(c, "staff", roles("sales", "GET", "/v1/sales/{sale_id}"), True)
        x = self.exchange("staff")
        media = (x or {}).get("response_headers", {}).get("content-type", "")
        self.rec.check(
            c,
            f"GET /api/sales/v1/sales/{sale_id} answers 200 application/xml",
            "200 application/xml",
            f"{s.get('status')} {media}",
            s.get("status") == 200 and "application/xml" in media,
            [Recorder.link(x["number"])] if x else [],
        )
        self.rec.check(
            c,
            "the client accepted it (its own validation against the bundled sales.xsd)",
            True,
            s.get("ok"),
        )
        valid, xml = self.validate(
            c, "staff", "response", "sales", "contracts/sales/sales.xsd"
        )
        if not xml:
            return
        sql = (
            "SELECT transaction_id, customer_id, store_id, channel_id, total, "
            "occurred_at FROM transaction WHERE transaction_id = %s"
        )
        anchor = self.rec.add_rows(
            f"transaction {sale_id}", self.db.table(sql, (sale_id,))
        )
        rows = self.db.rows(
            "SELECT total::text FROM transaction WHERE transaction_id = %s",
            (sale_id,),
        )
        self.rec.check(
            c,
            "the XML's sale_id and total are the transaction row's",
            f"{sale_id}, {rows[0][0] if rows else '(no row)'}",
            f"{element(xml, 'sale_id')}, {element(xml, 'total')}",
            bool(rows)
            and element(xml, "sale_id") == str(sale_id)
            and Decimal(element(xml, "total") or "-1") == Decimal(rows[0][0]),
            [anchor, Recorder.link(x["number"])],
        )
        self.rec.facts["sale X-Correlation-ID"] = s.get("correlation_id")

    def campaign(self) -> int | None:
        c = "2. MARKETING creates a draft campaign in XML"
        s = self.result["steps"]["marketing"]
        self.role(c, "marketing", roles("campaigns", "POST", "/v1/campaigns"), True)
        x = self.exchange("marketing")
        self.rec.check(
            c,
            "POST /api/campaigns/v1/campaigns answers 201",
            201,
            s.get("status"),
            links=[Recorder.link(x["number"])] if x else [],
        )
        self.validate(
            c, "marketing", "request", "campaigns", "contracts/campaigns/campaigns.xsd"
        )
        _valid, xml = self.validate(
            c, "marketing", "response", "campaigns", "contracts/campaigns/campaigns.xsd"
        )
        if not xml or element(xml, "campaign_id") is None:
            return None
        campaign_id = int(element(xml, "campaign_id"))
        self.rec.facts["campaign_id"] = campaign_id
        self.rec.facts["campaign X-Correlation-ID"] = s.get("correlation_id")
        sql = (
            "SELECT campaign_id, name, label_code, starts_on, ends_on, status "
            "FROM campaign WHERE campaign_id = %s"
        )
        anchor = self.rec.add_rows(
            f"campaign {campaign_id}", self.db.table(sql, (campaign_id,))
        )
        rows = self.db.rows(
            "SELECT name, label_code, starts_on::text, ends_on::text, status "
            "FROM campaign WHERE campaign_id = %s",
            (campaign_id,),
        )
        draft = self.result["draft"]
        expected = (
            draft["name"],
            draft["label_code"],
            draft["starts_on"],
            draft["ends_on"],
            "DRAFT",
        )
        got = tuple(
            element(xml, f)
            for f in ("name", "label_code", "starts_on", "ends_on", "status")
        )
        self.rec.check(
            c,
            "the response is the draft that was sent, in DRAFT",
            " | ".join(expected),
            " | ".join(str(v) for v in got),
            got == expected,
            [Recorder.link(x["number"])],
        )
        self.rec.check(
            c,
            "the campaign row is the response's",
            " | ".join(expected),
            " | ".join(rows[0]) if rows else "(no row)",
            bool(rows) and tuple(rows[0]) == expected,
            [anchor, Recorder.link(x["number"])],
        )
        return campaign_id

    def forbidden(self) -> None:
        c = "3. A role outside x-mosaiq-roles tries the same write"
        s = self.result["steps"]["forbidden"]
        allowed = roles("campaigns", "POST", "/v1/campaigns")
        self.role(c, "forbidden", allowed, False)
        self.rec.check(
            c,
            "the window draws no create control for this role (signed-in operations)",
            False,
            s.get("may_call"),
        )
        x = self.exchange("forbidden")
        mine = self.exchange("marketing")
        self.rec.check(
            c,
            "the same request body as the MARKETING step",
            "identical",
            (
                "identical"
                if x and mine and x["request_body"] == mine["request_body"]
                else "differs"
            ),
            links=[Recorder.link(n["number"]) for n in (x, mine) if n],
        )
        self.rec.check(
            c,
            "POST /api/campaigns/v1/campaigns answers 403",
            403,
            s.get("status"),
            links=[Recorder.link(x["number"])] if x else [],
        )
        _valid, xml = self.validate(
            c, "forbidden", "response", "campaigns", "contracts/common/v1/common.xsd"
        )
        if not xml:
            return
        self.rec.check(
            c,
            "the envelope is common:Error forbidden_role, the request's correlation id",
            f"forbidden_role, {s.get('correlation_id')}",
            f"{element(xml, 'code')}, {element(xml, 'correlation_id')}",
            element(xml, "code") == "forbidden_role"
            and element(xml, "correlation_id") == s.get("correlation_id"),
            [Recorder.link(x["number"])],
        )
        self.rec.check(
            c,
            "the error the client hands the screen is that envelope",
            f"forbidden_role, {s.get('correlation_id')}",
            f"{(s.get('error') or {}).get('code')}, "
            f"{(s.get('error') or {}).get('correlation_id')}",
        )
        sql = "SELECT count(*) FROM campaign WHERE name = %s"
        name = self.result["draft"]["name"]
        anchor = self.rec.add_rows(
            "campaigns with the draft's name", self.db.table(sql, (name,))
        )
        [[count]] = self.db.rows(sql, (name,))
        self.rec.check(
            c,
            "the refused write created nothing: one campaign has the draft's name",
            "1",
            count,
            links=[anchor],
        )
        self.rec.facts["forbidden X-Correlation-ID"] = s.get("correlation_id")


def refuse_tokens(electron: Path) -> None:
    """Stop before reading a client directory that holds a token anywhere.

    That includes `bodies/`, the exact bytes kept for xmllint, which the
    client's own check also covers but which this collector does not trust.
    """
    files = [electron / "result.json", electron / "exchanges.json"]
    files += sorted((electron / "bodies").glob("*"))
    for path in files:
        if JWT.search(path.read_text(encoding="utf-8")):
            raise SystemExit(f"{path} holds a token; refusing to read it.")


def run(args: argparse.Namespace) -> int:
    electron = Path(args.electron)
    refuse_tokens(electron)
    rec = Recorder(
        Path(args.out), "F20-02 Electron reads the sale and writes a draft", "f20-02"
    )
    checked = Checked(electron, rec, Database.from_environment())
    checked.sale()
    checked.campaign()
    checked.forbidden()
    versions(rec, edge_from_environment(), SERVICES)
    return 0 if rec.write() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.acceptance.f20_02")
    parser.add_argument(
        "--electron", required=True, help="npm run evidence:f20-02's --out"
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    code = run(args)
    print(Path(args.out) / "summary.md")
    return code


if __name__ == "__main__":
    sys.exit(main())
