"""F18-05 (#462) and F29-10 (#896): what the rehearsal needs, read from the instance.

docs/demo-distributed.md, "Preparation", lists what must hold before a team
member rehearses the demonstration. This reads what can be read through the
gateway and says, for each item, whether it holds:

    python -m tests.acceptance.demo --out DIR \\
        --customer <uuid> --store 11 --product 2 \\
        [--campaign 301 --experiment 31]

Part 1 (the sale) needs the first three ids. Part 2 (the experiment) adds the
campaign and the experiment, and then also needs a MARKETING account.

It writes nothing to the instance: every request is a GET except the sign-ins
and the sign-outs, which only make and end a session. Environment, as for the
other collectors (tests/acceptance/evidence.py); nothing secret is ever an
argument:

    MOSAIQ_BASE_URL       the gateway's origin (https://<host>)
    MOSAIQ_HOST, MOSAIQ_INSECURE_TLS
                          as for tests/negative, for a loopback hop
    MOSAIQ_DEMO_FLOOR_EMAIL / _PASSWORD
                          the STORE_MANAGER account the phone signs in as
                          (prompted when absent)
    MOSAIQ_DEMO_MARKETING_EMAIL / _PASSWORD
                          the MARKETING account that plans the experiment
                          (Part 2 only; prompted when absent)

Two sign-ins are spaced by the edge's six-a-minute limit
(`INSTANCE_SIGN_IN_INTERVAL`), so a run with both accounts takes about ten
seconds longer than one with the floor account alone.

The output directory holds summary.md (each check, expected and actual, linked
to its request), exchanges.md (every request with its `X-Correlation-ID`) and
record.json, the same files the other collectors write. The exit code is 0 only
when every check held.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from tests.acceptance.evidence import (
    Recorder,
    contract_version,
    credential,
    edge_from_environment,
    sign_in,
    sign_out,
)
from tests.negative.stack import (
    INSTANCE_SIGN_IN_INTERVAL,
    ROOT,
    Edge,
    implemented,
    registry_names,
)

FLOOR_ROLE = "STORE_MANAGER"
MARKETING_ROLE = "MARKETING"
IN_STORE_CHANNEL = "physical_store"
SIZE = 100
PURPOSES = ("PERSONALIZATION", "MARKETING_CONTACT")
LOCATION_FIELDS = ("latitude", "longitude", "geofence_radius_m")


# ---------- what each answer must say (pure, tested on contract examples) ----------


def checkout_version(name: str) -> str | None:
    """`info.version` of the contract in this checkout."""
    path = ROOT / "contracts" / name / "openapi.yaml"
    return contract_version(path.read_text()) if path.is_file() else None


def store_location(store: dict[str, Any]) -> str | None:
    """The store's location as the check-in is judged against: the three fields
    together, or None when the store has none or only part of one."""
    location = store.get("location")
    if not isinstance(location, dict):
        return None
    if any(location.get(name) is None for name in LOCATION_FIELDS):
        return None
    return ", ".join(f"{name} {location[name]}" for name in LOCATION_FIELDS)


def granted(consent: dict[str, Any], purpose: str) -> bool:
    """`getConsentState`: whether one purpose reads GRANTED."""
    return any(
        entry.get("purpose") == purpose and entry.get("state") == "GRANTED"
        for entry in consent.get("purposes", [])
    )


def in_store_channel(channels: dict[str, Any]) -> int | None:
    """The id of the channel a floor sale is recorded in, as the phone reads it."""
    for channel in channels.get("channels", []):
        if channel.get("name") == IN_STORE_CHANNEL:
            return int(channel["channel_id"])
    return None


def promotions_for(
    page: dict[str, Any], campaign_id: int, store_id: int, channel_id: int
) -> list[dict[str, Any]]:
    """The active promotions of the campaign that apply at this store and channel,
    as the phone filters them (PromotionsViewModel)."""
    return [
        promotion
        for promotion in page.get("promotions", [])
        if promotion.get("campaign_id") == campaign_id
        and promotion.get("is_active") is True
        and store_id in promotion.get("store_ids", [])
        and channel_id in promotion.get("channel_ids", [])
    ]


def campaign_is_running(campaign: dict[str, Any], today: date) -> bool:
    """ACTIVE and today inside its validity, which is what makes a promotion active."""
    try:
        starts = date.fromisoformat(campaign["starts_on"])
        ends = date.fromisoformat(campaign["ends_on"])
    except (KeyError, ValueError):
        return False
    return campaign.get("status") == "ACTIVE" and starts <= today <= ends


def not_analysed_yet(experiment: dict[str, Any], now: datetime) -> bool:
    """ASSIGNED, and before the analysis instant. After it, the first read of the
    primary result stores it and the phone stops being told an arm."""
    analysis_at = (experiment.get("design") or {}).get("analysis_at")
    if experiment.get("state") != "ASSIGNED" or not analysis_at:
        return False
    return now < datetime.fromisoformat(analysis_at.replace("Z", "+00:00"))


def arms_of(page: dict[str, Any], customer_id: str) -> list[str]:
    """`arm_kind` of each assignment the customer holds in the page."""
    return [
        str(row.get("arm_kind"))
        for row in page.get("assignments", [])
        if row.get("customer_id") == customer_id
    ]


# ---------- the run ----------


class Rehearsal:
    def __init__(self, rec: Recorder, edge: Edge) -> None:
        self.rec = rec
        self.edge = edge
        self.outcome = ""

    def get(
        self, path: str, token: str | None, note: str
    ) -> tuple[dict[str, Any], str]:
        """One GET: its body when it answered 200 with a JSON object, else {}, and
        the link of its exchange. `outcome` keeps what a failed read answered."""
        reply, number = self.rec.call(self.edge, "GET", path, note=note, token=token)
        self.outcome = reply.outcome()
        body: Any = {}
        if reply.status == 200:
            try:
                body = reply.json()
            except ValueError:
                body = {}
        return (body if isinstance(body, dict) else {}), Recorder.link(number)

    def check(
        self,
        criterion: str,
        case: str,
        expected: object,
        actual: object,
        held: bool,
        link: str,
    ) -> bool:
        return self.rec.check(criterion, case, expected, actual, held, [link])

    def versions(self) -> None:
        """The served contract against this checkout's, for each service that has an
        implementation. The team names the commit the instance runs and checks it
        out; a difference means the checkout is not what is deployed."""
        for name in registry_names():
            if not implemented(name):
                continue
            reply, number = self.rec.call(
                self.edge,
                "GET",
                f"/api/{name}/openapi.yaml",
                note="served contract",
                accept=None,
            )
            served = (
                contract_version(reply.body.decode("utf-8", "replace"))
                if reply.status == 200
                else reply.outcome()
            )
            wanted = checkout_version(name)
            self.check(
                "P1 versions",
                f"{name}: the served contract is this checkout's",
                wanted,
                served,
                served == wanted,
                Recorder.link(number),
            )
            self.rec.facts.setdefault("versions", {})[f"{name} contract"] = served

    def floor(self, token: str, customer: str, store: int, product: int) -> None:
        me, link = self.get(
            "/api/users/v1/users/me", token, "who the phone signs in as"
        )
        self.check(
            "P2 accounts",
            "the floor account is an active STORE_MANAGER",
            f"{FLOOR_ROLE}, active",
            (
                f"{me.get('role_code')}, active={me.get('is_active')}"
                if me
                else self.outcome
            ),
            me.get("role_code") == FLOOR_ROLE and me.get("is_active") is True,
            link,
        )
        assigned, link = self.get(
            f"/api/users/v1/store-assignments/{quote(str(me.get('user_id')))}",
            token,
            "the stores the account works in",
        )
        self.check(
            "P3 store",
            f"the account is assigned to store {store}",
            store,
            assigned.get("store_ids") if assigned else self.outcome,
            store in assigned.get("store_ids", []),
            link,
        )
        found, link = self.get(f"/api/stores/v1/stores/{store}", token, "the store")
        location = store_location(found)
        self.check(
            "P3 store",
            f"store {store} has the location the check-in is judged against",
            "latitude, longitude and geofence_radius_m",
            location or (self.outcome if not found else "no location"),
            location is not None,
            link,
        )
        item, link = self.get(
            f"/api/products/v1/products/{product}", token, "the product"
        )
        self.check(
            "P4 product",
            f"product {product} is active",
            True,
            item.get("is_active") if item else self.outcome,
            item.get("is_active") is True,
            link,
        )
        level, link = self.get(
            f"/api/stock/v1/stock-levels/{store}/{product}", token, "the stock"
        )
        quantity = level.get("quantity_on_hand")
        self.check(
            "P4 product",
            f"store {store} holds at least one unit of product {product}",
            "at least 1",
            quantity if level else self.outcome,
            isinstance(quantity, int) and quantity >= 1,
            link,
        )
        self.customer(token, customer, store)

    def customer(self, token: str, customer: str, store: int) -> None:
        record, link = self.get(
            f"/api/customers/v1/customers/{quote(customer)}", token, "the customer"
        )
        self.check(
            "P5 customer",
            "the customer exists",
            customer,
            record.get("customer_id") if record else self.outcome,
            record.get("customer_id") == customer,
            link,
        )
        name = record.get("name", "")
        found, link = self.get(
            "/api/customers/v1/customers?" + urlencode({"q": name, "size": SIZE}),
            token,
            "the phone's search by the customer's name",
        )
        ids = [row.get("customer_id") for row in found.get("customers", [])]
        self.check(
            "P5 customer",
            "the search by name finds the customer (#462 step 4)",
            customer,
            f"{len(ids)} found" if found else self.outcome,
            bool(name) and customer in ids,
            link,
        )
        segment, link = self.get(
            "/api/segments/v1/segment-assignments/current?"
            + urlencode({"customer_id": customer}),
            token,
            "the customer's current segment",
        )
        self.check(
            "P5 customer",
            "the current assignment state is assigned (#462 step 4)",
            "assigned",
            (
                f"{segment.get('state')} {segment.get('label_code') or ''}".strip()
                if segment
                else self.outcome
            ),
            segment.get("state") == "assigned",
            link,
        )
        reco, link = self.get(
            "/api/recommendations/v1/recommendations?"
            + urlencode({"customer_id": customer}),
            token,
            "the customer's recommendations, as the Sale screen asks",
        )
        self.check(
            "P5 customer",
            "the recommendations status is recommended (#462 step 4)",
            "recommended",
            reco.get("status") if reco else self.outcome,
            reco.get("status") == "recommended",
            link,
        )
        self.check(
            "P5 customer",
            f"the customer's usual store is the demonstration store ({store})",
            store,
            reco.get("usual_store_id") if reco else self.outcome,
            reco.get("usual_store_id") == store,
            link,
        )
        consent, link = self.get(
            f"/api/customer-preferences/v1/preference-profiles/{quote(customer)}/consent",
            token,
            "the customer's consent",
        )
        for purpose in PURPOSES:
            held = granted(consent, purpose)
            self.check(
                "P5 customer",
                f"{purpose} reads GRANTED (recommendations and the frame need it)",
                "GRANTED",
                "GRANTED" if held else (self.outcome if not consent else "not granted"),
                held,
                link,
            )

    def experiment(
        self,
        floor: str,
        marketing: str,
        customer: str,
        store: int,
        campaign: int,
        experiment: int,
        now: datetime,
    ) -> None:
        me, link = self.get(
            "/api/users/v1/users/me", marketing, "who prepares the campaign"
        )
        self.check(
            "P2 accounts",
            "the campaign account is an active MARKETING user",
            f"{MARKETING_ROLE}, active",
            (
                f"{me.get('role_code')}, active={me.get('is_active')}"
                if me
                else self.outcome
            ),
            me.get("role_code") == MARKETING_ROLE and me.get("is_active") is True,
            link,
        )
        channels, link = self.get("/api/sales/v1/channels", floor, "the sales channels")
        channel = in_store_channel(channels)
        self.check(
            "P6 campaign",
            f"sales names the {IN_STORE_CHANNEL} channel",
            IN_STORE_CHANNEL,
            f"channel {channel}" if channel is not None else self.outcome,
            channel is not None,
            link,
        )
        found, link = self.get(
            f"/api/campaigns/v1/campaigns/{campaign}", marketing, "the campaign"
        )
        self.check(
            "P6 campaign",
            f"campaign {campaign} is ACTIVE and today lies within its dates",
            "ACTIVE, starts_on <= today <= ends_on",
            (
                f"{found.get('status')} "
                f"{found.get('starts_on')}..{found.get('ends_on')}"
                if found
                else self.outcome
            ),
            campaign_is_running(found, now.date()),
            link,
        )
        page, link = self.get(
            "/api/campaigns/v1/promotions?"
            + urlencode(
                {
                    "store_id": store,
                    "channel_id": channel if channel is not None else 0,
                    "active": "true",
                    "size": SIZE,
                }
            ),
            floor,
            "the phone's read of the store's active promotions",
        )
        offers = promotions_for(page, campaign, store, channel or 0)
        self.check(
            "P6 campaign",
            f"the floor account reads an active promotion of campaign {campaign} "
            f"for store {store}",
            "at least 1",
            len(offers) if page else self.outcome,
            bool(offers),
            link,
        )
        plan, link = self.get(
            f"/api/experimentation/v1/experiments/{experiment}",
            marketing,
            "the experiment",
        )
        self.check(
            "P7 experiment",
            f"experiment {experiment} belongs to campaign {campaign}",
            campaign,
            plan.get("campaign_id") if plan else self.outcome,
            plan.get("campaign_id") == campaign,
            link,
        )
        self.check(
            "P7 experiment",
            "the experiment is ASSIGNED and its analysis instant has not come",
            "ASSIGNED, now < analysis_at",
            (
                f"{plan.get('state')}, analysis_at "
                f"{(plan.get('design') or {}).get('analysis_at')}"
                if plan
                else self.outcome
            ),
            not_analysed_yet(plan, now),
            link,
        )
        self.check(
            "P7 experiment",
            "the design has a control, so an effect can be stated",
            "CONTROLLED",
            plan.get("design_kind") if plan else self.outcome,
            plan.get("design_kind") == "CONTROLLED",
            link,
        )
        rows, link = self.get(
            "/api/experimentation/v1/experiment-assignments?"
            + urlencode(
                {"experiment_id": experiment, "customer_id": customer, "size": SIZE}
            ),
            marketing,
            "the customer's assignment, read by marketing",
        )
        arms = arms_of(rows, customer)
        self.check(
            "P7 experiment",
            "the demonstration customer is assigned once, to a treatment arm",
            "TREATMENT",
            ", ".join(arms) or "not assigned" if rows else self.outcome,
            arms == ["TREATMENT"],
            link,
        )
        current, link = self.get(
            "/api/experimentation/v1/experiment-assignments/current?"
            + urlencode({"customer_id": customer, "campaign_id": campaign}),
            floor,
            "the phone's read of the customer's arm",
        )
        self.check(
            "P7 experiment",
            "the phone's read answers the treatment arm (F25-17)",
            "TREATMENT",
            current.get("arm_kind") if current else self.outcome,
            current.get("arm_kind") == "TREATMENT",
            link,
        )


def run(args: argparse.Namespace, edge: Edge, pace: float | None = None) -> int:
    """Collect into `args.out`; 0 when every check held. `pace` is the wait
    between two sign-ins: the edge's interval over HTTPS, none on a loopback."""
    part_two = args.experiment is not None
    if part_two != (args.campaign is not None):
        raise SystemExit("--campaign and --experiment go together.")
    if pace is None:
        pace = INSTANCE_SIGN_IN_INTERVAL if edge.base.scheme == "https" else 0.0
    rec = Recorder(Path(args.out), "F18-05 and F29-10 preconditions", "demo")
    rec.facts["gateway"] = edge.base.geturl()
    rec.facts["parts"] = "1 and 2" if part_two else "1"
    rehearsal = Rehearsal(rec, edge)
    floor = marketing = None
    try:
        floor = sign_in(
            rec,
            edge,
            credential(
                "MOSAIQ_DEMO_FLOOR_EMAIL", "Floor (STORE_MANAGER) email: ", False
            ),
            credential("MOSAIQ_DEMO_FLOOR_PASSWORD", "Floor password: ", True),
            "the floor account",
        )
        if part_two:
            time.sleep(pace)
            marketing = sign_in(
                rec,
                edge,
                credential("MOSAIQ_DEMO_MARKETING_EMAIL", "Marketing email: ", False),
                credential(
                    "MOSAIQ_DEMO_MARKETING_PASSWORD", "Marketing password: ", True
                ),
                "the marketing account",
            )
        rehearsal.versions()
        rehearsal.floor(floor, args.customer, args.store, args.product)
        if marketing is not None:
            rehearsal.experiment(
                floor,
                marketing,
                args.customer,
                args.store,
                args.campaign,
                args.experiment,
                datetime.now(UTC),
            )
    finally:
        for label, token in (
            ("the floor account", floor),
            ("the marketing account", marketing),
        ):
            if token is not None:
                sign_out(rec, edge, token, label)
    held = rec.write()
    for check in rec.checks:
        mark = "PASS" if check.held else "FAIL"
        print(f"{mark}  {check.criterion}: {check.case}: {check.actual}")
    print(f"{sum(c.held for c in rec.checks)}/{len(rec.checks)} preconditions held")
    return 0 if held else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.acceptance.demo")
    parser.add_argument("--out", required=True, help="the evidence directory")
    parser.add_argument(
        "--customer", required=True, help="the demonstration customer's id"
    )
    parser.add_argument("--store", type=int, required=True)
    parser.add_argument("--product", type=int, required=True)
    parser.add_argument("--campaign", type=int)
    parser.add_argument("--experiment", type=int)
    args = parser.parse_args(argv)
    code = run(args, edge_from_environment())
    print(Path(args.out) / "summary.md")
    return code


if __name__ == "__main__":
    sys.exit(main())
