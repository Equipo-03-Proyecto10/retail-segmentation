"""The status labels follow the native relationships (F19-09, docs/process.md §8)."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/status-labels.yml"
spec = importlib.util.spec_from_file_location(
    "sync_status_labels", ROOT / ".github/scripts/sync_status_labels.py"
)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def issue(number: int, labels: list[str], blocked_by: int, blocking: int) -> dict:
    return {
        "number": number,
        "labels": {"nodes": [{"name": name} for name in labels]},
        "issueDependenciesSummary": {"blockedBy": blocked_by, "blocking": blocking},
    }


@pytest.mark.parametrize(
    ("labels", "blocked_by", "blocking", "expected"),
    [
        ([], 0, 0, []),
        ([sync.BLOCKED, sync.BLOCKING], 2, 1, []),
        ([], 1, 0, [(7, "add", sync.BLOCKED)]),
        ([], 0, 3, [(7, "add", sync.BLOCKING)]),
        ([sync.BLOCKED], 0, 0, [(7, "remove", sync.BLOCKED)]),
        ([sync.BLOCKING], 0, 0, [(7, "remove", sync.BLOCKING)]),
        (
            [sync.BLOCKED],
            0,
            2,
            [(7, "remove", sync.BLOCKED), (7, "add", sync.BLOCKING)],
        ),
    ],
)
def test_the_label_follows_the_relationship(labels, blocked_by, blocking, expected):
    assert sync.changes_for(issue(7, labels, blocked_by, blocking)) == expected


def test_any_other_label_is_left_alone() -> None:
    other = ["type:bug", "priority:P0", "area:app", "status:review"]
    assert sync.changes_for(issue(7, other, 0, 0)) == []


def test_a_correct_issue_changes_nothing_the_second_time() -> None:
    """Applying the changes leaves nothing to change."""
    before = issue(7, [sync.BLOCKED], 0, 2)
    held = {sync.BLOCKED}
    for _, action, label in sync.changes_for(before):
        (held.add if action == "add" else held.discard)(label)
    assert sync.changes_for(issue(7, sorted(held), 0, 2)) == []


def test_every_page_is_read() -> None:
    pages = [
        {"hasNextPage": True, "endCursor": "next"},
        {"hasNextPage": False, "endCursor": None},
    ]
    calls: list[list[str]] = []

    def run(arguments: list[str]) -> str:
        calls.append(arguments)
        info = pages[len(calls) - 1]
        issues = {"pageInfo": info, "nodes": [issue(len(calls), [], 0, 0)]}
        return json.dumps({"data": {"repository": {"issues": issues}}})

    found = list(sync.open_issues("owner", "name", run))
    assert [item["number"] for item in found] == [1, 2]
    assert "cursor=next" in calls[1]
    assert not any(argument.startswith("cursor=") for argument in calls[0])


def test_a_label_is_added_and_removed_by_its_escaped_name() -> None:
    calls: list[list[str]] = []

    def run(arguments: list[str]) -> str:
        calls.append(arguments)
        return ""

    sync.apply("o/r", (7, "add", sync.BLOCKED), run)
    sync.apply("o/r", (7, "remove", sync.BLOCKED), run)
    assert calls[0][:4] == ["api", "-X", "POST", "repos/o/r/issues/7/labels"]
    assert "labels[]=status:blocked" in calls[0]
    assert calls[1][3] == "repos/o/r/issues/7/labels/status%3Ablocked"


def test_the_workflow_token_can_only_write_issues() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    block = re.search(r"^permissions:\n((?:  .+\n)+)", text, re.MULTILINE).group(1)
    assert block.split() == ["issues:", "write"]
    assert "types: [closed, reopened]" in text
    assert "schedule:" in text
