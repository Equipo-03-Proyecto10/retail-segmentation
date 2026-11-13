"""Drivers for the two paths of the parity harness (F20-07, F20-08, F20-09):
HTTP and SQL only.

The web path is the browser's: sign in, upload a sales CSV, read /catalog/stock,
submit the administrator's forms, read a customer's segment timeline and the
segment history report, run a segmentation. The service path is a client's:
sign in to auth-service, POST /v1/sales, POST /v1/sale-reversals, GET
/v1/stock-levels/{store_id}/{product_id}, write through a service's contract,
read GET /v1/audit-entries, register a customer and read the two reads of
/v1/segment-assignments. Neither imports web/ or services/ (ADR-0045). Every
SQL statement is parameterized.

Configuration (all optional; a path without its settings is skipped):

    PARITY_WEB_URL            the web system, http://127.0.0.1:5000
    PARITY_WEB_EMAIL/PASSWORD the administrator, who imports CSVs, reads stock,
                              the reports index
                              and, on both paths, makes the audited changes
                              (F20-08: every service write it uses is ADMIN's)
                              and runs a segmentation (RN-05, F20-09)
    PARITY_GATEWAY_URL        the services' gateway, http://127.0.0.1:8080
    PARITY_SERVICE_EMAIL/PASSWORD  a STORE_MANAGER, who records sales,
                              registers a customer and reads segment
                              assignments
    PARITY_DATABASE_URL       the throwaway stack's PostgreSQL, as its owner
    PARITY_PROPAGATION_BOUND_SECONDS  how long a change may take to show (5)
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.cookiejar import CookieJar
from pathlib import Path

from tests.conformance.harness import Client, load_contract
from tests.parity.compare import (
    ASSIGNED,
    NO_OPEN_ASSIGNMENT,
    UNASSIGNED_RESULT,
    Assignment,
    Pair,
    assignment_from_service,
    parse_customer_current,
    parse_propagation_bound,
    parse_report_open_members,
    parse_reports_revenue,
    parse_stock_page,
)

ROOT = Path(__file__).resolve().parents[2]
_CSRF = re.compile(r'name="csrf_token"\s+value="([^"]+)"')
_WINDOW = re.compile(r'name="window"\s+value="(\d+)"')
RUN_RECORDED_CONTRACT = ROOT / "contracts/streams/segments.run_recorded.v1.md"
MAX_RATE_LIMIT_RETRIES = 3
MAX_RETRY_AFTER_SECONDS = 60


class ParityClient(Client):
    """Gateway client whose reads respect the services' rate-limit window."""

    def __init__(
        self,
        base_url,
        contract,
        *,
        sleep=time.sleep,
        transport=None,
        rate_limit_retries=MAX_RATE_LIMIT_RETRIES,
    ) -> None:
        super().__init__(base_url, contract)
        self._sleep = sleep
        self._transport = transport
        self._rate_limit_retries = rate_limit_retries

    def call(self, method: str, path: str, **kwargs):
        for retry in range(self._rate_limit_retries + 1):
            if self._transport is None:
                reply = super().call(method, path, **kwargs)
            else:
                reply = self._transport(method, path, **kwargs)
            if reply.status != 429 or method.upper() != "GET":
                return reply
            if retry == self._rate_limit_retries:
                raise AssertionError(
                    f"{method} {path}: still rate limited after "
                    f"{self._rate_limit_retries} retries"
                )
            retry_after = reply.headers.get("Retry-After")
            try:
                wait = int(retry_after)
            except (TypeError, ValueError) as error:
                raise AssertionError(
                    f"{method} {path}: rate limit response has invalid "
                    f"Retry-After {retry_after!r}"
                ) from error
            self._sleep(min(MAX_RETRY_AFTER_SECONDS, max(0, wait)))

        raise AssertionError("unreachable")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def propagation_bound() -> float:
    return float(env("PARITY_PROPAGATION_BOUND_SECONDS", "5"))


def run_recorded_bound() -> float:
    """Seconds segments-service may take to reflect a run (F20-09): the
    maximum its `segments` group is contracted to take, from the outbox commit
    to the acknowledgement, in the stream's `## Propagation bound` table."""

    _, maximum = parse_propagation_bound(RUN_RECORDED_CONTRACT.read_text(), "segments")
    return maximum / 1000


def poll(read, expected, bound: float | None = None):
    """Read until the value equals `expected` or the bound passes; the last
    value is returned either way, so a difference is printed, not hidden."""

    deadline = time.monotonic() + (propagation_bound() if bound is None else bound)
    while True:
        value = read()
        if value == expected or time.monotonic() >= deadline:
            return value
        time.sleep(0.25)


class WebPath:
    def __init__(self, base_url: str, email: str, password: str, db) -> None:
        self.base = base_url.rstrip("/")
        self.db = db
        self._label_codes: dict[str, str] | None = None
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        page = self._get("/login")
        self._post(
            "/login",
            {"email": email, "password": password, "csrf_token": _token(page)},
        )

    def _get(self, path: str) -> str:
        with self.opener.open(self.base + path, timeout=30) as reply:
            return reply.read().decode()

    def _post(self, path: str, fields: dict[str, str], files=None) -> str:
        boundary = uuid.uuid4().hex
        body = b""
        for key, value in fields.items():
            body += (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"'
                f"\r\n\r\n{value}\r\n"
            ).encode()
        for key, (name, content) in (files or {}).items():
            body += (
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; '
                    f'filename="{name}"\r\nContent-Type: text/csv\r\n\r\n'
                ).encode()
                + content
                + b"\r\n"
            )
        body += f"--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            self.base + path,
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Origin": self.base,
            },
        )
        with self.opener.open(request, timeout=60) as reply:
            return reply.read().decode()

    def import_sales(self, rows: list[dict[str, object]]) -> None:
        """One CSV in the contract of ADR-0020, through the upload page."""

        out = io.StringIO()
        writer = csv.DictWriter(
            out,
            fieldnames=[
                "transaction_id",
                "customer_id",
                "store_id",
                "channel_id",
                "occurred_at",
                "product_id",
                "quantity",
                "unit_price",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
        token = _token(self._get("/admin/sales-import/"))
        self._post(
            "/admin/sales-import/",
            {"csrf_token": token},
            files={"file": ("parity.csv", out.getvalue().encode())},
        )

    def stock(self, pairs: list[Pair]) -> dict[Pair, int | None]:
        found: dict[Pair, int | None] = {}
        for store_id, product_id in pairs:
            [[store_name, sku]] = self.db(
                "SELECT s.name, p.sku FROM store AS s, product AS p "
                "WHERE s.store_id = %s AND p.product_id = %s",
                (store_id, product_id),
            )
            query = urllib.parse.urlencode({"store": store_id, "q": sku})
            rows = parse_stock_page(self._get(f"/catalog/stock?{query}"))
            found[(store_id, product_id)] = next(
                (q for name, s, q in rows if s == sku and name == store_name), None
            )
        return found

    def revenue(self) -> tuple[Decimal, int] | None:
        """(revenue, sales) the reports index shows (F21-03): net of reversals."""

        return parse_reports_revenue(self._get("/reports/"))

    def submit(self, form_path: str, fields: dict[str, str], action=None) -> str:
        """Fill the form served at `form_path` and post it to `action` (the
        form's own path by default), as a browser does, CSRF token included."""

        token = _token(self._get(form_path))
        return self._post(action or form_path, {"csrf_token": token} | fields)

    # ---------- segments (F20-09) ----------

    def label_code(self, name: str) -> str:
        """The page shows a label's name; the service answers its code."""

        if self._label_codes is None:
            rows = self.db("SELECT name, label_code FROM segment_label")
            self._label_codes = dict(rows)
            assert len(self._label_codes) == len(rows), "two labels share a name"
        return self._label_codes[name]

    def current_segment(self, customer_id: str) -> Assignment:
        """What /catalog/customers/<id> shows as open, in the service's terms."""

        found = parse_customer_current(self._get(f"/catalog/customers/{customer_id}"))
        if found is None:
            return Assignment(NO_OPEN_ASSIGNMENT)
        run_id, name, (r, f, m) = found
        if name is None:
            return Assignment(UNASSIGNED_RESULT, None, r, f, m, run_id)
        return Assignment(ASSIGNED, self.label_code(name), r, f, m, run_id)

    def label_members(self, label_code: str) -> set[str]:
        """Every customer the segment history report lists as still open under
        the label, over all its pages."""

        members: set[str] = set()
        page = 1
        while True:
            query = urllib.parse.urlencode({"label": label_code, "page": page})
            found, shown, count = parse_report_open_members(
                self._get(f"/segment-history-report/?{query}")
            )
            members |= found
            if shown >= count:
                return members
            page = shown + 1

    def run_segmentation(self) -> None:
        """One RFM run, confirmed, over the form's default window."""

        page = self._get("/segment-run/")
        window = _WINDOW.search(page)
        assert window, "the run form carries no window"
        # A refused run answers 400, which urllib raises.
        self._post(
            "/segment-run/",
            {
                "csrf_token": _token(page),
                "method": "RFM_RULES",
                "window": window.group(1),
                "confirm": "yes",
            },
        )


def _token(page: str) -> str:
    match = _CSRF.search(page)
    if not match:
        raise AssertionError("the page carries no csrf_token field")
    return match.group(1)


def sign_in(gateway_url: str, email: str, password: str) -> str:
    """An access token from auth-service, the only signer (ADR-0036)."""

    auth = ParityClient(gateway_url, load_contract(ROOT / "contracts/auth"))
    reply = auth.call(
        "POST",
        "/api/auth/v1/sessions",
        content_type="application/json",
        body=json.dumps(
            {"email": email, "password": password, "device_label": "parity"}
        ).encode(),
    )
    assert reply.status == 201, reply.body
    return reply.json()["access_token"]


class ServicePath:
    def __init__(self, gateway_url: str, email: str, password: str) -> None:
        self.sales = ParityClient(gateway_url, load_contract(ROOT / "contracts/sales"))
        self.stock_client = ParityClient(
            gateway_url, load_contract(ROOT / "contracts/stock")
        )
        self.token = sign_in(gateway_url, email, password)

    def record_sale(self, sale: dict, key: str | None = None):
        return self.sales.call(
            "POST",
            "/api/sales/v1/sales",
            token=self.token,
            content_type="application/json",
            body=json.dumps(sale).encode(),
            headers={"Idempotency-Key": key or str(uuid.uuid4())},
        )

    def record_reversal(self, reversal: dict, key: str | None = None):
        """POST /v1/sale-reversals: a cancellation or a return (F21-03)."""

        return self.sales.call(
            "POST",
            "/api/sales/v1/sale-reversals",
            token=self.token,
            content_type="application/json",
            body=json.dumps(reversal).encode(),
            headers={"Idempotency-Key": key or str(uuid.uuid4())},
        )

    def stock(self, pairs: list[Pair]) -> dict[Pair, int | None]:
        found: dict[Pair, int | None] = {}
        for store_id, product_id in pairs:
            reply = self.stock_client.call(
                "GET",
                f"/api/stock/v1/stock-levels/{store_id}/{product_id}",
                token=self.token,
            )
            found[(store_id, product_id)] = (
                reply.json()["quantity_on_hand"] if reply.status == 200 else None
            )
        return found


class AuditPath:
    """The administrator as a client of the writing services and of
    audit-trail (F20-08)."""

    WRITERS = ("categories", "products", "users", "campaigns")

    def __init__(self, gateway_url: str, email: str, password: str) -> None:
        self.clients = {
            name: ParityClient(gateway_url, load_contract(ROOT / "contracts" / name))
            for name in (*self.WRITERS, "audit-trail")
        }
        self.token = sign_in(gateway_url, email, password)

    def call(self, service: str, method: str, path: str, body=None):
        """One request to `service`, `path` relative to its /v1."""

        return self.clients[service].call(
            method,
            f"/api/{service}/v1/{path}",
            token=self.token,
            content_type=None if body is None else "application/json",
            body=None if body is None else json.dumps(body).encode(),
        )

    def audit_entries(self, entity: str, entity_pk: str) -> dict:
        query = urllib.parse.urlencode(
            {"entity": entity, "entity_pk": entity_pk, "size": 100}
        )
        reply = self.call("audit-trail", "GET", f"audit-entries?{query}")
        assert (
            reply.status == 200
        ), f"{entity} {entity_pk}: {reply.status} {reply.body!r}"
        return reply.json()


class SegmentsPath:
    """segments-service as a client reads it (F20-09)."""

    def __init__(self, gateway_url: str, email: str, password: str) -> None:
        self.client = ParityClient(
            gateway_url, load_contract(ROOT / "contracts/segments")
        )
        self.customers = ParityClient(
            gateway_url, load_contract(ROOT / "contracts/customers")
        )
        self.token = sign_in(gateway_url, email, password)

    def _get(self, path: str, query: dict) -> dict:
        reply = self.client.call(
            "GET",
            f"/api/segments/v1/{path}?{urllib.parse.urlencode(query)}",
            token=self.token,
        )
        assert reply.status == 200, f"{path} {query}: {reply.status} {reply.body!r}"
        return reply.json()

    def register_customer(self, name: str, channel_id: int) -> str:
        """A customer as a client registers one, POST /v1/customers, so the
        customers boundary records it as it records any other."""

        reply = self.customers.call(
            "POST",
            "/api/customers/v1/customers",
            token=self.token,
            content_type="application/json",
            body=json.dumps(
                # email and phone are required and nullable; the server dates
                # the registration.
                {
                    "name": name,
                    "email": None,
                    "phone": None,
                    "registration_channel_id": channel_id,
                }
            ).encode(),
        )
        assert reply.status == 201, f"{reply.status} {reply.body!r}"
        return reply.json()["customer_id"]

    def current_segment(self, customer_id: str) -> Assignment:
        return assignment_from_service(
            self._get("segment-assignments/current", {"customer_id": customer_id})
        )

    def label_members(self, label_code: str) -> set[str]:
        """Every customer with an open row under the label, over all pages."""

        members: set[str] = set()
        page = 1
        while True:
            body = self._get(
                "segment-assignments",
                {"label_code": label_code, "open": "true", "page": page, "size": 100},
            )
            members |= {a["customer_id"] for a in body["assignments"]}
            info = body["page_info"]
            if page * info["size"] >= info["total"]:
                return members
            page += 1


def occurred_at() -> datetime:
    return datetime.now(UTC) - timedelta(minutes=1)
