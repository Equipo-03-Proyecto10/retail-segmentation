"""The repository guards of the distributed stage (F13-09).

.github/scripts/guards.sh must print nothing on this repository and on a
correctly rendered service, and name the record for each rule a change breaks.
Each case runs it in a scratch git repository holding what the guards read.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COPIED = [
    ".github/scripts/guards.sh",
    "contracts/registry.yaml",
    "deploy/infra/compose.yaml",
    "deploy/templates/service",
    "packages/kit-py/src/mosaiq_kit/auth.py",
    "packages/kit-rs/src/auth.rs",
]

pytestmark = pytest.mark.skipif(
    shutil.which("yq") is None or shutil.which("git") is None,
    reason="the guards need git and the jq-syntax yq that CI pins",
)


def guards(repo: Path) -> subprocess.CompletedProcess[str]:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    return subprocess.run(
        ["bash", ".github/scripts/guards.sh"], cwd=repo, capture_output=True, text=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for relative in COPIED:
        source, target = ROOT / relative, tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    service = tmp_path / "services/products"
    subprocess.run(
        [sys.executable, str(tmp_path / "deploy/templates/service/render.py")]
        + ["products", "5003", "python", str(service)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (service / "VERSION").write_text("0.1.0\n")
    (service / "uv.lock").write_text("version = 1\n")
    (service / "pyproject.toml").write_text(
        'dependencies = ["mosaiq-kit @ git+https://github.com/x/y'
        '@kit-py/v0.1.1#subdirectory=packages/kit-py"]\n'
    )
    (service / "redis").mkdir()
    (service / "redis/products.acl").write_text(
        "user svc_products on {{password}} ~products:cache:*\n"
    )
    (service / "app").mkdir()
    (service / "app/__init__.py").write_text("OUTBOX = 'products_outbox'\n")
    return tmp_path


def write(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_this_repository_passes() -> None:
    result = subprocess.run(
        ["bash", ".github/scripts/guards.sh"], cwd=ROOT, capture_output=True, text=True
    )
    assert (result.returncode, result.stdout) == (0, "")


def test_a_rendered_service_passes(repo: Path) -> None:
    result = guards(repo)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


@pytest.mark.parametrize(
    ("relative", "text", "record"),
    [
        ("services/products/app/rules.py", "from web.db import users\n", "ADR-0045"),
        (
            "services/products/Cargo.lock",
            "version = 3\n",
            "ADR-0045: services/products has 2 lock files",
        ),
        (
            "services/products/pyproject.toml",
            "dependencies = []\n",
            "ADR-0045: services/products/pyproject.toml does not pin",
        ),
        ("services/products/app/token.py", "ALGORITHM = 'HS256'\n", "ADR-0036"),
        (
            "services/products/app/sign.py",
            "jwt.encode(claims, key)\n",
            "ADR-0036 signing outside auth",
        ),
        (
            "services/products/app/store.py",
            "import pymongo\n",
            "ADR-0038 MongoDB named outside engagement",
        ),
        (
            "services/products/app/relay.py",
            "client.xadd('stream:x', {})\n",
            "ADR-0044 direct XADD",
        ),
        (
            "services/products/app/wake.py",
            "LISTEN = 'sales_outbox'\n",
            "ADR-0044: products names sales_outbox",
        ),
        (
            "services/products/sql/patch.sql",
            "CREATE TABLE products.extra (id int);\n",
            "ADR-0046",
        ),
        (
            "services/sales/README.md",
            "Sales.\n",
            "ADR-0045: services/sales has no golden fixtures",
        ),
        (
            ".github/workflows/deploy-products.yml",
            "image: mosaiq-products\n",
            "ADR-0040 a service named",
        ),
        ("web/cache.py", "import redis\n", "ADR-0037 the web names Redis"),
        (
            "services/products/nginx/products.conf",
            "location /api/products/ {}\n",
            "ADR-0035",
        ),
    ],
)
def test_each_broken_rule_is_named(
    repo: Path, relative: str, text: str, record: str
) -> None:
    write(repo, relative, text)
    result = guards(repo)
    assert result.returncode == 1
    assert record in result.stdout, result.stdout


def test_a_services_tests_may_sign_throwaway_tokens(repo: Path) -> None:
    write(repo, "services/products/tests/conftest.py", "jwt.encode(claims, key)\n")
    result = guards(repo)
    assert (result.returncode, result.stdout) == (0, "")


def test_a_compose_project_with_depends_on_is_named(repo: Path) -> None:
    compose = repo / "services/products/compose.yaml"
    compose.write_text(
        compose.read_text().replace("    restart:", "    depends_on: [x]\n    restart:")
    )
    result = guards(repo)
    assert "ADR-0040: services/products/compose.yaml has depends_on" in result.stdout
