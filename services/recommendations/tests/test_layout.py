"""The files and deployment boundaries required of recommendations-service (#477)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
REQUIRED = {
    "Dockerfile",
    "VERSION",
    "app/__init__.py",
    "compose.yaml",
    "nginx/recommendations.conf",
    "pyproject.toml",
    "redis/recommendations.acl",
    "tests/__init__.py",
    "tests/golden/recommendations.json",
    "tests/test_acl.py",
    "tests/test_golden.py",
    "tests/test_layout.py",
    "uv.lock",
}


def test_the_storys_required_files_are_present() -> None:
    present = {
        str(path.relative_to(ROOT)) for path in ROOT.rglob("*") if path.is_file()
    }
    assert REQUIRED <= present


def test_version_is_semver_and_uv_lock_pins_the_kit_by_exact_tag() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", (ROOT / "VERSION").read_text().strip())
    lock = (ROOT / "uv.lock").read_text()
    source = re.search(
        r'name = "mosaiq-kit"\nversion = "([^"]+)"\n' r'source = \{ git = "([^"]+)" \}',
        lock,
    )
    assert source is not None
    version, url = source.groups()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    tag = re.search(r"[?&]rev=kit-py%2Fv(\d+\.\d+\.\d+)#", url)
    assert tag is not None and tag.group(1) == version
    assert "file://" not in url


def test_compose_is_one_hardened_independent_loopback_service() -> None:
    compose = re.sub(r"#.*", "", (ROOT / "compose.yaml").read_text())
    assert "name: mosaiq-recommendations" in compose
    published = re.findall(r'^\s*-\s*"([^\"]+:\d+:\d+)"\s*$', compose, re.M)
    assert published == ["127.0.0.1:5011:5011"]
    assert "mem_limit: 192m" in compose and "cpus: 0.5" in compose
    assert "depends_on" not in compose
    assert "/recommendations.env" in compose
    for hardening in (
        'user: "10001:10001"',
        "cap_drop: [ALL]",
        'security_opt: ["no-new-privileges:true"]',
        "read_only: true",
        "/tmp:uid=10001,gid=10001,mode=0700",
    ):
        assert hardening in compose


def test_dockerfile_uses_only_its_context_and_the_contracted_gunicorn_shape() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    code = re.sub(r"#.*", "", dockerfile)
    copy_or_add = [
        line.strip()
        for line in code.splitlines()
        if re.match(r"^\s*(COPY|ADD)\s", line, re.I)
    ]
    assert copy_or_add
    assert all("../" not in line and "web/" not in line for line in copy_or_add)
    assert '"--workers", "1", "--threads", "4"' in code
    assert '"0.0.0.0:5011"' in code and "app:create_app()" in code


def test_nginx_forwards_only_its_prefix_with_required_headers_and_cap() -> None:
    conf = (ROOT / "nginx/recommendations.conf").read_text()
    assert "location /api/recommendations/ {" in conf
    assert "proxy_pass http://127.0.0.1:5011/;" in conf
    assert "proxy_set_header X-Real-IP $remote_addr;" in conf
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in conf
    assert "client_max_body_size 64k;" in conf


def test_the_distributed_stack_and_local_environment_include_recommendations() -> None:
    stack = (REPO / "compose.distributed.yaml").read_text()
    assert "services/recommendations/compose.yaml" in stack
    env = (REPO / ".env.example").read_text()
    assert re.search(r"^REDIS_RECOMMENDATIONS_PASSWORD=svc_recommendations$", env, re.M)
    local = (REPO / "docker/distributed/svc/recommendations.env").read_text()
    assert (
        "DATABASE_URL=postgresql://svc_recommendations@/retail?host=/run/postgresql"
        in local
    )
    assert "REDIS_STATE_URL=redis://redis-state:6379/0" in local
    assert "REDIS_RECOMMENDATIONS_PASSWORD=svc_recommendations" in local
    assert "AUTH_JWKS_PATH=/run/mosaiq/jwks.json" in local
    assert "MOSAIQ_SERVICE_NAME=recommendations" in local
