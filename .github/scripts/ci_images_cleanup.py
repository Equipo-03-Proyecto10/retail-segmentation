"""Delete test images older than 14 days, keeping each service's newest (ADR-0080).

    python3 .github/scripts/ci_images_cleanup.py [--dry-run]

Every services/<name>/ with a Dockerfile has a test-image package mosaiq-ci-<name>,
which the nightly chain and the emulator job push to (ci-images.sh). This deletes
the versions created more than 14 days ago, except the newest of each package, so
a service that has not changed in a while keeps the image the next run pulls. It
touches no other package: release images (mosaiq-<name>) are never listed here. A
package that does not exist yet is skipped. Run from the repository root. Needs gh
on PATH, GH_TOKEN with packages: write, and GITHUB_REPOSITORY_OWNER.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

KEEP_DAYS = 14

Runner = Callable[[list[str]], str]


class NotFound(Exception):
    """The package does not exist yet."""


def gh(arguments: list[str]) -> str:
    result = subprocess.run(["gh", *arguments], capture_output=True, text=True)
    if result.returncode != 0:
        if "HTTP 404" in result.stderr:
            raise NotFound(result.stderr.strip())
        raise subprocess.CalledProcessError(
            result.returncode, ["gh", *arguments], result.stdout, result.stderr
        )
    return result.stdout


def created(version: dict) -> datetime:
    return datetime.fromisoformat(version["created_at"].replace("Z", "+00:00"))


def to_delete(
    versions: Iterable[dict], now: datetime, keep_days: int = KEEP_DAYS
) -> list[dict]:
    """The versions older than keep_days, never the newest one."""
    ordered = sorted(versions, key=created, reverse=True)
    cutoff = now - timedelta(days=keep_days)
    return [version for version in ordered[1:] if created(version) < cutoff]


def services(root: Path) -> list[str]:
    return sorted(path.parent.name for path in root.glob("services/*/Dockerfile"))


def package_versions(owner: str, package: str, run: Runner) -> list[dict]:
    output = run(
        [
            "api",
            "--paginate",
            "--jq",
            ".[] | {id, created_at}",
            f"orgs/{owner}/packages/container/{package}/versions?per_page=100",
        ]
    )
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def clean(
    root: Path,
    owner: str,
    now: datetime,
    run: Runner = gh,
    dry_run: bool = False,
) -> list[str]:
    """Delete what to_delete names in every test-image package; return what it did."""
    done = []
    for name in services(root):
        package = f"mosaiq-ci-{name}"
        try:
            versions = package_versions(owner, package, run)
        except NotFound:
            done.append(f"{package}: no package yet")
            continue
        doomed = to_delete(versions, now)
        for version in doomed:
            if not dry_run:
                run(
                    [
                        "api",
                        "-X",
                        "DELETE",
                        f"orgs/{owner}/packages/container/{package}/versions/"
                        f"{version['id']}",
                    ]
                )
            done.append(
                f"{package}: {'would delete' if dry_run else 'deleted'} "
                f"version {version['id']} from {version['created_at']}"
            )
        done.append(f"{package}: kept {len(versions) - len(doomed)} of {len(versions)}")
    return done


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    owner = os.environ["GITHUB_REPOSITORY_OWNER"]
    for line in clean(Path("."), owner, datetime.now(UTC), dry_run=dry_run):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
