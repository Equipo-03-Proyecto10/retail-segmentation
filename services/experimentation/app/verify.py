"""Recompute an experiment's assignment from its published values (F25-08, ADR-0068 §2).

Standard library only, and no database: an auditor needs this file, the experiment's
salt, its id, its arm order and the customers it assigned. `python -m app.verify FILE`
reads a JSON document and says whether every customer landed where the service put
them::

    {"experiment_id": 12, "salt": "<64 hex>", "arms": [31, 32],
     "assignments": [{"customer_id": "<uuid>", "arm_id": 31}, ...]}

`arms` is the deal order (the control first, then the treatments by arm id), as
`getExperiment` lists them. `assignments` is every row of `listExperimentAssignments`
for the experiment. Exit status 0 when the recomputation matches, 1 when it does not.

A customer's key is `HMAC-SHA-256(key = salt, message = "mosaiq-assign-v1|" +
experiment_id + "|" + customer_id)` in lowercase hexadecimal. The customers are
ordered by `(key, customer_id)` and dealt round-robin to the arms in order, so arm
sizes differ by at most one. Nothing here reads a clock or a random source.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

PREFIX = "mosaiq-assign-v1|"


def assignment_key(salt: str, experiment_id: int, customer_id: str) -> str:
    """The key of one customer: lowercase hex, from the salt's 32 bytes."""
    message = f"{PREFIX}{experiment_id}|{UUID(customer_id)}".encode("ascii")
    return hmac.new(bytes.fromhex(salt), message, hashlib.sha256).hexdigest()


def deal(
    salt: str, experiment_id: int, arms: Sequence[int], customers: Iterable[str]
) -> dict[str, int]:
    """The arm of every customer: ordered by `(key, customer_id)`, dealt round-robin."""
    if not arms:
        raise ValueError("an experiment has at least one arm")
    ordered = sorted(
        (assignment_key(salt, experiment_id, c), str(UUID(c))) for c in customers
    )
    return {customer: arms[i % len(arms)] for i, (_, customer) in enumerate(ordered)}


def verify(document: dict[str, Any]) -> list[str]:
    """The disagreements between a document's assignments and their recomputation."""
    experiment_id = int(document["experiment_id"])
    arms = [int(a) for a in document["arms"]]
    stored = {
        str(UUID(r["customer_id"])): int(r["arm_id"]) for r in document["assignments"]
    }
    problems = []
    if len(stored) != len(document["assignments"]):
        problems.append("a customer is assigned more than once")
    expected = deal(document["salt"], experiment_id, arms, stored)
    for customer, arm in sorted(stored.items()):
        if expected[customer] != arm:
            problems.append(
                f"{customer}: stored arm {arm}, recomputed {expected[customer]}"
            )
        key = next(
            (
                r.get("assignment_key")
                for r in document["assignments"]
                if str(UUID(r["customer_id"])) == customer
            ),
            None,
        )
        if key is not None and key != assignment_key(
            document["salt"], experiment_id, customer
        ):
            problems.append(f"{customer}: stored key does not match its recomputation")
    sizes = [sum(1 for a in stored.values() if a == arm) for arm in arms]
    if sizes and max(sizes) - min(sizes) > 1:
        problems.append(f"arm sizes {sizes} differ by more than one")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m app.verify assignment.json", file=sys.stderr)
        return 2
    with open(args[0], encoding="utf-8") as handle:
        problems = verify(json.load(handle))
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print("the recomputed assignment matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
