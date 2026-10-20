"""ADR-0040's memory gate: every container's mem_limit sums to at most 3,000 MB.

    python3 .github/scripts/memory_gate.py [compose.yaml ...]

Without arguments it reads deploy/infra/compose.yaml and every
services/*/compose.yaml. A limit is a whole number of `m` or `g` (1g = 1024m);
a container without one, or with any other form, fails the gate. Prints the
total when it passes; prints each problem and exits 1 when it does not.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

BUDGET_MB = 3000
_LIMIT = re.compile(r"([0-9]+)([mg])")


def megabytes(value: object) -> int | None:
    match = _LIMIT.fullmatch(str(value))
    if match is None:
        return None
    number, unit = int(match.group(1)), match.group(2)
    return number * 1024 if unit == "g" else number


def check(files: list[Path]) -> tuple[int, list[str]]:
    total = 0
    problems = []
    for path in files:
        services = (yaml.safe_load(path.read_text()) or {}).get("services") or {}
        for name, service in services.items():
            value = (service or {}).get("mem_limit")
            size = megabytes(value)
            if size is None:
                problems.append(f"FAIL: {path}: {name} has mem_limit {value!r}")
            else:
                total += size
    if total > BUDGET_MB:
        problems.append(
            f"FAIL: mem_limit sums to {total} MB, above {BUDGET_MB:,} MB (ADR-0040)"
        )
    return total, problems


def main(arguments: list[str]) -> int:
    files = [Path(argument) for argument in arguments] or [
        Path("deploy/infra/compose.yaml"),
        *sorted(Path("services").glob("*/compose.yaml")),
    ]
    total, problems = check(files)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"mem_limit sums to {total} MB of {BUDGET_MB:,} MB across {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
