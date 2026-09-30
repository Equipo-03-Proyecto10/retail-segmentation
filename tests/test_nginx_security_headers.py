"""The compose overlay sends the same security headers as the instance (#293).

The overlay's own comment says it stays in sync with the instance file, but it
sent none of the headers. This test makes the two unable to drift again.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTANCE = ROOT / "deploy/nginx/mosaiq.conf"
COMPOSE = ROOT / "deploy/nginx/mosaiq.compose.conf"
_ADD_HEADER = re.compile(r'^\s*add_header\s+(\S+)\s+"([^"]*)"\s+always;', re.MULTILINE)
_HIDE_HEADER = re.compile(r"^\s*proxy_hide_header\s+(\S+)\s*;", re.MULTILINE)
EXPECTED = {
    "Strict-Transport-Security",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Content-Security-Policy",
    "Permissions-Policy",
}


def _blocks(text: str, directive: str) -> list[str]:
    blocks = []
    for match in re.finditer(rf"\b{directive}\b[^{{;]*{{", text):
        start = match.end()
        depth = 1
        for end in range(start, len(text)):
            depth += text[end] == "{"
            depth -= text[end] == "}"
            if depth == 0:
                blocks.append(text[start:end])
                break
    return blocks


def _server_block(path: Path, port: int | None = None) -> str:
    text = re.sub(r"#.*$", "", path.read_text(), flags=re.MULTILINE)
    servers = _blocks(text, "server")
    if port is not None:
        servers = [
            block
            for block in servers
            if re.search(rf"^\s*listen\s+(?:\[::\]:)?{port}\b", block, re.MULTILINE)
        ]
    assert len(servers) == 1
    return servers[0]


def _headers(path: Path, port: int | None = None) -> dict[str, str]:
    block = _server_block(path, port)
    direct_lines = []
    depth = 0
    for line in block.splitlines():
        if depth == 0:
            direct_lines.append(line)
        depth += line.count("{") - line.count("}")
    return dict(_ADD_HEADER.findall("\n".join(direct_lines)))


def _hidden_headers(path: Path, port: int | None = None) -> set[str]:
    block = _server_block(path, port)
    direct_lines = []
    depth = 0
    for line in block.splitlines():
        if depth == 0:
            direct_lines.append(line)
        depth += line.count("{") - line.count("}")
    return set(_HIDE_HEADER.findall("\n".join(direct_lines)))


@pytest.mark.parametrize("path", [INSTANCE, COMPOSE], ids=lambda p: p.name)
def test_every_security_header_is_sent(path: Path) -> None:
    assert set(_headers(path, 443 if path == INSTANCE else None)) == EXPECTED


@pytest.mark.parametrize("path", [INSTANCE, COMPOSE], ids=lambda p: p.name)
def test_upstream_security_headers_are_hidden_before_proxy_headers_are_added(
    path: Path,
) -> None:
    port = 443 if path == INSTANCE else None
    assert _hidden_headers(path, port) == EXPECTED


def test_permissions_policy_denies_what_the_application_does_not_use() -> None:
    assert (
        _headers(INSTANCE, 443)["Permissions-Policy"]
        == "camera=(), microphone=(), geolocation=(), payment=()"
    )


def test_the_compose_overlay_sends_the_same_values_as_the_instance() -> None:
    assert _headers(COMPOSE) == _headers(INSTANCE, 443)


@pytest.mark.parametrize("path", [INSTANCE, COMPOSE], ids=lambda p: p.name)
def test_locations_do_not_override_security_headers(path: Path) -> None:
    text = re.sub(r"#.*$", "", path.read_text(), flags=re.MULTILINE)
    assert all(not _ADD_HEADER.search(block) for block in _blocks(text, "location"))


def test_the_compose_overlay_defines_the_map_its_csp_uses() -> None:
    assert "map $uri $mosaiq_script_sources" in COMPOSE.read_text()
