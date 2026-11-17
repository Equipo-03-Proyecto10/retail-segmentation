"""The test images of the nightly chain and the emulator job (ADR-0080).

ci-images.sh keys each service image by git trees, pulls what exists, builds the
rest and pushes only what it built. These tests run it in a scratch repository,
with a stand-in for docker that records each call and keeps a fake registry in
files. They also hold the workflows and the instance check to the record.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "ghcr.io/equipo-03-proyecto10"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("sha256sum") is None,
    reason="ci-images.sh needs git and sha256sum",
)

# Logs each call; keeps the registry and every image's revision label in files.
FAKE_DOCKER = r"""#!/usr/bin/env bash
log=$DOCKER_STUB/log registry=$DOCKER_STUB/registry revisions=$DOCKER_STUB/revisions
touch "$registry" "$revisions"
printf '%s\n' "$*" >>"$log"
last=${@: -1}
revision_of() { awk -v r="$1" '$1 == r {v = $2} END {print v}' "$revisions"; }
label=org.opencontainers.image.revision=
case $1 in
  pull)
    grep -qxF "$last" "$registry" && exit 0
    echo "Error response: manifest unknown" >&2
    exit 1 ;;
  manifest) grep -qxF "$last" "$registry" ;;
  push) echo "$last" >>"$registry" ;;
  tag) echo "$3 $(revision_of "$2")" >>"$revisions" ;;
  image) revision_of "$last" ;;
  build)
    shift; revision=; tags=()
    while [ $# -gt 1 ]; do
      case $1 in
        --label) [[ $2 == "$label"* ]] && revision=${2#"$label"}; shift 2 ;;
        -t) tags+=("$2"); shift 2 ;;
        *) shift ;;
      esac
    done
    for t in "${tags[@]}"; do echo "$t $revision" >>"$revisions"; done ;;
esac
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV},
    ).stdout.strip()


def write(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def commit(repo: Path) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "change")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    for script in ("stage-contracts.sh", "ci-images.sh"):
        write(repo, f".github/scripts/{script}", "")
        shutil.copy2(ROOT / ".github/scripts" / script, repo / ".github/scripts")
    for name in ("alpha", "beta"):
        write(repo, f"services/{name}/Dockerfile", "FROM scratch\n")
        write(repo, f"services/{name}/app.py", f"NAME = {name!r}\n")
        write(repo, f"contracts/{name}/openapi.yaml", f"title: {name}\n")
    write(repo, "contracts/common/v1/common.xsd", "<schema/>\n")
    write(repo, "docker/distributed/local.env", "IMAGE_TAG=local\n")
    write(repo, "docs/notes.md", "notes\n")
    git(repo, "init", "-q")
    commit(repo)
    return repo


def run(repo: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", ".github/scripts/ci-images.sh", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], **env},
    )


def key(repo: Path, name: str) -> str:
    result = run(repo, "key", name)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def keys(repo: Path) -> dict[str, str]:
    return {name: key(repo, name) for name in ("alpha", "beta")}


@pytest.mark.parametrize(
    ("relative", "changed"),
    [
        ("services/alpha/app.py", {"alpha"}),
        ("contracts/beta/openapi.yaml", {"beta"}),
        ("contracts/common/v1/common.xsd", {"alpha", "beta"}),
        ("docs/notes.md", set()),
        ("contracts/gamma/openapi.yaml", set()),
    ],
)
def test_a_change_moves_exactly_the_keys_of_what_it_builds_into(
    repo: Path, relative: str, changed: set[str]
) -> None:
    before = keys(repo)
    write(repo, relative, "changed\n")
    commit(repo)
    after = keys(repo)
    assert {name for name in before if before[name] != after[name]} == changed


def test_a_key_is_a_valid_tag_and_differs_per_service(repo: Path) -> None:
    alpha, beta = key(repo, "alpha"), key(repo, "beta")
    assert re.fullmatch(r"[0-9a-f]{24}", alpha)
    assert alpha != beta


def test_a_key_names_a_commit_and_ignores_uncommitted_files(repo: Path) -> None:
    first = key(repo, "alpha")
    write(repo, "services/alpha/app.py", "NAME = 'edited'\n")
    assert key(repo, "alpha") == first
    commit(repo)
    assert key(repo, "alpha") != first
    assert run(repo, "key", "alpha", "HEAD~1").stdout.strip() == first


def test_a_missing_path_fails_and_prints_no_key(repo: Path) -> None:
    shutil.rmtree(repo / "contracts/beta")
    commit(repo)
    result = run(repo, "key", "beta")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "contracts/beta does not exist" in result.stderr


def test_the_key_covers_exactly_what_stage_contracts_copies(repo: Path) -> None:
    subprocess.run(
        ["bash", ".github/scripts/stage-contracts.sh", "alpha"], cwd=repo, check=True
    )
    staged_root = repo / "services/alpha/contracts"
    staged = {
        path.relative_to(staged_root).as_posix()
        for path in staged_root.rglob("*")
        if path.is_file()
    }
    sources = subprocess.run(
        ["bash", ".github/scripts/stage-contracts.sh", "--sources", "alpha"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    expected = {
        (Path(source) / path.relative_to(repo / source))
        .as_posix()
        .removeprefix("contracts/")
        for source in sources
        for path in (repo / source).rglob("*")
        if path.is_file()
    }
    assert sources == ["contracts/alpha", "contracts/common/v1"]
    assert staged == expected == {"alpha/openapi.yaml", "common/v1/common.xsd"}
    for relative in staged:
        assert (staged_root / relative).read_bytes() == (
            repo / "contracts" / relative
        ).read_bytes()


@pytest.fixture
def docker(tmp_path: Path) -> dict[str, str]:
    stub = tmp_path / "stub"
    (stub / "bin").mkdir(parents=True)
    (stub / "bin/docker").write_text(FAKE_DOCKER)
    (stub / "bin/docker").chmod(0o755)
    return {
        "PATH": f"{stub / 'bin'}:{os.environ['PATH']}",
        "DOCKER_STUB": str(stub),
        "CI_IMAGES_STATE": str(stub / "built.tsv"),
        "GITHUB_STEP_SUMMARY": str(stub / "summary.md"),
    }


def calls(env: dict[str, str]) -> list[str]:
    return (Path(env["DOCKER_STUB"]) / "log").read_text().splitlines()


def test_prepare_pulls_what_exists_and_builds_the_rest(
    repo: Path, docker: dict[str, str]
) -> None:
    alpha, beta = key(repo, "alpha"), key(repo, "beta")
    stub = Path(docker["DOCKER_STUB"])
    (stub / "registry").write_text(f"{REGISTRY}/mosaiq-ci-alpha:{alpha}\n")
    (stub / "revisions").write_text(f"{REGISTRY}/mosaiq-ci-alpha:{alpha} 1111\n")
    head = git(repo, "rev-parse", "HEAD")

    result = run(repo, "prepare", **docker)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f"pulled alpha {alpha}", f"built beta {beta}"]
    log = calls(docker)
    assert (
        f"tag {REGISTRY}/mosaiq-ci-alpha:{alpha} {REGISTRY}/mosaiq-alpha:local" in log
    )
    (build,) = [line for line in log if line.startswith("build ")]
    assert f"--label org.opencontainers.image.revision={head}" in build
    assert (
        f"-t {REGISTRY}/mosaiq-beta:local -t {REGISTRY}/mosaiq-ci-beta:{beta}" in build
    )
    assert build.endswith(" services/beta")
    assert Path(docker["CI_IMAGES_STATE"]).read_text() == (
        f"beta\t{REGISTRY}/mosaiq-ci-beta:{beta}\n"
    )
    summary = Path(docker["GITHUB_STEP_SUMMARY"]).read_text()
    assert f"| alpha | pulled | `{alpha}` | 1111 |" in summary
    assert f"| beta | built | `{beta}` | {head} |" in summary
    assert "no " + f"{REGISTRY}/mosaiq-ci-beta:{beta}" in result.stderr


def test_push_sends_only_what_was_built_and_never_over_a_tag(
    repo: Path, docker: dict[str, str]
) -> None:
    assert run(repo, "prepare", **docker).returncode == 0
    alpha, beta = key(repo, "alpha"), key(repo, "beta")
    # Another run pushed beta's key in the meantime.
    (Path(docker["DOCKER_STUB"]) / "registry").write_text(
        f"{REGISTRY}/mosaiq-ci-beta:{beta}\n"
    )

    result = run(repo, "push", **docker)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"pushed {REGISTRY}/mosaiq-ci-alpha:{alpha}",
        f"kept {REGISTRY}/mosaiq-ci-beta:{beta}: the tag already exists",
    ]
    pushes = [line for line in calls(docker) if line.startswith("push ")]
    assert pushes == [f"push --quiet {REGISTRY}/mosaiq-ci-alpha:{alpha}"]


def test_push_after_pulling_everything_pushes_nothing(
    repo: Path, docker: dict[str, str]
) -> None:
    (Path(docker["DOCKER_STUB"]) / "registry").write_text(
        "".join(f"{REGISTRY}/mosaiq-ci-{n}:{key(repo, n)}\n" for n in ("alpha", "beta"))
    )
    assert run(repo, "prepare", **docker).returncode == 0
    result = run(repo, "push", **docker)
    assert result.stdout.strip() == "ci-images: nothing was built, nothing to push"
    assert not [line for line in calls(docker) if line.startswith(("build ", "push "))]


@pytest.mark.parametrize(
    ("image", "report"),
    [
        (f"{REGISTRY}/mosaiq-ci-stores:0123456789abcdef01234567", "a CI test image"),
        (f"{REGISTRY}/mosaiq-stores:1.4.0", None),
        ("redis:7.4.2-alpine@sha256:" + "0" * 64, None),
        ("mongo:7.0.41", None),
        ("nginx:1.27-alpine", "an unexpected image"),
    ],
)
def test_the_instance_check_refuses_a_test_image(
    image: str, report: str | None
) -> None:
    text = (ROOT / "deploy/check-services.sh").read_text()
    block = re.search(r"^ *case \$image in\n.*?^ *esac\n", text, re.M | re.S)
    assert block, "deploy/check-services.sh has no case over $image"
    script = f"GHCR={REGISTRY}/mosaiq-\nname=c\nimage=$1\n{block.group(0)}"
    result = subprocess.run(
        ["bash", "-c", script, "_", image], capture_output=True, text=True, check=True
    )
    if report is None:
        assert result.stdout == ""
    else:
        assert report in result.stdout


def workflow_jobs() -> list[tuple[str, str, dict]]:
    yaml = pytest.importorskip("yaml")
    found = []
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        document = yaml.safe_load(path.read_text())
        for job_id, job in (document.get("jobs") or {}).items():
            found.append((path.name, job_id, job))
    return found


def runs(job: dict, text: str) -> list[dict]:
    return [step for step in job.get("steps", []) if text in (step.get("run") or "")]


def test_only_the_nightly_chain_and_the_emulator_use_test_images() -> None:
    users = {
        (name, job_id)
        for name, job_id, job in workflow_jobs()
        if runs(job, "ci-images.sh")
    }
    assert users == {("nightly.yml", "e2e"), ("android.yml", "emulator")}


def test_only_a_run_on_develop_pushes_and_only_these_jobs_write_packages() -> None:
    for name, job_id, job in workflow_jobs():
        if not runs(job, "ci-images.sh"):
            continue
        assert job["permissions"] == {"contents": "read", "packages": "write"}, job_id
        pushes = runs(job, "ci-images.sh push")
        assert len(pushes) == 1, (name, job_id)
        assert pushes[0]["if"] == "github.ref == 'refs/heads/develop'", (name, job_id)


def test_these_jobs_build_nothing_through_compose_and_the_from_source_checks_stay() -> (
    None
):
    for workflow in ("nightly.yml", "android.yml"):
        text = (ROOT / ".github/workflows" / workflow).read_text()
        assert "up --build" not in text, workflow
        assert "up --no-build --wait" in text, workflow
    assert "up --build" in (ROOT / ".github/workflows/service-template.yml").read_text()


def test_the_cleanup_workflow_runs_weekly_with_packages_write() -> None:
    (job,) = [
        job for name, _, job in workflow_jobs() if name == "ci-images-cleanup.yml"
    ]
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert runs(job, ".github/scripts/ci_images_cleanup.py")
