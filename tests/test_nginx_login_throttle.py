"""The reverse proxy shares client throttling across application workers."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = [ROOT / "deploy/nginx/mosaiq.conf", ROOT / "deploy/nginx/mosaiq.compose.conf"]


@pytest.mark.parametrize("path", CONFIGS, ids=lambda path: path.name)
def test_login_has_a_shared_client_limit(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert re.search(
        r"map\s+\$request_method\s+\$mosaiq_login_rate_key\s*\{\s*"
        r'default\s+"";\s*POST\s+\$binary_remote_addr;\s*\}',
        text,
    )
    assert (
        "limit_req_zone $mosaiq_login_rate_key zone=mosaiq_login:10m rate=6r/m;" in text
    )
    assert "location = /login" in text
    assert "limit_req zone=mosaiq_login burst=5 nodelay;" in text
    assert "limit_req_status 429;" in text
