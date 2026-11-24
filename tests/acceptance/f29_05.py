"""F29-05 (#890): the evidence of personalization and campaign measurement.

docs/evidence/f29-05-personalization-campaign.md is the runbook. Each
subcommand writes an evidence directory with a summary.md:

    recommendations  "Recomendaciones", read-only: a recommendation offers no
                     product that has no stock at the store, its decision
                     counts them under `out_of_stock` and stores the same, and
                     F24-08's movement-driven run (#831), committed beside it,
                     held on the same version
    ab               "Campaña A/B": an assigned experiment's groups are read,
                     then changes are tried (assign again, edit or delete the
                     experiment, update or delete an assignment as the
                     application's own roles, add the customer to the other
                     arm, rename a group as the owner), and the groups are read
                     again. Every attempt is refused and the groups are the
                     same. Changes that already happened since the assignment
                     (a segment moved, a consent decided) are counted, so the
                     check cannot hold for want of a change
    run              "Exposición y conversión", read-only: for one experiment,
                     the assignments, the exposures and the conversions are
                     three sets of facts, each with its own table, its own
                     time and its own count, and the service reads each back
                     the way the database holds it
    suites           the same properties on a throwaway database and on the
                     phone's unit tests: F25-14's pipeline validation (segment
                     and consent changes after assignment, #845) and the
                     Android tests of #860 and #861

Nothing here writes business data. `ab` tries changes that must be refused:
the three calls that reach the service are refused before any write; the
statements that reach PostgreSQL run inside a transaction that is always rolled
back, so even a statement that was wrongly accepted leaves nothing behind (and
is reported as a failed check).

Environment, as for the other collectors (tests/acceptance/evidence.py):

    MOSAIQ_BASE_URL       the gateway's origin
    MOSAIQ_HOST, MOSAIQ_INSECURE_TLS
                          as for tests/negative, for a loopback hop
    MOSAIQ_DB_EXEC        the psql prefix (evidence.Database)
    MOSAIQ_ACCEPTANCE_EMAIL / _PASSWORD
                          a reader of recommendations and experiments
                          (ANALYST; prompted)
    MOSAIQ_MARKETING_EMAIL / _PASSWORD
                          `ab` only: a MARKETING account, whose role may assign
                          an experiment and so is refused for being too late
                          (prompted)
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from tests.acceptance.evidence import (
    Database,
    Recorder,
    credential,
    sign_in,
    sign_out,
    versions,
)
from tests.acceptance.f24_08 import (
    OFFERED_KINDS,
    Session,
    describe,
    exclusion_count,
    shape,
)
from tests.negative.stack import ROOT, Edge

EXPERIMENTATION = "/api/experimentation/v1"
PAGE_SIZE = 100
NIL = "00000000-0000-0000-0000-"
WHY_RECOMMENDATIONS = "Recomendaciones"
WHY_AB = "Campaña A/B"
WHY_RUN = "Exposición y conversión"


# ---------- what the answers say (pure, tested on fixtures) ----------


def held_counts(summary: str) -> tuple[int, int]:
    """(checks, held) from a collector's summary.md: `17 checks, 17 held, 0 did not.`"""
    match = re.search(r"(\d+) checks, (\d+) held", summary)
    if match is None:
        raise ValueError("no `N checks, M held` line in the summary")
    return int(match.group(1)), int(match.group(2))


def held_case(summary: str, fragment: str) -> bool:
    """Whether a check whose case contains `fragment` is in the summary and held."""
    rows = [
        line
        for line in summary.splitlines()
        if line.startswith("| ") and fragment in line
    ]
    return bool(rows) and all(line.rstrip().endswith("✓ |") for line in rows)


def evidence_link(run: Path, part: str) -> str:
    """A link from `docs/evidence/f29-05/<run>/<command>/` to a part of F24-08's run,
    which sits beside it under `docs/evidence/f24-08/`."""
    return f"../../../f24-08/{run.name}/{part}/summary.md"


def customer_uuid(number: int) -> str:
    return f"{NIL}{number:012d}"


def seeded_assignment(experiment: int) -> dict[str, str]:
    """The groups sql/02_seed_30_per_table.sql assigns to experiment `n`.

    Assignee k (0 to 3) is customer 1 + ((n - 1) * 4 + k) % 30; the first two
    go to the control group, the last two to the treatment group. This is the
    seed's own rule, written down here as the independent record of what the
    groups were when they were assigned."""
    return {
        customer_uuid(1 + ((experiment - 1) * 4 + k) % 30): (
            "CONTROL" if k < 2 else "TREATMENT"
        )
        for k in range(4)
    }


def arms_by_customer(assignments: list[dict[str, Any]]) -> dict[str, str]:
    """customer -> arm kind, from `GET /v1/experiment-assignments` items."""
    return {a["customer_id"]: a["arm_kind"] for a in assignments}


def arm_ids_by_customer(assignments: list[dict[str, Any]]) -> dict[str, int]:
    return {a["customer_id"]: int(a["arm_id"]) for a in assignments}


def differences(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """What differs between two customer -> arm maps; empty when they are equal."""
    lines = [
        f"{k}: {before[k]} -> {after.get(k, '(gone)')}"
        for k in before
        if k in after and before[k] != after[k]
    ]
    lines += [f"{k}: {before[k]} -> (gone)" for k in before if k not in after]
    lines += [f"{k}: (new) -> {after[k]}" for k in after if k not in before]
    return lines


def refused_with(error: str, fragments: tuple[str, ...]) -> bool:
    """Whether the database's refusal says one of the expected things: a missing
    privilege, or the trigger that refuses the statement for every role."""
    return any(f.lower() in error.lower() for f in fragments)


def zero_stock_offered(body: dict[str, Any], zero: set[int]) -> list[int]:
    """Products with no stock here that the answer still offers at this store."""
    return sorted(
        {
            i["product_id"]
            for i in body.get("items") or []
            if i.get("kind") in OFFERED_KINDS and i["product_id"] in zero
        }
    )


def thin_availability(body: dict[str, Any]) -> list[int]:
    """Offered items that state no unit on hand."""
    return sorted(
        i["product_id"]
        for i in body.get("items") or []
        if i.get("kind") in OFFERED_KINDS
        and int(i["availability"]["quantity_on_hand"]) < 1
    )


def run_facts(
    assignments: list[tuple[int, str]],
    exposures: list[tuple[int, str, bool]],
    conversions: list[tuple[int, int, bool]],
) -> dict[str, Any]:
    """What the three sets of facts say about one another.

    assignments: (assignment id, arm kind)
    exposures:   (assignment id, arm kind, before its assignment)
    conversions: (assignment id, sale id, outside its window)

    The two time comparisons are made by PostgreSQL, on the instants themselves."""
    arm = {a: kind for a, kind in assignments}
    exposed = {a for a, _, _ in exposures}
    converted = {a for a, _, _ in conversions}
    return {
        "assigned": len(assignments),
        "exposed assignments": len(exposed),
        "exposure facts": len(exposures),
        "converted assignments": len(converted),
        "conversion facts": len(conversions),
        "control exposures": sum(1 for _, kind, _ in exposures if kind == "CONTROL"),
        "exposure before its assignment": [a for a, _, early in exposures if early],
        "sale outside its window": [a for a, _, outside in conversions if outside],
        "assigned, never exposed": sorted(set(arm) - exposed),
        "exposed, no sale": sorted(exposed - converted),
        "sale, never exposed": sorted(converted - exposed),
    }


# ---------- the service's side ----------


def read_all(
    rec: Recorder, edge: Edge, token: str, path: str, key: str, note: str
) -> tuple[list[dict[str, Any]], list[int]]:
    """Every item of a paged list, and the exchanges that read it."""
    items: list[dict[str, Any]] = []
    numbers: list[int] = []
    page = 1
    while True:
        separator = "&" if "?" in path else "?"
        reply, number = rec.call(
            edge,
            "GET",
            f"{path}{separator}size={PAGE_SIZE}&page={page}",
            note=f"{note}, page {page}",
            token=token,
        )
        numbers.append(number)
        if reply.status != 200:
            raise SystemExit(
                f"{path} answered {reply.outcome()} (exchange {number}); "
                "nothing collected."
            )
        body = reply.json()
        items += body[key]
        total = int(body["page_info"]["total"])
        if len(items) >= total or not body[key]:
            return items, numbers
        page += 1


# ---------- recommendations ----------

ZERO_STOCK = """
SELECT p.product_id
  FROM api_products.product_ref_v1 AS p
  LEFT JOIN api_stock.stock_level_v1 AS s
    ON s.product_id = p.product_id AND s.store_id = %s
 WHERE p.is_active AND coalesce(s.quantity_on_hand, 0) = 0
 ORDER BY p.product_id
"""


def run_recommendations(args: argparse.Namespace) -> int:
    rec = Recorder(Path(args.out), "F29-05 Recomendaciones", "f29-05")
    rec.facts["customer"] = args.customer
    rec.facts["store"] = args.store
    s = Session(rec, planner=False)
    try:
        versions(rec, s.edge, ["auth", "stock", "products", "recommendations"])
        zero = {int(r[0]) for r in s.db.rows(ZERO_STOCK, (args.store,))}
        anchor = rec.add_rows(
            "active products with no stock at the store",
            s.db.table(ZERO_STOCK, (args.store,)),
        )
        rec.facts["products with no stock here"] = len(zero)
        rec.check(
            WHY_RECOMMENDATIONS,
            "the store has active products with no stock (the check is not empty)",
            "at least one",
            len(zero),
            bool(zero),
            [anchor],
        )
        body, number = s.recommend(args.customer, args.store, args.channel, "the read")
        if body is None:
            rec.check(
                WHY_RECOMMENDATIONS,
                "the read answers",
                200,
                "not 200",
                False,
                [rec.link(number)],
            )
            return 0 if rec.write() else 1
        rec.facts["read"] = describe(body, next(iter(sorted(zero)), 0))
        offered = zero_stock_offered(body, zero)
        rec.check(
            WHY_RECOMMENDATIONS,
            "no product without stock here is offered (primary or alternative)",
            "none",
            offered or "none",
            links=[rec.link(number), anchor],
        )
        thin = thin_availability(body)
        rec.check(
            WHY_RECOMMENDATIONS,
            "every offered item states at least one unit on hand",
            "none below 1",
            thin or "none below 1",
            links=[rec.link(number)],
        )
        out = exclusion_count(body, "out_of_stock")
        rec.check(
            WHY_RECOMMENDATIONS,
            "the decision counts the exclusions as `out_of_stock`",
            "at least 1",
            out,
            out >= 1,
            [rec.link(number)],
        )
        recorded, recorded_anchor = s.decision(
            body["decision_id"], "the stored decision"
        )
        rec.check(
            WHY_RECOMMENDATIONS,
            "the stored decision records the same items and exclusions",
            shape(body),
            None if recorded is None else shape(recorded),
            recorded is not None and shape(body) == shape(recorded),
            [rec.link(number), recorded_anchor],
        )
        if args.f24_08:
            run = Path(args.f24_08)
            for name, fragment in (
                ("cycle", "is not offered at store"),
                ("exclusions", "the decision counts"),
            ):
                path = run / name / "summary.md"
                link = evidence_link(run, name)
                if not path.is_file():
                    rec.check(
                        WHY_RECOMMENDATIONS,
                        f"F24-08 {name} evidence",
                        "a file",
                        "none",
                        False,
                    )
                    continue
                text = path.read_text()
                checks, held = held_counts(text)
                rec.check(
                    WHY_RECOMMENDATIONS,
                    f"F24-08 `{name}` on the instance (#831): every check held",
                    f"{checks} of {checks}",
                    f"{held} of {checks}",
                    checks > 0 and held == checks,
                    [link],
                )
                if name == "cycle":
                    rec.check(
                        WHY_RECOMMENDATIONS,
                        "F24-08: a product counted to zero was no longer offered "
                        "after the bound",
                        "held",
                        "held" if held_case(text, fragment) else "not held",
                        held_case(text, fragment),
                        [link],
                    )
    finally:
        s.close()
    return 0 if rec.write() else 1


# ---------- ab ----------

GROUPS = """
SELECT a.assignment_id, a.customer_id, g.group_id, g.kind
  FROM public.experiment_assignment AS a
  JOIN public.experiment_group AS g
    ON g.group_id = a.group_id AND g.experiment_id = a.experiment_id
 WHERE a.experiment_id = %s
 ORDER BY a.assignment_id
"""

CHANGED_SINCE = """
SELECT a.customer_id,
       (SELECT h.label_code FROM public.customer_segment_history AS h
         WHERE h.customer_id = a.customer_id AND h.valid_from <= a.assigned_at
         ORDER BY h.valid_from DESC, h.history_id DESC LIMIT 1) AS label_then,
       (SELECT count(*) FROM public.customer_segment_history AS h
         WHERE h.customer_id = a.customer_id AND h.valid_from > a.assigned_at
           AND h.label_code IS DISTINCT FROM (
               SELECT h2.label_code FROM public.customer_segment_history AS h2
                WHERE h2.customer_id = a.customer_id AND h2.valid_from <= a.assigned_at
                ORDER BY h2.valid_from DESC, h2.history_id DESC LIMIT 1)
       ) AS segment_changes_since,
       (SELECT count(*) FROM preferences.consent_decision AS d
         WHERE d.customer_id = a.customer_id AND d.recorded_at > a.assigned_at
       ) AS consent_decisions_since
  FROM public.experiment_assignment AS a
 WHERE a.experiment_id = %s
 ORDER BY a.assignment_id
"""

# Each attempt: (title, role, statement with $n placeholders, the parameters it
# takes from the experiment, what the refusal says). The role is the one whose
# privileges are tried; `postgres` is the owner, whose refusal comes from a
# trigger. Identifiers are fixed here and are never an argument.
ATTEMPTS = (
    (
        "the application role moves a customer to the other arm",
        "retail_app",
        "UPDATE public.experiment_assignment SET group_id = $1 "
        "WHERE assignment_id = $2",
        ("other_group", "assignment"),
        ("permission denied", "never rewritten"),
    ),
    (
        "the application role deletes an assignment",
        "retail_app",
        "DELETE FROM public.experiment_assignment WHERE assignment_id = $1",
        ("assignment",),
        ("permission denied", "never rewritten"),
    ),
    (
        "experimentation's own role moves a customer to the other arm",
        "svc_experiments",
        "UPDATE public.experiment_assignment SET group_id = $1 "
        "WHERE assignment_id = $2",
        ("other_group", "assignment"),
        ("permission denied", "never rewritten"),
    ),
    (
        "experimentation's own role deletes an assignment",
        "svc_experiments",
        "DELETE FROM public.experiment_assignment WHERE assignment_id = $1",
        ("assignment",),
        ("permission denied", "never rewritten"),
    ),
    (
        "the owner moves a customer to the other arm",
        "postgres",
        "UPDATE public.experiment_assignment SET group_id = $1 "
        "WHERE assignment_id = $2",
        ("other_group", "assignment"),
        ("never rewritten",),
    ),
    (
        "the owner deletes an assignment",
        "postgres",
        "DELETE FROM public.experiment_assignment WHERE assignment_id = $1",
        ("assignment",),
        ("never rewritten",),
    ),
    (
        "experimentation's own role assigns the same customer to the other arm too",
        "svc_experiments",
        "INSERT INTO public.experiment_assignment "
        "(experiment_id, group_id, customer_id) "
        "VALUES ($1, $2, $3::uuid)",
        ("experiment", "other_group", "customer"),
        ("duplicate key value violates unique constraint",),
    ),
    (
        "the owner renames an arm after its customers were assigned",
        "postgres",
        "UPDATE public.experiment_group SET name = name || ' (changed)' "
        "WHERE group_id = $1",
        ("group",),
        ("fixed after assignment",),
    ),
)


def attempt(
    db: Database, role: str, statement: str, values: list[Any]
) -> tuple[bool, str]:
    """Run one statement as `role` inside a transaction that is always rolled
    back. Returns (was_refused, what happened)."""
    names = " ".join(f":p{i}" for i in range(1, len(values) + 1))
    script = (
        f"BEGIN;\nSET LOCAL ROLE {role};\n{statement}\n\\bind {names}\n\\g\nROLLBACK;\n"
    )
    arguments = ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", "retail"]
    for i, value in enumerate(values, 1):
        arguments += ["-v", f"p{i}={value}"]
    arguments += ["-f", "-"]
    try:
        db._run(arguments, script)  # noqa: SLF001 - the one write-shaped use
    except RuntimeError as error:
        return True, str(error).removeprefix("psql failed: ").splitlines()[0]
    return False, "accepted (and rolled back)"


def _groups(db: Database, experiment: int) -> list[tuple[str, ...]]:
    return db.rows(GROUPS, (experiment,))


def run_ab(args: argparse.Namespace) -> int:
    rec = Recorder(Path(args.out), "F29-05 Campaña A/B", "f29-05")
    experiment = args.experiment
    rec.facts["experiment"] = experiment
    s = Session(rec, planner=False)
    marketing = sign_in(
        rec,
        s.edge,
        credential("MOSAIQ_MARKETING_EMAIL", "Marketing email: ", False),
        credential("MOSAIQ_MARKETING_PASSWORD", "Marketing password: ", True),
        "marketing",
    )
    rec.facts["marketing"] = os.environ.get("MOSAIQ_MARKETING_EMAIL", "(prompted)")
    path = f"{EXPERIMENTATION}/experiments/{experiment}"
    listing = f"{EXPERIMENTATION}/experiment-assignments?experiment_id={experiment}"
    try:
        versions(rec, s.edge, ["auth", "experimentation"])
        reply, number = rec.call(
            s.edge, "GET", path, note="the experiment", token=s.reader
        )
        info = reply.json() if reply.status == 200 else {}
        rec.check(
            WHY_AB,
            "the experiment is ASSIGNED",
            "ASSIGNED",
            info.get("state", reply.outcome()),
            links=[rec.link(number)],
        )
        rec.facts["data origin"] = info.get("data_origin")
        rec.facts["assignment rule"] = info.get("assignment_rule")
        assigned_total = (info.get("assignment") or {}).get("assigned_total", 0)

        before, read_before = read_all(
            rec, s.edge, s.reader, listing, "assignments", "assignments before"
        )
        db_before = _groups(s.db, experiment)
        anchor_before = rec.add_rows(
            "the groups before", s.db.table(GROUPS, (experiment,))
        )
        served = arms_by_customer(before)
        rec.check(
            WHY_AB,
            "the service lists as many assignments as the experiment states",
            assigned_total,
            len(before),
            len(before) > 0 and len(before) == assigned_total,
            [rec.link(n) for n in read_before],
        )
        held_db = {r[1]: r[3] for r in db_before}
        rec.check(
            WHY_AB,
            "the service and the database give every customer the same arm",
            "no difference",
            differences(held_db, served) or "no difference",
            bool(held_db) and not differences(held_db, served),
            [rec.link(n) for n in read_before] + [anchor_before],
        )
        arms = Counter(served.values())
        rec.facts["arms before"] = dict(sorted(arms.items()))
        if args.seed_rule:
            expected = seeded_assignment(experiment)
            rec.check(
                WHY_AB,
                "the groups are the ones the seed assigned (its own rule, "
                "written down in f29_05)",
                "no difference",
                differences(expected, served) or "no difference",
                not differences(expected, served),
                [rec.link(n) for n in read_before],
            )

        moved = s.db.rows(CHANGED_SINCE, (experiment,))
        anchor_moved = rec.add_rows(
            "what changed for each assigned customer since the assignment",
            s.db.table(CHANGED_SINCE, (experiment,)),
        )
        segments = sum(int(r[2]) for r in moved)
        consents = sum(int(r[3]) for r in moved)
        rec.facts["segment changes since assignment"] = segments
        rec.facts["consent decisions since assignment"] = consents
        rec.check(
            WHY_AB,
            "since the assignment a customer's segment moved or a consent was decided "
            "(a change really happened)",
            "at least one",
            f"{segments} segment changes, {consents} consent decisions",
            segments + consents > 0,
            [anchor_moved],
        )

        # ---- the tries
        assignment, customer, group, kind = db_before[0][:4]
        other_group_row = s.db.rows(
            "SELECT group_id FROM public.experiment_group "
            "WHERE experiment_id = %s AND group_id <> %s ORDER BY group_id LIMIT 1",
            (experiment, group),
        )
        if not other_group_row:
            raise SystemExit("The experiment has one arm only; there is no other arm.")
        other = other_group_row[0][0]
        rec.facts["the customer the attempts target"] = customer
        rec.facts["its arm"] = f"{kind} (group {group}); the other group: {other}"
        reply, number = rec.call(
            s.edge,
            "POST",
            f"{EXPERIMENTATION}/experiment-assignments",
            note="assign the experiment again",
            token=marketing,
            payload={"experiment_id": experiment},
        )
        code = reply.json().get("code") if reply.body else None
        rec.check(
            WHY_AB,
            "assigning the experiment again is refused",
            "409 already_assigned",
            f"{reply.status} {code}",
            reply.status == 409 and code == "already_assigned",
            [rec.link(number)],
        )
        for method in ("PATCH", "DELETE"):
            reply, number = rec.call(
                s.edge,
                method,
                path,
                note=f"{method.lower()} the experiment",
                token=marketing,
                payload={"name": "renamed"} if method == "PATCH" else None,
            )
            rec.check(
                WHY_AB,
                f"{method} on the experiment is refused: there is no such operation",
                "405",
                reply.status,
                reply.status == 405,
                [rec.link(number)],
            )
        for method in ("PATCH", "DELETE"):
            reply, number = rec.call(
                s.edge,
                method,
                f"{EXPERIMENTATION}/experiment-assignments/{assignment}",
                note=f"{method.lower()} an assignment",
                token=marketing,
                payload={"arm_id": 0} if method == "PATCH" else None,
            )
            rec.check(
                WHY_AB,
                f"{method} on an assignment is refused: there is no such operation",
                "404 or 405",
                reply.status,
                reply.status in {404, 405},
                [rec.link(number)],
            )
        values = {
            "experiment": experiment,
            "assignment": assignment,
            "customer": customer,
            "group": group,
            "other_group": other,
        }
        tried = []
        for title, role, statement, names, fragment in ATTEMPTS:
            given = [values[n] for n in names]
            refused, what = attempt(s.db, role, statement, given)
            tried.append(
                f"-- as {role}: {title}\n{statement}\n-- with {given}\n-- {what}"
            )
            rec.check(
                WHY_AB,
                f"{title}: refused",
                "refused: " + " or ".join(fragment),
                what,
                refused and refused_with(what, fragment),
                [f"rows.md#rows-{len(rec.rows) + 1}"],
            )
        rec.add_rows(
            "the statements tried, each inside a transaction that is rolled back",
            "\n\n".join(tried),
        )

        # ---- the same read, after
        after, read_after = read_all(
            rec, s.edge, s.reader, listing, "assignments", "assignments after"
        )
        db_after = _groups(s.db, experiment)
        anchor_after = rec.add_rows(
            "the groups after", s.db.table(GROUPS, (experiment,))
        )
        rec.check(
            WHY_AB,
            "after the attempts, the service gives every customer the arm it "
            "gave before",
            "no difference",
            differences(arms_by_customer(before), arms_by_customer(after))
            or "no difference",
            bool(after)
            and not differences(arms_by_customer(before), arms_by_customer(after)),
            [rec.link(n) for n in read_after],
        )
        rec.check(
            WHY_AB,
            "after the attempts, the database holds the same assignments, "
            "groups and arms",
            "no difference",
            "no difference" if db_before == db_after else "different",
            bool(db_after) and db_before == db_after,
            [anchor_before, anchor_after],
        )
        rec.check(
            WHY_AB,
            "the arms' ids are unchanged too",
            "no difference",
            differences(arm_ids_by_customer(before), arm_ids_by_customer(after))
            or "no difference",
            not differences(arm_ids_by_customer(before), arm_ids_by_customer(after)),
            [rec.link(n) for n in read_after],
        )
    finally:
        sign_out(rec, s.edge, marketing, "marketing")
        s.close()
    return 0 if rec.write() else 1


# ---------- run ----------

RUN_ASSIGNMENTS = """
SELECT a.assignment_id, g.kind
  FROM public.experiment_assignment AS a
  JOIN public.experiment_group AS g
    ON g.group_id = a.group_id AND g.experiment_id = a.experiment_id
 WHERE a.experiment_id = %s
 ORDER BY a.assignment_id
"""

RUN_EXPOSURES = """
SELECT x.assignment_id, g.kind, (x.exposed_at < a.assigned_at) AS before_assignment,
       (x.source_event_id IS NOT NULL) AS from_engagement
  FROM public.experiment_exposure AS x
  JOIN public.experiment_assignment AS a USING (assignment_id)
  JOIN public.experiment_group AS g
    ON g.group_id = a.group_id AND g.experiment_id = a.experiment_id
 WHERE a.experiment_id = %s
   AND NOT EXISTS (
       SELECT FROM experiments.exposure_notice AS v
        WHERE v.source_event_id = x.source_event_id AND v.notice = 'VOIDED')
 ORDER BY x.exposure_id
"""

RUN_CONVERSIONS = """
SELECT c.assignment_id, c.transaction_id,
       (t.occurred_at < a.assigned_at
        OR t.occurred_at >= a.assigned_at
                           + make_interval(days => e.conversion_window_days))
       AS outside_window
  FROM public.experiment_conversion AS c
  JOIN public.experiment_assignment AS a USING (assignment_id)
  JOIN public.experiment AS e ON e.experiment_id = a.experiment_id
  JOIN public.transaction AS t ON t.transaction_id = c.transaction_id
 WHERE a.experiment_id = %s
 ORDER BY c.conversion_id
"""


def run_run(args: argparse.Namespace) -> int:
    rec = Recorder(Path(args.out), "F29-05 Exposición y conversión", "f29-05")
    experiment = args.experiment
    rec.facts["experiment"] = experiment
    s = Session(rec, planner=False)
    try:
        versions(rec, s.edge, ["auth", "experimentation", "engagement"])
        base = f"{EXPERIMENTATION}/experiments/{experiment}"
        assignments, read_a = read_all(
            rec,
            s.edge,
            s.reader,
            f"{EXPERIMENTATION}/experiment-assignments?experiment_id={experiment}",
            "assignments",
            "the assignments",
        )
        conversions, read_c = read_all(
            rec,
            s.edge,
            s.reader,
            f"{base}/conversions",
            "conversions",
            "the conversions",
        )
        reply, number = rec.call(
            s.edge,
            "GET",
            f"{EXPERIMENTATION}/experiment-results/{experiment}",
            note="the result",
            token=s.reader,
        )
        result = reply.json() if reply.status == 200 else {}
        db_a = [tuple(r) for r in s.db.rows(RUN_ASSIGNMENTS, (experiment,))]
        db_x = [tuple(r) for r in s.db.rows(RUN_EXPOSURES, (experiment,))]
        db_c = [tuple(r) for r in s.db.rows(RUN_CONVERSIONS, (experiment,))]
        a_assign = rec.add_rows(
            "assignments", s.db.table(RUN_ASSIGNMENTS, (experiment,))
        )
        a_exp = rec.add_rows("exposures", s.db.table(RUN_EXPOSURES, (experiment,)))
        a_conv = rec.add_rows("conversions", s.db.table(RUN_CONVERSIONS, (experiment,)))
        facts = run_facts(
            [(int(a), k) for a, k in db_a],
            [(int(a), k, early == "t") for a, k, early, _ in db_x],
            [(int(a), int(t), outside == "t") for a, t, outside in db_c],
        )
        for key in (
            "assigned",
            "exposed assignments",
            "exposure facts",
            "converted assignments",
            "conversion facts",
        ):
            rec.facts[key] = facts[key]
        rec.facts["exposures from engagement events"] = sum(
            1 for r in db_x if r[3] == "t"
        )
        why = WHY_RUN
        rec.check(
            why,
            "the run has assignments, exposures and sales, each in its own records",
            "all three",
            f"{facts['assigned']} assignments, {facts['exposure facts']} exposures, "
            f"{facts['conversion facts']} sales",
            facts["assigned"] > 0
            and facts["exposure facts"] > 0
            and facts["conversion facts"] > 0,
            [a_assign, a_exp, a_conv],
        )
        rec.check(
            why,
            "the service lists the assignments the database holds",
            facts["assigned"],
            len(assignments),
            len(assignments) == facts["assigned"],
            [rec.link(n) for n in read_a] + [a_assign],
        )
        listed = sum(int(a["exposure_count"]) for a in assignments)
        rec.check(
            why,
            "the exposures the assignments report are the ones the database holds",
            facts["exposure facts"],
            listed,
            listed == facts["exposure facts"],
            [rec.link(n) for n in read_a] + [a_exp],
        )
        rec.check(
            why,
            "the service lists the sales the database holds as conversions",
            facts["conversion facts"],
            len(conversions),
            len(conversions) == facts["conversion facts"],
            [rec.link(n) for n in read_c] + [a_conv],
        )
        arms = {a["kind"]: a for a in result.get("arms", [])}
        by_kind: Counter[str] = Counter(k for _, k in db_a)
        exposed_by_kind: Counter[str] = Counter()
        for kind in {r[1] for r in db_x}:
            exposed_by_kind[kind] = len({r[0] for r in db_x if r[1] == kind})
        for kind in ("CONTROL", "TREATMENT"):
            arm = arms.get(kind)
            rec.check(
                why,
                f"the result's {kind.lower()} arm counts assigned and exposed apart",
                f"assigned {by_kind[kind]}, exposed {exposed_by_kind[kind]}",
                (
                    "no such arm"
                    if arm is None
                    else f"assigned {arm['assigned']}, exposed {arm['exposed']}"
                ),
                arm is not None
                and arm["assigned"] == by_kind[kind]
                and arm["exposed"] == exposed_by_kind[kind],
                [rec.link(number), a_assign, a_exp],
            )
        rec.check(
            why,
            "no one in the control arm was exposed (ADR-0027)",
            0,
            facts["control exposures"],
            links=[a_exp],
        )
        rec.check(
            why,
            "every exposure came after its assignment",
            "none before",
            facts["exposure before its assignment"] or "none before",
            links=[a_exp],
        )
        rec.check(
            why,
            "every sale counted as a conversion lies in its window, after the "
            "assignment",
            "none outside",
            facts["sale outside its window"] or "none outside",
            links=[a_conv],
        )
        rec.check(
            why,
            "an assignment is not an exposure: someone was assigned and never exposed",
            "at least one",
            len(facts["assigned, never exposed"]),
            bool(facts["assigned, never exposed"]),
            [a_assign, a_exp],
        )
        rec.check(
            why,
            "an exposure is not a sale: someone was exposed and did not buy, "
            "or bought without an exposure",
            "at least one of the two",
            f"{len(facts['exposed, no sale'])} exposed without a sale, "
            f"{len(facts['sale, never exposed'])} sales without an exposure",
            bool(facts["exposed, no sale"]) or bool(facts["sale, never exposed"]),
            [a_exp, a_conv],
        )
    finally:
        s.close()
    return 0 if rec.write() else 1


# ---------- suites ----------

PYTEST = (
    "experimentation pipeline validation (F25-14, #845)",
    [
        "tests/test_validation_pipeline.py",
        "tests/test_validation_statistics.py",
    ],
)
GRADLE_TESTS = ("*ExplainedRecommendationsTest", "*PromotionsViewModelTest")


def junit_counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in total:
            total[key] += int(suite.get(key, 0))
    return total


def clean(counts: dict[str, int]) -> bool:
    return (
        counts.get("tests", 0) > 0
        and counts.get("failures", 0) == 0
        and counts.get("errors", 0) == 0
        and counts.get("skipped", 0) == 0
    )


def run_suites(args: argparse.Namespace) -> int:
    out = Path(args.out)
    rec = Recorder(out, "F29-05 the suites", "f29-05")
    rec.facts["commit"] = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    service = ROOT / "services/experimentation"
    junit = out / "experimentation-junit.xml"
    command = [
        "uv",
        "run",
        "--frozen",
        "pytest",
        "-q",
        "-rs",
        "-p",
        "no:cacheprovider",
        f"--junitxml={junit}",
        *PYTEST[1],
    ]
    rec.facts["pytest"] = " ".join(command)
    environment = os.environ | {
        "MOSAIQ_REQUIRE_POSTGRES": "1",
        "MOSAIQ_REQUIRE_REDIS": "1",
    }
    result = subprocess.run(
        command, cwd=service, env=environment, capture_output=True, text=True
    )
    (out / "experimentation-pytest.log").write_text(result.stdout + result.stderr)
    counts = junit_counts(junit) if junit.exists() else {}
    rec.check(
        WHY_AB,
        PYTEST[0] + ": segment and consent changes after assignment move no arm; "
        "overlapping experiments contaminate no one; a sale counts once",
        "exit 0, at least one case, no failure, error or skip",
        f"exit {result.returncode}, {counts}",
        result.returncode == 0 and clean(counts),
        ["experimentation-pytest.log", "experimentation-junit.xml"],
    )
    if args.android:
        gradle = [
            "./gradlew",
            *os.environ.get("GW_OPTS", "").split(),
            "--console=plain",
            "testDebugUnitTest",
        ]
        for name in GRADLE_TESTS:
            gradle += ["--tests", name]
        rec.facts["gradle"] = " ".join(gradle)
        run = subprocess.run(
            gradle,
            cwd=ROOT / "clients/android",
            env=os.environ,
            capture_output=True,
            text=True,
        )
        (out / "android-gradle.log").write_text(run.stdout + run.stderr)
        reports = sorted(
            (ROOT / "clients/android/app/build/test-results/testDebugUnitTest").glob(
                "TEST-*.xml"
            )
        )
        wanted = [
            r for r in reports if any(n.strip("*") in r.name for n in GRADLE_TESTS)
        ]
        totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        for report in wanted:
            for key, value in junit_counts(report).items():
                totals[key] += value
            (out / report.name).write_text(report.read_text())
        rec.check(
            WHY_RUN,
            "Android (#860, #861): one impression per item shown, a tap is a separate "
            "click, loading a list records no exposure and one shown offer queues one",
            "exit 0, both test classes ran, no failure, error or skip",
            f"exit {run.returncode}, {len(wanted)} classes, {totals}",
            run.returncode == 0 and len(wanted) == len(GRADLE_TESTS) and clean(totals),
            ["android-gradle.log"],
        )
    return 0 if rec.write() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.acceptance.f29_05")
    commands = parser.add_subparsers(dest="command", required=True)
    recs = commands.add_parser("recommendations")
    recs.add_argument("--out", required=True)
    recs.add_argument("--customer", required=True)
    recs.add_argument("--store", type=int, required=True)
    recs.add_argument("--channel", type=int)
    recs.add_argument(
        "--f24-08", help="F24-08's committed instance run (its cycle/ and exclusions/)"
    )
    ab = commands.add_parser("ab")
    ab.add_argument("--out", required=True)
    ab.add_argument("--experiment", type=int, required=True)
    ab.add_argument(
        "--seed-rule",
        action="store_true",
        help="also compare the groups with the ones the seed assigns",
    )
    run = commands.add_parser("run")
    run.add_argument("--out", required=True)
    run.add_argument("--experiment", type=int, required=True)
    suites = commands.add_parser("suites")
    suites.add_argument("--out", required=True)
    suites.add_argument("--android", action="store_true")
    args = parser.parse_args(argv)
    code = {
        "recommendations": run_recommendations,
        "ab": run_ab,
        "run": run_run,
        "suites": run_suites,
    }[args.command](args)
    print(Path(args.out) / "summary.md")
    return code


if __name__ == "__main__":
    sys.exit(main())
