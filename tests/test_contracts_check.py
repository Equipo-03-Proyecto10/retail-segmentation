"""Contract convention checks from issue #412."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

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


def _add_v2(repo: Path) -> None:
    """A second major beside v1: its XSD, and its example in examples/v2/."""

    v1 = "https://mosaiq.maxthecoder.online/xml/alpha/v1"
    v2 = "https://mosaiq.maxthecoder.online/xml/alpha/v2"
    xsd = (repo / "contracts/alpha/alpha.xsd").read_text().replace(v1, v2)
    _write(repo / "contracts/alpha/alpha.v2.xsd", xsd)
    example = (repo / "contracts/alpha/examples/alpha.xml").read_text()
    directory = repo / "contracts/alpha/examples/v2"
    directory.mkdir()
    _write(directory / "alpha.xml", example.replace(v1, v2))
    _bump_contract(repo)


def test_a_later_major_with_valid_examples_passes_without_output(
    tmp_path: Path,
) -> None:
    repo, base = _repository(tmp_path)
    _add_v2(repo)
    _commit(repo, "publish a v2 beside v1")

    result = _run(repo, base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""


def test_a_v2_example_the_v2_xsd_refuses_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    _add_v2(repo)
    _replace(repo / "contracts/alpha/examples/v2/alpha.xml", "<id>1</id>", "<id>x</id>")
    _commit(repo, "break a v2 example")

    _assert_fails(
        _run(repo, base), "invalid XML example: contracts/alpha/examples/v2/alpha.xml"
    )


def test_a_v2_example_in_the_v1_namespace_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    _add_v2(repo)
    valid_v1 = (repo / "contracts/alpha/examples/alpha.xml").read_text()
    _write(repo / "contracts/alpha/examples/v2/alpha.xml", valid_v1)
    _commit(repo, "put a v1 document in examples/v2")

    _assert_fails(
        _run(repo, base), "invalid XML example: contracts/alpha/examples/v2/alpha.xml"
    )


def test_an_invalid_v2_example_that_validates_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    _add_v2(repo)
    valid = (repo / "contracts/alpha/examples/v2/alpha.xml").read_text()
    _write(repo / "contracts/alpha/examples/v2/alpha.invalid.xml", valid)
    _commit(repo, "make the invalid v2 example valid")

    _assert_fails(
        _run(repo, base),
        "invalid example accepted: contracts/alpha/examples/v2/alpha.invalid.xml",
    )


def test_a_refused_invalid_v2_example_passes(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    _add_v2(repo)
    valid = (repo / "contracts/alpha/examples/v2/alpha.xml").read_text()
    _write(
        repo / "contracts/alpha/examples/v2/alpha.invalid.xml",
        valid.replace("<id>1</id>", "<id>x</id>"),
    )
    _commit(repo, "add a refused v2 example")

    result = _run(repo, base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""


def test_examples_of_a_major_with_no_xsd_fail(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    _add_v2(repo)
    (repo / "contracts/alpha/alpha.v2.xsd").unlink()
    _commit(repo, "drop the v2 schema")

    _assert_fails(
        _run(repo, base),
        "missing XSD for contracts/alpha/examples/v2: contracts/alpha/alpha.v2.xsd",
    )


def test_an_examples_directory_that_is_not_a_major_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    directory = repo / "contracts/alpha/examples/drafts"
    directory.mkdir()
    _write(directory / "alpha.xml", "<not-validated/>\n")
    _bump_contract(repo)
    _commit(repo, "hide an example in an unchecked directory")

    _assert_fails(
        _run(repo, base),
        "unexpected examples directory: contracts/alpha/examples/drafts",
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


def test_renaming_a_v1_response_root_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    _replace(
        repo / "contracts/alpha/alpha.xsd",
        'element name="alpha-item"',
        'element name="alpha-renamed"',
    )
    _replace(repo / "contracts/alpha/examples/alpha.xml", "alpha-item", "alpha-renamed")
    _bump_contract(repo)
    _commit(repo, "rename a response root")

    _assert_fails(
        _run(repo, base),
        "XML response root removed or changed in v1: "
        "contracts/alpha/examples/alpha.xml",
    )


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


def _mark(repo: Path, status: str) -> None:
    _replace(
        repo / "contracts/alpha/openapi.yaml",
        "      x-mosaiq-roles: [ADMIN]\n",
        f"      x-mosaiq-roles: [ADMIN]\n      x-mosaiq-status: {status}\n",
        count=1,
    )
    _bump_contract(repo)
    _commit(repo, "mark an operation")


def test_an_unknown_operation_status_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    _mark(repo, "later")

    _assert_fails(_run(repo, base), "x-mosaiq-status must be planned")


def test_a_planned_operation_passes(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    _mark(repo, "planned")

    result = _run(repo, base)
    assert result.returncode == 0, result.stdout + result.stderr


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


# ---------- vocabulary growth (ADR-0076) ----------

_KIND_SCHEMA = (
    "              schema:\n"
    "                type: object\n"
    "                properties:\n"
    "                  kind:\n"
    "                    type: string\n"
    "                    enum: [ADMIN, ANALYST]\n"
)
_KIND_TYPE = (
    '  <xs:simpleType name="Kind">\n'
    '    <xs:restriction base="xs:string">\n'
    '      <xs:enumeration value="ADMIN"/>\n'
    '      <xs:enumeration value="ANALYST"/>\n'
    "    </xs:restriction>\n"
    "  </xs:simpleType>\n"
)


def _vocabulary_repository(tmp_path: Path) -> tuple[Path, str]:
    """The fixture with a closed `kind` vocabulary in alpha's v1 response."""
    repo, _ = _repository(tmp_path)
    contract = repo / "contracts/alpha"
    _replace(
        contract / "openapi.yaml",
        "              schema:\n                type: object\n",
        _KIND_SCHEMA,
    )
    _replace(
        contract / "alpha.xsd",
        '  <xs:complexType name="AlphaItem">',
        _KIND_TYPE + '  <xs:complexType name="AlphaItem">',
    )
    _replace(
        contract / "alpha.xsd",
        '      <xs:element name="name" type="xs:string"/>\n    </xs:sequence>\n'
        '  </xs:complexType>\n  <xs:complexType name="AlphaRequest">',
        '      <xs:element name="name" type="xs:string"/>\n'
        '      <xs:element name="kind" type="alpha:Kind"/>\n    </xs:sequence>\n'
        '  </xs:complexType>\n  <xs:complexType name="AlphaRequest">',
    )
    _replace(
        contract / "examples/alpha.xml",
        "  <name>First</name>",
        "  <name>First</name>\n  <kind>ADMIN</kind>",
    )
    _bump_contract(repo)
    (repo / "docs/adr").mkdir(parents=True, exist_ok=True)
    _write(repo / "docs/adr/0001-kinds-grow.md", "# ADR-0001 — Kinds grow\n")
    base = _commit(repo, "a closed vocabulary in v1")
    return repo, base


def _grow(repo: Path, *values: str, version: str = "1.1.0") -> None:
    contract = repo / "contracts/alpha"
    listed = ", ".join(["ADMIN", "ANALYST", *values])
    _replace(contract / "openapi.yaml", "enum: [ADMIN, ANALYST]", f"enum: [{listed}]")
    _replace(contract / "openapi.yaml", "version: 1.0.1", f"version: {version}")
    _replace(
        contract / "alpha.xsd",
        '      <xs:enumeration value="ANALYST"/>\n',
        '      <xs:enumeration value="ANALYST"/>\n'
        + "".join(f'      <xs:enumeration value="{value}"/>\n' for value in values),
    )
    _replace(
        contract / "examples/alpha.xml",
        "<kind>ADMIN</kind>",
        f"<kind>{values[0]}</kind>",
    )


def _entry(
    repo: Path, *values: str, version: str = "1.1.0", adr: str = "ADR-0001"
) -> None:
    _write(
        repo / "contracts/vocabulary-growth.yaml",
        "entries:\n"
        f"  - adr: {adr}\n"
        "    service: alpha\n"
        f"    version: {version}\n"
        f"    values: [{', '.join(values)}]\n",
    )


def test_a_listed_value_grows_the_vocabulary_at_its_version(tmp_path: Path) -> None:
    repo, base = _vocabulary_repository(tmp_path)
    _grow(repo, "SALES_ASSOCIATE")
    _entry(repo, "SALES_ASSOCIATE")
    _commit(repo, "grow the vocabulary")

    result = _run(repo, base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""


def test_a_value_added_without_an_entry_is_breaking(tmp_path: Path) -> None:
    repo, base = _vocabulary_repository(tmp_path)
    _grow(repo, "SALES_ASSOCIATE")
    _commit(repo, "grow the vocabulary without an entry")

    result = _run(repo, base)

    _assert_fails(result, "breaking OpenAPI change in v1: alpha")
    _assert_fails(
        result, "XML response changed in v1: contracts/alpha/examples/alpha.xml"
    )


def test_an_entry_for_another_version_excuses_nothing(tmp_path: Path) -> None:
    repo, base = _vocabulary_repository(tmp_path)
    _grow(repo, "SALES_ASSOCIATE", version="1.2.0")
    _entry(repo, "SALES_ASSOCIATE", version="1.1.0")
    _commit(repo, "an entry left behind by an earlier version")

    _assert_fails(_run(repo, base), "breaking OpenAPI change in v1: alpha")


def test_an_entry_excuses_only_the_values_it_lists(tmp_path: Path) -> None:
    repo, base = _vocabulary_repository(tmp_path)
    _grow(repo, "LOYALTY_OFFICER", "SALES_ASSOCIATE")
    _entry(repo, "SALES_ASSOCIATE")
    _commit(repo, "one value the entry does not list")

    result = _run(repo, base)

    _assert_fails(result, "breaking OpenAPI change in v1: alpha")
    _assert_fails(
        result, "XML response changed in v1: contracts/alpha/examples/alpha.xml"
    )


@pytest.mark.parametrize("unlisted", ["sneaky_role", "Rogue-Role"])
def test_an_unlisted_value_in_any_spelling_is_still_breaking(
    tmp_path: Path, unlisted: str
) -> None:
    """A value the entry does not list fails whatever its case or characters."""
    repo, base = _vocabulary_repository(tmp_path)
    _grow(repo, "SALES_ASSOCIATE", unlisted)
    _entry(repo, "SALES_ASSOCIATE")
    _commit(repo, "an unlisted value outside the code pattern")

    _assert_fails(_run(repo, base), "breaking OpenAPI change in v1: alpha")


def test_an_entry_must_name_an_existing_record(tmp_path: Path) -> None:
    repo, base = _vocabulary_repository(tmp_path)
    _grow(repo, "SALES_ASSOCIATE")
    _entry(repo, "SALES_ASSOCIATE", adr="ADR-0999")
    _commit(repo, "an entry with no record behind it")

    _assert_fails(
        _run(repo, base),
        "contracts/vocabulary-growth.yaml: entry 1: "
        "ADR-0999 is not a record in docs/adr/",
    )
