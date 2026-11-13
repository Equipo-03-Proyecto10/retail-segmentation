"""The files and deployment boundaries required of model-registry-service (#806)."""

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
    "nginx/model-registry.conf",
    "pyproject.toml",
    "redis/model-registry.acl",
    "tests/__init__.py",
    "uv.lock",
}


def test_the_storys_required_files_are_present() -> None:
    present = {
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".venv" not in path.parts
        and "contracts" not in path.parts
        and "__pycache__" not in path.parts
        and ".pytest_cache" not in path.parts
        and ".ruff_cache" not in path.parts
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
    tag = re.search(r"[?&]rev=kit-py%2Fv(\d+\.\d+\.\d+)#", url)
    assert tag is not None and tag.group(1) == version
    assert "file://" not in url


def test_compose_is_one_hardened_independent_loopback_service() -> None:
    compose = re.sub(r"#.*", "", (ROOT / "compose.yaml").read_text())
    assert "name: mosaiq-model-registry" in compose
    published = re.findall(r'^\s*-\s*"([^\"]+:\d+:\d+)"\s*$', compose, re.M)
    assert published == ["127.0.0.1:5019:5019"]
    assert "mem_limit: 160m" in compose and "cpus: 0.5" in compose
    assert "depends_on" not in compose
    assert "/model-registry.env" in compose
    for hardening in (
        'user: "10001:10001"',
        "cap_drop: [ALL]",
        'security_opt: ["no-new-privileges:true"]',
        "read_only: true",
    ):
        assert hardening in compose


def test_dockerfile_uses_only_its_context() -> None:
    code = re.sub(r"#.*", "", (ROOT / "Dockerfile").read_text())
    copies = [
        line.strip()
        for line in code.splitlines()
        if re.match(r"^\s*(COPY|ADD)\s", line, re.I)
    ]
    assert copies
    assert all("../" not in line and "web/" not in line for line in copies)
    assert '"0.0.0.0:5019"' in code and "app:create_app()" in code


def test_nginx_forwards_only_its_prefix() -> None:
    conf = (ROOT / "nginx/model-registry.conf").read_text()
    assert "location /api/model-registry/ {" in conf
    assert "proxy_pass http://127.0.0.1:5019/;" in conf
    assert "client_max_body_size 64k;" in conf


def test_the_redis_acl_is_the_one_models_user_line() -> None:
    assert (ROOT / "redis/model-registry.acl").read_text().splitlines() == [
        "user svc_models on {{password}} resetchannels -@all +@connection"
        " +@read +@write -@dangerous -scan -randomkey ~models:rl:*"
        " (%R~pub:auth:v1:live:* +exists)"
        " (~stream:models.v1.publication_recorded +xadd)"
    ]


def test_the_distributed_stack_and_local_environment_include_the_service() -> None:
    assert (
        "services/model-registry/compose.yaml"
        in (REPO / "compose.distributed.yaml").read_text()
    )
    env = (REPO / ".env.example").read_text()
    assert re.search(r"^REDIS_MODELS_PASSWORD=svc_models$", env, re.M)
    assert re.search(r"^SVC_MODELS_DB_PASSWORD=svc_models$", env, re.M)
    local = (REPO / "docker/distributed/svc/model-registry.env").read_text()
    assert "DATABASE_URL=postgresql://svc_models@/retail?host=/run/postgresql" in local
    assert "REDIS_MODELS_PASSWORD=svc_models" in local
    assert "MOSAIQ_SERVICE_NAME=model-registry" in local
