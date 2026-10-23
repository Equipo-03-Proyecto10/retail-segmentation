"""The committed service includes form the API edge contract (F18-02, #452)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
INSTANCE = ROOT / "deploy/nginx/mosaiq.conf"
REGISTRY = ROOT / "contracts/registry.yaml"
INCLUDE = re.compile(r"include /etc/nginx/mosaiq-api\.d/\*\.conf;")


def _blocks(text: str, opening: str) -> list[str]:
    """Return bodies for blocks whose opening text matches ``opening``."""
    text = re.sub(r"#.*$", "", text, flags=re.MULTILINE)
    blocks = []
    for match in re.finditer(opening + r"\s*\{", text):
        depth = 1
        for index in range(match.end(), len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(text[match.end() : index])
                    break
        else:
            raise AssertionError("unclosed NGINX block")
    return blocks


def _registry_services() -> list[dict[str, object]]:
    return yaml.safe_load(REGISTRY.read_text())["services"]


def test_instance_include_is_only_in_the_443_server() -> None:
    text = INSTANCE.read_text()
    servers = _blocks(text, r"\bserver\b")
    assert len(servers) == 2
    http = [
        block for block in servers if re.search(r"listen\s+(?:\[::\]:)?80\b", block)
    ]
    https = [
        block for block in servers if re.search(r"listen\s+(?:\[::\]:)?443\b", block)
    ]
    assert len(http) == len(https) == 1
    assert len(INCLUDE.findall(text)) == 1
    assert not INCLUDE.search(http[0])
    assert INCLUDE.search(https[0])
    assert https[0].index("include /etc/nginx/mosaiq-api.d/*.conf;") < https[0].index(
        "location /docs/"
    )


def test_instance_declares_only_the_two_rate_limit_zones() -> None:
    text = INSTANCE.read_text()
    zones = re.findall(r"^\s*limit_req_zone\s+.*$", text, flags=re.MULTILINE)
    assert len(zones) == 2
    assert any("zone=mosaiq_login:" in zone for zone in zones)
    assert any("zone=mosaiq_auth_signin:" in zone for zone in zones)


def test_auth_signin_map_counts_only_post_sessions() -> None:
    text = INSTANCE.read_text()
    maps = _blocks(
        text,
        r'map\s+"\$request_method:\$uri"\s+\$mosaiq_auth_signin_key',
    )
    assert len(maps) == 1
    directives = [line.strip() for line in maps[0].splitlines() if line.strip()]
    assert directives == [
        'default "";',
        '"POST:/api/auth/v1/sessions" $binary_remote_addr;',
    ]


@pytest.mark.parametrize("service", _registry_services(), ids=lambda item: item["name"])
def test_committed_include_matches_registry_and_error_contract(service) -> None:
    name = service["name"]
    path = ROOT / "services" / name / "nginx" / f"{name}.conf"
    if not path.exists():
        pytest.skip("service has no committed NGINX include")

    text = path.read_text()
    location = _blocks(text, rf"\blocation\s+/api/{re.escape(name)}/")
    assert len(location) == 1
    body = location[0]
    assert f"proxy_pass http://127.0.0.1:{service['port']}/;" in body
    assert "proxy_set_header X-Real-IP $remote_addr;" in body
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in body
    assert "$proxy_add_x_forwarded_for" not in text
    assert "client_max_body_size 64k;" in body
    assert f"error_page 413 @mosaiq_{name}_413_$mosaiq_error_format;" in body
    assert not re.search(r"\blocation\b", body)
    assert not re.search(r"^\s*add_header\b", body, flags=re.MULTILINE)

    for fmt in ("json", "xml"):
        named = _blocks(text, rf"\blocation\s+@mosaiq_{name}_413_{fmt}")
        assert len(named) == 1
        assert "return 413 " in named[0]
        assert "payload_too_large" in named[0]
        headers = re.findall(r"^\s*add_header\b.*$", named[0], flags=re.MULTILINE)
        assert headers
        assert all(header.rstrip().endswith("always;") for header in headers)

    if name == "auth":
        assert "limit_req zone=mosaiq_auth_signin burst=5 nodelay;" in body
        assert "limit_req_status 429;" in body
        assert "error_page 429 @mosaiq_auth_429_$mosaiq_error_format;" in body
        for fmt in ("json", "xml"):
            named = _blocks(text, rf"\blocation\s+@mosaiq_auth_429_{fmt}")
            assert len(named) == 1
            assert "return 429 " in named[0]
            assert "rate_limited" in named[0]
    else:
        assert "limit_req" not in text
