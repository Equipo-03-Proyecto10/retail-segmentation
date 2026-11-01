"""Contract convention checks from issue #412."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "contracts/check.sh"
FIXTURES = ROOT / "tests/fixtures/contracts"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _copy_contents(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, dirs_exist_ok=True)


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "contracts@example.test")
    _git(repo, "config", "user.name", "Contract tests")

    base_fixture = FIXTURES / "base"
    for child in base_fixture.iterdir():
        if child.name != "services":
            target = repo / child.name
            if child.is_dir():
                _copy_contents(child, target)
            else:
                shutil.copy2(child, target)
    _commit(repo, "contracts before code")
    _copy_contents(base_fixture / "services", repo / "services")
    base = _commit(repo, "add alpha implementation")
    return repo, base


def _run(repo: Path, base: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["BASE"] = base
    return subprocess.run(
        ["bash", str(CHECK)],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
    )


def _write(path: Path, text: str) -> None:
    path.write_text(text)


def _replace(path: Path, old: str, new: str, count: int = -1) -> None:
    text = path.read_text()
    assert old in text
    _write(path, text.replace(old, new, count))


def _bump_contract(repo: Path, service: str = "alpha") -> None:
    _replace(
        repo / f"contracts/{service}/openapi.yaml",
        "version: 1.0.0",
        "version: 1.0.1",
    )


def _assert_fails(result: subprocess.CompletedProcess[str], message: str) -> None:
    assert result.returncode != 0, result.stdout + result.stderr
    assert message in result.stdout, result.stdout + result.stderr


def _operation(path: str) -> str:
    parameter = ""
    if "{customer_id}" in path:
        parameter = (
            "    parameters:\n"
            "      - name: customer_id\n"
            "        in: path\n"
            "        required: true\n"
            "        schema:\n"
            "          type: integer\n"
        )
    return (
        f"  {path}:\n"
        f"{parameter}"
        "    get:\n"
        "      security:\n"
        "        - BearerAuth: []\n"
        "      x-mosaiq-roles: [ADMIN]\n"
        "      responses:\n"
        '        "200":\n'
        "          description: Fixture response\n"
    )


def _add_paths(repo: Path, *paths: str) -> None:
    openapi = repo / "contracts/alpha/openapi.yaml"
    additions = "".join(_operation(path) for path in paths)
    _replace(openapi, "components:\n", additions + "components:\n")


def test_clean_tree_passes_without_output(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)

    result = _run(repo, base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_a_resource_registered_twice_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    registry = repo / "contracts/registry.yaml"
    _replace(registry, "resources: [beta-items,", "resources: [customers, beta-items,")
    _commit(repo, "duplicate a resource")

    _assert_fails(_run(repo, base), "/v1/customers: registered to alpha and beta")


def test_a_foreign_resource_nested_in_a_path_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    _add_paths(repo, "/v1/customers/{customer_id}/sales")
    _bump_contract(repo)
    _commit(repo, "nest sales under customers")

    _assert_fails(
        _run(repo, base),
        "alpha: /v1/customers/{customer_id}/sales nests sales, registered to beta",
    )


def test_an_unversioned_path_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    _add_paths(repo, "/customers")
    _bump_contract(repo)
    _commit(repo, "add an unversioned path")

    _assert_fails(
        _run(repo, base), "alpha: /customers is neither versioned nor a meta route"
    )


def test_a_wrong_xsd_namespace_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    xsd = repo / "contracts/alpha/alpha.xsd"
    _replace(
        xsd,
        'targetNamespace="https://mosaiq.maxthecoder.online/xml/alpha/v1"',
        'targetNamespace="https://mosaiq.maxthecoder.online/xml/wrong/v1"',
    )
    _bump_contract(repo)
    _commit(repo, "use the wrong namespace")

    _assert_fails(_run(repo, base), "wrong namespace: contracts/alpha/alpha.xsd")


def test_an_import_other_than_common_v1_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    xsd = repo / "contracts/alpha/alpha.xsd"
    _replace(xsd, "../common/v1/common.xsd", "../foreign/v1/foreign.xsd")
    _bump_contract(repo)
    _commit(repo, "import a foreign schema")

    _assert_fails(_run(repo, base), "imports more than common/v1: alpha")


def test_an_invalid_example_that_validates_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    valid = (repo / "contracts/alpha/examples/alpha.xml").read_text()
    _write(repo / "contracts/alpha/examples/alpha.invalid.xml", valid)
    _bump_contract(repo)
    _commit(repo, "make the invalid example valid")

    _assert_fails(
        _run(repo, base),
        "invalid example accepted: contracts/alpha/examples/alpha.invalid.xml",
    )


def test_an_oasdiff_breaking_change_within_v1_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    openapi = repo / "contracts/alpha/openapi.yaml"
    text = openapi.read_text()
    before, remainder = text.split("paths:\n", 1)
    _, after = remainder.split("components:\n", 1)
    _write(openapi, before + "paths: {}\ncomponents:\n" + after)
    _bump_contract(repo)
    _commit(repo, "remove a v1 operation")

    _assert_fails(_run(repo, base), "breaking OpenAPI change in v1: alpha")


def test_an_element_added_to_a_v1_response_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    xsd = repo / "contracts/alpha/alpha.xsd"
    _replace(
        xsd,
        '      <xs:element name="name" type="xs:string"/>',
        '      <xs:element name="name" type="xs:string"/>\n'
        '      <xs:element name="description" type="xs:string"/>',
        count=1,
    )
    response = repo / "contracts/alpha/examples/alpha.xml"
    _replace(
        response,
        "  <name>First</name>",
        "  <name>First</name>\n  <description>New</description>",
    )
    _bump_contract(repo)
    _commit(repo, "add a response element")

    _assert_fails(
        _run(repo, base),
        "XML response changed in v1: contracts/alpha/examples/alpha.xml",
    )


def test_a_response_of_a_new_root_element_passes(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    xsd = repo / "contracts/alpha/alpha.xsd"
    _replace(
        xsd,
        '  <xs:element name="alpha-request"',
        '  <xs:element name="alpha-extra" type="alpha:AlphaItem"/>\n'
        '  <xs:element name="alpha-request"',
    )
    _write(
        repo / "contracts/alpha/examples/alpha-extra.xml",
        (repo / "contracts/alpha/examples/alpha.xml")
        .read_text()
        .replace("alpha-item", "alpha-extra"),
    )
    _bump_contract(repo)
    _commit(repo, "add a response with a new root element")

    result = _run(repo, base)

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_change_to_existing_common_v1_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    common = repo / "contracts/common/v1/common.xsd"
    _replace(common, "</xs:schema>", "  <!-- frozen -->\n</xs:schema>")
    _commit(repo, "change common v1")

    _assert_fails(_run(repo, base), "contracts/common/v1 is frozen")


def test_a_contract_change_without_an_info_version_bump_fails(
    tmp_path: Path,
) -> None:
    repo, base = _repository(tmp_path)
    example = repo / "contracts/alpha/examples/alpha.json"
    _write(example, example.read_text() + "\n")
    _commit(repo, "change a contract without a bump")

    _assert_fails(_run(repo, base), "info.version not bumped: alpha")


def test_a_service_change_without_a_version_bump_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    _write(repo / "services/alpha/app.py", '"""Fixture service."""\n')
    _commit(repo, "change service without a bump")

    _assert_fails(_run(repo, base), "VERSION not bumped: alpha")


def test_implementation_committed_before_its_contract_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    beta = FIXTURES / "beta"
    _copy_contents(beta / "services", repo / "services")
    _commit(repo, "implement beta first")
    _copy_contents(beta / "contracts", repo / "contracts")
    _commit(repo, "add beta contract late")

    _assert_fails(_run(repo, base), "implemented before its contract: beta")


def test_a_protected_operation_without_roles_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    openapi = repo / "contracts/alpha/openapi.yaml"
    _replace(openapi, "      x-mosaiq-roles: [ADMIN]\n", "", count=1)
    _bump_contract(repo)
    _commit(repo, "omit operation roles")

    _assert_fails(
        _run(repo, base),
        "contracts/alpha/openapi.yaml: /v1/alpha-items get: missing x-mosaiq-roles",
    )


def test_whole_segment_matching_allows_preference_subresources(
    tmp_path: Path,
) -> None:
    repo, base = _repository(tmp_path)
    _add_paths(
        repo,
        "/v1/preference-profiles/{customer_id}/interest-categories",
        "/v1/preference-profiles/{customer_id}/preferred-channels",
    )
    _bump_contract(repo)
    _commit(repo, "add preference subresources")

    result = _run(repo, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""


def test_first_addition_of_common_v1_is_not_frozen(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "contracts@example.test")
    _git(repo, "config", "user.name", "Contract tests")
    base_fixture = FIXTURES / "base"
    _copy_contents(base_fixture / "docs", repo / "docs")
    (repo / "contracts").mkdir()
    shutil.copy2(
        base_fixture / "contracts/registry.yaml", repo / "contracts/registry.yaml"
    )
    base = _commit(repo, "base without common v1")
    _copy_contents(base_fixture / "contracts/common", repo / "contracts/common")
    _commit(repo, "add common v1")

    result = _run(repo, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
