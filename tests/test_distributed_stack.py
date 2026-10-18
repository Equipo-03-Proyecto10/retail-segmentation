"""The local distributed stack and its gateway (F13-08).

compose.distributed.yaml runs the whole distributed stage on a laptop; it must
include every service project, and its gateway's configuration is generated
from the registry. The instance never reads any of it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "docker/distributed/gateway"

_spec = importlib.util.spec_from_file_location(
    "gateway_generate", GATEWAY / "generate.py"
)
gateway = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gateway)


def test_gateway_configuration_is_generated_from_the_registry() -> None:
    committed = (GATEWAY / "default.conf").read_text()
    assert committed == gateway.generate(), "run docker/distributed/gateway/generate.py"
    registry = yaml.safe_load((ROOT / "contracts/registry.yaml").read_text())
    for entry in registry["services"]:
        assert f"location /api/{entry['name']}/ {{" in committed
        assert f"{entry['name']}:{entry['port']};" in committed
    for line in ("client_max_body_size 64k;", "X-Real-IP $remote_addr;", "resolver "):
        assert line in committed


def test_every_service_project_has_a_gateway_location() -> None:
    committed = (GATEWAY / "default.conf").read_text()
    for compose in sorted(ROOT.glob("services/*/compose.yaml")):
        name = compose.parent.name
        assert f"location /api/{name}/ {{" in committed, name


def test_local_stack_includes_exactly_the_service_projects() -> None:
    local = yaml.safe_load((ROOT / "compose.distributed.yaml").read_text())
    included = set()
    for entry in local["include"]:
        paths = entry["path"] if isinstance(entry, dict) else entry
        paths = [paths] if isinstance(paths, str) else paths
        if paths[0].startswith("services/"):
            included.add(paths[0])
    on_disk = {
        compose.relative_to(ROOT).as_posix()
        for compose in ROOT.glob("services/*/compose.yaml")
    }
    assert included == on_disk
    assert local["networks"] == {"mosaiq": {"external": True}}
    gateway_service = local["services"]["gateway"]
    assert gateway_service["image"] == "nginx:1.27-alpine"
    assert gateway_service["ports"] == ["127.0.0.1:${MOSAIQ_GATEWAY_PORT:-8080}:8080"]


def test_the_instance_never_reads_the_local_stack() -> None:
    paths = [p for p in (ROOT / "deploy").rglob("*") if p.is_file()]
    paths += list((ROOT / ".github/workflows").glob("deploy-service.yml"))
    for path in paths:
        assert "compose.distributed" not in path.read_text(errors="ignore"), path
    assert not any("gateway" in p.parts for p in (ROOT / "deploy").rglob("*"))
