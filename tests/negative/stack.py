"""The negative suite's view of a deployed stack (F18-04, #459).

Every request goes through the stack's edge, `/api/<name>/v1/...`, with real
tokens that auth-service signed for real accounts. Nothing here holds a
signing key unless MOSAIQ_SIGNING_KEY names one, which only the local stack's
throwaway key ever does.
"""

from __future__ import annotations

import base64
import contextlib
import http.client
import json
import os
import secrets
import ssl
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tests.conformance import tokens
from tests.conformance.harness import ROLE_CODES, Contract, Operation, load_contract

ROOT = Path(__file__).resolve().parents[2]
AUTH_SESSIONS = "/api/auth/v1/sessions"
USERS = "/api/users/v1/users"
LOCAL_PASSWORD = "Password123!"
INSTANCE_SIGN_IN_INTERVAL = 10.5
ACCESS_REFRESH_AFTER = 480
EVIDENCE_FILE = ROOT / "docs/evidence/f18-04-negative-tests.md"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str) -> bool:
    value = env(name).strip().lower()
    if value in {"", "0", "false", "no"}:
        return False
    if value in {"1", "true", "yes"}:
        return True
    raise ValueError(f"{name} must be true or false.")


@dataclass
class Reply:
    status: int
    headers: http.client.HTTPMessage
    body: bytes

    @property
    def media_type(self) -> str:
        return (self.headers.get("Content-Type") or "").split(";")[0].strip()

    def json(self) -> Any:
        return json.loads(self.body)

    def code(self) -> str | None:
        if self.media_type != "application/json" or not self.body:
            return None
        try:
            return self.json().get("code")
        except (ValueError, AttributeError):
            return None

    def outcome(self) -> str:
        """The status and the envelope's code, as the evidence shows them."""
        code = self.code()
        return f"{self.status} {code}" if code else str(self.status)


class Edge:
    """HTTP or HTTPS to one origin, optionally naming another Host.

    On the instance the suite reaches NGINX on 127.0.0.1:443, whose certificate
    names the published host, so MOSAIQ_INSECURE_TLS turns verification off
    for that loopback hop only.
    """

    def __init__(self, base_url: str, host: str = "", insecure: bool = False):
        self.base = urlsplit(base_url)
        self.host = host
        self.context = None
        if self.base.scheme == "https":
            self.context = ssl.create_default_context()
            if insecure:
                self.context.check_hostname = False
                self.context.verify_mode = ssl.CERT_NONE

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        accept: str | None = "application/json",
        content_type: str | None = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Reply:
        sent = {"X-Correlation-ID": "f18-04-" + uuid.uuid4().hex[:16]}
        if self.host:
            sent["Host"] = self.host
        if token is not None:
            sent["Authorization"] = "Bearer " + token
        if accept is not None:
            sent["Accept"] = accept
        if content_type is not None:
            sent["Content-Type"] = content_type
        sent |= headers or {}
        if self.base.scheme == "https":
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(
                self.base.hostname, self.base.port, timeout=20, context=self.context
            )
        else:
            connection = http.client.HTTPConnection(
                self.base.hostname, self.base.port, timeout=20
            )
        try:
            try:
                connection.request(
                    method, self.base.path.rstrip("/") + path, body=body, headers=sent
                )
            except (BrokenPipeError, ConnectionResetError):
                # An oversized body may be answered before it is read.
                pass
            response = connection.getresponse()
            return Reply(response.status, response.headers, response.read())
        finally:
            connection.close()

    def json(self, method: str, path: str, payload: object, **kwargs: Any) -> Reply:
        return self.call(
            method,
            path,
            content_type="application/json",
            body=json.dumps(payload).encode(),
            **kwargs,
        )


@dataclass
class Service:
    name: str
    port: int
    contract: Contract

    @property
    def prefix(self) -> str:
        return f"/api/{self.name}"

    def protected(self) -> list[Operation]:
        return [
            op
            for op in self.contract.operations
            if op.path.startswith("/v") and op.protected and op.roles
        ]

    def probe(self) -> Operation:
        """A protected operation that needs no body, a GET when there is one."""
        return min(
            self.protected(),
            key=lambda op: (op.method != "GET", op.body, len(op.required()), op.path),
        )

    def writer(self) -> Operation | None:
        writers = [op for op in self.protected() if op.body]
        if not writers:
            return None
        return min(writers, key=lambda op: (len(op.required()), op.path, op.method))

    def paginated(self) -> list[Operation]:
        return [op for op in self.protected() if op.paginated]

    def body_operation(self) -> Operation | None:
        """Where the body cases go: a protected writer, or else a public one.

        auth has only public writers. Its sign-in is left out, because the
        edge counts every request to it against the per-IP sign-in limit.
        """
        writer = self.writer()
        if writer is not None:
            return writer
        public = [
            op
            for op in self.contract.operations
            if op.path.startswith("/v")
            and op.body
            and (self.prefix + op.path, op.method) != (AUTH_SESSIONS, "POST")
        ]
        return min(public, key=lambda op: op.path) if public else None


def registry() -> list[Service]:
    import yaml

    entries = yaml.safe_load((ROOT / "contracts/registry.yaml").read_text())
    return [
        Service(
            e["name"], int(e["port"]), load_contract(ROOT / "contracts" / e["name"])
        )
        for e in entries["services"]
    ]


def registry_names() -> list[str]:
    """The registered names, read without PyYAML, for collection."""
    names = []
    for line in (ROOT / "contracts/registry.yaml").read_text().splitlines():
        if line.startswith("  - name: "):
            names.append(line.removeprefix("  - name: ").strip())
    return names


@dataclass
class Session:
    access: str
    refresh: str
    account: Account
    stack: Stack
    closed: bool = False
    issued: float = field(default_factory=time.monotonic)

    def token(self) -> str:
        """The access token, rotated first once it is over eight minutes old.

        Access tokens live ten minutes, and an instance run is longer.
        """
        if time.monotonic() - self.issued > ACCESS_REFRESH_AFTER:
            _old, reply = self.rotate()
            require(reply, 200)
        return self.access

    def rotate(self) -> tuple[str, Reply]:
        """Use the current refresh token and adopt the rotated token pair."""
        old_refresh = self.refresh
        reply = self.stack.edge.json(
            "POST",
            AUTH_SESSIONS + "/refresh",
            {"refresh_token": old_refresh},
        )
        if reply.status == 200:
            result = reply.json()
            self.access = result["access_token"]
            self.refresh = result["refresh_token"]
            self.issued = time.monotonic()
        return old_refresh, reply

    def reuse(self, refresh_token: str) -> Reply:
        reply = self.stack.edge.json(
            "POST",
            AUTH_SESSIONS + "/refresh",
            {"refresh_token": refresh_token},
        )
        if reply.status == 401 and reply.code() == "refresh_reused":
            self.closed = True
        return reply

    def current(self) -> Reply:
        return self.stack.edge.call(
            "GET", AUTH_SESSIONS + "/current", token=self.token()
        )

    def sign_out(self) -> Reply:
        reply = self.stack.edge.call(
            "DELETE", AUTH_SESSIONS + "/current", token=self.token()
        )
        if reply.status == 204 or (
            reply.status == 401 and reply.code() == "session_revoked"
        ):
            self.closed = True
        return reply


@dataclass
class Account:
    role: str
    user_id: str
    email: str
    password: str
    created_for_run: bool = False


@dataclass
class Stack:
    """Accounts and real auth sessions owned by one negative-suite run."""

    edge: Edge
    target: str
    run_id: str = field(default_factory=lambda: secrets.token_hex(5))
    accounts: dict[str, Account] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)
    opened_sessions: list[Session] = field(default_factory=list)
    spare: list[Account] = field(default_factory=list)
    last_sign_in_started: float | None = None

    @classmethod
    def from_environment(cls) -> Stack:
        base_url = env("MOSAIQ_BASE_URL").rstrip("/")
        if not base_url:
            raise ValueError("MOSAIQ_BASE_URL is required.")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("MOSAIQ_BASE_URL must be an HTTP or HTTPS origin.")

        target = env("MOSAIQ_TARGET").strip().lower()
        if not target:
            loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            target = "local" if parsed.scheme == "http" and loopback else "instance"
        if target not in {"local", "instance"}:
            raise ValueError("MOSAIQ_TARGET must be local or instance.")

        return cls(
            Edge(
                base_url,
                host=env("MOSAIQ_HOST"),
                insecure=env_bool("MOSAIQ_INSECURE_TLS"),
            ),
            target,
        )

    @property
    def is_instance(self) -> bool:
        return self.target == "instance"

    def _pace_sign_in(self) -> None:
        if self.is_instance and self.last_sign_in_started is not None:
            elapsed = time.monotonic() - self.last_sign_in_started
            time.sleep(max(0.0, INSTANCE_SIGN_IN_INTERVAL - elapsed))
        self.last_sign_in_started = time.monotonic()

    def sign_in_reply(self, email: str, password: str, *, paced: bool = True) -> Reply:
        """Attempt sign-in, pacing starts at the instance's six/minute edge."""
        if paced:
            self._pace_sign_in()
        else:
            # A following ordinary attempt must still be 10.5 s after this one.
            self.last_sign_in_started = time.monotonic()
        return self.edge.json(
            "POST",
            AUTH_SESSIONS,
            {
                "email": email,
                "password": password,
                "device_label": "F18-04 negative suite",
            },
        )

    def fresh_session(self, role: str) -> Session:
        """A session of its own, for a case that ends the session it uses."""
        return self.sign_in(self.accounts[role])

    def token(self, role: str) -> str:
        return self.sessions[role].token()

    def sign_in(self, account: Account) -> Session:
        reply = self.sign_in_reply(account.email, account.password)
        require(reply, 201)
        result = reply.json()
        session = Session(
            access=result["access_token"],
            refresh=result["refresh_token"],
            account=account,
            stack=self,
        )
        self.opened_sessions.append(session)
        return session

    def provision(self) -> None:
        if self.target == "local":
            self.accounts = local_accounts()
            for role in ROLE_CODES:
                self.sessions[role] = self.sign_in(self.accounts[role])
            return

        admin_email = env("MOSAIQ_NEGATIVE_ADMIN_EMAIL").strip()
        admin_password = env("MOSAIQ_NEGATIVE_ADMIN_PASSWORD")
        if not admin_email or not admin_password:
            raise ValueError(
                "The instance requires MOSAIQ_NEGATIVE_ADMIN_EMAIL and "
                "MOSAIQ_NEGATIVE_ADMIN_PASSWORD."
            )

        admin = Account("ADMIN", "", admin_email, admin_password)
        admin_session = self.sign_in(admin)
        profile = self.edge.call("GET", USERS + "/me", token=admin_session.access)
        require(profile, 200)
        admin.user_id = profile.json()["user_id"]
        self.accounts["ADMIN"] = admin
        self.sessions["ADMIN"] = admin_session

        run_password = "Negative-" + secrets.token_urlsafe(18)
        for role in ROLE_CODES:
            if role == "ADMIN":
                continue
            account = self._create_account(role, run_password, admin_session.access)
            self.accounts[role] = account
        for role in ROLE_CODES:
            if role != "ADMIN":
                self.sessions[role] = self.sign_in(self.accounts[role])

    @contextlib.contextmanager
    def throwaway_account(self, role: str):
        """An account of its own, for a case that spends a per-account counter.

        Those counters last fifteen minutes, so the run's shared account would
        refuse the next run. It is deactivated when the case ends.
        """
        password = "Negative-" + secrets.token_urlsafe(18)
        account = self._create_account(role, password, self.token("ADMIN"), "-spare")
        self.spare.append(account)
        try:
            yield account
        finally:
            if self.deactivate(account).status == 200:
                self.spare.remove(account)

    def deactivate(self, account: Account) -> Reply:
        return self.edge.call(
            "POST",
            f"{USERS}/{account.user_id}/deactivation",
            token=self.token("ADMIN"),
        )

    def forget_sessions(self) -> None:
        """Every session of the run is gone: redis-state lost them (#714)."""
        for session in self.opened_sessions:
            session.closed = True

    def _create_account(
        self, role: str, password: str, admin_token: str, tag: str = ""
    ) -> Account:
        slug = role.lower().replace("_", "-") + tag
        email = f"f18-04-{self.run_id}-{slug}@example.test"
        reply = self.edge.json(
            "POST",
            USERS,
            {
                "name": f"F18-04 {role.replace('_', ' ').title()}",
                "email": email,
                "role_code": role,
                "password": password,
            },
            token=admin_token,
        )
        require(reply, 201)
        return Account(role, reply.json()["user_id"], email, password, True)

    def cleanup(self) -> None:
        """Close sessions and deactivate every account created on the instance."""
        failures = []
        admin_session = self.sessions.get("ADMIN")

        for session in reversed(self.opened_sessions):
            if session is admin_session or session.closed:
                continue
            reply = session.sign_out()
            if reply.status != 204 and not (
                reply.status == 401 and reply.code() == "session_revoked"
            ):
                failures.append(
                    f"sign out {session.account.role}: expected 204, got "
                    + reply.outcome()
                )

        if admin_session is not None and not admin_session.closed:
            created = [a for a in self.accounts.values() if a.created_for_run]
            for account in reversed(created + self.spare):
                reply = self.deactivate(account)
                if reply.status != 200:
                    failures.append(
                        f"deactivate {account.role}: expected 200, got "
                        + reply.outcome()
                    )
            reply = admin_session.sign_out()
            if reply.status != 204 and not (
                reply.status == 401 and reply.code() == "session_revoked"
            ):
                failures.append("sign out ADMIN: expected 204, got " + reply.outcome())

        if failures:
            raise AssertionError(
                "Negative-suite cleanup failed: " + "; ".join(failures)
            )


def local_accounts() -> dict[str, Account]:
    """One deterministic seeded account for each of the seven roles."""
    accounts = {}
    for number, role in enumerate(ROLE_CODES, start=1):
        email = (
            "admin@mosaiq-demo.com"
            if role == "ADMIN"
            else f"user{number}@mosaiq-demo.com"
        )
        accounts[role] = Account(
            role=role,
            user_id=f"11111111-1111-1111-1111-{number:012d}",
            email=email,
            password=LOCAL_PASSWORD,
        )
    return accounts


def require(reply: Reply, status: int, code: str | None = None) -> None:
    expected = f"{status} {code}" if code else str(status)
    if reply.status != status or (code is not None and reply.code() != code):
        body = reply.body.decode("utf-8", errors="replace")
        raise AssertionError(
            f"Expected {expected}, got {reply.outcome()}: {body[:500]}"
        )


# Tokens ------------------------------------------------------------------------
#
# The forged tokens below start from a live token auth signed, so the one thing
# wrong with each is the thing its case names.


def claims_of(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def header_of(token: str) -> dict:
    header = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))


def tampered_signature(token: str) -> str:
    header, payload, signature = token.split(".")
    flipped = "A" if signature[-2] != "A" else "B"
    return ".".join((header, payload, signature[:-2] + flipped + signature[-1]))


def unsigned(token: str) -> str:
    header = header_of(token) | {"alg": "none"}
    return tokens.unsigned(header, claims_of(token))


def _der_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _der(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _der_length(len(content)) + content


def _der_integer(value: bytes) -> bytes:
    value = value.lstrip(b"\0") or b"\0"
    if value[0] & 0x80:
        value = b"\0" + value
    return _der(0x02, value)


def public_pem(jwk: dict) -> bytes:
    """The PEM of a JWKS entry's RSA public key, as an HS256 forger keys it."""

    def number(field_name: str) -> bytes:
        text = jwk[field_name]
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))

    rsa_key = _der(0x30, _der_integer(number("n")) + _der_integer(number("e")))
    algorithm = bytes.fromhex("300d06092a864886f70d0101010500")
    spki = _der(0x30, algorithm + _der(0x03, b"\0" + rsa_key))
    body = base64.encodebytes(spki).decode("ascii").replace("\n", "")
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    return (
        "-----BEGIN PUBLIC KEY-----\n"
        + "\n".join(lines)
        + "\n-----END PUBLIC KEY-----\n"
    ).encode("ascii")


def hs256_with_public_key(token: str, jwk: dict) -> str:
    header = header_of(token) | {"alg": "HS256"}
    return tokens.sign_hs256(header, claims_of(token), public_pem(jwk))


def signing_key() -> tuple[Path, str] | None:
    """The local stack's throwaway key, when MOSAIQ_SIGNING_KEY names it."""
    path = env("MOSAIQ_SIGNING_KEY")
    if not path:
        return None
    return Path(path), env("MOSAIQ_SIGNING_KID", "local-dev")


def resigned(token: str, key: tuple[Path, str], **changes: Any) -> str:
    path, kid = key
    header = header_of(token) | {"alg": "RS256", "kid": kid}
    return tokens.sign_rs256(header, claims_of(token) | changes, path)


# Ordering ------------------------------------------------------------------------


def run_last(function: Any) -> Any:
    """Run after every other case (conftest.py): the per-IP sign-in limit
    refuses every later sign-in from the same address for a while."""
    function.negative_run_last = True
    return function


# Evidence -------------------------------------------------------------------------


@dataclass
class Row:
    area: str
    case: str
    expected: str
    actual: str
    verdict: bool | None = None

    @property
    def held(self) -> bool:
        return self.actual == self.expected if self.verdict is None else self.verdict


@dataclass
class Note:
    """A case this target does not run, for the services it applies to."""

    kind: str
    case: str
    reason: str
    areas: list[str] = field(default_factory=list)

    def line(self) -> str:
        areas = ", ".join(self.areas)
        return f"- **{areas}, {self.case}:** {self.kind}. {self.reason}"


@dataclass
class Evidence:
    """Every case's expected and actual result, for the evidence document."""

    label: str
    rows: list[Row] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)

    def check(
        self, area: str, case: str, reply: Reply, status: int, code: str | None = None
    ) -> bool:
        expected = f"{status} {code}" if code else str(status)
        row = Row(area, case, expected, reply.outcome())
        self.rows.append(row)
        return row.held

    def record(
        self,
        area: str,
        case: str,
        expected: str,
        actual: str,
        held: bool | None = None,
    ) -> bool:
        """A result that is not one reply; `held` decides when it is not equality."""
        row = Row(area, case, expected, actual, held)
        self.rows.append(row)
        return row.held

    def not_run(self, area: str, case: str, reason: str) -> None:
        self._note("not run here", area, case, reason)

    def not_applicable(self, area: str, case: str, reason: str) -> None:
        self._note("not applicable", area, case, reason)

    def _note(self, kind: str, area: str, case: str, reason: str) -> None:
        """One line for a case and reason, naming every area it applies to."""
        for note in self.notes:
            if (note.kind, note.case, note.reason) == (kind, case, reason):
                if area not in note.areas:
                    note.areas.append(area)
                return
        self.notes.append(Note(kind, case, reason, [area]))

    def section(self, base_url: str) -> str:
        held = sum(row.held for row in self.rows)
        stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        lines = [
            f"## {self.label}",
            "",
            f"Run at {stamp} against `{base_url}`: {len(self.rows)} cases, "
            f"{held} held, {len(self.rows) - held} did not.",
            "",
        ]
        if self.notes:
            lines += [note.line() for note in self.notes] + [""]
        lines += ["| Area | Case | Expected | Actual | |", "|---|---|---|---|---|"]
        for row in self.rows:
            cells = [row.area, row.case, row.expected, row.actual]
            cells = [cell.replace("|", "\\|").replace("\n", " ") for cell in cells]
            lines.append(
                "| " + " | ".join(cells) + (" | ✓ |" if row.held else " | ✗ |")
            )
        return "\n".join(lines) + "\n"

    def write(self, base_url: str, path: Path = EVIDENCE_FILE) -> None:
        """Replace this run's section in the document, keeping the others."""
        if not self.rows and not self.notes:
            return
        text = path.read_text() if path.exists() else EVIDENCE_HEADER
        head, *sections = text.split("\n## ")
        kept = {
            "## " + section.split("\n", 1)[0]: "## " + section.rstrip("\n") + "\n"
            for section in sections
        }
        kept[f"## {self.label}"] = self.section(base_url)
        order = [f"## {label}" for label in SECTION_ORDER]
        ordered = [kept[key] for key in order if key in kept]
        ordered += [value for key, value in kept.items() if key not in order]
        path.write_text(head.rstrip("\n") + "\n\n" + "\n".join(ordered))


SECTION_ORDER = ("Local stack", "Instance", "Instance, probed from outside")

EVIDENCE_HEADER = """\
# F18-04 — negative tests across every deployed service and boundary

Issue #459. Written by `tests/negative/` (see its README), one section per
target: each run replaces its own section and keeps the others. A case holds
when its actual result is the expected one.

Running the case that stops `redis-state` showed that a restart of it revokes
every session, although it keeps an append-only file
([#714](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/714)).
That case therefore runs last, and the cases that follow it in the same run
would fail on sessions that no longer exist.
"""
