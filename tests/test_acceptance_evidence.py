"""The pure parts of the acceptance collectors (tests/acceptance/, #888, #889, #891).

What they say about a live system is shown by running them (the runbooks in
docs/evidence/f29-03-*.md and f29-06-*.md); these cases pin what they do with
what they read: masking, bind parameters, page parsing and the counts.
"""

from __future__ import annotations

import json
import shutil

import pytest

from tests.acceptance.evidence import (
    JWT,
    Database,
    Masker,
    Recorder,
    UnmaskedSecretError,
    contract_version,
    to_bind,
)
from tests.acceptance.f29_03 import parse_profile, stock_on_page
from tests.acceptance.f29_06 import evidence_counts, is_local, junit_counts

TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJl"


def test_a_token_is_masked_in_headers_json_and_forms() -> None:
    masker = Masker()
    assert masker.header("Authorization", "Bearer " + TOKEN) == "Bearer <masked #1>"
    body = masker.body(json.dumps({"access_token": TOKEN, "user": {"password": "x"}}))
    assert TOKEN not in body and '"x"' not in body
    form = masker.body(b"email=a%40b.test&password=secret&csrf_token=abc")
    assert "secret" not in form and "abc" not in form and "a@b.test" in form
    assert not JWT.search(masker.body("free text with " + TOKEN))


def test_the_same_secret_keeps_its_mark() -> None:
    masker = Masker()
    assert masker.mark("a") == masker.mark("a") == "<masked #1>"
    assert masker.mark("b") == "<masked #2>"


def test_write_refuses_evidence_that_still_holds_a_token(tmp_path) -> None:
    rec = Recorder(tmp_path, "t", "t")
    rec.add_rows("leak", TOKEN)
    with pytest.raises(UnmaskedSecretError):
        rec.write()
    assert not (tmp_path / "rows.md").exists()


def test_write_reports_whether_every_check_held(tmp_path) -> None:
    rec = Recorder(tmp_path, "t", "t")
    assert rec.check("c", "equal", 1, "1")
    assert not rec.check("c", "differs", 1, 2)
    assert rec.write() is False
    summary = (tmp_path / "summary.md").read_text()
    assert "2 checks, 1 held, 1 did not." in summary


def test_placeholders_become_bind_parameters() -> None:
    assert to_bind("a = %s AND b = %s") == "a = $1 AND b = $2"
    script = Database.bind_script("SELECT %s, %s;", 2)
    assert "SELECT $1, $2\n\\bind :p1 :p2\n\\g" in script
    assert script.startswith("BEGIN READ ONLY;") and script.endswith("ROLLBACK;\n")


def test_values_travel_as_arguments_quoted_only_for_a_remote_shell() -> None:
    local = Database("docker exec -i db")
    assert local.command(["-v", "p1=O'Brien; x"]) == [
        "docker", "exec", "-i", "db", "-v", "p1=O'Brien; x",
    ]  # fmt: skip
    remote = Database("ssh host sudo -n -u postgres")
    assert remote.remote_shell
    assert remote.command(["-v", "p1=a b"])[-1] == "'p1=a b'"


def test_the_profile_page_figures_are_read() -> None:
    html = (
        '<span class="mq-metric__label">Total spend <span>x</span></span>\n'
        '<span class="mq-metric__value">1,203.50<span class="mq-metric__unit">MXN'
        '<span class="mq-metric__label">Purchase frequency</span>\n'
        '<span class="mq-metric__value">7<span class="mq-metric__unit">purchases'
    )
    assert parse_profile(html) == {"purchases": "7", "spend": "1203.50"}
    assert parse_profile("<p>No sales</p>") == {"purchases": "0", "spend": "0.00"}


def test_the_stock_page_row_is_found_by_store_and_sku() -> None:
    html = (
        "<table><tr><td>Centro</td><td>SKU-1</td><td>Tea</td><td>12 Low</td></tr>"
        "<tr><td>Norte</td><td>SKU-1</td><td>Tea</td><td>4</td></tr></table>"
    )
    assert stock_on_page(html, "Norte", "SKU-1") == 4
    assert stock_on_page(html, "Centro", "SKU-1") == 12
    assert stock_on_page(html, "Sur", "SKU-1") is None


def test_the_served_contract_version_is_read() -> None:
    text = "openapi: 3.1.0\ninfo:\n  title: sales\n  version: 1.9.0\npaths: {}\n"
    assert contract_version(text) == "1.9.0"
    assert contract_version("not a contract") is None


def test_the_f18_04_section_and_junit_are_counted(tmp_path) -> None:
    section = (
        "## Local stack\n\n| Area | Case | Expected | Actual | |\n"
        "|---|---|---|---|---|\n"
        "| a | x | 403 | 403 | ✓ |\n| a | y | 403 | 200 | ✗ |\n"
    )
    assert evidence_counts(section) == (2, 1)
    junit = tmp_path / "j.xml"
    junit.write_text(
        '<testsuites><testsuite tests="5" failures="1" errors="0" skipped="2"/>'
        "</testsuites>"
    )
    assert junit_counts(junit) == {"tests": 5, "failures": 1, "errors": 0, "skipped": 2}


def test_replay_refuses_anything_but_a_local_stack(monkeypatch) -> None:
    monkeypatch.delenv("MOSAIQ_TARGET", raising=False)
    assert is_local("http://127.0.0.1:18080")
    assert not is_local("https://mosaiq.maxthecoder.online")
    assert not is_local("https://127.0.0.1")
    monkeypatch.setenv("MOSAIQ_TARGET", "instance")
    assert not is_local("http://127.0.0.1:18080")


# ---------- F20-02 (#760) ----------


def test_f20_02_reads_elements_whatever_their_prefix() -> None:
    from tests.acceptance.f20_02 import element

    xml = "<c:Error><c:code>forbidden_role</c:code><correlation_id>x</correlation_id>"
    assert element(xml, "code") == "forbidden_role"
    assert element(xml, "correlation_id") == "x"
    assert element(xml, "message") is None


def test_f20_02_reads_the_roles_from_the_committed_contracts() -> None:
    from tests.acceptance.f20_02 import roles

    assert roles("campaigns", "POST", "/v1/campaigns") == ["ADMIN", "MARKETING"]
    assert "STORE_MANAGER" in roles("sales", "GET", "/v1/sales/{sale_id}")


@pytest.mark.skipif(shutil.which("xmllint") is None, reason="xmllint is not installed")
def test_f20_02_validates_an_error_against_common_xsd_and_a_body_against_its_own(
    tmp_path,
) -> None:
    from tests.acceptance.f20_02 import xmllint
    from tests.negative.stack import ROOT

    examples = ROOT / "contracts/campaigns/examples"
    valid, detail = xmllint(examples / "forbidden-role.xml", "campaigns")
    assert valid and detail.startswith("contracts/common/v1/common.xsd: ")
    valid, detail = xmllint(examples / "campaign.xml", "campaigns")
    assert valid and detail.startswith("contracts/campaigns/campaigns.xsd: ")
    broken = tmp_path / "broken.xml"
    broken.write_text(
        (examples / "campaign.xml").read_text().replace("DRAFT", "PAUSED", 1)
    )
    assert not xmllint(broken, "campaigns")[0]


def test_f20_02_refuses_a_client_directory_that_holds_a_token_in_any_file(
    tmp_path,
) -> None:
    from tests.acceptance.f20_02 import refuse_tokens

    token = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJzdGFmZiJ9.c2lnbmF0dXJl"
    (tmp_path / "bodies").mkdir()
    (tmp_path / "result.json").write_text("{}")
    (tmp_path / "exchanges.json").write_text("[]")
    (tmp_path / "bodies" / "003-staff-response.xml").write_text("<Sale/>")
    refuse_tokens(tmp_path)
    for name in ("result.json", "exchanges.json", "bodies/003-staff-response.xml"):
        path = tmp_path / name
        clean = path.read_text()
        path.write_text(f"{clean}{token}")
        with pytest.raises(SystemExit, match="holds a token"):
            refuse_tokens(tmp_path)
        path.write_text(clean)


# ---------- F29-04 (#889) ----------


def test_f29_04_a_skip_or_an_empty_run_is_never_a_pass() -> None:
    from tests.acceptance.f29_04 import held

    ok = {"tests": 12, "failures": 0, "errors": 0, "skipped": 0}
    assert held(0, ok)
    assert not held(0, ok | {"skipped": 1})  # a missing database skips
    assert not held(0, ok | {"tests": 0})
    assert not held(1, ok)
    assert not held(0, {})


def test_f29_04_adds_every_cargo_result_line() -> None:
    from tests.acceptance.f29_04 import cargo_counts

    log = (
        "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured\n"
        "test result: ok. 5 passed; 0 failed; 1 ignored; 0 measured\n"
        "test result: FAILED. 3 passed; 2 failed; 0 ignored; 0 measured\n"
    )
    assert cargo_counts(log) == {"tests": 11, "failures": 2, "errors": 0, "skipped": 1}


def test_f29_04_runs_each_kind_where_it_lives(tmp_path) -> None:
    from tests.acceptance.f29_04 import SUITES, command_for

    junit = tmp_path / "j.xml"
    kinds = {s.kind: command_for(s, junit) for s in SUITES}
    assert kinds["service"][:3] == ["uv", "run", "--frozen"]
    assert kinds["cargo"][:4] == ["cargo", "test", "--locked", "--no-fail-fast"]
    assert "--test" in kinds["cargo"]
    assert kinds["pytest"][1:3] == ["-m", "pytest"]
    assert {s.criterion for s in SUITES} == {
        "RFM",
        "Migración",
        "Incremental",
        "K-means",
        "Drift",
    }


def test_f29_04_names_the_registered_kmeans_and_drift_results() -> None:
    from pathlib import Path

    from tests.acceptance.f29_04 import REGISTERED_DRIFT, REGISTERED_PARTITION

    root = Path(__file__).resolve().parents[1] / "docs" / "evidence"
    assert REGISTERED_PARTITION in (root / "f23-04-kmeans-stability.md").read_text()
    assert REGISTERED_DRIFT in (root / "f23-10-drift-detection.md").read_text()
