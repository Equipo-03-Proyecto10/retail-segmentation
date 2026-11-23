"""F24-08 (#831): a stock change, and the recommendation that follows it.

docs/evidence/f24-08-stock-recommendation-update.md is the runbook. Each
subcommand writes an evidence directory with a summary.md:

    cycle       criteria 1 and 2. One customer's recommendation at one store
                (`GET /v2/recommendations`) offers a product. A `COUNT`
                movement leaves it at zero; the reads are repeated until it is
                no longer offered at that store, and once more after the
                propagation bound. A `RECEIPT` of the same quantity restocks
                it, and the same is done until it is offered again.
    exclusions  criterion 3, read-only. A discontinued product and the
                products of a category the customer restricted are not
                offered, and the recorded decision counts both reasons
                (`not_sellable`, `PERSONALIZATION_WITHDRAWN`). With
                `--before-decision`, a decision recorded while the product
                was still active is read back too (from its table): it
                offered the product, and the later one counts one more
                `not_sellable`. Decisions are read from schema
                `recommendations`, read-only: the `/v2/recommendation-decisions`
                reads are still planned.

**The bound** is not a figure of this file. It is the `recommendations`
group's row in `contracts/streams/stock.changed.v1.md`, `## Propagation bound`
(F20-06, #764): the time from the commit of stock's outbox row to the
group's acknowledgement, which comes after the consumer has emptied the
cache. A read after the maximum therefore cannot be served an answer cached
before the movement.

**The delay** is measured on this process's monotonic clock, from the moment
the movement's 201 is received to the moment the first read that no longer
offers the product (or, after the restock, offers it again) is received. It
overstates the propagation by at most one poll interval plus one read, and the
summary gives both ends.

**Offered at the store** means a `primary` or `alternative` item for the
product. An `elsewhere` entry reports the product held at *another* store, with
`unavailable_reason`; it is recorded and named in the summary, and does not
count as offered at the reference store.

Every read of the recommendation is warmed first and read twice, so the run
shows the second read served from the cache (the same `decision_id`), and the
change can only be seen once the stream has emptied it.

The only writes are the two movements, both with an `Idempotency-Key`. Every
query runs in a read-only transaction with bind parameters. Environment:

    MOSAIQ_BASE_URL       the gateway's origin
    MOSAIQ_HOST, MOSAIQ_INSECURE_TLS
                          as for tests/negative, for a loopback hop
    MOSAIQ_DB_EXEC        the psql prefix (evidence.Database)
    MOSAIQ_ACCEPTANCE_EMAIL / _PASSWORD
                          a reader of recommendations and stock (ANALYST;
                          prompted)
    MOSAIQ_PLANNER_EMAIL / _PASSWORD
                          `cycle` only: an INVENTORY_PLANNER (prompted)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from tests.acceptance.evidence import (
    Database,
    Recorder,
    credential,
    edge_from_environment,
    sign_in,
    sign_out,
    versions,
)
from tests.negative.stack import ROOT, Edge

SERVICES = ["auth", "stock", "products", "recommendations"]
STREAM_CONTRACT = ROOT / "contracts/streams/stock.changed.v1.md"
GROUP = "recommendations"
OFFERED_KINDS = ("primary", "alternative")
RECOMMENDATIONS = "/api/recommendations/v2/recommendations"
MOVEMENTS = "/api/stock/v1/stock-movements"
# Reads go on past the bound by this much before a run gives up, so a delay
# over the bound is measured and reported instead of cut off.
OVERRUN_S = 10.0


def _propagation() -> Any:
    """ops/propagation/propagation.py, which is not a package (#764)."""
    path = ROOT / "ops/propagation/propagation.py"
    spec = importlib.util.spec_from_file_location("propagation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("propagation", module)
    spec.loader.exec_module(module)
    return module


def stream_bound(path: Path = STREAM_CONTRACT, group: str = GROUP) -> tuple[int, int]:
    """(p95 ms, max ms) of one group, from a stream contract's bound table."""
    bounds = _propagation().parse_bounds(path.read_text())
    if group not in bounds:
        raise SystemExit(f"{path.name} states no propagation bound for `{group}`.")
    bound = bounds[group]
    return bound.p95_ms, bound.max_ms


# ---------- what a recommendation says ----------


def items_for(body: dict[str, Any], product_id: int) -> list[dict[str, Any]]:
    return [i for i in body.get("items") or [] if i.get("product_id") == product_id]


def offered_here(body: dict[str, Any], product_id: int) -> bool:
    """A primary or alternative item for the product: offered at the store."""
    return any(i.get("kind") in OFFERED_KINDS for i in items_for(body, product_id))


def primary(body: dict[str, Any], product_id: int) -> dict[str, Any] | None:
    return next(
        (i for i in items_for(body, product_id) if i.get("kind") == "primary"), None
    )


def elsewhere(body: dict[str, Any], product_id: int) -> dict[str, Any] | None:
    return next(
        (i for i in items_for(body, product_id) if i.get("kind") == "elsewhere"), None
    )


def exclusion_count(body: dict[str, Any], reason: str) -> int:
    return next(
        (
            int(e["count"])
            for e in body.get("exclusions") or []
            if e.get("reason") == reason
        ),
        0,
    )


def shape(body: dict[str, Any]) -> dict[str, Any]:
    """What a decision keeps of an answer (ADR-0067): each item's position,
    product, kind, category, quantity, `alternative_for` and
    `unavailable_reason`, and the exclusion count per reason. An answer and the
    decision stored for it agree when their shapes are equal."""
    return {
        "items": sorted(
            (
                int(i["position"]),
                int(i["product_id"]),
                i["kind"],
                int(i["category_id"]),
                int(i["availability"]["quantity_on_hand"]),
                None if i.get("alternative_for") is None else int(i["alternative_for"]),
                i.get("unavailable_reason") or None,
            )
            for i in body.get("items") or []
        ),
        "exclusions": {
            e["reason"]: int(e["count"]) for e in body.get("exclusions") or []
        },
    }


def describe(body: dict[str, Any], product_id: int) -> str:
    """One line for the summary: how the product appears, and the exclusions."""
    shown = (
        ", ".join(
            f"{i['kind']} at position {i['position']}"
            + (
                f" (store {i['availability']['store_id']})"
                if i.get("availability")
                else ""
            )
            + (f", {i['unavailable_reason']}" if i.get("unavailable_reason") else "")
            for i in items_for(body, product_id)
        )
        or "absent"
    )
    counts = ", ".join(
        f"{e['reason']} {e['count']}" for e in body.get("exclusions") or []
    )
    return f"product {product_id}: {shown}; exclusions: {counts or 'none'}"


# ---------- waiting for the change ----------


@dataclass
class Poll:
    """One read while waiting: when it was sent and received, relative to the
    movement's 201, and whether the condition held."""

    number: int
    sent_ms: float
    received_ms: float
    held: bool
    decision_id: str | None


@dataclass
class Wait:
    polls: list[Poll] = field(default_factory=list)

    @property
    def first_held(self) -> Poll | None:
        return next((p for p in self.polls if p.held), None)

    @property
    def last_before(self) -> Poll | None:
        """The last read before the first that held."""
        first = self.first_held
        before = [p for p in self.polls if first is None or p.number < first.number]
        return before[-1] if before else None

    def as_dict(self) -> dict[str, Any]:
        first, last = self.first_held, self.last_before
        return {
            "delay_ms": None if first is None else round(first.received_ms, 1),
            "last_unchanged_sent_ms": None if last is None else round(last.sent_ms, 1),
            "reads": len(self.polls),
            "polls": [p.__dict__ for p in self.polls],
        }


def wait_for(
    read: Callable[[], tuple[dict[str, Any] | None, int]],
    condition: Callable[[dict[str, Any]], bool],
    start: float,
    deadline_s: float,
    interval_s: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Wait:
    """Read until the condition holds or `deadline_s` after `start` passes.
    A read that failed (None) does not hold."""
    wait = Wait()
    while True:
        sent = clock()
        body, number = read()
        received = clock()
        held = body is not None and condition(body)
        wait.polls.append(
            Poll(
                number,
                (sent - start) * 1000,
                (received - start) * 1000,
                held,
                None if body is None else body.get("decision_id"),
            )
        )
        if held or received - start >= deadline_s:
            return wait
        sleep(max(0.0, interval_s - (clock() - sent)))


# ---------- the session ----------


class Session:
    def __init__(self, rec: Recorder, planner: bool) -> None:
        self.rec = rec
        self.edge: Edge = edge_from_environment()
        self.db = Database.from_environment()
        email = credential("MOSAIQ_ACCEPTANCE_EMAIL", "Reader email: ", False)
        password = credential("MOSAIQ_ACCEPTANCE_PASSWORD", "Reader password: ", True)
        self.reader = sign_in(rec, self.edge, email, password, "reader")
        rec.facts["reader"] = email
        self.planner: str | None = None
        if planner:
            email = credential("MOSAIQ_PLANNER_EMAIL", "Planner email: ", False)
            password = credential("MOSAIQ_PLANNER_PASSWORD", "Planner password: ", True)
            self.planner = sign_in(rec, self.edge, email, password, "planner")
            rec.facts["planner"] = email
        rec.facts["gateway"] = self.edge.base.geturl()

    def close(self) -> None:
        sign_out(self.rec, self.edge, self.reader, "reader")
        if self.planner is not None:
            sign_out(self.rec, self.edge, self.planner, "planner")

    def recommend(
        self, customer: str, store: int, channel: int | None, note: str
    ) -> tuple[dict[str, Any] | None, int]:
        query: dict[str, Any] = {"customer_id": customer, "store_id": store}
        if channel is not None:
            query["channel_id"] = channel
        reply, number = self.rec.call(
            self.edge,
            "GET",
            f"{RECOMMENDATIONS}?{urlencode(query)}",
            note=note,
            token=self.reader,
        )
        return (reply.json() if reply.status == 200 else None), number

    def decision(
        self, decision_id: str, title: str
    ) -> tuple[dict[str, Any] | None, str]:
        """The decision as stored in schema `recommendations` (F24-05, #828), in
        the answer's own shape. Read from the tables, read-only: the
        `/v2/recommendation-decisions` reads are still planned
        (docs/services/recommendations.md), and the table is the record they
        will serve."""
        head = (
            "SELECT decision_id, customer_id, status, computed_at, store_id, "
            "store_source, channel_id FROM recommendations.recommendation_decision "
            "WHERE decision_id = %s"
        )
        items = (
            "SELECT position, product_id, kind, category_id, quantity_on_hand, "
            "held_at_store_id, alternative_for, unavailable_reason "
            "FROM recommendations.recommendation_item WHERE decision_id = %s "
            "ORDER BY position"
        )
        exclusions = (
            "SELECT reason, product_count "
            "FROM recommendations.recommendation_exclusion "
            "WHERE decision_id = %s ORDER BY reason"
        )
        key = (decision_id,)
        anchor = self.rec.add_rows(
            title,
            "\n".join(self.db.table(sql, key) for sql in (head, items, exclusions)),
        )
        found = self.db.rows(head, key)
        if not found:
            return None, anchor
        store = int(found[0][4]) if found[0][4] else None
        return {
            "decision_id": decision_id,
            "items": [
                {
                    "position": int(r[0]),
                    "product_id": int(r[1]),
                    "kind": r[2],
                    "category_id": int(r[3]),
                    "availability": {
                        "store_id": int(r[5]) if r[5] else store,
                        "quantity_on_hand": int(r[4]),
                    },
                    "alternative_for": int(r[6]) if r[6] else None,
                    "unavailable_reason": r[7] or None,
                }
                for r in self.db.rows(items, key)
            ],
            "exclusions": [
                {"reason": r[0], "count": int(r[1])}
                for r in self.db.rows(exclusions, key)
            ],
        }, anchor

    def stock_level(
        self, store: int, product: int, note: str
    ) -> tuple[int | None, int]:
        reply, number = self.rec.call(
            self.edge,
            "GET",
            f"/api/stock/v1/stock-levels/{store}/{product}",
            note=note,
            token=self.reader,
        )
        return (
            reply.json()["quantity_on_hand"] if reply.status == 200 else None
        ), number

    def inventory(self, store: int, product: int, title: str) -> tuple[int | None, str]:
        sql = (
            "SELECT store_id, product_id, quantity_on_hand, updated_at "
            "FROM inventory WHERE store_id = %s AND product_id = %s"
        )
        rows = self.db.rows(sql, (store, product))
        anchor = self.rec.add_rows(title, self.db.table(sql, (store, product)))
        return (int(rows[0][2]) if rows else None), anchor


# ---------- cycle: criteria 1 and 2 ----------

ZERO = "1. Agotado"
BACK = "2. Reabastecido"


def _movement(
    s: Session, payload: dict[str, Any], note: str
) -> tuple[dict[str, Any] | None, int, float, str]:
    key = str(uuid.uuid4())
    reply, number = s.rec.call(
        s.edge,
        "POST",
        MOVEMENTS,
        note=note,
        token=s.planner,
        payload=payload,
        headers={"Idempotency-Key": key},
    )
    received = time.monotonic()
    return (reply.json() if reply.status == 201 else None), number, received, key


def _after_bound(
    s: Session,
    args: argparse.Namespace,
    criterion: str,
    start: float,
    max_ms: int,
    product: int,
    expect_offered: bool,
    quantity: int,
    before: dict[str, Any],
) -> dict[str, Any] | None:
    """The read once the bound has passed since the movement, read twice, the
    decision behind it read back, and the stock it was judged on."""
    remaining = max_ms / 1000 - (time.monotonic() - start)
    if remaining > 0:
        time.sleep(remaining)
    elapsed_ms = (time.monotonic() - start) * 1000
    body, number = s.recommend(
        args.customer, args.store, args.channel, f"read after the bound ({criterion})"
    )
    again, again_number = s.recommend(
        args.customer, args.store, args.channel, f"read again ({criterion})"
    )
    rec = s.rec
    if body is None:
        rec.check(
            criterion,
            "the read after the bound answers",
            200,
            "not 200",
            False,
            [rec.link(number)],
        )
        return None
    word = "offered" if expect_offered else "not offered"
    rec.check(
        criterion,
        f"{elapsed_ms:.0f} ms after the movement (bound {max_ms} ms), product "
        f"{product} is {word} at store {args.store}",
        word,
        "offered" if offered_here(body, product) else "not offered",
        offered_here(body, product) == expect_offered,
        [rec.link(number)],
    )
    rec.facts[f"{criterion}: after the bound"] = describe(body, product)
    if again is not None:
        rec.check(
            criterion,
            "the next read agrees (it may be served from the cache)",
            word,
            "offered" if offered_here(again, product) else "not offered",
            offered_here(again, product) == expect_offered,
            [rec.link(again_number)],
        )
    if expect_offered:
        item = primary(body, product)
        rec.check(
            criterion,
            "it is a primary item again, with the restocked quantity",
            f"primary, {quantity}",
            (
                "absent"
                if item is None
                else f"primary, " f"{item['availability']['quantity_on_hand']}"
            ),
            item is not None and item["availability"]["quantity_on_hand"] == quantity,
            [rec.link(number)],
        )
    else:
        was = exclusion_count(before, "out_of_stock")
        now = exclusion_count(body, "out_of_stock")
        rec.check(
            criterion,
            "the decision counts one more `out_of_stock` exclusion",
            was + 1,
            now,
            links=[rec.link(number)],
        )
        entry = elsewhere(body, product)
        if entry is not None:
            rec.facts[f"{criterion}: elsewhere entry"] = (
                f"product {product} reported held at store "
                f"{entry['availability']['store_id']} "
                f"({entry['availability']['quantity_on_hand']} units), "
                f"unavailable_reason {entry.get('unavailable_reason')}: not offered "
                f"at store {args.store}"
            )
    recorded, recorded_anchor = s.decision(
        body["decision_id"], f"stored decision ({criterion})"
    )
    rec.check(
        criterion,
        "the stored decision is the answer's: same items and exclusions",
        "same",
        (
            "same"
            if recorded is not None and shape(recorded) == shape(body)
            else "absent" if recorded is None else "different"
        ),
        links=[rec.link(number), recorded_anchor],
    )
    level, level_number = s.stock_level(
        args.store, product, f"stock level ({criterion})"
    )
    row, anchor = s.inventory(args.store, product, f"inventory after ({criterion})")
    rec.check(
        criterion,
        "stock-service and `inventory` hold the movement's result",
        f"{quantity} = {quantity}",
        f"{level} = {row}",
        level == row == quantity,
        [rec.link(level_number), anchor],
    )
    return body


def _measure(
    s: Session,
    args: argparse.Namespace,
    criterion: str,
    start: float,
    max_ms: int,
    product: int,
    expect_offered: bool,
) -> Wait:
    def read() -> tuple[dict[str, Any] | None, int]:
        return s.recommend(
            args.customer, args.store, args.channel, f"poll ({criterion})"
        )

    wait = wait_for(
        read,
        lambda body: offered_here(body, product) == expect_offered,
        start,
        max_ms / 1000 + OVERRUN_S,
        args.poll_ms / 1000,
    )
    first, last = wait.first_held, wait.last_before
    rec = s.rec
    measured = "never" if first is None else f"{first.received_ms:.0f} ms"
    rec.check(
        criterion,
        f"measured delay, movement 201 to the first read that "
        f"{'offers' if expect_offered else 'no longer offers'} it, within the "
        f"bound's maximum",
        f"≤ {max_ms} ms",
        measured
        + (
            ""
            if last is None
            else f" (last unchanged read sent at {last.sent_ms:.0f} ms)"
        ),
        first is not None and first.received_ms <= max_ms,
        [
            rec.link(p.number)
            for p in ([last] if last else []) + ([first] if first else [])
        ],
    )
    return wait


def run_cycle(args: argparse.Namespace) -> int:
    p95_ms, max_ms = stream_bound()
    rec = Recorder(
        Path(args.out), "F24-08 a stock change and the recommendation", "f24-08"
    )
    rec.facts["bound"] = (
        f"{STREAM_CONTRACT.relative_to(ROOT)}, group `{GROUP}`: p95 {p95_ms} ms, "
        f"max {max_ms} ms"
    )
    rec.facts["customer"] = args.customer
    rec.facts["store"] = args.store
    rec.facts["channel"] = args.channel if args.channel is not None else "(any)"
    rec.facts["poll interval ms"] = args.poll_ms
    s = Session(rec, planner=True)
    delays: dict[str, Any] = {"bound": {"p95_ms": p95_ms, "max_ms": max_ms}}
    try:
        versions(rec, s.edge, SERVICES)
        before, number = s.recommend(args.customer, args.store, args.channel, "before")
        if before is None:
            rec.check(
                ZERO,
                "the first read answers",
                200,
                "not 200",
                False,
                [rec.link(number)],
            )
            return 0 if rec.write() else 1
        product = args.product
        if product is None:
            first = next(
                (i for i in before.get("items") or [] if i.get("kind") == "primary"),
                None,
            )
            product = None if first is None else first["product_id"]
        item = None if product is None else primary(before, product)
        rec.check(
            ZERO,
            "before: the product is a primary item at the store",
            "primary",
            "absent" if item is None else "primary",
            item is not None,
            [rec.link(number)],
        )
        if item is None:
            return 0 if rec.write() else 1
        quantity = int(item["availability"]["quantity_on_hand"])
        rec.facts["product"] = product
        rec.facts["before"] = describe(before, product)
        cached, cached_number = s.recommend(
            args.customer, args.store, args.channel, "before, again (the cache)"
        )
        rec.check(
            ZERO,
            "before, again: served from the cache (same decision_id)",
            before["decision_id"],
            None if cached is None else cached.get("decision_id"),
            links=[rec.link(number), rec.link(cached_number)],
        )
        level, level_number = s.stock_level(args.store, product, "stock level before")
        row, anchor = s.inventory(args.store, product, "inventory before")
        rec.check(
            ZERO,
            "before: the item's quantity = stock-service = `inventory`",
            f"{quantity} = {quantity} = {quantity}",
            f"{quantity} = {level} = {row}",
            quantity == level == row,
            [rec.link(number), rec.link(level_number), anchor],
        )

        # 1. The movement that leaves it at zero.
        payload = {
            "movement_type": "COUNT",
            "store_id": args.store,
            "product_id": product,
            "quantity": 0,
            "counted_by": "F24-08 acceptance (#831)",
            "note": "F24-08 (#831): leaves the product at zero",
        }
        moved, number, start, key = _movement(s, payload, "COUNT to zero")
        rec.facts["COUNT Idempotency-Key"] = key
        rec.check(
            ZERO,
            "the COUNT to zero is recorded",
            201,
            "201" if moved is not None else "not 201",
            moved is not None,
            [rec.link(number)],
        )
        if moved is None:
            return 0 if rec.write() else 1
        wait = _measure(s, args, ZERO, start, max_ms, product, expect_offered=False)
        delays["zero"] = wait.as_dict()
        after = _after_bound(s, args, ZERO, start, max_ms, product, False, 0, before)

        # 2. The restock.
        payload = {
            "movement_type": "RECEIPT",
            "store_id": args.store,
            "product_id": product,
            "quantity": quantity,
            "reference": "F24-08 (#831) restock",
            "note": "F24-08 (#831): restores the counted quantity",
        }
        moved, number, start, key = _movement(s, payload, "RECEIPT (restock)")
        rec.facts["RECEIPT Idempotency-Key"] = key
        rec.check(
            BACK,
            f"the RECEIPT of {quantity} is recorded",
            201,
            "201" if moved is not None else "not 201",
            moved is not None,
            [rec.link(number)],
        )
        if moved is not None:
            wait = _measure(s, args, BACK, start, max_ms, product, expect_offered=True)
            delays["restock"] = wait.as_dict()
            _after_bound(
                s, args, BACK, start, max_ms, product, True, quantity, after or before
            )
        sql = (
            "SELECT movement_id, movement_type, store_id, product_id, quantity_delta, "
            "quantity_before, quantity_after, recorded_at FROM stock.stock_movement "
            "WHERE idempotency_key IN (%s::uuid, %s::uuid) ORDER BY movement_id"
        )
        rec.add_rows(
            "the two movements",
            s.db.table(sql, (rec.facts["COUNT Idempotency-Key"], key)),
        )
        (rec.out / "delays.json").write_text(json.dumps(delays, indent=2))
    finally:
        s.close()
    return 0 if rec.write() else 1


# ---------- exclusions: criterion 3 ----------

WHY = "3. Excluidos con motivo"


def run_exclusions(args: argparse.Namespace) -> int:
    rec = Recorder(
        Path(args.out), "F24-08 discontinued and restricted products", "f24-08"
    )
    rec.facts["customer"] = args.customer
    rec.facts["store"] = args.store
    rec.facts["discontinued product"] = args.discontinued
    s = Session(rec, planner=False)
    try:
        versions(rec, s.edge, SERVICES)
        sql = (
            "SELECT r.product_id, r.sku, r.is_active, st.commercial_status "
            "FROM api_products.product_ref_v1 AS r "
            "LEFT JOIN products.product_status AS st USING (product_id) "
            "WHERE r.product_id = %s"
        )
        rows = s.db.rows(sql, (args.discontinued,))
        anchor = rec.add_rows(
            "the discontinued product", s.db.table(sql, (args.discontinued,))
        )
        rec.check(
            WHY,
            f"product {args.discontinued} is not sellable (is_active false)",
            "f",
            rows[0][2] if rows else "(no row)",
            links=[anchor],
        )
        sql = (
            "WITH RECURSIVE restricted AS ("
            " SELECT target_id AS category_id FROM api_preferences.restriction_v1"
            " WHERE customer_id = %s::uuid AND kind = 'INTEREST_CATEGORY'"
            " UNION SELECT t.category_id FROM api_categories.category_tree_v1 AS t"
            " JOIN restricted AS r ON t.parent_category_id = r.category_id)"
            " SELECT p.product_id, p.category_id, a.quantity_on_hand"
            " FROM api_products.product_ref_v1 AS p"
            " JOIN restricted USING (category_id)"
            " JOIN api_stock.stock_level_v1 AS a"
            "   ON a.product_id = p.product_id AND a.store_id = %s"
            " WHERE p.is_active AND a.quantity_on_hand > 0 ORDER BY p.product_id"
        )
        restricted = [int(r[0]) for r in s.db.rows(sql, (args.customer, args.store))]
        restricted_anchor = rec.add_rows(
            "products in stock at the store, in a category the customer restricted",
            s.db.table(sql, (args.customer, args.store)),
        )
        rec.facts["restricted products in stock"] = restricted or "(none)"
        rec.check(
            WHY,
            "the customer restricted a category that has products in stock here",
            "at least one",
            len(restricted),
            bool(restricted),
            [restricted_anchor],
        )

        body, number = s.recommend(args.customer, args.store, args.channel, "the read")
        if body is None:
            rec.check(
                WHY, "the read answers", 200, "not 200", False, [rec.link(number)]
            )
            return 0 if rec.write() else 1
        rec.facts["read"] = describe(body, args.discontinued)
        shown = {i["product_id"] for i in body.get("items") or []}
        rec.check(
            WHY,
            f"the discontinued product {args.discontinued} is not offered, nor "
            "reported elsewhere",
            "absent",
            "present" if args.discontinued in shown else "absent",
            links=[rec.link(number)],
        )
        rec.check(
            WHY,
            "no product of a restricted category is offered",
            "none",
            sorted(shown & set(restricted)) or "none",
            links=[rec.link(number), restricted_anchor],
        )
        for reason in ("not_sellable", "PERSONALIZATION_WITHDRAWN"):
            count = exclusion_count(body, reason)
            rec.check(
                WHY,
                f"the decision counts `{reason}`",
                "at least 1",
                count,
                count >= 1,
                [rec.link(number)],
            )
        recorded, recorded_anchor = s.decision(
            body["decision_id"], "the stored decision"
        )
        rec.check(
            WHY,
            "the stored decision records the same exclusions",
            shape(body)["exclusions"],
            None if recorded is None else shape(recorded)["exclusions"],
            links=[rec.link(number), recorded_anchor],
        )
        if args.before_decision:
            earlier, earlier_anchor = s.decision(
                args.before_decision, "the stored decision before it was discontinued"
            )
            if earlier is None:
                rec.check(
                    WHY,
                    "the earlier decision is stored",
                    "stored",
                    "absent",
                    False,
                    [earlier_anchor],
                )
            else:
                rec.facts["earlier decision"] = describe(earlier, args.discontinued)
                rec.check(
                    WHY,
                    f"before, product {args.discontinued} was offered here",
                    "offered",
                    (
                        "offered"
                        if offered_here(earlier, args.discontinued)
                        else "not offered"
                    ),
                    links=[earlier_anchor],
                )
                rec.check(
                    WHY,
                    "the later decision counts one more `not_sellable`",
                    exclusion_count(earlier, "not_sellable") + 1,
                    exclusion_count(body, "not_sellable"),
                    links=[earlier_anchor, rec.link(number)],
                )
    finally:
        s.close()
    return 0 if rec.write() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.acceptance.f24_08")
    commands = parser.add_subparsers(dest="command", required=True)
    cycle = commands.add_parser("cycle", help="criteria 1 and 2: zero, then restock")
    exclusions = commands.add_parser("exclusions", help="criterion 3, read-only")
    for sub in (cycle, exclusions):
        sub.add_argument("--out", required=True)
        sub.add_argument("--customer", required=True)
        sub.add_argument("--store", type=int, required=True)
        sub.add_argument("--channel", type=int)
    cycle.add_argument("--product", type=int, help="default: the first primary item")
    cycle.add_argument("--poll-ms", type=int, default=100)
    exclusions.add_argument("--discontinued", type=int, required=True)
    exclusions.add_argument("--before-decision")
    args = parser.parse_args(argv)
    code = run_cycle(args) if args.command == "cycle" else run_exclusions(args)
    print(Path(args.out) / "summary.md")
    return code


if __name__ == "__main__":
    sys.exit(main())
