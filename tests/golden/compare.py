"""Compare a fresh capture with the committed fixtures: the drift check.

    python -m tests.golden.compare CAPTURED_DIR [--services-dir services]

For each port, reads services/<name>/tests/golden/<name>.json and the fresh
CAPTURED_DIR/<name>.json, and compares them case by case, ignoring the header.
A port with no services/<name>/tests/golden/ yet is "not yet captured" and is
skipped. Any other difference is one DRIFT line naming the fixture, the case
and the web commit the fixture was captured from, and the exit status is 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PORTS = ("sales", "recommendations", "users", "campaigns")


def _cases(fixture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {case["id"]: case for case in fixture["cases"]}


def compare_port(name: str, captured_dir: Path, services_dir: Path) -> list[str]:
    golden = services_dir / name / "tests" / "golden"
    if not golden.is_dir():
        return [f"{name}: not yet captured ({golden} does not exist); skipped"]
    committed_path = golden / f"{name}.json"
    if not committed_path.is_file():
        return [f"DRIFT {committed_path}: the fixture file is missing"]
    committed = json.loads(committed_path.read_text())
    fresh = json.loads((captured_dir / f"{name}.json").read_text())
    commit = committed["header"].get("web_commit", "unknown")
    before, after = _cases(committed), _cases(fresh)

    problems = []
    for case_id in sorted(before.keys() | after.keys()):
        where = f"DRIFT {committed_path}: case {case_id} (captured from web {commit})"
        if case_id not in after:
            problems.append(f"{where}: no longer captured")
        elif case_id not in before:
            problems.append(f"{where}: captured now, absent from the fixture")
        else:
            for part in ("input", "outcome", "service_differs"):
                if before[case_id].get(part) != after[case_id].get(part):
                    was = json.dumps(before[case_id].get(part), sort_keys=True)
                    now = json.dumps(after[case_id].get(part), sort_keys=True)
                    problems.append(f"{where}: {part} was {was}, now {now}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("captured_dir", type=Path)
    parser.add_argument("--services-dir", type=Path, default=Path("services"))
    args = parser.parse_args(argv)

    drift = False
    for name in PORTS:
        for line in compare_port(name, args.captured_dir, args.services_dir):
            print(line)
            drift = drift or line.startswith("DRIFT")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
