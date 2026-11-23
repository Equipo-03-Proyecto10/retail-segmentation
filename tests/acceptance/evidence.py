"""What every acceptance collector shares (F29-03 #888, F29-06 #891).

- `Recorder`: each HTTP exchange with its own `X-Correlation-ID`, its
  credentials masked, and each check with its expected and actual value,
  written to an evidence directory.
- `Database`: read-only queries through `MOSAIQ_DB_EXEC`, every value a bind
  parameter (`\\bind`), never text spliced into a statement.
- `Portal`: the web system read as a browser reads it, every page recorded.
- `versions`: the commit run from and each service's contract version.

Credentials come from the environment or a prompt, never from an argument,
and nothing written to the directory holds one: `Recorder.write` refuses to
finish if anything shaped like a token survived masking.
"""

from __future__ import annotations

import getpass
import http.cookiejar
import json
import os
import re
import shlex
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tests.negative.stack import ROOT, Edge, Reply, env_bool, registry_names

AUTH_SESSIONS = "/api/auth/v1/sessions"
MASK_MARK = re.compile(r"<masked #(\d+)>")
MASKED_HEADERS = {"authorization", "cookie", "set-cookie"}
MASKED_FIELDS = {
    "access_token",
    "refresh_token",
    "token",
    "password",
    "current_password",
    "new_password",
    "csrf_token",
}
KEPT_RESPONSE_HEADERS = (
    "content-type",
    "x-correlation-id",
    "location",
    "idempotent-replayed",
    "retry-after",
)
# Three base64url segments, the first two starting with an encoded '{"'.
JWT = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+")
BODY_LIMIT = 6000
FIELD_SEPARATOR = "\x1f"


class UnmaskedSecretError(RuntimeError):
    """The evidence about to be written still holds a token."""


# ---------- masking ----------


class Masker:
    """Each distinct secret becomes `<masked #n>`: the same secret shows the
    same mark across the run, and nothing about it is derived from it."""

    def __init__(self) -> None:
        self.seen: dict[str, int] = {}
        self.taken: set[int] = set()

    def reserve(self, text: str) -> None:
        """Take the numbers of every mark another masker already wrote in
        `text`, before this masker numbers a new secret: otherwise the same
        `<masked #n>` could name two different secrets."""
        self.taken.update(int(n) for n in MASK_MARK.findall(text))

    def mark(self, secret: str) -> str:
        # A value another masker already marked (a client's record) keeps its
        # number, and no new secret is given that number afterwards.
        if (kept := MASK_MARK.fullmatch(secret)) is not None:
            number = int(kept.group(1))
            if number in self.seen.values():
                raise ValueError(
                    f"{secret} is already the mark of a secret this run numbered; "
                    "reserve the client's marks before masking anything"
                )
            self.taken.add(number)
            return secret
        if secret not in self.seen:
            ordinal = 1
            while ordinal in self.taken:
                ordinal += 1
            self.seen[secret] = ordinal
            self.taken.add(ordinal)
        return f"<masked #{self.seen[secret]}>"

    def header(self, name: str, value: str) -> str:
        if name.lower() not in MASKED_HEADERS:
            return value
        self.reserve(value)
        scheme, _, credential = value.partition(" ")
        if name.lower() == "authorization" and credential:
            return f"{scheme} {self.mark(credential)}"
        return self.mark(value)

    def value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: (
                    self.mark(str(item))
                    if key in MASKED_FIELDS and item is not None
                    else self.value(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.value(item) for item in value]
        return value

    def body(self, raw: bytes | str | None, media_type: str = "") -> str:
        if not raw:
            return ""
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        self.reserve(text)
        if "json" in media_type or text.lstrip().startswith(("{", "[")):
            try:
                return json.dumps(
                    self.value(json.loads(text)), indent=2, ensure_ascii=False
                )
            except ValueError:
                pass
        # A form body: mask its secret fields too.
        if "=" in text and "&" in text and "\n" not in text:
            pairs = urllib.parse.parse_qsl(text, keep_blank_values=True)
            if pairs:
                return "&".join(
                    f"{k}={self.mark(v) if k in MASKED_FIELDS else v}" for k, v in pairs
                )
        return JWT.sub(lambda m: self.mark(m.group(0)), text)


# ---------- the record ----------


@dataclass
class Exchange:
    number: int
    correlation_id: str
    note: str
    method: str
    url: str
    request_headers: list[tuple[str, str]]
    request_body: str
    status: int
    response_headers: list[tuple[str, str]]
    response_body: str
    at: str


@dataclass
class Check:
    criterion: str
    case: str
    expected: str
    actual: str
    held: bool
    links: list[str] = field(default_factory=list)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Recorder:
    """One evidence directory: exchanges.md, rows.md, checks in summary.md,
    and record.json with all of it, every secret masked."""

    def __init__(self, out: Path, title: str, prefix: str) -> None:
        self.out = out
        self.title = title
        self.prefix = prefix
        self.masker = Masker()
        self.lock = threading.Lock()
        self.exchanges: list[Exchange] = []
        self.checks: list[Check] = []
        self.rows: list[tuple[str, str]] = []
        self.facts: dict[str, Any] = {"started_at": utc_now()}
        out.mkdir(parents=True, exist_ok=True)

    def correlation_id(self) -> str:
        return f"{self.prefix}-{uuid.uuid4().hex[:16]}"

    def call(
        self,
        edge: Edge,
        method: str,
        path: str,
        *,
        note: str = "",
        token: str | None = None,
        accept: str | None = "application/json",
        payload: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[Reply, int]:
        """One request through the edge; returns the reply and its number."""
        correlation = self.correlation_id()
        sent = {"X-Correlation-ID": correlation} | (headers or {})
        body = None
        content_type = None
        if payload is not None:
            body = json.dumps(payload).encode()
            content_type = "application/json"
        reply = edge.call(
            method,
            path,
            token=token,
            accept=accept,
            content_type=content_type,
            body=body,
            headers=sent,
        )
        shown = dict(sent)
        if token is not None:
            shown["Authorization"] = "Bearer " + token
        if accept is not None:
            shown["Accept"] = accept
        if content_type is not None:
            shown["Content-Type"] = content_type
        number = self.add(
            note=note,
            method=method,
            url=edge.base.geturl().rstrip("/") + path,
            correlation=correlation,
            request_headers=shown,
            request_body=body,
            status=reply.status,
            response_headers={
                k: v for k, v in reply.headers.items() if k.lower() != "set-cookie"
            },
            response_body=reply.body,
            media_type=reply.media_type,
        )
        return reply, number

    def add(
        self,
        *,
        note: str,
        method: str,
        url: str,
        correlation: str,
        request_headers: dict[str, str],
        request_body: bytes | str | None,
        status: int,
        response_headers: dict[str, str],
        response_body: bytes | str | None,
        media_type: str = "",
    ) -> int:
        masked_response = self.masker.body(response_body, media_type)
        if len(masked_response) > BODY_LIMIT:
            masked_response = (
                masked_response[:BODY_LIMIT]
                + f"\n… ({len(masked_response) - BODY_LIMIT} more characters)"
            )
        with self.lock:
            number = len(self.exchanges) + 1
            self.exchanges.append(
                Exchange(
                    number=number,
                    correlation_id=correlation,
                    note=note,
                    method=method,
                    url=url,
                    request_headers=[
                        (k, self.masker.header(k, v))
                        for k, v in request_headers.items()
                    ],
                    request_body=self.masker.body(request_body),
                    status=status,
                    response_headers=[
                        (k, self.masker.header(k, v))
                        for k, v in response_headers.items()
                        if k.lower() in KEPT_RESPONSE_HEADERS
                    ],
                    response_body=masked_response,
                    at=utc_now(),
                )
            )
            return number

    def check(
        self,
        criterion: str,
        case: str,
        expected: object,
        actual: object,
        held: bool | None = None,
        links: list[str] | None = None,
    ) -> bool:
        result = str(actual) == str(expected) if held is None else held
        self.checks.append(
            Check(criterion, case, str(expected), str(actual), result, links or [])
        )
        return result

    def add_rows(self, title: str, text: str) -> str:
        """A block of query output; returns its anchor for a check to link."""
        self.rows.append((title, text))
        return f"rows.md#rows-{len(self.rows)}"

    @staticmethod
    def link(number: int) -> str:
        return f"exchanges.md#exchange-{number}"

    # ---------- writing ----------

    def _exchanges_md(self) -> str:
        lines = [f"# {self.title}: requests and responses", ""]
        for x in self.exchanges:
            lines += [
                f'<a id="exchange-{x.number}"></a>',
                f"## {x.number}. {x.method} {urllib.parse.urlsplit(x.url).path}"
                + (f" ({x.note})" if x.note else ""),
                "",
                f"`X-Correlation-ID: {x.correlation_id}`, at {x.at}",
                "",
                "```http",
                f"{x.method} {x.url}",
                *[f"{k}: {v}" for k, v in x.request_headers],
                "",
                x.request_body,
                "```",
                "",
                "```http",
                f"HTTP {x.status}",
                *[f"{k}: {v}" for k, v in x.response_headers],
                "",
                x.response_body,
                "```",
                "",
            ]
        return "\n".join(lines)

    def _rows_md(self) -> str:
        lines = [f"# {self.title}: PostgreSQL rows", ""]
        for number, (title, text) in enumerate(self.rows, 1):
            lines += [f'<a id="rows-{number}"></a>', f"## {number}. {title}", ""]
            lines += ["```text", text.rstrip(), "```", ""]
        return "\n".join(lines)

    def summary_md(self) -> str:
        held = sum(c.held for c in self.checks)
        lines = [
            f"# {self.title}",
            "",
            f"Started {self.facts['started_at']}, "
            f"finished {self.facts.get('finished_at', '')}.",
            f"{len(self.checks)} checks, {held} held, "
            f"{len(self.checks) - held} did not.",
            "",
        ]
        for key, value in self.facts.items():
            if key in {"started_at", "finished_at", "versions"}:
                continue
            lines.append(f"- **{key}:** `{value}`")
        lines += ["", "| Criterion | Case | Expected | Actual | Evidence | |"]
        lines.append("|---|---|---|---|---|---|")
        for c in self.checks:
            cells = [c.criterion, c.case, c.expected, c.actual]
            cells = [x.replace("|", "\\|").replace("\n", " ") for x in cells]
            links = ", ".join(f"[{i + 1}]({ref})" for i, ref in enumerate(c.links))
            lines.append(
                "| "
                + " | ".join(cells)
                + f" | {links} |"
                + (" ✓ |" if c.held else " ✗ |")
            )
        versions = self.facts.get("versions")
        if versions:
            lines += ["", "## Versions", "", "| Piece | Version |", "|---|---|"]
            lines += [f"| {k} | `{v}` |" for k, v in versions.items()]
        return "\n".join(lines) + "\n"

    def write(self) -> bool:
        """Write every file; refuse if a token survived. True when all held."""
        self.facts["finished_at"] = utc_now()
        files = {
            "exchanges.md": self._exchanges_md(),
            "rows.md": self._rows_md(),
            "summary.md": self.summary_md(),
            "record.json": json.dumps(
                {
                    "facts": self.facts,
                    "checks": [asdict(c) for c in self.checks],
                    "exchanges": [asdict(x) for x in self.exchanges],
                    "rows": self.rows,
                },
                indent=2,
                ensure_ascii=False,
            ),
        }
        for name, text in files.items():
            if JWT.search(text):
                raise UnmaskedSecretError(
                    f"{name} still holds a token; nothing written"
                )
        for name, text in files.items():
            (self.out / name).write_text(text, encoding="utf-8")
        return all(c.held for c in self.checks)


# ---------- credentials ----------


def credential(variable: str, prompt: str, secret: bool) -> str:
    """From the environment, else a prompt; a secret is never echoed."""
    value = os.environ.get(variable, "")
    if value:
        return value
    return getpass.getpass(prompt) if secret else input(prompt).strip()


def edge_from_environment() -> Edge:
    base = os.environ.get("MOSAIQ_BASE_URL", "").rstrip("/")
    if not base:
        raise SystemExit("MOSAIQ_BASE_URL is required (the gateway's origin).")
    return Edge(
        base,
        host=os.environ.get("MOSAIQ_HOST", ""),
        insecure=env_bool("MOSAIQ_INSECURE_TLS"),
    )


def sign_in(rec: Recorder, edge: Edge, email: str, password: str, label: str) -> str:
    reply, number = rec.call(
        edge,
        "POST",
        AUTH_SESSIONS,
        note=f"sign in, {label}",
        payload={"email": email, "password": password, "device_label": rec.title},
    )
    if reply.status != 201:
        raise SystemExit(
            f"Sign-in as {label} answered {reply.outcome()} "
            f"(exchange {number}); nothing collected."
        )
    return reply.json()["access_token"]


def sign_out(rec: Recorder, edge: Edge, token: str, label: str) -> Reply:
    reply, _ = rec.call(
        edge,
        "DELETE",
        AUTH_SESSIONS + "/current",
        note=f"sign out, {label}",
        token=token,
    )
    return reply


# ---------- PostgreSQL ----------


def to_bind(sql: str) -> str:
    """`%s` placeholders, as psycopg writes them, to `$1`, `$2`, …"""
    count = 0

    def number(_match: re.Match) -> str:
        nonlocal count
        count += 1
        return f"${count}"

    return re.sub(r"%s", number, sql)


class Database:
    """psql through a command prefix, read-only, with bind parameters.

    MOSAIQ_DB_EXEC is a prefix that runs its arguments as the database owner:
    `docker exec -i <db container>` with PGUSER/PGDATABASE locally,
    `runuser -u postgres --` on the instance, or `ssh <host> sudo -n -u
    postgres` from a workstation. Over ssh the remote shell splits the command
    again, so each argument is quoted (MOSAIQ_DB_REMOTE_SHELL, set by default
    when the prefix starts with ssh). Values travel as psql variables in the
    arguments and reach the server as bind parameters; the statements travel
    on standard input.
    """

    def __init__(self, prefix: str, remote_shell: bool | None = None) -> None:
        self.prefix = shlex.split(prefix)
        if remote_shell is None:
            remote_shell = bool(self.prefix) and Path(self.prefix[0]).name == "ssh"
        self.remote_shell = remote_shell

    @classmethod
    def from_environment(cls) -> Database:
        prefix = os.environ.get("MOSAIQ_DB_EXEC", "")
        if not prefix:
            raise SystemExit("MOSAIQ_DB_EXEC is required (see the runbook).")
        remote = os.environ.get("MOSAIQ_DB_REMOTE_SHELL")
        return cls(
            prefix, None if remote is None else env_bool("MOSAIQ_DB_REMOTE_SHELL")
        )

    def command(self, arguments: list[str]) -> list[str]:
        if self.remote_shell:
            return self.prefix + [shlex.quote(a) for a in arguments]
        return self.prefix + arguments

    def _run(self, arguments: list[str], script: str) -> str:
        result = subprocess.run(
            self.command(arguments),
            input=script,
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"psql failed: {result.stderr.strip()[:2000]}")
        return result.stdout

    @staticmethod
    def bind_script(sql: str, count: int) -> str:
        # `:p1`, not `:'p1'`: in a meta-command the quoted form keeps its
        # quotes in the value sent. The unquoted form is one argument whatever
        # it holds, spaces and quotes included (checked on PostgreSQL 18).
        names = " ".join(f":p{i}" for i in range(1, count + 1))
        statement = to_bind(sql).rstrip().rstrip(";")
        return f"BEGIN READ ONLY;\n{statement}\n\\bind {names}\n\\g\nROLLBACK;\n"

    def rows(self, sql: str, params: tuple = ()) -> list[tuple[str, ...]]:
        """Rows as text; `None` for NULL is not distinguished from ''."""
        arguments = ["psql", "-X", "-q", "-A", "-t", "-F", FIELD_SEPARATOR]
        arguments += ["-v", "ON_ERROR_STOP=1", "-d", "retail"]
        for i, value in enumerate(params, 1):
            arguments += ["-v", f"p{i}={value}"]
        arguments += ["-f", "-"]
        out = self._run(arguments, self.bind_script(sql, len(params)))
        return [tuple(line.split(FIELD_SEPARATOR)) for line in out.splitlines() if line]

    def table(self, sql: str, params: tuple = ()) -> str:
        """The same query, aligned for the evidence."""
        arguments = ["psql", "-X", "-q", "-P", "null=(null)"]
        arguments += ["-v", "ON_ERROR_STOP=1", "-d", "retail"]
        for i, value in enumerate(params, 1):
            arguments += ["-v", f"p{i}={value}"]
        arguments += ["-f", "-"]
        return self._run(arguments, self.bind_script(sql, len(params)))

    def script(self, path: Path, variables: dict[str, str]) -> str:
        """A committed psql script (it binds its own variables), on stdin."""
        arguments = ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", "retail"]
        for key, value in variables.items():
            arguments += ["-v", f"{key}={value}"]
        arguments += ["-f", "-"]
        return self._run(arguments, path.read_text())


# ---------- the web portal ----------

_CSRF = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


class Portal:
    """The web system read through its pages, each request recorded."""

    def __init__(self, base_url: str, rec: Recorder, insecure: bool = False) -> None:
        self.base = base_url.rstrip("/")
        self.rec = rec
        handlers: list[urllib.request.BaseHandler] = [
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        ]
        if insecure:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=context))
        self.opener = urllib.request.build_opener(*handlers)
        self.pages = rec.out / "pages"

    def _send(self, method: str, path: str, data: bytes | None, note: str):
        correlation = self.rec.correlation_id()
        headers = {"X-Correlation-ID": correlation, "Accept": "text/html"}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            headers["Origin"] = self.base
        request = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=60) as reply:
                status, got, body = reply.status, dict(reply.headers), reply.read()
        except urllib.error.HTTPError as error:
            status, got, body = error.code, dict(error.headers), error.read()
        html = body.decode("utf-8", "replace")
        number = len(self.rec.exchanges) + 1
        self.pages.mkdir(parents=True, exist_ok=True)
        page = self.pages / f"{number:03d}.html"
        page.write_text(self.rec.masker.body(html), encoding="utf-8")
        self.rec.add(
            note=note,
            method=method,
            url=self.base + path,
            correlation=correlation,
            request_headers=headers,
            request_body=data,
            status=status,
            response_headers={k: v for k, v in got.items()},
            response_body=f"(HTML page, {len(body)} bytes: pages/{page.name})",
        )
        return status, html, number

    def get(self, path: str, note: str = "") -> tuple[int, str, int]:
        return self._send("GET", path, None, note)

    def sign_in(self, email: str, password: str) -> None:
        _status, page, _n = self.get("/login", "the sign-in form")
        match = _CSRF.search(page)
        if not match:
            raise SystemExit("The portal's /login carries no csrf_token field.")
        form = urllib.parse.urlencode(
            {"email": email, "password": password, "csrf_token": match.group(1)}
        ).encode()
        status, _page, number = self._send("POST", "/login", form, "portal sign-in")
        if status >= 400:
            raise SystemExit(f"Portal sign-in answered {status} (exchange {number}).")


# ---------- versions ----------

_INFO_VERSION = re.compile(
    r"^info:\s*$.*?^\s+version:\s*['\"]?([^'\"\s]+)", re.M | re.S
)


def contract_version(text: str) -> str | None:
    match = _INFO_VERSION.search(text)
    return match.group(1) if match else None


def git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *arguments], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else "(unavailable)"


def versions(rec: Recorder, edge: Edge, names: list[str] | None = None) -> dict:
    """The checkout's commit, and each service's contract version as served.

    On the instance (MOSAIQ_RELEASE_ROOT, default /opt/mosaiq, when it
    exists) also the release each service's `current` points at and the
    web's commit.
    """
    found: dict[str, str] = {
        "checkout commit": git("rev-parse", "HEAD"),
        "checkout describe": git("describe", "--always", "--dirty", "--tags"),
    }
    for name in names or registry_names():
        reply, _ = rec.call(
            edge, "GET", f"/api/{name}/openapi.yaml", note="version", accept=None
        )
        served = contract_version(reply.body.decode("utf-8", "replace"))
        found[f"{name} contract"] = served if reply.status == 200 else reply.outcome()
    root = Path(os.environ.get("MOSAIQ_RELEASE_ROOT", "/opt/mosaiq"))
    if (root / "services").is_dir():
        for name in names or registry_names():
            current = root / "services" / name / "current"
            if current.exists():
                found[f"{name} release"] = os.path.realpath(current)
        if (root / "current").exists():
            result = subprocess.run(
                ["git", "-C", str(root / "current"), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
            )
            found["web commit"] = result.stdout.strip() or "(unavailable)"
    rec.facts["versions"] = found
    return found
