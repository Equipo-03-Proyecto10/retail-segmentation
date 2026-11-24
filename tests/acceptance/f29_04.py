"""F29-04 (#889): the evidence of the segmentation path, on one version.

docs/evidence/f29-04-segmentation-path.md is the runbook. Each subcommand writes
an evidence directory with a summary.md:

    suites     the five analytic acceptance tests at the checked-out commit, from
               a clean database built by the three scripts: "RFM" (the hand
               values), "Migración" (the previous assignment kept, the matrix
               against a table worked on paper), "Incremental" (the incremental
               profile against a full recomputation), "K-means" (the registered
               seed and configuration give the registered partition) and
               "Drift" (planted shifts detected, the alert explained, the
               false-alarm rate within ADR-0065's 1%)
    instance   on the deployed system, after F29-03's sale: a verification
               recalculation started as ADMIN, which compares every profile
               held incrementally with a full recomputation ("Incremental"),
               and the sale's customer's migrations ("Migración")

`suites` needs TEST_DATABASE_URL and MOSAIQ_TEST_OWNER_URL (the owner of a
throwaway database loaded from sql/00, 01 and 02), TEST_REDIS_URL, and
DRIFT_STUDY_DATABASE_URL (a second such database, which the drift study writes
to), plus `uv` and `cargo`. Every suite runs with MOSAIQ_REQUIRE_POSTGRES and
MOSAIQ_REQUIRE_REDIS set, and a check holds only with nothing skipped, so a
missing database is a failure, never a pass. `instance` needs MOSAIQ_BASE_URL
and an ADMIN's credential (MOSAIQ_ACCEPTANCE_EMAIL / _PASSWORD, else prompted).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

from tests.acceptance.evidence import (
    JWT,
    Masker,
    Recorder,
    credential,
    edge_from_environment,
    git,
    sign_in,
    sign_out,
    versions,
)
from tests.negative.stack import ROOT

# The registered result of F23-04 (docs/evidence/f23-04-kmeans-stability.md §1):
# k = 4, seed 11, 100 iterations, tolerance 0.0001, 180-day window, dataset seed
# 20261031. The digest names every customer with its cluster.
REGISTERED_PARTITION = (
    "040e8e1a8171ac792d4e0c6235376b13a45602afa075f67d372a020e677d26e0"
)
# The figures of F23-10 (docs/evidence/f23-10-drift-detection.md §5).
REGISTERED_DRIFT = "cfb2ddcfacaa1cef431f6a2ae54294fa8138e44f73a31facc4d5be6d51630f25"

REQUIRED = (
    "TEST_DATABASE_URL",
    "MOSAIQ_TEST_OWNER_URL",
    "TEST_REDIS_URL",
    "DRIFT_STUDY_DATABASE_URL",
)


@dataclass(frozen=True)
class Suite:
    """One command of one criterion, run from `cwd`."""

    criterion: str
    name: str
    kind: str  # "pytest" (root), "service" (uv, in services/<cwd>), "cargo"
    cwd: str
    targets: tuple[str, ...]
    selection: str | None = None
    claims: str = ""


SUITES: tuple[Suite, ...] = (
    Suite(
        "RFM",
        "web against the hand values",
        "pytest",
        ".",
        ("tests/test_rfm_hand_computed.py", "tests/test_rfm_hand_fixture_copies.py"),
        claims="F29-01: 14 customers worked on paper, the web's R, F, M and scores",
    ),
    Suite(
        "RFM",
        "rfm-engine against the hand values",
        "service",
        "rfm-engine",
        ("tests/test_hand_computed.py",),
        claims="F29-01: the same fixture, byte for byte, through rfm-engine",
    ),
    Suite(
        "Migración",
        "a provoked change keeps the previous assignment",
        "service",
        "rfm-engine",
        ("tests/test_assignment.py",),
        claims="F22-15: a change closes the previous assignment and opens one",
    ),
    Suite(
        "Migración",
        "the matrix against a table worked on paper",
        "cargo",
        "segments",
        ("migration_validation", "assignment_cause"),
        claims="F29-02: six customers moved by each cause, matrix and history",
    ),
    Suite(
        "Incremental",
        "incremental profiles against a full recomputation",
        "service",
        "rfm-engine",
        ("tests/test_verification.py",),
        claims="F22-08: a recorded sale, the profile before, after and recomputed",
    ),
    Suite(
        "K-means",
        "the registry's fit pinned to the web's",
        "service",
        "model-registry",
        ("tests/test_training.py",),
        claims="F22-12: golden fixtures shared with the web",
    ),
    Suite(
        "Drift",
        "a planted shift through the running monitor",
        "service",
        "model-registry",
        ("tests/test_monitor.py", "tests/test_drift.py"),
        claims="F23-09, F23-10: two passes, the alert and its review read back",
    ),
)


# ---------- reading results ----------


def junit_counts(path: Path) -> dict[str, int]:
    """Cases, failures, errors and skips of a pytest JUnit file."""
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in total:
            total[key] += int(suite.get(key, 0))
    return total


_CARGO = re.compile(r"test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored")


def cargo_counts(log: str) -> dict[str, int]:
    """The totals of every `test result:` line of a cargo test log."""
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for _, passed, failed, ignored in _CARGO.findall(log):
        total["tests"] += int(passed) + int(failed) + int(ignored)
        total["failures"] += int(failed)
        total["skipped"] += int(ignored)
    return total


def held(returncode: int, counts: dict[str, int]) -> bool:
    """A suite holds when it exits 0, ran something, and failed, erred or
    skipped nothing: a skip would be a case the acceptance did not see."""
    return (
        returncode == 0
        and counts.get("tests", 0) > 0
        and counts.get("failures", 0) == 0
        and counts.get("errors", 0) == 0
        and counts.get("skipped", 0) == 0
    )


def command_for(suite: Suite, junit: Path) -> list[str]:
    if suite.kind == "cargo":
        command = ["cargo", "test", "--locked", "--no-fail-fast"]
        for target in suite.targets:
            command += ["--test", target]
        return command + ["--", "--test-threads=1"]
    pytest = ["pytest", "-q", "-rs", "-p", "no:cacheprovider", f"--junitxml={junit}"]
    if suite.selection:
        pytest += ["-k", suite.selection]
    if suite.kind == "service":
        return ["uv", "run", "--frozen", *pytest, *suite.targets]
    return [sys.executable, "-m", *pytest, *suite.targets]


def _directory(suite: Suite) -> Path:
    return ROOT if suite.kind == "pytest" else ROOT / "services" / suite.cwd


@dataclass
class Ran:
    returncode: int
    counts: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0


# A connection string carries its password between the first colon and the "@".
_DSN_PASSWORD = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)(@)")


def mask_log(masker: Masker, text: str) -> str:
    """A raw log, with every token and every connection-string password masked.

    A failed connection prints its DSN, so a log can hold a credential the
    runbook promises no file holds (step 3)."""
    text = JWT.sub(lambda m: masker.mark(m.group(0)), text)
    return _DSN_PASSWORD.sub(lambda m: m[1] + masker.mark(m[2]) + m[3], text)


def run_command(
    suite: Suite, out: Path, environment: dict[str, str], index: int, masker: Masker
) -> tuple[Ran, str]:
    stem = f"{index:02d}-{suite.criterion.lower().replace('ó', 'o')}-{suite.cwd}"
    stem = stem.replace(".", "web")
    junit = out / f"{stem}.junit.xml"
    log = out / f"{stem}.log"
    if suite.kind in (
        "service",
        "cargo",
    ):  # both read their contracts from the build context
        staged = subprocess.run(
            ["bash", ".github/scripts/stage-contracts.sh", suite.cwd],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if staged.returncode != 0:
            log.write_text(mask_log(masker, staged.stdout + staged.stderr))
            return Ran(staged.returncode), log.name
    started = time.monotonic()
    result = subprocess.run(
        command_for(suite, junit),
        cwd=_directory(suite),
        env=environment,
        capture_output=True,
        text=True,
    )
    log.write_text(
        mask_log(
            masker,
            " ".join(command_for(suite, junit))
            + "\n\n"
            + result.stdout
            + result.stderr,
        )
    )
    counts = (
        cargo_counts(result.stdout + result.stderr)
        if suite.kind == "cargo"
        else (junit_counts(junit) if junit.exists() else {})
    )
    return Ran(result.returncode, counts, time.monotonic() - started), log.name


# ---------- the two studies ----------


def kmeans_reproduction() -> dict:
    from analysis import stability_study

    return stability_study.reproduce()


def drift_figures(database_url: str) -> dict:
    from analysis import drift_detection_study as study

    found = study.figures(study.run(database_url=database_url))
    return found | {"digest": study.digest(found)}


def check_kmeans(rec: Recorder, out: Path, reproduce: Callable[[], dict]) -> None:
    started = time.monotonic()
    result = reproduce()
    record = result["record"]
    lines = [f"{key}: {value}" for key, value in sorted(record.items())]
    lines += [f"digest: {result['digest']}", f"repeats: {result['repeats']}"]
    lines += [f"differences: {result['differences'] or 'none'}"]
    lines += [f"seconds: {time.monotonic() - started:.1f}"]
    (out / "kmeans-reproduction.txt").write_text("\n".join(lines) + "\n")
    rec.check(
        "K-means",
        f"the registered configuration fitted {result['repeats']} times from scratch",
        "the same assignments, centroids and recorded metrics each time",
        result["differences"] or "the same each time",
        not result["differences"],
        ["kmeans-reproduction.txt"],
    )
    rec.check(
        "K-means",
        "the groups are the registered ones (F23-04's partition digest)",
        REGISTERED_PARTITION,
        result["digest"],
        links=["kmeans-reproduction.txt"],
    )


def drift_row(item: dict) -> tuple[str, str, str, bool]:
    """The row of one planted (kind, size): case, expected, actual, held.

    A size no pair detected still gets its row, as F23-10 registers it (the
    digest pins the figures); a detected size holds only when every pair
    detected it and each had an alert on the planted variable, moving the
    planted way."""
    pairs, alerted, explained = item["pairs"], item["alerted_pairs"], item["explained"]
    name = f"{item['kind']} planted at {item['size']:g}"
    if not alerted:
        return (
            f"{name}: not detected, as F23-10 registers it",
            "recorded, not a pass: the digest above pins the figures",
            f"0 of {pairs} detected",
            True,
        )
    return (
        f"{name}: detected, with an alert on the planted variable and direction",
        f"{pairs} of {pairs} detected, and in each an alert on the planted variable",
        f"{alerted} detected, {explained} with such an alert",
        alerted == pairs == explained and pairs > 0,
    )


def check_drift(rec: Recorder, out: Path, figures: Callable[[], dict]) -> None:
    import json

    started = time.monotonic()
    found = figures()
    (out / "drift-figures.json").write_text(json.dumps(found, indent=2, sort_keys=True))
    seconds = time.monotonic() - started
    rec.facts["drift study seconds"] = f"{seconds:.0f}"
    rec.check(
        "Drift",
        "the study's figures are F23-10's (SHA-256 of the figures)",
        REGISTERED_DRIFT,
        found["digest"],
        links=["drift-figures.json"],
    )
    rows = [i for i in found["detection"] if i["kind"] in ("SPEND", "FREQUENCY")]
    for item in rows:
        rec.check("Drift", *drift_row(item), ["drift-figures.json"])
    for kind in ("SPEND", "FREQUENCY"):
        # A kind with no detected size would leave the criterion unshown.
        detected = [i for i in rows if i["kind"] == kind and i["alerted_pairs"]]
        rec.check(
            "Drift",
            f"{kind}: at least one planted size is detected",
            "at least 1 size detected",
            f"{len(detected)} of {sum(i['kind'] == kind for i in rows)} sizes",
            bool(detected),
            ["drift-figures.json"],
        )
    fa = found["false_alarms"]
    rec.check(
        "Drift",
        "no shift: alerts raised, and the 95% upper bound of the rate",
        "within 1%",
        f"{fa['alerted_pairs']} of {fa['pairs']}, bound {fa['alert_upper'] * 100:.2f}%",
        fa["alert_upper"] <= 0.01,
        ["drift-figures.json"],
    )


def run_suites(args: argparse.Namespace) -> int:
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        raise SystemExit("Set " + ", ".join(missing) + " (see the module docstring).")
    out = Path(args.out).resolve()  # the suites run in other directories
    rec = Recorder(out, "F29-04 the segmentation path, on one version", "f29-04")
    rec.facts["commit"] = git("rev-parse", "HEAD")
    rec.facts["describe"] = git("describe", "--always", "--dirty", "--tags")
    rec.facts["environment"] = "a clean database built by sql/00, 01 and 02"
    environment = os.environ | {
        "MOSAIQ_REQUIRE_POSTGRES": "1",
        "MOSAIQ_REQUIRE_REDIS": "1",
    }
    for index, suite in enumerate(SUITES, start=1):
        if args.only and suite.criterion not in args.only:
            continue
        ran, log = run_command(suite, out, environment, index, rec.masker)
        rec.check(
            suite.criterion,
            f"{suite.name} ({suite.claims})",
            "exit 0; every case run and passed, none skipped",
            f"exit {ran.returncode}, {ran.counts}, {ran.seconds:.0f} s",
            held(ran.returncode, ran.counts),
            [log],
        )
    if not args.only or "K-means" in args.only:
        check_kmeans(rec, out, kmeans_reproduction)
    if not args.only or "Drift" in args.only:
        check_drift(
            rec, out, lambda: drift_figures(os.environ["DRIFT_STUDY_DATABASE_URL"])
        )
    return 0 if rec.write() else 1


# ---------- on the instance ----------

RECALCULATIONS = "/api/rfm-engine/v1/rfm-recalculations"
MIGRATIONS = "/api/segments/v1/segment-migrations"


def run_instance(args: argparse.Namespace) -> int:
    rec = Recorder(Path(args.out), "F29-04 on the instance", "f29-04")
    edge = edge_from_environment()
    rec.facts["target"] = edge.base.geturl()
    versions(rec, edge, ["rfm-engine", "segments", "model-registry"])
    email = credential("MOSAIQ_ACCEPTANCE_EMAIL", "ADMIN email: ", secret=False)
    password = credential("MOSAIQ_ACCEPTANCE_PASSWORD", "Password: ", secret=True)
    token = sign_in(rec, edge, email, password, "ADMIN")
    try:
        _verification(rec, edge, token, args.timeout)
        _migrations(rec, edge, token, args.customer, args.since)
    finally:
        sign_out(rec, edge, token, "ADMIN")
    return 0 if rec.write() else 1


def _verification(rec: Recorder, edge, token: str, timeout: float) -> None:
    reply, number = rec.call(
        edge,
        "POST",
        RECALCULATIONS,
        note="start a verification",
        token=token,
        payload={},
    )
    if reply.status != 201:
        rec.check(
            "Incremental",
            "a verification starts",
            "201",
            reply.outcome(),
            False,
            [rec.link(number)],
        )
        return
    recalculation = reply.json()
    path = f"{RECALCULATIONS}/{recalculation['recalculation_id']}"
    deadline = time.monotonic() + timeout
    while recalculation["state"] == "RUNNING" and time.monotonic() < deadline:
        time.sleep(5)
        reply, number = rec.call(edge, "GET", path, note="poll", token=token)
        if reply.status != 200:
            rec.check(
                "Incremental",
                "the verification can be polled until it ends",
                "200",
                reply.outcome(),
                False,
                [rec.link(number)],
            )
            return
        recalculation = reply.json()
    rec.check(
        "Incremental",
        "every profile held incrementally against a full recomputation at one instant",
        "COMPLETED, EQUIVALENT, 0 different, at least 1 customer checked",
        f"{recalculation['state']}, {recalculation.get('outcome')},"
        f" {recalculation.get('customers_different')} different of"
        f" {recalculation.get('customers_checked')}",
        recalculation["state"] == "COMPLETED"
        and recalculation.get("outcome") == "EQUIVALENT"
        and recalculation.get("customers_different") == 0
        and (recalculation.get("customers_checked") or 0) >= 1,
        [rec.link(number)],
    )
    if recalculation.get("customers_different"):
        rec.call(edge, "GET", path + "/differences", note="which differ", token=token)


def _migrations(rec: Recorder, edge, token: str, customer: str, since: str) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = urlencode({"from": since, "to": now, "customer_id": customer})
    reply, number = rec.call(
        edge,
        "GET",
        f"{MIGRATIONS}?{query}",
        note="the customer's migrations",
        token=token,
    )
    items = reply.json().get("migrations", []) if reply.status == 200 else []
    changed = [m for m in items if m.get("changed_at")]

    held_, actual = migration_verdict(reply.status, changed)
    rec.check(
        "Migración",
        "the sale's customer: the sale changed the segment, and each change still"
        " names the assignment before it",
        "at least 1 changed migration, each with its previous state (and label)",
        actual,
        held_,
        [rec.link(number)],
    )


def previous_kept(m: dict) -> bool:
    # `new` is a first assignment; a label before must still be named.
    state = m.get("previous_state")
    return state in ("new", "unassigned") or (
        state == "label" and bool(m.get("previous_label_code"))
    )


def migration_verdict(status: int, changed: list[dict]) -> tuple[bool, str]:
    """Whether the instance's migration check holds, and what to record.

    No change means the sale did not provoke one: that is recorded and does
    not hold, because `all([])` would otherwise pass on nothing (the runbook:
    "Migración" then rests on step 1)."""
    if status != 200:
        return False, f"HTTP {status}"
    kept = sum(map(previous_kept, changed))
    actual = f"{len(changed)} changed, {kept} with it"
    if not changed:
        return False, actual + ": the sale did not provoke a change"
    return kept == len(changed), actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    suites = sub.add_parser(
        "suites", help="the five tests at this commit, clean database"
    )
    suites.add_argument("--out", required=True)
    suites.add_argument(
        "--only",
        action="append",
        choices=sorted({s.criterion for s in SUITES}),
        help="run one criterion (repeatable); the acceptance runs all",
    )
    instance = sub.add_parser("instance", help="the deployed system, after F29-03")
    instance.add_argument("--out", required=True)
    instance.add_argument("--customer", required=True, help="F29-03's customer uuid")
    instance.add_argument("--since", required=True, help="UTC before F29-03's sale")
    instance.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)
    if args.command == "suites":
        return run_suites(args)
    return run_instance(args)


if __name__ == "__main__":
    sys.exit(main())
