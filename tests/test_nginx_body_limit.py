"""NGINX must not cut a request off before Flask can answer it (#292).

Flask's MAX_CONTENT_LENGTH is MAX_UPLOAD_BYTES plus FORM_OVERHEAD_BYTES. If
NGINX's client_max_body_size is at or below that, NGINX answers with its own
stock 413 page (and, unless server_tokens is off, its version) and the
branded MOSAIQ page is unreachable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from web.config import FORM_OVERHEAD_BYTES

ROOT = Path(__file__).resolve().parents[1]
NGINX_FILES = [
    ROOT / "deploy/nginx/mosaiq.conf",
    ROOT / "deploy/nginx/mosaiq.compose.conf",
]
# MAX_UPLOAD_BYTES is 5 MiB in .env.example; web/app.py adds the form overhead.
FLASK_LIMIT = 5 * 1024 * 1024 + FORM_OVERHEAD_BYTES
_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
_BODY_LIMIT = re.compile(r"^\s*client_max_body_size\s+(\d+)([kmg]?)\s*;", re.I | re.M)


def _body_limit(path: Path) -> int:
    match = _BODY_LIMIT.search(path.read_text())
    assert match, f"{path.name} sets no client_max_body_size"
    return int(match.group(1)) * _UNITS[match.group(2).lower()]


@pytest.mark.parametrize("path", NGINX_FILES, ids=lambda p: p.name)
def test_nginx_accepts_every_request_flask_would_answer(path: Path) -> None:
    assert _body_limit(path) > FLASK_LIMIT


@pytest.mark.parametrize("path", NGINX_FILES, ids=lambda p: p.name)
def test_server_tokens_are_off_in_every_server_block(path: Path) -> None:
    text = path.read_text()
    servers = len(re.findall(r"^server\s*\{", text, re.MULTILINE))

    assert servers >= 1
    assert text.count("server_tokens off;") == servers
