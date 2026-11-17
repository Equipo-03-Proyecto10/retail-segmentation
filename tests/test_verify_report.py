"""ops/verify/report.py: what fails, what is skipped, and what is flaky (F20-03, #761).

The verification run's exit status and its report rest on these rules, so each
one is pinned here with small JUnit and cargo fixtures.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_report", ROOT / "ops" / "verify" / "report.py"
)
assert SPEC is not None and SPEC.loader is not None
report = importlib.util.module_from_spec(SPEC)
sys.modules["verify_report"] = report
SPEC.loader.exec_module(report)

META = {
    "commit": "a" * 40,
    "subject": "chore: a commit",
    "started": "2026-11-09T00:00:00Z",
    "finished": "2026-11-09T01:00:00Z",
    "runner_commit": "b" * 40,
    "toolbox": "sha256:toolbox",
    "images": {"postgres:18": "sha256:pg"},
    "tools": {"python": "Python 3.12"},
}


def junit_file(path: Path, *cases: str) -> Path:
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="s">'
        + "".join(cases)
        + "</testsuite></testsuites>"
    )
    return path


def passed(name: str) -> str:
    return f'<testcase classname="pkg.test_mod" name="{name}"/>'


def failed(name: str, message: str = "assert 1 == 2") -> str:
    return (
        f'<testcase classname="pkg.test_mod" name="{name}">'
        f'<failure message="{message}">trace</failure></testcase>'
    )


def skipped(name: str, reason: str) -> str:
    return (
        f'<testcase classname="pkg.test_mod" name="{name}">'
        f'<skipped message="{reason}"/></testcase>'
    )


def cases_of(run: Path, suite: str) -> list[dict[str, str]]:
    return report._collect(run)[suite]


@pytest.fixture
def run(tmp_path: Path) -> Path:
    directory = tmp_path / "run"
    (directory / "results").mkdir(parents=True)
    (directory / "meta.json").write_text(json.dumps(META))
    return directory


@pytest.fixture
def log(tmp_path: Path) -> Path:
    path = tmp_path / "step.log"
    path.write_text("\n".join(f"line {n}" for n in range(100)))
    return path


def test_a_check_that_exits_zero_is_one_passed_case(run: Path, log: Path) -> None:
    report.check(run, "contracts", "check", 0, log)
    assert cases_of(run, "contracts") == [
        {"id": "contracts.checks::check", "outcome": "passed", "message": ""}
    ]


def test_a_check_that_exits_non_zero_fails_with_the_log_tail(
    run: Path, log: Path
) -> None:
    report.check(run, "contracts", "guards", 3, log)
    [case] = cases_of(run, "contracts")
    assert case["outcome"] == "failed"
    assert case["message"] == "exited 3"
    text = (run / "results" / "contracts" / "guards.xml").read_text()
    assert "line 99" in text and "line 59" not in text  # only the last 40 lines


def test_a_runner_that_wrote_no_junit_could_not_run_and_fails(
    run: Path, log: Path, tmp_path: Path
) -> None:
    report.junit(run, "e2e", "suite", 4, log, [tmp_path / "missing.xml"])
    [case] = cases_of(run, "e2e")
    assert case["outcome"] == "failed"
    assert case["message"].startswith("could not run: exited 4")


def test_a_runner_that_wrote_no_junit_fails_even_when_it_exits_zero(
    run: Path, log: Path, tmp_path: Path
) -> None:
    report.junit(run, "e2e", "suite", 0, log, [tmp_path / "missing.xml"])
    assert [c["outcome"] for c in cases_of(run, "e2e")] == ["failed"]


def test_a_non_zero_exit_with_only_passing_cases_adds_an_exit_status_failure(
    run: Path, log: Path, tmp_path: Path
) -> None:
    junit = junit_file(tmp_path / "r.xml", passed("test_a"))
    report.junit(run, "web", "pytest", 2, log, [junit])
    outcomes = sorted(
        (c["id"], c["outcome"], c["message"]) for c in cases_of(run, "web")
    )
    assert outcomes == [
        ("pkg.test_mod::test_a", "passed", ""),
        (
            "web.pytest::exit status",
            "failed",
            "exited 2 with no failed case in its JUnit file",
        ),
    ]


def test_a_failed_case_in_the_junit_is_enough_on_its_own(
    run: Path, log: Path, tmp_path: Path
) -> None:
    junit = junit_file(tmp_path / "r.xml", passed("test_a"), failed("test_b"))
    report.junit(run, "web", "pytest", 1, log, [junit])
    assert sorted(c["outcome"] for c in cases_of(run, "web")) == ["failed", "passed"]


def test_an_unreadable_junit_file_is_a_failed_case(
    run: Path, log: Path, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuites><testsuite>")
    report.junit(run, "web", "pytest", 0, log, [broken])
    [case] = cases_of(run, "web")
    assert case["outcome"] == "failed"
    assert case["message"].startswith("unreadable JUnit")


def test_skip_reasons_are_kept(run: Path, log: Path, tmp_path: Path) -> None:
    junit = junit_file(
        tmp_path / "r.xml", skipped("test_c", "not set: PARITY_DATABASE_URL")
    )
    report.junit(run, "parity", "suite", 0, log, [junit])
    assert cases_of(run, "parity") == [
        {
            "id": "pkg.test_mod::test_c",
            "outcome": "skipped",
            "message": "not set: PARITY_DATABASE_URL",
        }
    ]


CARGO = """\
     Running unittests src/lib.rs (target/debug/deps/stock-0123456789abcdef)
test rules::tests::never_negative ... ok
test rules::tests::slow_case ... ignored, needs a live PostgreSQL
test rules::tests::bare_ignore ... ignored
     Running tests/api.rs (target/debug/deps/api-fedcba9876543210)
test reads_a_level ... FAILED
"""


def test_cargo_output_becomes_cases_with_outcomes_and_ignore_reasons(
    run: Path, tmp_path: Path
) -> None:
    log = tmp_path / "cargo.log"
    log.write_text(CARGO)
    report.libtest(run, "service-stock", "cargo-test", 101, log)
    cases = {c["id"]: c for c in cases_of(run, "service-stock")}
    assert cases["service-stock.stock::rules::tests::never_negative"]["outcome"] == (
        "passed"
    )
    assert cases["service-stock.stock::rules::tests::slow_case"] == {
        "id": "service-stock.stock::rules::tests::slow_case",
        "outcome": "skipped",
        "message": "needs a live PostgreSQL",
    }
    assert (
        cases["service-stock.stock::rules::tests::bare_ignore"]["message"]
        == "#[ignore] with no reason"
    )
    assert cases["service-stock.api::reads_a_level"]["outcome"] == "failed"


def test_cargo_that_printed_no_test_could_not_run(run: Path, tmp_path: Path) -> None:
    log = tmp_path / "cargo.log"
    log.write_text("error: could not compile `stock`\n")
    report.libtest(run, "service-stock", "cargo-test", 101, log)
    [case] = cases_of(run, "service-stock")
    assert case["outcome"] == "failed"
    assert case["message"].startswith("could not run")


def test_build_fails_on_any_failed_case_and_reports_counts_and_skips(
    run: Path, log: Path, tmp_path: Path
) -> None:
    report.check(run, "contracts", "check", 0, log)
    junit = junit_file(
        tmp_path / "r.xml",
        passed("test_a"),
        failed("test_b", "boom"),
        skipped("test_c", "needs Docker"),
    )
    report.junit(run, "web", "pytest", 1, log, [junit])

    assert report.build(run) == 1

    data = json.loads((run / "report.json").read_text())
    assert data["result"] == "failed"
    assert data["commit"] == META["commit"]
    assert data["summary"] == {
        "contracts": {"passed": 1, "failed": 0, "skipped": 0},
        "web": {"passed": 1, "failed": 1, "skipped": 1},
    }
    markdown = (run / "report.md").read_text()
    assert "**Result: failed** — 1 failed case(s)." in markdown
    assert "`pkg.test_mod::test_b`: boom" in markdown
    assert "| web | `pkg.test_mod::test_c` | needs Docker |" in markdown
    assert "| `postgres:18` | `sha256:pg` |" in markdown


def test_build_passes_when_nothing_failed(run: Path, log: Path) -> None:
    report.check(run, "contracts", "check", 0, log)
    assert report.build(run) == 0
    assert json.loads((run / "report.json").read_text())["result"] == "passed"


def write_run(path: Path, commit: str, cases: dict[str, list[tuple[str, str]]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "commit": commit,
                "started": "2026-11-09T00:00:00Z",
                "cases": {
                    suite: [
                        {"id": case, "outcome": outcome, "message": ""}
                        for case, outcome in items
                    ]
                    for suite, items in cases.items()
                },
            }
        )
    )
    return path


def test_two_runs_that_agree_compare_clean(tmp_path: Path) -> None:
    cases = {"web": [("pkg::test_a", "passed"), ("pkg::test_b", "skipped")]}
    first = write_run(tmp_path / "a.json", "c1", cases)
    second = write_run(tmp_path / "b.json", "c1", cases)
    out = tmp_path / "comparison.md"
    assert report.compare(first, second, out) == 0
    assert "The runs agree." in out.read_text()


def test_a_case_that_differs_or_is_missing_is_flaky(tmp_path: Path) -> None:
    first = write_run(
        tmp_path / "a.json",
        "c1",
        {"web": [("pkg::test_a", "passed"), ("pkg::test_gone", "passed")]},
    )
    second = write_run(tmp_path / "b.json", "c1", {"web": [("pkg::test_a", "failed")]})
    out = tmp_path / "comparison.md"
    assert report.compare(first, second, out) == 1
    text = out.read_text()
    assert "| web | `pkg::test_a` | passed | failed |" in text
    assert "| web | `pkg::test_gone` | passed | absent |" in text


def test_a_random_uuid_in_a_case_id_is_not_a_difference(tmp_path: Path) -> None:
    first = write_run(
        tmp_path / "a.json",
        "c1",
        {"negative": [("t::test_x[1b4e28ba-2fa1-11d2-883f-0016d3cca427]", "passed")]},
    )
    second = write_run(
        tmp_path / "b.json",
        "c1",
        {"negative": [("t::test_x[6fa459ea-ee8a-3ca4-894e-db77e160355e]", "passed")]},
    )
    assert report.compare(first, second, None) == 0


def test_runs_of_different_commits_say_so(tmp_path: Path) -> None:
    cases = {"web": [("pkg::test_a", "passed")]}
    first = write_run(tmp_path / "a.json", "c1", cases)
    second = write_run(tmp_path / "b.json", "c2", cases)
    out = tmp_path / "comparison.md"
    report.compare(first, second, out)
    assert "different commits" in out.read_text()
