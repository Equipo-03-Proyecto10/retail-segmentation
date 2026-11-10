"""F20-01 (#759): the masking and the queries behind the Android sale evidence.

clients/android/evidence/sale_evidence.py turns the live run's HTTP exchanges
into Markdown with every token masked, and sale_evidence.sql reads the rows
with bind parameters only. Neither talks to the instance here: the refusals
run against a local stand-in, and the SQL against the test database.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "clients/android/evidence"
SQL = EVIDENCE / "sale_evidence.sql"

_spec = importlib.util.spec_from_file_location(
    "sale_evidence", EVIDENCE / "sale_evidence.py"
)
assert _spec and _spec.loader
sale_evidence = importlib.util.module_from_spec(_spec)
# Registered first: its dataclasses look their module up while being built.
sys.modules["sale_evidence"] = sale_evidence
_spec.loader.exec_module(sale_evidence)

# Token-shaped, never a real signature.
ACCESS = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJzdG9yZS1tYW5hZ2VyIn0.c2lnbmF0dXJl"
REFRESH = "r3fr35h-0paque-value"


def _entry(method, url, *, req_headers=(), req_body=None, status, resp_body=""):
    entry = {
        "request": {
            "method": method,
            "url": url,
            "httpVersion": "HTTP/2.0",
            "headers": [{"name": n, "value": v} for n, v in req_headers],
        },
        "response": {
            "status": status,
            "httpVersion": "HTTP/2.0",
            "headers": [{"name": "x-correlation-id", "value": "c0ffee"}],
            "content": {"mimeType": "application/json", "text": resp_body},
        },
    }
    if req_body is not None:
        entry["request"]["postData"] = {
            "mimeType": "application/json",
            "text": json.dumps(req_body),
        }
    return entry


def _har() -> dict:
    sale = {
        "customer_id": "00000000-0000-0000-0000-000000000001",
        "store_id": 1,
        "channel_id": 3,
        "occurred_at": "2026-11-10T10:00:00-06:00",
        "lines": [{"product_id": 1, "quantity": 1, "unit_price": "18.75"}],
    }
    recorded = json.dumps({"sale_id": 301, **sale, "total": "18.75"})
    return {
        "log": {
            "entries": [
                _entry(
                    "POST",
                    "https://mosaiq.maxthecoder.online/api/auth/v1/sessions",
                    req_body={
                        "email": "manager@example.com",
                        "password": "not-shown",
                        "device_label": "Pixel",
                    },
                    status=201,
                    resp_body=json.dumps(
                        {"access_token": ACCESS, "refresh_token": REFRESH}
                    ),
                ),
                _entry(
                    "GET",
                    "https://mosaiq.maxthecoder.online/static/app.css",
                    status=200,
                ),
                _entry(
                    "POST",
                    "https://mosaiq.maxthecoder.online/api/sales/v1/sales",
                    req_headers=[
                        ("authorization", f"Bearer {ACCESS}"),
                        ("idempotency-key", "0f8fad5b-d9cb-469f-a165-70867728950e"),
                    ],
                    req_body=sale,
                    status=201,
                    resp_body=recorded,
                ),
            ]
        }
    }


def test_the_har_is_rendered_with_every_token_masked() -> None:
    text = sale_evidence.render(sale_evidence.exchanges_from_har(_har()))

    assert ACCESS not in text and REFRESH not in text and "not-shown" not in text
    mark = sale_evidence.fingerprint(ACCESS)
    # The token the sign-in returned is the one the sale carried.
    assert f'"access_token": "{mark}"' in text
    assert f"authorization: Bearer {mark}" in text
    assert "### 2. `POST /api/sales/v1/sales`" in text
    assert "idempotency-key: 0f8fad5b-d9cb-469f-a165-70867728950e" in text
    assert "x-correlation-id: c0ffee" in text
    assert '"unit_price": "18.75"' in text
    assert "app.css" not in text


def test_the_path_filter_keeps_only_the_named_exchanges() -> None:
    kept = sale_evidence.exchanges_from_har(_har(), ["/api/sales/v1/sales"])

    assert [e.request.start_line for e in kept] == ["POST /api/sales/v1/sales HTTP/2.0"]


def test_a_base64_response_body_is_decoded() -> None:
    har = _har()
    content = har["log"]["entries"][2]["response"]["content"]
    content["text"] = base64.b64encode(content["text"].encode()).decode()
    content["encoding"] = "base64"

    text = sale_evidence.render(sale_evidence.exchanges_from_har(har))

    assert '"sale_id": 301' in text


def test_output_that_still_holds_a_token_is_refused() -> None:
    har = _har()
    har["log"]["entries"][2]["request"]["headers"].append(
        {"name": "x-forwarded-token", "value": ACCESS}
    )

    with pytest.raises(sale_evidence.UnmaskedTokenError):
        sale_evidence.render(sale_evidence.exchanges_from_har(har))


class _StandIn(BaseHTTPRequestHandler):
    """Answers like the gateway does for the refusal calls."""

    forbidden_code = "forbidden_role"

    def _send(self, status: int, body: dict | None = None) -> None:
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        correlation = self.headers.get("X-Correlation-ID") or ""
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", correlation):
            correlation = str(uuid.uuid4())
        self.send_header("X-Correlation-ID", correlation)
        self.end_headers()
        self.wfile.write(raw)

    def _read(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_POST(self) -> None:  # noqa: N802
        body = self._read()
        if self.path == "/api/auth/v1/sessions":
            assert body["device_label"] == sale_evidence.DEVICE_LABEL
            self._send(201, {"access_token": ACCESS, "refresh_token": REFRESH})
        elif self.headers.get("Authorization") is None:
            self._send(401, {"code": "invalid_token"})
        else:
            self._send(500, {"code": "unexpected"})

    def do_GET(self) -> None:  # noqa: N802
        assert self.headers["Authorization"] == f"Bearer {ACCESS}"
        self._send(403, {"code": type(self).forbidden_code})

    def do_DELETE(self) -> None:  # noqa: N802
        self._send(204)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def stand_in():
    server = HTTPServer(("127.0.0.1", 0), _StandIn)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    _StandIn.forbidden_code = "forbidden_role"


def test_the_refusals_hold_and_print_no_token(stand_in: str) -> None:
    outcome = sale_evidence.refusals(stand_in, "manager@example.com", "secret")
    text = sale_evidence.render(outcome.exchanges)

    assert outcome.held
    assert "expected 403 forbidden_role, got 403 forbidden_role (held)" in "".join(
        outcome.summary
    )
    assert "without a token: expected 401 invalid_token" in "".join(outcome.summary)
    assert ACCESS not in text and REFRESH not in text and "secret" not in text
    assert [e.request.start_line for e in outcome.exchanges] == [
        "POST /api/auth/v1/sessions",
        "GET /api/users/v1/users",
        "POST /api/sales/v1/sales",
        "DELETE /api/auth/v1/sessions/current",
    ]
    no_token = outcome.exchanges[2].request.headers
    assert not any(name == "Authorization" for name, _ in no_token)


def test_a_wrong_refusal_is_reported(stand_in: str) -> None:
    _StandIn.forbidden_code = "something_else"

    outcome = sale_evidence.refusals(stand_in, "manager@example.com", "secret")

    assert not outcome.held
    assert any("DID NOT HOLD" in line for line in outcome.summary)


def test_the_refusals_default_to_the_local_gateway() -> None:
    assert sale_evidence.DEFAULT_BASE_URL == "http://127.0.0.1:8080"


def _statements(sql: str) -> list[str]:
    """The SQL text of the file: no comments, no psql meta-commands."""

    kept = [
        line for line in sql.splitlines() if not line.lstrip().startswith(("--", "\\"))
    ]
    return [s for s in "\n".join(kept).split("\n\n") if s.strip()]


def test_every_query_takes_its_values_as_bind_parameters() -> None:
    sql = SQL.read_text(encoding="utf-8")
    statements = [s for s in _statements(sql) if "SELECT" in s]

    assert len(statements) == 7
    for statement in statements:
        # No psql interpolation (:name, :'name') reaches the server.
        assert not re.search(r"(?<![:\w]):['\"]?[A-Za-z_]", statement), statement
    assert sql.count("\\bind ") == len(statements)
    assert "BEGIN READ ONLY;" in sql and sql.rstrip().endswith("ROLLBACK;")


@pytest.fixture
def psql() -> str:
    """The psql client; only it is needed, not a scratch cluster."""
    path = shutil.which("psql")
    if path is None:
        if os.environ.get("MOSAIQ_REQUIRE_POSTGRES") == "1":
            pytest.fail("psql is missing, and MOSAIQ_REQUIRE_POSTGRES forbids skipping")
        pytest.skip("psql is missing; this test needs a real PostgreSQL")
    return path


def _run_sql(url: str, psql: str, **variables: str) -> subprocess.CompletedProcess:
    command = [psql, url, "-X", "-v", "ON_ERROR_STOP=1"]
    for name, value in variables.items():
        command += ["-v", f"{name}={value}"]
    command += ["-f", str(SQL)]
    return subprocess.run(command, capture_output=True, text=True)


@pytest.mark.postgres
def test_the_queries_run_read_only_on_the_three_scripts(
    owner_url: str, psql: str
) -> None:
    before = _run_sql(
        owner_url,
        psql,
        email="USER3@mosaiq-demo.com",
        store_id="1",
        product_ids="{1,2}",
    )
    assert before.returncode == 0, before.stderr
    assert "STORE_MANAGER" in before.stdout
    assert before.stdout.count("(1 row)") == 1  # the account
    assert before.stdout.count("(2 rows)") == 1  # both inventory rows

    # A seeded sale: it has lines, but no capture row and no service key.
    after = _run_sql(owner_url, psql, sale_id="1", idempotency_key="not-a-key")
    assert after.returncode == 0, after.stderr
    assert "SEEDED" in after.stdout
    assert re.search(r"\|\s+f\s+\|", after.stdout)  # key_matches is false
    assert "(0 rows)" in after.stdout  # sale_capture

    hostile = _run_sql(owner_url, psql, email="x' OR '1'='1")
    assert hostile.returncode == 0, hostile.stderr
    assert "(0 rows)" in hostile.stdout
