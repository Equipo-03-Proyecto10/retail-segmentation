"""The files the repository rules ask of a service, and what ADR-0038 forbids."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
FORBIDDEN = re.compile(
    r"\b(delete_one|delete_many|find_one_and_delete|drop_collection|drop_database"
    r"|collMod|createUser)\b|\.drop\("
)


def test_version_is_semver_and_the_kit_is_pinned_by_exact_tag():
    assert re.fullmatch(r"\d+\.\d+\.\d+", (ROOT / "VERSION").read_text().strip())
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert re.search(r"@kit-py/v\d+\.\d+\.\d+#subdirectory=packages/kit-py", pyproject)
    assert "file://" not in pyproject
    lock = (ROOT / "uv.lock").read_text()
    assert "kit-py%2Fv" in lock and 'name = "pymongo"' in lock


def test_compose_publishes_on_loopback_with_the_contracted_limits():
    compose = (ROOT / "compose.yaml").read_text()
    code = re.sub(r"#.*", "", compose)
    assert '"127.0.0.1:5012:5012"' in code
    assert "mem_limit: 160m" in code and "cpus: 0.5" in code
    assert "name: mosaiq-engagement" in code
    assert "depends_on" not in code
    assert "/engagement.env" in code
    assert "mongo:" not in code and "image: mongo" not in code
    assert (
        "no-new-privileges" in code and "read_only: true" in code and "cap_drop" in code
    )


def test_the_dockerfile_runs_one_worker_with_four_threads_from_its_own_context():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert '"--workers", "1", "--threads", "4"' in dockerfile
    assert "app:create_app()" in dockerfile
    assert "COPY app/" in dockerfile and "COPY contracts/" in dockerfile
    assert "../" not in re.sub(r"#.*", "", dockerfile)


def test_nginx_forwards_the_service_prefix_to_its_loopback_port():
    conf = (ROOT / "nginx/engagement.conf").read_text()
    assert "/api/engagement/" in conf and "http://127.0.0.1:5012/" in conf


def test_the_local_stack_includes_the_service_and_documents_its_variables():
    stack = (REPO / "compose.distributed.yaml").read_text()
    assert "services/engagement/compose.yaml" in stack
    env = (REPO / ".env.example").read_text()
    assert re.search(r"^REDIS_ENGAGEMENT_PASSWORD=svc_engagement$", env, re.M)
    password = re.search(r"^MONGO_ENGAGEMENT_PASSWORD=(.+)$", env, re.M).group(1)
    uri = re.search(r"^MONGO_URI=(.+)$", env, re.M).group(1)
    assert f"svc_engagement:{password}@" in uri
    local = (REPO / "docker/distributed/svc/engagement.env").read_text()
    assert (
        f"svc_engagement:{password}@mongodb:27017/engagement?authSource=engagement"
        in local
    )
    assert "REDIS_ENGAGEMENT_PASSWORD=svc_engagement" in local
    assert (
        "REDIS_CACHE_URL=" in local
        and "DATABASE_URL=postgresql://svc_engagement@" in local
    )


def test_the_code_never_deletes_or_reshapes_what_it_does_not_own():
    for path in (ROOT / "app").rglob("*.py"):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            assert not FORBIDDEN.search(line), f"{path.name}:{number}: {line.strip()}"


def test_mongodb_is_named_nowhere_outside_the_service():
    for path in (REPO / "services").glob("*/app/**/*.py"):
        if "engagement" not in path.parts:
            assert not re.search(r"MONGO_URI|pymongo|motor|mongodb", path.read_text())
    for compose in (REPO / "services").glob("*/compose.yaml"):
        assert not re.search(r"^\s*mongo(db)?:", compose.read_text(), re.M)


def test_the_contract_is_the_one_the_image_serves():
    source = REPO / "contracts/engagement"
    staged = ROOT / "contracts/engagement"
    if staged.exists():
        for name in ("openapi.yaml", "engagement.xsd"):
            assert (staged / name).read_bytes() == (source / name).read_bytes()
