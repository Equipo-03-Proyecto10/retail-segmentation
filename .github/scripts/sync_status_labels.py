"""Keep status:blocked and status:blocking equal to the native relationships.

    python3 .github/scripts/sync_status_labels.py [--dry-run]

docs/process.md §8 makes the relationship authoritative and the label its mirror,
so this reads the one and corrects the other, on every open issue. Those two labels
are the only thing it changes. issueDependenciesSummary counts open blockers and
open blocked issues, so one paginated query covers the repository. Needs gh on PATH,
GH_TOKEN, and GITHUB_REPOSITORY as owner/name.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from urllib.parse import quote

BLOCKED = "status:blocked"
BLOCKING = "status:blocking"

QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    issues(states: OPEN, first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        labels(first: 50) { nodes { name } }
        issueDependenciesSummary { blockedBy blocking }
      }
    }
  }
}
"""

Runner = Callable[[list[str]], str]


def gh(arguments: list[str]) -> str:
    return subprocess.run(
        ["gh", *arguments], capture_output=True, text=True, check=True
    ).stdout


def open_issues(owner: str, name: str, run: Runner = gh) -> Iterable[Mapping]:
    cursor = None
    while True:
        arguments = ["api", "graphql", "-f", f"query={QUERY}"]
        arguments += ["-f", f"owner={owner}", "-f", f"name={name}"]
        if cursor:
            arguments += ["-f", f"cursor={cursor}"]
        page = json.loads(run(arguments))["data"]["repository"]["issues"]
        yield from page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return
        cursor = page["pageInfo"]["endCursor"]


def changes_for(issue: Mapping) -> list[tuple[int, str, str]]:
    """What must change on one issue, as (number, "add" or "remove", label)."""
    held = {label["name"] for label in issue["labels"]["nodes"]}
    summary = issue["issueDependenciesSummary"]
    wanted = {BLOCKED: summary["blockedBy"] > 0, BLOCKING: summary["blocking"] > 0}
    changes = []
    for label, should_hold in wanted.items():
        if should_hold and label not in held:
            changes.append((issue["number"], "add", label))
        elif not should_hold and label in held:
            changes.append((issue["number"], "remove", label))
    return changes


def apply(repository: str, change: tuple[int, str, str], run: Runner = gh) -> None:
    number, action, label = change
    path = f"repos/{repository}/issues/{number}/labels"
    if action == "add":
        run(["api", "-X", "POST", path, "-f", f"labels[]={label}", "--silent"])
    else:
        run(["api", "-X", "DELETE", f"{path}/{quote(label, safe='')}", "--silent"])


def main(argv: list[str]) -> int:
    repository = os.environ["GITHUB_REPOSITORY"]
    owner, name = repository.split("/", 1)
    dry_run = "--dry-run" in argv
    checked, changes = 0, []
    for issue in open_issues(owner, name):
        checked += 1
        changes += changes_for(issue)
    lines = [f"{checked} open issues checked, {len(changes)} labels to change."]
    for change in changes:
        if not dry_run:
            apply(repository, change)
        number, action, label = change
        lines.append(f"- #{number}: {action} `{label}`")
    report = "\n".join(lines)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            output.write(report + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
