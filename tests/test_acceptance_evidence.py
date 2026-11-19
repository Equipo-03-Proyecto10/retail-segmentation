"""The pure parts of the acceptance collectors (tests/acceptance/, #888, #891).

What they say about a live system is shown by running them (the runbooks in
docs/evidence/f29-03-*.md and f29-06-*.md); these cases pin what they do with
what they read: masking, bind parameters, page parsing and the counts.
"""

from __future__ import annotations

import json

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
