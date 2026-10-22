"""The files and deployment boundaries required of campaigns-service (#481)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
APP = ROOT / "app"
REQUIRED = {
    "Dockerfile",
    "VERSION",
    "app/__init__.py",
    "compose.yaml",
    "nginx/campaigns.conf",
    "pyproject.toml",
    "redis/campaigns.acl",
    "tests/__init__.py",
    "tests/golden/campaigns.json",
    "tests/test_acl.py",
    "tests/test_golden.py",
    "tests/test_layout.py",
    "uv.lock",
}


def test_the_storys_required_files_are_present() -> None:
    present = {
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_file() and "contracts" not in path.relative_to(ROOT).parts[:1]
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
    assert "name: mosaiq-campaigns" in compose
    published = re.findall(r'^\s*-\s*"([^\"]+:\d+:\d+)"\s*$', compose, re.M)
    assert published == ["127.0.0.1:5014:5014"]
    assert "mem_limit: 160m" in compose and "cpus: 0.5" in compose
    assert "depends_on" not in compose
    assert "/campaigns.env" in compose
    for hardening in (
        'user: "10001:10001"',
        "cap_drop: [ALL]",
        'security_opt: ["no-new-privileges:true"]',
        "read_only: true",
        "/tmp:uid=10001,gid=10001,mode=0700",
    ):
        assert hardening in compose


def test_dockerfile_uses_only_its_context_and_the_contracted_gunicorn_shape() -> None:
    code = re.sub(r"#.*", "", (ROOT / "Dockerfile").read_text())
    copies = [
        line.strip()
        for line in code.splitlines()
        if re.match(r"^\s*(COPY|ADD)\s", line, re.I)
    ]
    assert copies
    assert all("../" not in line and "web/" not in line for line in copies)
    assert '"--workers", "1", "--threads", "4"' in code
    assert '"0.0.0.0:5014"' in code and "app:create_app()" in code


def test_nginx_forwards_only_the_campaigns_prefix() -> None:
    conf = (ROOT / "nginx/campaigns.conf").read_text()
    assert "location /api/campaigns/ {" in conf
    assert "proxy_pass http://127.0.0.1:5014/;" in conf
    assert "client_max_body_size 64k;" in conf


def test_the_distributed_stack_and_local_environment_include_campaigns() -> None:
    stack = (REPO / "compose.distributed.yaml").read_text()
    assert "services/campaigns/compose.yaml" in stack
    env = (REPO / ".env.example").read_text()
    assert re.search(r"^REDIS_CAMPAIGNS_PASSWORD=svc_campaigns$", env, re.M)
    local = (REPO / "docker/distributed/svc/campaigns.env").read_text()
    assert (
        "DATABASE_URL=postgresql://svc_campaigns@/retail?host=/run/postgresql" in local
    )
    assert "REDIS_CAMPAIGNS_PASSWORD=svc_campaigns" in local
    assert "MOSAIQ_SERVICE_NAME=campaigns" in local


def test_the_service_never_imports_the_web() -> None:
    offenders = [
        f"{path.name}:{number}"
        for path in APP.rglob("*.py")
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if re.search(r"^\s*(from|import)\s+web(\.|\s|$)", line)
    ]
    assert offenders == []


def test_the_service_never_deletes_a_campaign_or_reads_the_experiment_tables() -> None:
    text = "\n".join(path.read_text() for path in APP.rglob("*.py"))
    assert not re.search(r"delete\s+from", text, re.I)
    assert not re.search(r"(from|join)\s+(public\.)?experiment", text, re.I)
