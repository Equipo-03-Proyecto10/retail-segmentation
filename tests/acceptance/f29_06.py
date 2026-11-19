"""F29-06 (#891): the evidence of privacy, revocation and disconnection.

docs/evidence/f29-06-privacy-revocation-disconnection.md is the runbook.
Each subcommand writes an evidence directory with a summary.md:

    privacy        F18-04's privacy, role and store-scope cases
                   (tests/negative/test_privacy.py, test_roles.py,
                   test_store_scope.py), "Privacidad"
    revocation     F18-04's sign-out cases on every service
                   (test_revocation.py, and test_auth.py's sign-out and
                   refresh-reuse cases), "Sesión revocada"
    disconnection  read-only: the sales one floor account recorded in a
                   window, with no two of the same request, and each
                   queued Idempotency-Key recorded exactly once, "Desconexión"
    replay         the server half of "Desconexión" on a local stack only:
                   one sale sent once, again, and three times at once with
                   the same Idempotency-Key, recorded once

`privacy` and `revocation` run pytest with the environment tests/negative's
README describes (MOSAIQ_BASE_URL and the rest, and on the instance the
administrator's credential read at a prompt), and keep its evidence section,
JUnit file and log in the directory. `disconnection` needs MOSAIQ_DB_EXEC
only; `replay` needs MOSAIQ_BASE_URL, MOSAIQ_DB_EXEC and a STORE_MANAGER's
credential (MOSAIQ_ACCEPTANCE_EMAIL / _PASSWORD, else prompted).
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import os
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from tests.acceptance.evidence import (
    Database,
    Recorder,
    credential,
    edge_from_environment,
    sign_in,
    sign_out,
    versions,
)
from tests.negative.stack import ROOT

SUITES = {
    "privacy": (
        "Privacidad",
        [
            "tests/negative/test_privacy.py",
            "tests/negative/test_roles.py",
            "tests/negative/test_store_scope.py",
        ],
        None,
    ),
    "revocation": (
        "Sesión revocada",
        ["tests/negative/test_revocation.py", "tests/negative/test_auth.py"],
        "signed_out or revoked or sign_out or refresh_reuse",
    ),
}


def junit_counts(path: Path) -> dict[str, int]:
    """Cases, failures, errors and skips of a pytest JUnit file."""
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in total:
            total[key] += int(suite.get(key, 0))
    return total


def evidence_counts(markdown: str) -> tuple[int, int]:
    """(rows, held) of an F18-04 evidence section's table."""
    rows = [
        line
        for line in markdown.splitlines()
        if line.startswith("| ") and not line.startswith("| Area")
    ]
    return len(rows), sum(line.rstrip().endswith("✓ |") for line in rows)


def run_suite(args: argparse.Namespace) -> int:
    criterion, files, selection = SUITES[args.command]
    out = Path(args.out)
    rec = Recorder(out, f"F29-06 {criterion}", "f29-06")
    rec.facts["target"] = os.environ.get("MOSAIQ_BASE_URL", "(not set)")
    versions(rec, edge_from_environment())
    evidence_file = out / "negative-evidence.md"
    junit = out / "junit.xml"
    command = [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider"]
    command += ["--confcutdir=tests/negative", f"--junitxml={junit}", *files]
    if selection:
        command += ["-k", selection]
    rec.facts["command"] = " ".join(command[1:])
    environment = os.environ | {"MOSAIQ_EVIDENCE_FILE": str(evidence_file)}
    result = subprocess.run(
        command, cwd=ROOT, env=environment, capture_output=True, text=True
    )
    (out / "pytest.log").write_text(result.stdout + result.stderr)
    counts = junit_counts(junit) if junit.exists() else {}
    rec.check(
        criterion,
        "pytest " + ", ".join(Path(f).name for f in files),
        "exit 0, no failure or error",
        f"exit {result.returncode}, {counts}",
        result.returncode == 0,
        ["pytest.log", "junit.xml"],
    )
    if evidence_file.exists():
        rows, held = evidence_counts(evidence_file.read_text())
        rec.check(
            criterion,
            "every F18-04 case of the run held",
            f"{rows} of {rows}",
            f"{held} of {rows}",
            rows > 0 and rows == held,
            ["negative-evidence.md"],
        )
    else:
        rec.check(criterion, "F18-04 wrote its evidence", "a file", "none", False)
    return 0 if rec.write() else 1


# ---------- disconnection, read-only ----------

ACCOUNT_SALES = """
SELECT t.transaction_id, t.source_transaction_id,
       encode(c.request_sha256, 'hex') AS request_sha256,
       t.customer_id, t.store_id, t.occurred_at, t.total, c.captured_at
  FROM sales.sale_capture AS c
  JOIN transaction AS t USING (transaction_id)
  JOIN app_user AS u ON u.user_id = c.recorded_by
 WHERE lower(u.email) = lower(%s)
   AND c.captured_at >= %s::timestamptz AND c.captured_at < %s::timestamptz
 ORDER BY c.captured_at
"""

SAME_REQUEST_TWICE = """
SELECT encode(c.request_sha256, 'hex'), count(*), array_agg(c.transaction_id)
  FROM sales.sale_capture AS c
  JOIN app_user AS u ON u.user_id = c.recorded_by
 WHERE lower(u.email) = lower(%s)
   AND c.captured_at >= %s::timestamptz AND c.captured_at < %s::timestamptz
 GROUP BY c.request_sha256
HAVING count(*) > 1
"""

SAME_CONTENT_TWICE = """
SELECT t.customer_id, t.store_id, t.occurred_at, t.total, count(*),
       array_agg(t.transaction_id)
  FROM sales.sale_capture AS c
  JOIN transaction AS t USING (transaction_id)
  JOIN app_user AS u ON u.user_id = c.recorded_by
 WHERE lower(u.email) = lower(%s)
   AND c.captured_at >= %s::timestamptz AND c.captured_at < %s::timestamptz
 GROUP BY t.customer_id, t.store_id, t.occurred_at, t.total
HAVING count(*) > 1
"""

BY_KEY = """
SELECT t.transaction_id, t.source_transaction_id, t.total, c.captured_at
  FROM transaction AS t
  LEFT JOIN sales.sale_capture AS c USING (transaction_id)
 WHERE t.source_transaction_id = 'svc-sales:' || lower(%s)
"""


def run_disconnection(args: argparse.Namespace) -> int:
    rec = Recorder(Path(args.out), "F29-06 Desconexión", "f29-06")
    db = Database.from_environment()
    window = (args.account, args.since, args.until)
    rec.facts |= {"account": args.account, "since": args.since, "until": args.until}

    sales = db.rows(ACCOUNT_SALES, window)
    anchor = rec.add_rows(
        "the account's sales in the window", db.table(ACCOUNT_SALES, window)
    )
    if args.expect is not None:
        rec.check(
            "Desconexión",
            "sales recorded = sales the tester queued and confirmed",
            args.expect,
            len(sales),
            links=[anchor],
        )
    twice = db.rows(SAME_REQUEST_TWICE, window)
    a2 = rec.add_rows(
        "the same request recorded twice", db.table(SAME_REQUEST_TWICE, window)
    )
    rec.check(
        "Desconexión",
        "no request body recorded as two sales",
        0,
        len(twice),
        links=[a2],
    )
    content = db.rows(SAME_CONTENT_TWICE, window)
    a3 = rec.add_rows(
        "the same customer, store, time and total twice",
        db.table(SAME_CONTENT_TWICE, window),
    )
    rec.check(
        "Desconexión", "no sale's content recorded twice", 0, len(content), links=[a3]
    )
    for key in args.key or []:
        rows = db.rows(BY_KEY, (key,))
        a = rec.add_rows(f"the sale of Idempotency-Key {key}", db.table(BY_KEY, (key,)))
        rec.check(
            "Desconexión", f"key {key} recorded exactly once", 1, len(rows), links=[a]
        )
    return 0 if rec.write() else 1


# ---------- replay, local only ----------

PICK = """
SELECT i.store_id, i.product_id, p.list_price::text, i.quantity_on_hand,
       (SELECT customer_id::text FROM customer WHERE registered_on < CURRENT_DATE
         ORDER BY customer_id LIMIT 1),
       (SELECT min(channel_id) FROM channel)
  FROM inventory AS i JOIN product AS p USING (product_id)
 WHERE p.is_active AND p.list_price > 0 AND i.quantity_on_hand > 5
   AND i.store_id = ANY (%s::smallint[])
 ORDER BY i.store_id, i.product_id
 LIMIT 1
"""


def is_local(base_url: str) -> bool:
    target = os.environ.get("MOSAIQ_TARGET", "").strip().lower()
    if target:
        return target == "local"
    parsed = urlsplit(base_url)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}


def run_replay(args: argparse.Namespace) -> int:
    edge = edge_from_environment()
    if not is_local(edge.base.geturl()):
        raise SystemExit("replay writes a sale: it runs against a local stack only.")
    rec = Recorder(Path(args.out), "F29-06 Desconexión, the server's replay", "f29-06")
    db = Database.from_environment()
    versions(rec, edge, ["auth", "sales", "stock"])
    email = credential("MOSAIQ_ACCEPTANCE_EMAIL", "Store manager email: ", False)
    password = credential(
        "MOSAIQ_ACCEPTANCE_PASSWORD", "Store manager password: ", True
    )
    token = sign_in(rec, edge, email, password, "store manager")
    try:
        stores = db.rows(
            "SELECT coalesce(array_agg(a.store_id), "
            "(SELECT array_agg(store_id) FROM store))::text "
            "FROM users.store_assignment AS a JOIN app_user AS u USING (user_id) "
            "WHERE lower(u.email) = lower(%s)",
            (email,),
        )[0][0]
        [[store, product, price, on_hand, customer, channel]] = db.rows(PICK, (stores,))
        sale = {
            "customer_id": customer,
            "store_id": int(store),
            "channel_id": int(channel),
            "occurred_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(
                timespec="seconds"
            ),
            "lines": [{"product_id": int(product), "quantity": 1, "unit_price": price}],
        }
        key = str(uuid.uuid4())
        rec.facts["Idempotency-Key"] = key

        def send(note: str):
            return rec.call(
                edge,
                "POST",
                "/api/sales/v1/sales",
                note=note,
                token=token,
                payload=sale,
                headers={"Idempotency-Key": key},
            )

        first, n1 = send("first delivery")
        rec.check(
            "Desconexión", "first delivery", "201", first.status, links=[rec.link(n1)]
        )
        sale_id = first.json().get("sale_id") if first.status == 201 else None
        second, n2 = send("resent after a lost response")
        with futures.ThreadPoolExecutor(3) as pool:
            burst = list(pool.map(send, ["resent, concurrent"] * 3))
        for reply, number in [(second, n2), *burst]:
            replayed = reply.headers.get("Idempotent-Replayed", "")
            same = reply.status == 200 and reply.json().get("sale_id") == sale_id
            rec.check(
                "Desconexión",
                f"resend {number}: the original sale, replayed",
                f"200, Idempotent-Replayed: true, sale {sale_id}",
                f"{reply.status}, Idempotent-Replayed: {replayed or '(none)'}, "
                f"sale {reply.json().get('sale_id') if reply.body else None}",
                same and replayed.lower() == "true",
                [rec.link(number)],
            )
        rows = db.rows(BY_KEY, (key,))
        anchor = rec.add_rows("the sale by its key", db.table(BY_KEY, (key,)))
        rec.check(
            "Desconexión", "one transaction for the key", 1, len(rows), links=[anchor]
        )
        sql = (
            "SELECT quantity_on_hand FROM inventory "
            "WHERE store_id = %s AND product_id = %s"
        )
        [[now]] = db.rows(sql, (store, product))
        a2 = rec.add_rows("inventory after", db.table(sql, (store, product)))
        rec.check(
            "Desconexión",
            "stock fell by one unit, once",
            int(on_hand) - 1,
            int(now),
            links=[a2],
        )
    finally:
        sign_out(rec, edge, token, "store manager")
    return 0 if rec.write() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.acceptance.f29_06")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in SUITES:
        commands.add_parser(name).add_argument("--out", required=True)
    dis = commands.add_parser("disconnection")
    dis.add_argument("--out", required=True)
    dis.add_argument("--account", required=True, help="the floor account's email")
    dis.add_argument("--since", required=True, help="ISO 8601, with its offset")
    dis.add_argument("--until", required=True, help="ISO 8601, with its offset")
    dis.add_argument("--expect", type=int, help="sales the tester confirmed")
    dis.add_argument("--key", action="append", help="a queued Idempotency-Key")
    commands.add_parser("replay").add_argument("--out", required=True)
    args = parser.parse_args(argv)
    run = {"disconnection": run_disconnection, "replay": run_replay}.get(
        args.command, run_suite
    )
    code = run(args)
    print(Path(args.out) / "summary.md")
    return code


if __name__ == "__main__":
    sys.exit(main())
