"""One consolidated result for one release (F20-12, #784).

Reads what was observed and writes the document; it measures nothing itself.

    publish.py --verify <report dir> --negative-junit <xml> \\
        --negative-evidence <md> --instance-images <tsv> --issues <tsv> \\
        [--out docs/evidence/f20-12-<sha>.md]

- `--verify`: the directory F20-03 (`ops/verify/run.sh`) wrote for the commit,
  with `report.json`.
- `--negative-junit` and `--negative-evidence`: `tests/negative` run on the
  instance (`--junitxml`, and `MOSAIQ_EVIDENCE_FILE`) at the same version.
- `--instance-images`: the output of `ops/verify/instance-images.sh` on the
  instance.
- `--issues`: one line per failed case, `<suite><TAB><case><TAB><issue>`. A
  case ending in `*` matches by prefix, for a parametrized test. The suite of
  the instance's negative run is `negative (instance)`.

Rules the document rests on:
- A failed case with no issue stops the run and writes nothing. The document
  does not explain a failure away, and it does not leave one unexplained: it
  lists the case, its message exactly as the runner gave it, and its issue.
- The release is one version only when every image the instance runs carries
  the verified commit as its revision. Any that does not is listed, and the
  result is `failed`.
- Every skip is listed with the reason its runner gave. A skip with none says
  so.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report as verify_report  # noqa: E402

REPO = "https://github.com/Equipo-03-Proyecto10/retail-segmentation"
INSTANCE_SUITE = "negative (instance)"
STORY = f"[#784]({REPO}/issues/784)"


class Refused(Exception):
    """The inputs cannot make a document that is true; nothing is written."""


def md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def issue_link(issue: str) -> str:
    issue = issue.strip()
    if issue.startswith("http"):
        number = issue.rstrip("/").rsplit("/", 1)[-1]
        return f"[#{number}]({issue})"
    number = issue.lstrip("#")
    return f"[#{number}]({REPO}/issues/{number})"


def load_cases(junit: Path) -> list[dict[str, str]]:
    cases = []
    for case in verify_report._cases(junit):
        outcome, message = verify_report._outcome(case)
        cases.append(
            {
                "id": f"{case.get('classname', '')}::{case.get('name', '')}",
                "outcome": outcome,
                "message": message,
            }
        )
    return cases


def load_issues(path: Path) -> list[tuple[str, str, str]]:
    rows = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) != 3 or not all(parts):
            raise Refused(f"{path}:{number}: want <suite><TAB><case><TAB><issue>")
        rows.append((parts[0], parts[1], parts[2]))
    return rows


def issue_for(
    issues: list[tuple[str, str, str]], suite: str, case_id: str
) -> str | None:
    for issue_suite, pattern, issue in issues:
        if issue_suite != suite:
            continue
        if pattern.endswith("*") and case_id.startswith(pattern[:-1]):
            return issue
        if pattern == case_id:
            return issue
    return None


def load_instance_images(path: Path) -> list[dict[str, str]]:
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        fields = (line.split("\t") + [""] * 5)[:5]
        rows.append(
            dict(
                zip(
                    ("container", "image", "digest", "revision", "version"),
                    fields,
                    strict=True,
                )
            )
        )
    if not rows:
        raise Refused(f"{path} lists no container: run instance-images.sh there")
    return rows


def evidence_lines(path: Path) -> tuple[list[str], list[str]]:
    """The negative run's own header lines, and its not-run notes, verbatim."""
    runs, notes = [], []
    for line in path.read_text().splitlines():
        if line.startswith("Run at "):
            runs.append(line)
        elif line.startswith("- **") and (
            "not run here" in line or "not applicable" in line
        ):
            notes.append(line)
    return runs, notes


def counts(cases: list[dict[str, str]]) -> dict[str, int]:
    found = Counter(case["outcome"] for case in cases)
    return {key: found.get(key, 0) for key in ("passed", "failed", "skipped")}


def build(
    verify: dict,
    instance_cases: list[dict[str, str]],
    runs: list[str],
    notes: list[str],
    images: list[dict[str, str]],
    issues: list[tuple[str, str, str]],
) -> str:
    commit = verify["commit"]
    suites = dict(verify["cases"])
    if INSTANCE_SUITE in suites:
        raise Refused(f"the verify report already has a suite {INSTANCE_SUITE!r}")
    suites[INSTANCE_SUITE] = instance_cases

    failures = [
        (suite, case)
        for suite, cases in suites.items()
        for case in cases
        if case["outcome"] == "failed"
    ]
    unmatched = [
        f"{suite}: {case['id']}"
        for suite, case in failures
        if issue_for(issues, suite, case["id"]) is None
    ]
    if unmatched:
        raise Refused(
            "failed with no issue in --issues (open one, then list it):\n  "
            + "\n  ".join(unmatched)
        )
    skips = [
        (suite, case)
        for suite, cases in suites.items()
        for case in cases
        if case["outcome"] == "skipped"
    ]
    other = [row for row in images if row["revision"] != commit]
    failed_total = len(failures)
    result = "failed" if failed_total or other else "passed"
    short = commit[:8]

    out = [
        f"# F20-12 — consolidated result for `{short}`",
        "",
        f"Issue {STORY}. One release, one version: F20-03 "
        f"([#761]({REPO}/issues/761)) at the commit below, and `tests/negative` "
        "on the instance at the same version. Every figure is copied from the "
        "runs' own reports by `ops/verify/publish.py`; nothing is estimated.",
        "",
        "## The result",
        "",
        f"**{result}** — {failed_total} failed case(s)"
        + (f", and {len(other)} image(s) not at this commit." if other else "."),
        "",
        f"- Commit: `{commit}` ({md(verify.get('subject', ''))})",
        f"- F20-03 started {verify.get('started', '')}, finished "
        f"{verify.get('finished', '')}; runner `ops/verify` at "
        f"`{verify.get('runner_commit', '')}`",
    ]
    out += [f"- Negative suite on the instance: {md(run)}" for run in runs] or [
        "- Negative suite on the instance: its evidence file has no `Run at` line."
    ]

    out += [
        "",
        "## Images on the instance",
        "",
        "| Container | Image | Digest | Revision | Version | At this commit |",
        "|---|---|---|---|---|---|",
    ]
    for row in images:
        ok = "✓" if row["revision"] == commit else "✗"
        out.append(
            f"| {md(row['container'])} | `{md(row['image'])}` "
            f"| `{md(row['digest']) or 'none'}` | `{md(row['revision']) or 'none'}` "
            f"| {md(row['version']) or 'none'} | {ok} |"
        )
    if other:
        out += [
            "",
            "Not at this commit, so the instance does not run one version: "
            + ", ".join(f"`{md(row['container'])}`" for row in other)
            + ".",
        ]

    out += [
        "",
        "## Totals per suite",
        "",
        "| Suite | Passed | Failed | Skipped |",
        "|---|---:|---:|---:|",
    ]
    grand = Counter()
    for suite, cases in suites.items():
        found = counts(cases)
        grand.update(found)
        out.append(
            f"| {md(suite)} | {found['passed']} | {found['failed']}"
            f" | {found['skipped']} |"
        )
    out.append(
        f"| **All** | **{grand['passed']}** | **{grand['failed']}**"
        f" | **{grand['skipped']}** |"
    )

    out += ["", "## Failures", ""]
    if failures:
        out += ["| Suite | Case | Message | Issue |", "|---|---|---|---|"]
        for suite, case in failures:
            issue = issue_link(issue_for(issues, suite, case["id"]) or "")
            out.append(
                f"| {md(suite)} | `{md(case['id'])}` | {md(case['message'])} "
                f"| {issue} |"
            )
    else:
        out.append("None.")

    out += ["", "## Skips and their reasons", ""]
    if skips:
        out += ["| Suite | Case | Reason |", "|---|---|---|"]
        out += [
            f"| {md(suite)} | `{md(case['id'])}` | {md(case['message'])} |"
            for suite, case in skips
        ]
    else:
        out.append("None.")
    if notes:
        out += [
            "",
            "Cases `tests/negative` records as not run on the instance, in its "
            "own words:",
            "",
            *notes,
        ]

    out += [
        "",
        "## Images F20-03 ran",
        "",
        "A pulled image shows its registry digest; one built from the commit "
        "shows its image ID.",
        "",
        "| Image | Digest or ID |",
        "|---|---|",
    ]
    out += [
        f"| `{md(name)}` | `{md(value)}` |"
        for name, value in sorted(verify.get("images", {}).items())
    ]
    out += [
        "",
        "## Reproduce",
        "",
        "```bash",
        f"gh workflow run verify.yml -R Equipo-03-Proyecto10/retail-segmentation "
        f"--ref develop -f commit={commit}",
        "bash ops/verify/instance-images.sh   # on the instance",
        "```",
        "",
        "The negative suite's instance command is in `tests/negative/README.md`, "
        '"The instance".',
    ]
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--verify", type=Path, required=True)
    parser.add_argument("--negative-junit", type=Path, required=True)
    parser.add_argument("--negative-evidence", type=Path, required=True)
    parser.add_argument("--instance-images", type=Path, required=True)
    parser.add_argument("--issues", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        verify = json.loads((args.verify / "report.json").read_text())
        text = build(
            verify,
            load_cases(args.negative_junit),
            *evidence_lines(args.negative_evidence),
            load_instance_images(args.instance_images),
            load_issues(args.issues),
        )
    except (Refused, OSError, KeyError, json.JSONDecodeError) as error:
        print(f"publish.py: {error}", file=sys.stderr)
        return 2
    out = args.out or Path("docs/evidence") / f"f20-12-{verify['commit'][:8]}.md"
    out.write_text(text)
    print(out)
    return 1 if re.search(r"^\*\*failed\*\*", text, re.M) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
