"""The auditor's verifier, with no database (F25-08, ADR-0068 section 2, ADR-0045).

`app/verify.py` reads nothing but its input. These tests pin it with a golden fixture
whose keys and order were made by `openssl` and `sort`, not by this code, and check the
properties ADR-0068's Compliance asks for: adding a customer to a copy of the frame
leaves the others' keys and relative order alone, and the arms differ by at most one.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from app import verify

SERVICE = Path(__file__).resolve().parents[1]
GOLDEN = SERVICE / "tests/fixtures/assignment-golden.json"


def _golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_the_verifier_reproduces_the_golden_assignment() -> None:
    golden = _golden()
    customers = [row["customer_id"] for row in golden["assignments"]]
    dealt = verify.deal(
        golden["salt"], golden["experiment_id"], golden["arms"], customers
    )
    assert dealt == {r["customer_id"]: r["arm_id"] for r in golden["assignments"]}
    for row in golden["assignments"]:
        assert row["assignment_key"] == verify.assignment_key(
            golden["salt"], golden["experiment_id"], row["customer_id"]
        )
    assert verify.verify(golden) == []


def test_a_customer_moved_to_another_arm_is_reported() -> None:
    golden = _golden()
    moved = copy.deepcopy(golden)
    moved["assignments"][0]["arm_id"] = golden["arms"][-1]
    problems = verify.verify(moved)
    assert any(golden["assignments"][0]["customer_id"] in p for p in problems)


def test_a_wrong_key_or_salt_or_experiment_is_reported() -> None:
    golden = _golden()
    key = copy.deepcopy(golden)
    key["assignments"][3]["assignment_key"] = "0" * 64
    assert any("key" in p for p in verify.verify(key))
    for field, value in (("salt", "aa" * 32), ("experiment_id", 8)):
        other = copy.deepcopy(golden)
        other[field] = value
        assert verify.verify(other) != []


def test_a_customer_listed_twice_is_reported() -> None:
    golden = _golden()
    golden["assignments"].append(golden["assignments"][0])
    assert any("more than once" in p for p in verify.verify(golden))


def test_adding_a_customer_leaves_the_others_keys_and_relative_order_alone() -> None:
    golden = _golden()
    customers = [row["customer_id"] for row in golden["assignments"]]
    before = sorted(
        customers,
        key=lambda c: (
            verify.assignment_key(golden["salt"], golden["experiment_id"], c),
            c,
        ),
    )
    extra = str(uuid.UUID(int=12345))
    after = sorted(
        [*customers, extra],
        key=lambda c: (
            verify.assignment_key(golden["salt"], golden["experiment_id"], c),
            c,
        ),
    )
    assert [c for c in after if c != extra] == before
    for customer in customers:
        assert verify.assignment_key(
            golden["salt"], golden["experiment_id"], customer
        ) == next(
            r["assignment_key"]
            for r in golden["assignments"]
            if r["customer_id"] == customer
        )


@pytest.mark.parametrize("arms", [2, 3, 4, 5])
@pytest.mark.parametrize("size", [1, 2, 3, 7, 29, 30, 101, 1000, 10000])
def test_the_arms_differ_by_at_most_one(arms: int, size: int) -> None:
    ids = list(range(1, arms + 1))
    customers = [str(uuid.UUID(int=n)) for n in range(1, size + 1)]
    dealt = verify.deal("11" * 32, 3, ids, customers)
    counts = [list(dealt.values()).count(arm) for arm in ids]
    assert sum(counts) == size
    assert max(counts) - min(counts) <= 1


def test_no_customers_and_no_arms_are_handled() -> None:
    assert verify.deal("11" * 32, 3, [1, 2], []) == {}
    with pytest.raises(ValueError):
        verify.deal("11" * 32, 3, [], ["00000000-0000-4000-8000-000000000001"])


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.verify", *args],
        cwd=SERVICE,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_command_line_says_whether_the_recomputation_matches(
    tmp_path: Path,
) -> None:
    ok = _run(str(GOLDEN))
    assert (ok.returncode, ok.stdout.strip()) == (
        0,
        "the recomputed assignment matches",
    )
    broken = _golden()
    broken["assignments"][1]["arm_id"] = 999
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    bad = _run(str(path))
    assert bad.returncode == 1 and broken["assignments"][1]["customer_id"] in bad.stdout
    assert _run().returncode == 2


def test_the_verifier_imports_nothing_that_needs_a_database() -> None:
    source = (SERVICE / "app/verify.py").read_text(encoding="utf-8")
    imports = {
        line.split()[1].split(".")[0]
        for line in source.splitlines()
        if line.startswith(("import ", "from "))
    }
    assert imports <= {
        "__future__",
        "hashlib",
        "hmac",
        "json",
        "sys",
        "collections",
        "typing",
        "uuid",
    }


def test_the_file_runs_alone_with_the_standard_library_only(tmp_path: Path) -> None:
    """An auditor copies `verify.py` out of the service: no package, no site-packages.

    `-I -S` drops the working directory and every installed distribution, and the
    script lives outside the `app` package, so importing Flask or the service's kit
    would fail here, as it does on an auditor's machine.
    """
    alone = tmp_path / "verify.py"
    alone.write_text((SERVICE / "app/verify.py").read_text(encoding="utf-8"), "utf-8")
    run = subprocess.run(
        [sys.executable, "-I", "-S", str(alone), str(GOLDEN)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (run.returncode, run.stdout.strip()) == (
        0,
        "the recomputed assignment matches",
    ), run.stderr


def test_a_large_listing_is_verified_in_linear_time() -> None:
    """10,000 customers (ADR-0068's frame bound) must not take minutes to check."""
    salt = "5a" * 32
    customers = [str(uuid.UUID(int=n)) for n in range(1, 5001)]
    dealt = verify.deal(salt, 9, [1, 2], customers)
    document = {
        "experiment_id": 9,
        "salt": salt,
        "arms": [1, 2],
        "assignments": [
            {
                "customer_id": c,
                "arm_id": arm,
                "assignment_key": verify.assignment_key(salt, 9, c),
            }
            for c, arm in dealt.items()
        ],
    }
    started = time.monotonic()
    assert verify.verify(document) == []
    assert time.monotonic() - started < 3.0
