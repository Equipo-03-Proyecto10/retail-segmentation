"""Drivers for the two paths of the parity harness (F20-07, F20-08): HTTP and
SQL only.

The web path is the browser's: sign in, upload a sales CSV, read /catalog/stock,
submit the administrator's forms. The service path is a client's: sign in to
auth-service, POST /v1/sales, GET /v1/stock-levels/{store_id}/{product_id},
write through a service's contract and read GET /v1/audit-entries. Neither
imports web/ or services/ (ADR-0045). Every SQL statement is parameterized.

Configuration (all optional; a path without its settings is skipped):

    PARITY_WEB_URL            the web system, http://127.0.0.1:5000
    PARITY_WEB_EMAIL/PASSWORD the administrator, who imports CSVs, reads stock
                              and, on both paths, makes the audited changes
                              (F20-08: every service write it uses is ADMIN's)
    PARITY_GATEWAY_URL        the services' gateway, http://127.0.0.1:8080
    PARITY_SERVICE_EMAIL/PASSWORD  a STORE_MANAGER, who records sales
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
from http.cookiejar import CookieJar
from pathlib import Path

from tests.conformance.harness import Client, load_contract
from tests.parity.compare import Pair, parse_stock_page

ROOT = Path(__file__).resolve().parents[2]
_CSRF = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def propagation_bound() -> float:
    return float(env("PARITY_PROPAGATION_BOUND_SECONDS", "5"))


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

    def submit(self, form_path: str, fields: dict[str, str], action=None) -> str:
        """Fill the form served at `form_path` and post it to `action` (the
        form's own path by default), as a browser does, CSRF token included."""

        token = _token(self._get(form_path))
        return self._post(action or form_path, {"csrf_token": token} | fields)


def _token(page: str) -> str:
    match = _CSRF.search(page)
    if not match:
        raise AssertionError("the page carries no csrf_token field")
    return match.group(1)


def sign_in(gateway_url: str, email: str, password: str) -> str:
    """An access token from auth-service, the only signer (ADR-0036)."""

    auth = Client(gateway_url, load_contract(ROOT / "contracts/auth"))
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
        self.sales = Client(gateway_url, load_contract(ROOT / "contracts/sales"))
        self.stock_client = Client(gateway_url, load_contract(ROOT / "contracts/stock"))
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
            name: Client(gateway_url, load_contract(ROOT / "contracts" / name))
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


def occurred_at() -> datetime:
    return datetime.now(UTC) - timedelta(minutes=1)
