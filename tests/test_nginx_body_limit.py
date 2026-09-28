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
_EXPECTED_SERVER_BLOCKS = {NGINX_FILES[0]: 2, NGINX_FILES[1]: 1}
# MAX_UPLOAD_BYTES is 5 MiB in .env.example; web/app.py adds the form overhead.
FLASK_LIMIT = 5 * 1024 * 1024 + FORM_OVERHEAD_BYTES
_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
_BODY_LIMIT = re.compile(r"^\s*client_max_body_size\s+(\d+)([kmg]?)\s*;", re.I | re.M)
_SERVER_BLOCK = re.compile(r"^\s*server\s*\{", re.MULTILINE)
_SERVER_TOKENS_OFF = re.compile(r"^\s*server_tokens\s+off\s*;", re.I | re.M)


def _config_text(path: Path) -> str:
    return re.sub(r"#.*$", "", path.read_text(), flags=re.MULTILINE)


def _body_limit(path: Path) -> int:
    match = _BODY_LIMIT.search(_config_text(path))
    assert match, f"{path.name} sets no client_max_body_size"
    return int(match.group(1)) * _UNITS[match.group(2).lower()]


def _server_blocks(path: Path) -> list[str]:
    text = _config_text(path)
    blocks = []

    for match in _SERVER_BLOCK.finditer(text):
        depth = 0
        for index in range(match.end() - 1, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(text[match.end() : index])
                    break
        else:
            raise AssertionError(f"{path.name} has an unclosed server block")

    return blocks


@pytest.mark.parametrize("path", NGINX_FILES, ids=lambda p: p.name)
def test_nginx_accepts_every_request_flask_would_answer(path: Path) -> None:
    assert _body_limit(path) > FLASK_LIMIT


@pytest.mark.parametrize("path", NGINX_FILES, ids=lambda p: p.name)
def test_server_tokens_are_off_in_every_server_block(path: Path) -> None:
    blocks = _server_blocks(path)

    assert len(blocks) == _EXPECTED_SERVER_BLOCKS[path]
    assert all(_SERVER_TOKENS_OFF.search(block) for block in blocks)
