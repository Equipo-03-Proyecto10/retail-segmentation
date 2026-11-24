"""The Swagger UI page at /api/docs/ (F29-16, #1192, ADR-0090)."""

from __future__ import annotations

import hashlib
import importlib.util
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs/api-docs"
VENDOR = PAGE / "vendor"
VERSION = "5.34.1"
INSTANCE = ROOT / "deploy/nginx/mosaiq.conf"
GATEWAY = ROOT / "docker/distributed/gateway/default.conf"
REGISTRY = ROOT / "contracts/registry.yaml"
_ADD_HEADER = re.compile(r'^\s*add_header\s+(\S+)\s+"([^"]*)"\s+always;', re.MULTILINE)

_spec = importlib.util.spec_from_file_location(
    "api_docs_config", ROOT / ".github/scripts/api_docs_config.py"
)
api_docs_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api_docs_config)


def _blocks(text: str, opening: str) -> list[str]:
    """Return the bodies of the blocks opened by ``opening``."""
    text = re.sub(r"#.*$", "", text, flags=re.MULTILINE)
    bodies = []
    for match in re.finditer(opening + r"\s*\{", text):
        depth = 1
        for index in range(match.end(), len(text)):
            depth += text[index] == "{"
            depth -= text[index] == "}"
            if depth == 0:
                bodies.append(text[match.end() : index])
                break
        else:
            raise AssertionError("unclosed NGINX block")
    return bodies


def _block(text: str, opening: str) -> str:
    bodies = _blocks(text, opening)
    assert len(bodies) == 1, f"expected one {opening!r} block"
    return bodies[0]


def _https_server() -> str:
    servers = [
        body
        for body in _blocks(INSTANCE.read_text(), r"\bserver")
        if re.search(r"^\s*listen 443\b", body, re.MULTILINE)
    ]
    assert len(servers) == 1
    return servers[0]


def _direct(block: str) -> str:
    """The lines of ``block`` outside its nested blocks."""
    lines, depth = [], 0
    for line in block.splitlines():
        if depth == 0:
            lines.append(line)
        depth += line.count("{") - line.count("}")
    return "\n".join(lines)


def test_service_list_is_generated_from_the_registry() -> None:
    committed = (PAGE / "swagger-config.json").read_text(encoding="utf-8")
    assert (
        committed == api_docs_config.generate()
    ), "run .github/scripts/api_docs_config.py"


def test_every_registered_service_points_at_its_served_contract() -> None:
    import json

    names = [
        entry["name"] for entry in yaml.safe_load(REGISTRY.read_text())["services"]
    ]
    urls = json.loads((PAGE / "swagger-config.json").read_text())["urls"]
    assert [entry["name"] for entry in urls] == names
    assert all(e["url"] == f"/api/{e['name']}/openapi.yaml" for e in urls)


def test_docs_is_reserved_and_never_a_service_name() -> None:
    names = {
        entry["name"] for entry in yaml.safe_load(REGISTRY.read_text())["services"]
    }
    assert "docs" not in names


def test_the_page_holds_no_copy_of_a_contract() -> None:
    copies = [p for p in PAGE.rglob("*") if p.suffix in {".yaml", ".yml", ".xsd"}]
    assert copies == []


def test_vendored_assets_match_their_pinned_checksums() -> None:
    lines = (VENDOR / "SHA256SUMS").read_text().splitlines()
    pinned = dict(reversed(line.split(maxsplit=1)) for line in lines)
    assert set(pinned) | {"SHA256SUMS"} == {p.name for p in VENDOR.iterdir()}
    for name, digest in pinned.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, name
    bundle = (VENDOR / "swagger-ui-bundle.js").read_text(encoding="utf-8")
    assert f'PACKAGE_VERSION:"{VERSION}"' in bundle
    assert f"**{VERSION}**" in (PAGE / "README.md").read_text()


class _Page(HTMLParser):
    """Scripts, event handlers and references of a page, in any letter case.

    HTMLParser lower-cases tag and attribute names, so ``<SCRIPT SRC=…>`` and
    ``onLoad=`` are found as ``script``/``src`` and ``onload``.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[dict[str, str | None]] = []
        self.script_text: list[str] = []
        self.handlers: list[str] = []
        self.references: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handlers += [name for name, _ in attrs if name.startswith("on")]
        self.references += [
            value or "" for name, value in attrs if name in {"src", "href"}
        ]
        if tag == "script":
            self.scripts.append(dict(attrs))
            self._in_script = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script and data.strip():
            self.script_text.append(data)


def test_the_page_loads_only_local_assets_and_no_inline_script() -> None:
    page = _Page()
    page.feed((PAGE / "index.html").read_text(encoding="utf-8"))
    page.close()
    assert page.scripts, "the page loads its scripts"
    assert all(attrs.get("src") for attrs in page.scripts), "every script has a src"
    assert page.script_text == [], "no inline script body"
    assert page.handlers == [], "no inline event handler"
    external = [
        ref
        for ref in page.references
        if ref.startswith("//") or urlsplit(ref).scheme.lower() not in {"", "data"}
    ]
    assert external == [], "no external reference"
    for ref in page.references:
        if not ref.startswith("data:"):
            assert (PAGE / ref).is_file(), ref


def test_the_setup_stores_no_credentials_and_calls_no_third_party() -> None:
    script = (PAGE / "init.js").read_text(encoding="utf-8")
    code = re.sub(r"//.*$", "", script, flags=re.MULTILINE)
    assert "persistAuthorization: false" in code
    assert "withCredentials: false" in code
    assert "queryConfigEnabled: false" in code
    assert 'validatorUrl: "none"' in code
    assert not re.search(r"https?://", code)
    assert not re.search(r"localStorage|sessionStorage|document\.cookie", code)
    assert not re.search(r"\b(authorization|bearer)\b\s*:", code, re.IGNORECASE)


def test_instance_serves_the_page_from_the_published_docs_copy() -> None:
    location = _block(_https_server(), r"location /api/docs/")
    assert re.search(r"^\s*alias /opt/mosaiq/docs/api-docs/;", location, re.MULTILINE)
    # No add_header of its own, so the server's CSP and headers are inherited.
    assert "add_header" not in location
    deploy = (ROOT / "deploy/deploy.sh").read_text()
    assert "DOCS_DST=${DOCS_DST:-/opt/mosaiq/docs}" in deploy


def test_gateway_page_sends_the_instance_security_headers() -> None:
    expected = dict(_ADD_HEADER.findall(_direct(_https_server())))
    assert expected
    location = _block(GATEWAY.read_text(), r"location /api/docs/")
    assert re.search(r"^\s*alias /usr/share/nginx/api-docs/;", location, re.MULTILINE)
    assert dict(_ADD_HEADER.findall(location)) == expected


def test_gateway_mounts_the_page_read_only() -> None:
    local = yaml.safe_load((ROOT / "compose.distributed.yaml").read_text())
    volumes = local["services"]["gateway"]["volumes"]
    assert "./docs/api-docs:/usr/share/nginx/api-docs:ro" in volumes
