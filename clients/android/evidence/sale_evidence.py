"""F20-01 (#759): turn the live run's HTTP exchanges into masked evidence.

Two commands, both printing Markdown for docs/evidence/f20-01-android-sale.md:

``har <capture.har> [--path SUBSTRING ...]``
    Reads the HAR that mitmproxy wrote while the release build signed in and
    recorded the sale, keeps the exchanges whose path contains one of the
    substrings (all ``/api/`` exchanges by default), and prints each request
    (method, path, headers, body) and response (status, headers, body).

``refusals``
    Signs in as the store manager, then makes the two refused calls of the
    acceptance criteria and signs out: ``GET /api/users/v1/users``, outside
    STORE_MANAGER's ``x-mosaiq-roles`` (403 ``forbidden_role``), and
    ``POST /api/sales/v1/sales`` with no token (401 ``invalid_token``). The
    service refuses both before reading a body, so neither writes anything.
    It reads MOSAIQ_F20_BASE_URL (default: the local gateway, never the
    instance) and MOSAIQ_F20_EMAIL, and asks for the password unless
    MOSAIQ_F20_PASSWORD is set in the process environment.

Tokens never reach the output. An ``Authorization`` value, a cookie, and any
token or password field of a JSON body become ``<masked #n>``, a
fingerprint that shows two requests carried the same token without revealing
it. As a last guard, output that still holds something shaped like a JWT is
refused with exit status 3. The raw HAR holds live tokens: keep it outside the
repository (``*.har`` is gitignored) and delete it once the evidence is written.

    python clients/android/evidence/sale_evidence.py har ~/f20-01/capture.har \\
        --path /api/auth/v1/sessions --path /api/users/v1/users/me \\
        --path /api/sales/v1/sales
    MOSAIQ_F20_BASE_URL=https://mosaiq.maxthecoder.online \\
    MOSAIQ_F20_EMAIL=<store manager> \\
        python clients/android/evidence/sale_evidence.py refusals
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEVICE_LABEL = "F20-01 refusal check"

MASKED_HEADERS = frozenset({"authorization", "cookie", "set-cookie"})
MASKED_FIELDS = frozenset(
    {
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "password",
        "current_password",
        "new_password",
    }
)
# Three base64url segments, the first two starting with '{"' encoded.
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+")


class UnmaskedTokenError(RuntimeError):
    """The rendered evidence still holds something shaped like a token."""


@dataclass
class Message:
    """One request or response, as the evidence shows it."""

    start_line: str
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: str = ""


@dataclass
class Exchange:
    request: Message
    response: Message


# Each distinct secret gets the next ordinal in this run: two requests that
# carried the same secret show the same mark, and nothing is derived from it.
_SEEN_SECRETS: dict[str, int] = {}


def fingerprint(secret: str) -> str:
    ordinal = _SEEN_SECRETS.setdefault(secret, len(_SEEN_SECRETS) + 1)
    return f"<masked #{ordinal}>"


def mask_header(name: str, value: str) -> str:
    if name.lower() not in MASKED_HEADERS:
        return value
    scheme, _, credential = value.partition(" ")
    if name.lower() == "authorization" and credential:
        return f"{scheme} {fingerprint(credential)}"
    return fingerprint(value)


def _mask_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: (
                fingerprint(str(item))
                if key in MASKED_FIELDS and item is not None
                else _mask_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask_value(item) for item in value]
    return value


def mask_body(text: str, media_type: str = "") -> str:
    """A JSON body pretty-printed with its secrets masked; other text as is."""

    if not text:
        return ""
    if "json" in media_type or text.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(text)
        except ValueError:
            return text
        return json.dumps(_mask_value(parsed), indent=2, ensure_ascii=False)
    return text


def _path(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _har_text(container: dict) -> str:
    text = container.get("text") or ""
    if container.get("encoding") == "base64":
        return base64.b64decode(text).decode("utf-8", errors="replace")
    return text


def _headers(raw: Iterable[dict]) -> list[tuple[str, str]]:
    return [(h["name"], mask_header(h["name"], h["value"])) for h in raw]


def exchanges_from_har(har: dict, paths: Sequence[str] = ("/api/",)) -> list[Exchange]:
    """The HAR's exchanges whose path contains one of ``paths``, in order."""

    found = []
    for entry in har.get("log", {}).get("entries", []):
        request, response = entry["request"], entry["response"]
        path = _path(request["url"])
        if not any(fragment in path for fragment in paths):
            continue
        post = request.get("postData") or {}
        content = response.get("content") or {}
        found.append(
            Exchange(
                Message(
                    f"{request['method']} {path} {request.get('httpVersion', '')}",
                    _headers(request.get("headers", [])),
                    mask_body(_har_text(post), post.get("mimeType", "")),
                ),
                Message(
                    f"{response.get('httpVersion', '')} {response['status']}",
                    _headers(response.get("headers", [])),
                    mask_body(_har_text(content), content.get("mimeType", "")),
                ),
            )
        )
    return found


def _block(message: Message) -> str:
    lines = [message.start_line.strip()]
    lines += [f"{name}: {value}" for name, value in message.headers]
    text = "\n".join(lines)
    if message.body:
        text += "\n\n" + message.body
    return text


def render(exchanges: Sequence[Exchange]) -> str:
    """Markdown for the exchanges; refuses output that still holds a JWT."""

    parts = []
    for number, exchange in enumerate(exchanges, start=1):
        title = exchange.request.start_line.rsplit(" ", 1)[0].strip()
        parts.append(f"### {number}. `{title}`\n")
        parts.append("Request:\n\n```http\n" + _block(exchange.request) + "\n```\n")
        parts.append("Response:\n\n```http\n" + _block(exchange.response) + "\n```\n")
    text = "\n".join(parts)
    if _JWT.search(text):
        raise UnmaskedTokenError("a token-shaped value survived masking")
    return text


# ---------- the refused calls ----------


def _call(
    base_url: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict | None = None,
) -> tuple[Exchange, dict]:
    headers = {"Accept": "application/json", "X-Correlation-ID": str(uuid.uuid4())}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        base_url.rstrip("/") + path, data=data, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            status, reply_headers, raw = reply.status, reply.headers, reply.read()
    except urllib.error.HTTPError as refused:
        status, reply_headers, raw = refused.code, refused.headers, refused.read()
    text = raw.decode("utf-8", errors="replace")
    media_type = reply_headers.get("Content-Type", "")
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {}
    exchange = Exchange(
        Message(
            f"{method} {path}",
            [(name, mask_header(name, value)) for name, value in headers.items()],
            mask_body(data.decode() if data else "", "json"),
        ),
        Message(
            str(status),
            [(name, mask_header(name, value)) for name, value in reply_headers.items()],
            mask_body(text, media_type),
        ),
    )
    return exchange, {"status": status, "json": parsed}


@dataclass
class Outcome:
    exchanges: list[Exchange]
    held: bool
    summary: list[str]


def refusals(base_url: str, email: str, password: str) -> Outcome:
    """Sign in, make the two refused calls, sign out; report what each got."""

    exchanges: list[Exchange] = []
    summary: list[str] = []

    sign_in, answer = _call(
        base_url,
        "POST",
        "/api/auth/v1/sessions",
        body={"email": email, "password": password, "device_label": DEVICE_LABEL},
    )
    exchanges.append(sign_in)
    if answer["status"] != 201:
        summary.append(f"sign-in answered {answer['status']}, expected 201")
        return Outcome(exchanges, False, summary)
    token = str(answer["json"]["access_token"])

    expected = [
        ("GET", "/api/users/v1/users", token, None, 403, "forbidden_role"),
        ("POST", "/api/sales/v1/sales", None, {}, 401, "invalid_token"),
    ]
    held = True
    for method, path, bearer, body, status, code in expected:
        exchange, answer = _call(base_url, method, path, token=bearer, body=body)
        exchanges.append(exchange)
        got = (answer["status"], answer["json"].get("code"))
        ok = got == (status, code)
        held &= ok
        summary.append(
            f"{method} {path} {'with' if bearer else 'without'} a token: "
            f"expected {status} {code}, got {got[0]} {got[1]}"
            f" {'(held)' if ok else '(DID NOT HOLD)'}"
        )

    sign_out, answer = _call(
        base_url, "DELETE", "/api/auth/v1/sessions/current", token=token
    )
    exchanges.append(sign_out)
    summary.append(f"sign-out answered {answer['status']}")
    return Outcome(exchanges, held, summary)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    har = commands.add_parser("har", help="mask and print a mitmproxy HAR")
    har.add_argument("capture", type=Path)
    har.add_argument("--path", action="append", dest="paths")
    commands.add_parser("refusals", help="the 403 and 401 calls, masked")
    args = parser.parse_args(argv)

    try:
        if args.command == "har":
            data = json.loads(args.capture.read_text(encoding="utf-8"))
            print(render(exchanges_from_har(data, args.paths or ("/api/",))))
            return 0
        base_url = os.environ.get("MOSAIQ_F20_BASE_URL") or DEFAULT_BASE_URL
        email = os.environ.get("MOSAIQ_F20_EMAIL") or ""
        if not email:
            print("MOSAIQ_F20_EMAIL is not set.", file=sys.stderr)
            return 2
        password = os.environ.get("MOSAIQ_F20_PASSWORD") or getpass.getpass(
            f"Password for {email}: "
        )
        outcome = refusals(base_url, email, password)
        print(f"Against {base_url}:\n")
        print("\n".join(f"- {line}" for line in outcome.summary) + "\n")
        print(render(outcome.exchanges))
        return 0 if outcome.held else 1
    except UnmaskedTokenError as error:
        print(f"refused to print: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
