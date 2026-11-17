"""ops/verify/publish.py: the consolidated result of one release (F20-12, #784).

The document is a copy of what the runs observed, so each rule that keeps it
true is pinned here with small fixtures: a failure needs an issue, an image of
another commit makes the release not one version, and every skip keeps its
reason.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_publish", ROOT / "ops" / "verify" / "publish.py"
)
assert SPEC is not None and SPEC.loader is not None
publish = importlib.util.module_from_spec(SPEC)
sys.modules["verify_publish"] = publish
SPEC.loader.exec_module(publish)

COMMIT = "a" * 40
OTHER = "b" * 40
VERIFY = {
    "commit": COMMIT,
    "subject": "feat: a release",
    "started": "2026-11-14T00:00:00Z",
    "finished": "2026-11-14T02:00:00Z",
    "runner_commit": "c" * 40,
    "images": {"sales:0.4.0": "sha256:built", "postgres:18": "sha256:pulled"},
    "cases": {
        "service-sales": [
            {"id": "tests.a::test_one", "outcome": "passed", "message": ""},
            {
                "id": "tests.a::test_two",
                "outcome": "skipped",
                "message": "needs WAITAOF",
            },
        ],
        "negative": [
            {"id": "tests.n::test_x", "outcome": "passed", "message": ""},
        ],
    },
}
INSTANCE = [
    {"id": "tests.negative.t::test_a", "outcome": "passed", "message": ""},
    {
        "id": "tests.negative.t::test_b[x]",
        "outcome": "skipped",
        "message": "local only",
    },
]
IMAGES = [
    {
        "container": "mosaiq-sales-1",
        "image": "ghcr.io/x/sales:0.4.0",
        "digest": "sha256:" + "1" * 64,
        "revision": COMMIT,
        "version": "0.4.0",
    }
]
RUNS = [
    "Run at 2026-11-14 22:00 UTC against `https://127.0.0.1`: "
    "10 cases, 10 held, 0 did not."
]
NOTES = ["- **sales, a reused key:** not run here. It needs an accepted sale."]


def document(instance=INSTANCE, images=IMAGES, issues=(), verify=VERIFY):
    return publish.build(verify, instance, RUNS, NOTES, images, list(issues))


def test_the_document_holds_commit_digests_totals_and_each_skip_with_its_reason() -> (
    None
):
    text = document()
    assert COMMIT in text
    assert "sha256:" + "1" * 64 in text and "sha256:pulled" in text
    assert "| service-sales | 1 | 0 | 1 |" in text
    assert "| negative (instance) | 1 | 0 | 1 |" in text
    assert "| **All** | **3** | **0** | **2** |" in text
    assert "`tests.a::test_two` | needs WAITAOF" in text
    assert "`tests.negative.t::test_b[x]` | local only" in text
    assert "**passed** — 0 failed case(s)." in text
    assert "Negative suite on the instance: Run at 2026-11-14 22:00 UTC" in text
    assert NOTES[0] in text


def test_a_failure_is_listed_verbatim_with_its_issue() -> None:
    failing = INSTANCE + [
        {"id": "tests.negative.t::test_c", "outcome": "failed", "message": "502 != 406"}
    ]
    text = document(
        failing, issues=[("negative (instance)", "tests.negative.t::test_c", "#977")]
    )
    assert "**failed** — 1 failed case(s)." in text
    assert (
        "| negative (instance) | `tests.negative.t::test_c` | 502 != 406 | "
        "[#977](https://github.com/Equipo-03-Proyecto10/retail-segmentation"
        "/issues/977) |"
    ) in text


def test_a_failure_with_no_issue_writes_no_document() -> None:
    failing = INSTANCE + [{"id": "tests.n::t", "outcome": "failed", "message": "boom"}]
    with pytest.raises(publish.Refused, match="tests.n::t"):
        document(failing)


def test_an_issue_matches_by_suite_and_by_prefix_only() -> None:
    issues = [("service-sales", "tests.a::test_p[*", "#5")]
    assert publish.issue_for(issues, "service-sales", "tests.a::test_p[x]") == "#5"
    assert publish.issue_for(issues, "negative", "tests.a::test_p[x]") is None
    assert publish.issue_for(issues, "service-sales", "tests.a::test_q") is None


def test_an_image_of_another_commit_makes_the_result_failed() -> None:
    mixed = IMAGES + [{**IMAGES[0], "container": "mosaiq-stock-1", "revision": OTHER}]
    text = document(images=mixed)
    assert "**failed** — 0 failed case(s), and 1 image(s) not at this commit." in text
    assert "| ✗ |" in text and "`mosaiq-stock-1`" in text


def test_an_image_with_no_revision_is_not_at_the_commit() -> None:
    blank = [{**IMAGES[0], "revision": "", "digest": ""}]
    text = document(images=blank)
    assert "`none`" in text and "not at this commit" in text


def test_a_skip_with_no_reason_says_so() -> None:
    quiet = INSTANCE + [
        {"id": "t::s", "outcome": "skipped", "message": "skipped with no message"}
    ]
    assert "skipped with no message" in document(quiet)


def test_the_cli_writes_the_file_and_exits_by_the_result(tmp_path: Path) -> None:
    report = tmp_path / "report"
    report.mkdir()
    (report / "report.json").write_text(json.dumps(VERIFY))
    junit = tmp_path / "negative.xml"
    junit.write_text(
        '<testsuites><testsuite name="n">'
        '<testcase classname="t" name="ok"/>'
        '<testcase classname="t" name="s"><skipped message="local only"/></testcase>'
        "</testsuite></testsuites>"
    )
    evidence = tmp_path / "evidence.md"
    evidence.write_text("\n".join(RUNS + NOTES) + "\n")
    images = tmp_path / "images.tsv"
    images.write_text("\t".join(IMAGES[0].values()) + "\n")
    issues = tmp_path / "issues.tsv"
    issues.write_text("# none\n")
    out = tmp_path / "f20-12.md"
    argv = [
        "--verify", str(report), "--negative-junit", str(junit),
        "--negative-evidence", str(evidence), "--instance-images", str(images),
        "--issues", str(issues), "--out", str(out),
    ]  # fmt: skip
    assert publish.main(argv) == 0
    assert "**passed**" in out.read_text()

    images.write_text("\t".join({**IMAGES[0], "revision": OTHER}.values()) + "\n")
    assert publish.main(argv) == 1

    images.write_text("")
    out.unlink()
    assert publish.main(argv) == 2 and not out.exists()
