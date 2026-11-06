"""The verification run's results and report (F20-03, #761).

Every suite step leaves one JUnit file under <report>/results/<suite>/. This
module writes the files the steps cannot write themselves, and builds the
report from all of them:

    report.py check   <report> <suite> <step> <status> <log>
    report.py junit   <report> <suite> <step> <status> <log> <junit>...
    report.py libtest <report> <suite> <step> <status> <log>
    report.py build   <report>
    report.py compare <first report.json> <second report.json> [<out.md>]

`check` records a command that is not a test runner (a lint, a build) as one
case. `junit` adopts a runner's JUnit files; when the runner exited non-zero
and its files hold no failure, or it left no file at all, the step gets a
failed case of its own, so a suite that could not run is never skipped.
`libtest` turns `cargo test` output into JUnit. `build` writes report.json and
report.md and exits 1 when any case failed. `compare` lists every case whose
outcome differs between two runs: the flaky ones.
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

TAIL_LINES = 40


def _tail(log: Path) -> str:
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return f"(no log at {log})"
    return "\n".join(lines[-TAIL_LINES:])


def _suite_dir(report: Path, suite: str) -> Path:
    path = report / "results" / suite
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write(path: Path, suite: str, cases: list[ET.Element]) -> None:
    root = ET.Element("testsuites")
    node = ET.SubElement(root, "testsuite", name=suite, tests=str(len(cases)))
    node.extend(cases)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _case(
    classname: str, name: str, failure: str | None = None, log: str = ""
) -> ET.Element:
    case = ET.Element("testcase", classname=classname, name=name)
    if failure is not None:
        node = ET.SubElement(case, "failure", message=failure)
        node.text = log
    return case


def check(report: Path, suite: str, step: str, status: int, log: Path) -> None:
    failure = None if status == 0 else f"exited {status}"
    case = _case(f"{suite}.checks", step, failure, _tail(log) if failure else "")
    _write(_suite_dir(report, suite) / f"{step}.xml", suite, [case])


def _cases(path: Path) -> list[ET.Element]:
    return list(ET.parse(path).getroot().iter("testcase"))


def _failed(case: ET.Element) -> bool:
    return case.find("failure") is not None or case.find("error") is not None


def junit(
    report: Path, suite: str, step: str, status: int, log: Path, files: list[Path]
) -> None:
    target = _suite_dir(report, suite)
    found = [path for path in files if path.is_file()]
    cases: list[ET.Element] = []
    for index, path in enumerate(found):
        try:
            parsed = _cases(path)
        except ET.ParseError as error:
            cases.append(
                _case(f"{suite}.{step}", path.name, f"unreadable JUnit: {error}")
            )
            continue
        cases.extend(parsed)
        (target / f"{step}-{index}.xml").write_bytes(path.read_bytes())
    if not found:
        reason = f"could not run: exited {status} and wrote no JUnit file"
        _write(
            target / f"{step}-not-run.xml",
            suite,
            [_case(f"{suite}.{step}", "run", reason, _tail(log))],
        )
        return
    if status != 0 and not any(_failed(case) for case in cases):
        reason = f"exited {status} with no failed case in its JUnit file"
        _write(
            target / f"{step}-exit.xml",
            suite,
            [_case(f"{suite}.{step}", "exit status", reason, _tail(log))],
        )


LIBTEST = re.compile(r"^test (\S+) \.\.\. (ok|FAILED|ignored)(?:, (.*))?$")
TARGET = re.compile(r"^\s*Running .*?([^/\s()]+?)(?:-[0-9a-f]{8,})?\)?$")


def libtest(report: Path, suite: str, step: str, status: int, log: Path) -> None:
    cases: list[ET.Element] = []
    target = "cargo"
    text = log.read_text(errors="replace") if log.is_file() else ""
    for line in text.splitlines():
        if match := TARGET.match(line):
            target = match.group(1)
            continue
        match = LIBTEST.match(line.strip())
        if not match:
            continue
        name, outcome, reason = match.groups()
        case = ET.Element("testcase", classname=f"{suite}.{target}", name=name)
        if outcome == "FAILED":
            ET.SubElement(case, "failure", message="FAILED")
        elif outcome == "ignored":
            ET.SubElement(case, "skipped", message=reason or "#[ignore] with no reason")
        cases.append(case)
    path = report / "tmp" / f"{suite}-{step}-libtest.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, suite, cases)
    junit(report, suite, step, status, log, [path] if cases else [])


def _outcome(case: ET.Element) -> tuple[str, str]:
    for tag, outcome in (
        ("failure", "failed"),
        ("error", "failed"),
        ("skipped", "skipped"),
    ):
        node = case.find(tag)
        if node is not None:
            message = node.get("message") or (node.text or "").strip().splitlines()[:1]
            if isinstance(message, list):
                message = message[0] if message else ""
            return outcome, message or f"{tag} with no message"
    return "passed", ""


def _collect(report: Path) -> dict[str, list[dict[str, str]]]:
    suites: dict[str, list[dict[str, str]]] = {}
    for directory in sorted((report / "results").iterdir()):
        if not directory.is_dir():
            continue
        cases = suites.setdefault(directory.name, [])
        for path in sorted(directory.glob("*.xml")):
            for case in _cases(path):
                outcome, message = _outcome(case)
                cases.append(
                    {
                        "id": f"{case.get('classname', '')}::{case.get('name', '')}",
                        "outcome": outcome,
                        "message": message,
                    }
                )
    return suites


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def build(report: Path) -> int:
    meta = json.loads((report / "meta.json").read_text())
    suites = _collect(report)
    summary = {}
    for suite, cases in suites.items():
        counts = Counter(case["outcome"] for case in cases)
        summary[suite] = {k: counts.get(k, 0) for k in ("passed", "failed", "skipped")}
    failed = sum(s["failed"] for s in summary.values())
    data = {
        **meta,
        "result": "failed" if failed else "passed",
        "summary": summary,
        "cases": suites,
    }
    (report / "report.json").write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n"
    )

    out = [
        f"# Verification run of {meta['commit']}",
        "",
        f"**Result: {data['result']}** — {failed} failed case(s).",
        "",
        f"- Commit: `{meta['commit']}` ({meta.get('subject', '')})",
        f"- Started: {meta.get('started', '')}, finished: {meta.get('finished', '')}",
        f"- Runner: `ops/verify` at `{meta.get('runner_commit', '')}`",
        f"- Toolbox image: `{meta.get('toolbox', '')}`",
        "",
        "## Suites",
        "",
        "| Suite | Passed | Failed | Skipped |",
        "|---|---:|---:|---:|",
    ]
    for suite, counts in summary.items():
        out.append(
            f"| {suite} | {counts['passed']} | {counts['failed']}"
            f" | {counts['skipped']} |"
        )
    out += ["", "## Failures", ""]
    failures = [
        (s, c) for s, cases in suites.items() for c in cases if c["outcome"] == "failed"
    ]
    out += [
        f"- **{s}** `{c['id']}`: {_md_cell(c['message'])}" for s, c in failures
    ] or ["None."]
    out += ["", "## Skips and their reasons", ""]
    skips = [
        (s, c)
        for s, cases in suites.items()
        for c in cases
        if c["outcome"] == "skipped"
    ]
    if skips:
        out += ["| Suite | Case | Reason |", "|---|---|---|"]
        out += [
            f"| {s} | `{_md_cell(c['id'])}` | {_md_cell(c['message'])} |"
            for s, c in skips
        ]
    else:
        out.append("None.")
    out += ["", "## Images", "", "| Image | Digest |", "|---|---|"]
    out += [
        f"| `{name}` | `{digest}` |"
        for name, digest in sorted(meta.get("images", {}).items())
    ]
    out += ["", "## Tool versions", "", "| Tool | Version |", "|---|---|"]
    out += [
        f"| {name} | `{_md_cell(v)}` |"
        for name, v in sorted(meta.get("tools", {}).items())
    ]
    (report / "report.md").write_text("\n".join(out) + "\n")
    print((report / "report.md").read_text())
    return 1 if failed else 0


UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _outcomes(run: dict) -> dict[tuple[str, str], str]:
    """Each case's outcome, keyed by suite and id.

    A test parametrized with a random UUID has a new id in every run, so a UUID
    in an id is replaced by `<uuid>`. Cases that then share an id are compared
    as the sorted list of their outcomes.
    """
    grouped: dict[tuple[str, str], list[str]] = {}
    for suite, cases in run["cases"].items():
        for case in cases:
            key = (suite, UUID.sub("<uuid>", case["id"]))
            grouped.setdefault(key, []).append(case["outcome"])
    return {key: ", ".join(sorted(values)) for key, values in grouped.items()}


def compare(first: Path, second: Path, out: Path | None) -> int:
    runs = [json.loads(path.read_text()) for path in (first, second)]
    outcomes = [_outcomes(run) for run in runs]
    keys = sorted(set(outcomes[0]) | set(outcomes[1]))
    differing = [
        (
            suite,
            case,
            outcomes[0].get((suite, case), "absent"),
            outcomes[1].get((suite, case), "absent"),
        )
        for suite, case in keys
        if outcomes[0].get((suite, case)) != outcomes[1].get((suite, case))
    ]
    lines = [
        "# Comparison of two verification runs",
        "",
        f"- First: `{runs[0]['commit']}`, started {runs[0].get('started', '')}",
        f"- Second: `{runs[1]['commit']}`, started {runs[1].get('started', '')}",
    ]
    if runs[0]["commit"] != runs[1]["commit"]:
        lines.append(
            "- **The runs are of different commits; differences are not flakiness.**"
        )
    lines += ["", f"{len(keys)} cases compared, {len(differing)} differ.", ""]
    if differing:
        lines += [
            "## Flaky",
            "",
            "| Suite | Case | First | Second |",
            "|---|---|---|---|",
        ]
        lines += [f"| {s} | `{_md_cell(c)}` | {a} | {b} |" for s, c, a, b in differing]
    else:
        lines.append("The runs agree.")
    text = "\n".join(lines) + "\n"
    if out:
        out.write_text(text)
    print(text)
    return 1 if differing else 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    command, args = argv[1], argv[2:]
    if command == "check":
        check(Path(args[0]), args[1], args[2], int(args[3]), Path(args[4]))
    elif command == "junit":
        junit(
            Path(args[0]),
            args[1],
            args[2],
            int(args[3]),
            Path(args[4]),
            [Path(a) for a in args[5:]],
        )
    elif command == "libtest":
        libtest(Path(args[0]), args[1], args[2], int(args[3]), Path(args[4]))
    elif command == "build":
        return build(Path(args[0]))
    elif command == "compare":
        return compare(
            Path(args[0]), Path(args[1]), Path(args[2]) if len(args) > 2 else None
        )
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
