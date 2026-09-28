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
EXPECTED = {
    "Strict-Transport-Security",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Content-Security-Policy",
    "Permissions-Policy",
}


def _headers(path: Path) -> dict[str, str]:
    return dict(_ADD_HEADER.findall(path.read_text()))


@pytest.mark.parametrize("path", [INSTANCE, COMPOSE], ids=lambda p: p.name)
def test_every_security_header_is_sent(path: Path) -> None:
    assert set(_headers(path)) == EXPECTED


def test_permissions_policy_denies_what_the_application_does_not_use() -> None:
    assert (
        _headers(INSTANCE)["Permissions-Policy"]
        == "camera=(), microphone=(), geolocation=(), payment=()"
    )


def test_the_compose_overlay_sends_the_same_values_as_the_instance() -> None:
    assert _headers(COMPOSE) == _headers(INSTANCE)


def test_the_compose_overlay_defines_the_map_its_csp_uses() -> None:
    assert "map $uri $mosaiq_script_sources" in COMPOSE.read_text()
