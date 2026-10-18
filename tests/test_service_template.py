"""The per-service compose template and its NGINX include (F13-08).

ADR-0040 fixes what each services/<name>/ project declares; ADR-0035 fixes the
envelope NGINX answers a refused body with. These checks read the files.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# The SQL job installs only the web's requirements; its `pytest -m postgres`
# still collects this module, so it skips there rather than failing to import.
yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy/templates/service"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render = _load("service_template_render", TEMPLATE / "render.py")

CASES = [
    ("products", 5003, "python"),
    ("stores", 5005, "rust"),
    ("auth", 5001, "python"),
]


@pytest.fixture(params=CASES, ids=[case[0] for case in CASES])
def rendered(request, tmp_path: Path):
    name, port, kind = request.param
    render.render(name, port, kind, tmp_path, None)
    compose = yaml.safe_load((tmp_path / "compose.yaml").read_text())
    return name, port, kind, tmp_path, compose


def test_compose_is_one_hardened_container(rendered) -> None:
    name, port, kind, _, compose = rendered
    assert compose["name"] == f"mosaiq-{name}"
    assert list(compose["services"]) == [name]
    service = compose["services"][name]
    assert service["image"] == (
        f"ghcr.io/equipo-03-proyecto10/mosaiq-{name}:${{IMAGE_TAG}}"
    )
    assert service["build"] == {"context": "."}
    assert service["ports"] == [f"127.0.0.1:{port}:{port}"]
    assert str(service["cpus"]) == {"python": "0.5", "rust": "0.25"}[kind]
    assert service["mem_limit"]
    assert service["restart"] == "unless-stopped"
    assert "depends_on" not in service
    assert service["user"] == "10001:10001"
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["read_only"] is True
    assert any(entry.startswith("/tmp:") for entry in service["tmpfs"])
    assert service["env_file"] == [
        f"${{MOSAIQ_SVC_ENV_DIR:-/etc/mosaiq/svc}}/{name}.env"
    ]
    assert service["networks"] == ["mosaiq"]
    assert compose["networks"] == {"mosaiq": {"external": True}}


def test_mounts_are_read_only_and_the_key_is_auths_alone(rendered) -> None:
    name, _, _, _, compose = rendered
    volumes = compose["services"][name]["volumes"]
    assert all(volume.endswith(":ro") for volume in volumes)
    assert not any(volume.endswith(":z") or ":z," in volume for volume in volumes)
    targets = [volume.split(":")[-2] for volume in volumes]
    assert "/run/postgresql" in targets
    assert "/run/mosaiq/jwks.json" in targets
    keys = [volume for volume in volumes if ".pem" in volume]
    assert len(keys) == (1 if name == "auth" else 0)


def test_nginx_include_proxies_with_overwritten_headers(rendered) -> None:
    name, port, _, outdir, _ = rendered
    text = (outdir / "nginx" / f"{name}.conf").read_text()
    location = re.search(rf"location /api/{name}/ \{{(.*?)\n\}}", text, re.S)
    assert location, "no location /api/<name>/"
    body = location.group(1)
    assert f"proxy_pass http://127.0.0.1:{port}/;" in body
    assert "proxy_set_header X-Real-IP $remote_addr;" in body
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in body
    assert "$proxy_add_x_forwarded_for" not in text
    assert "client_max_body_size 64k;" in body
    assert f"error_page 413 @mosaiq_{name}_413_$mosaiq_error_format;" in body
    assert "location" not in body, "no nested location"
    assert not re.search(r"^\s*#auth#", text, re.M)
    for fmt, mime in (("json", "application/json"), ("xml", "application/xml")):
        named = re.search(
            rf"location @mosaiq_{name}_413_{fmt} \{{(.*?)\n\}}", text, re.S
        )
        assert named, f"no @mosaiq_{name}_413_{fmt}"
        assert f"default_type {mime};" in named.group(1)
        assert "return 413 " in named.group(1)
        assert "payload_too_large" in named.group(1)
    for header in re.findall(r"^\s*add_header .*$", text, re.M):
        assert header.rstrip().endswith("always;"), header
    signin = [
        "limit_req zone=mosaiq_auth_signin burst=5 nodelay;",
        "limit_req_status 429;",
        "error_page 429 @mosaiq_auth_429_$mosaiq_error_format;",
    ]
    if name == "auth":
        assert all(line in body for line in signin)
        assert "location @mosaiq_auth_429_json" in text
        assert "location @mosaiq_auth_429_xml" in text
        assert text.count("add_header Retry-After 10 always;") == 2
    else:
        assert "limit_req" not in text


def test_dockerfile_builds_from_its_own_directory(rendered) -> None:
    *_, outdir, _ = rendered
    text = (outdir / "Dockerfile").read_text()
    copies = re.findall(r"^(?:COPY|ADD)\s.*$", text, re.M)
    assert copies
    for line in copies:
        assert "web/" not in line and "../" not in line, line
    assert re.search(r"^COPY contracts/ ", text, re.M)
    assert re.search(r"^USER 10001:10001$", text, re.M)


def test_render_refuses_a_bad_name_or_port(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        render.render("Products", 5003, "python", tmp_path, None)
    with pytest.raises(ValueError):
        render.render("products", 80, "python", tmp_path, None)


def _envelope(kind: str) -> str:
    text = render.render_nginx("auth", 5001)
    match = re.search(
        rf"location @mosaiq_auth_{kind}_xml \{{.*?return \d+ '(.*?)';", text, re.S
    )
    return (
        match.group(1)
        .replace("\\n", "\n")
        .replace("$mosaiq_correlation_id", "0f3c9a5e7d1b4c2a")
    )


@pytest.mark.skipif(shutil.which("xmllint") is None, reason="xmllint not installed")
@pytest.mark.parametrize("kind", ["413", "429"])
def test_xml_envelope_validates_against_common_xsd(kind: str, tmp_path: Path) -> None:
    body = tmp_path / "error.xml"
    body.write_text(_envelope(kind))
    result = subprocess.run(
        [
            "xmllint",
            "--noout",
            "--nonet",
            "--schema",
            str(ROOT / "contracts/common/v1/common.xsd"),
            str(body),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_instance_http_context_declares_the_maps_once() -> None:
    text = (ROOT / "deploy/nginx/mosaiq.conf").read_text()
    assert text.count("map $http_accept $mosaiq_error_format") == 1
    assert text.count("map $http_x_correlation_id $mosaiq_correlation_id") == 1
    assert re.search(
        r'map "\$request_method:\$uri" \$mosaiq_auth_signin_key \{\s*default "";\s*'
        r'"POST:/api/auth/v1/sessions" \$binary_remote_addr;\s*\}',
        text,
    )
    zone = "limit_req_zone $mosaiq_auth_signin_key zone=mosaiq_auth_signin:10m"
    assert text.count(zone + " rate=6r/m;") == 1
    # Quoted: unquoted, NGINX reads the regex's braces as a block.
    assert '"~^[0-9A-Za-z-]{1,64}$" $http_x_correlation_id;' in text


def test_infrastructure_network_is_external() -> None:
    infra = yaml.safe_load((ROOT / "deploy/infra/compose.yaml").read_text())
    assert infra["networks"] == {"mosaiq": {"external": True}}
    assert (
        "docker network create mosaiq" in (ROOT / "deploy/infra/README.md").read_text()
    )
