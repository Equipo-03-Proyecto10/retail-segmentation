"""ops/propagation/propagation.py: the measurement, the bounds and the check (#764).

None of these needs Redis or PostgreSQL: the tool's reads go through a small
protocol that a fake implements here. The live run is recorded in
docs/evidence/f20-06-stream-propagation.md.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "propagation", ROOT / "ops" / "propagation" / "propagation.py"
)
propagation = importlib.util.module_from_spec(_SPEC)
sys.modules["propagation"] = propagation
_SPEC.loader.exec_module(propagation)

STREAM = "stream:sales.v1.recorded"


# --- percentiles ----------------------------------------------------------------


def test_nearest_rank_percentiles():
    values = list(range(1, 101))
    assert propagation.percentile(values, 50) == 50
    assert propagation.percentile(values, 95) == 95
    assert propagation.percentile(values, 100) == 100
    assert propagation.percentile([7.0], 95) == 7.0
    # Order does not matter, and p95 of 20 values is the 19th.
    assert propagation.percentile(list(reversed(range(20))), 95) == 18


def test_percentile_refuses_nothing_and_a_bad_rank():
    with pytest.raises(ValueError):
        propagation.percentile([], 50)
    with pytest.raises(ValueError):
        propagation.percentile([1], 0)


def test_summary_rounds_to_a_tenth_of_a_millisecond():
    assert propagation.summarize([1.04, 2.06, 30.0]) == {
        "count": 3,
        "p50_ms": 2.1,
        "p95_ms": 30.0,
        "max_ms": 30.0,
    }


# --- acknowledgement tracking ---------------------------------------------------


def test_entry_ids_order_by_time_then_sequence():
    key = propagation.entry_key
    assert key("10-2") > key("10-1") > key("9-99")
    assert key("1700000000000-0") == (1_700_000_000_000, 0)


def test_an_entry_is_acknowledged_once_delivered_and_no_longer_pending():
    tracker = propagation.Tracker({STREAM: ["recommendations", "rfm-engine"]})
    tracker.add(STREAM, "10-0", "e1", committed=1.000)
    tracker.add(STREAM, "11-0", "e2", committed=1.010)

    # Delivered to recommendations but still pending: not acknowledged.
    tracker.observe(STREAM, "recommendations", "11-0", {"10-0": 1, "11-0": 1}, 1.020)
    assert not any(e.acked for e in tracker.entries[STREAM].values())

    # The first is acked; the second is still pending.
    tracker.observe(STREAM, "recommendations", "11-0", {"11-0": 1}, 1.030)
    # rfm-engine has not been delivered the second entry yet.
    tracker.observe(STREAM, "rfm-engine", "10-0", {}, 1.030)
    entries = tracker.entries[STREAM]
    assert entries["10-0"].acked == {"recommendations": 1.030, "rfm-engine": 1.030}
    assert entries["11-0"].acked == {}
    assert not tracker.done()

    tracker.observe(STREAM, "recommendations", "11-0", {}, 1.050)
    tracker.observe(STREAM, "rfm-engine", "11-0", {}, 1.060)
    assert tracker.done()
    rows = {r["group"]: r for r in tracker.results()}
    assert rows["recommendations"] == {
        "stream": STREAM,
        "group": "recommendations",
        "count": 2,
        "p50_ms": 30.0,
        "p95_ms": 40.0,
        "max_ms": 40.0,
        "unacknowledged": 0,
    }
    assert rows["rfm-engine"]["max_ms"] == 50.0


def test_an_acknowledgement_time_is_never_moved_by_a_later_poll():
    tracker = propagation.Tracker({STREAM: ["g"]})
    tracker.add(STREAM, "10-0", "e1", committed=0.0)
    tracker.observe(STREAM, "g", "10-0", {}, 0.5)
    tracker.observe(STREAM, "g", "10-0", {}, 9.0)
    assert tracker.entries[STREAM]["10-0"].acked["g"] == 0.5


def test_never_acknowledged_entries_are_counted_not_dropped():
    tracker = propagation.Tracker({STREAM: ["g"]})
    tracker.add(STREAM, "10-0", "e1", committed=0.0)
    (row,) = tracker.results()
    assert row["count"] == 0
    assert row["p95_ms"] is None
    assert row["unacknowledged"] == 1


def test_the_highest_delivery_count_seen_is_kept():
    tracker = propagation.Tracker({STREAM: ["g"]})
    tracker.add(STREAM, "10-0", "e1", committed=0.0)
    tracker.observe(STREAM, "g", "10-0", {"10-0": 2}, 0.1)
    tracker.observe(STREAM, "g", "10-0", {"10-0": 1}, 0.2)
    assert tracker.max_deliveries[(STREAM, "g")] == 2


class FakeReads:
    """A stream, its groups' positions and pending lists, edited by the test."""

    def __init__(self):
        self.entries: dict[str, list[tuple[str, dict[str, str]]]] = {STREAM: []}
        self.positions: dict[str, dict[str, str]] = {STREAM: {}}
        self.pendings: dict[tuple[str, str], dict[str, int]] = {}
        self.pending_requests: list[tuple[str, str, str]] = []

    def new_entries(self, cursors):
        out = {}
        for stream, cursor in cursors.items():
            after = propagation.entry_key(cursor)
            out[stream] = [
                (entry_id, fields)
                for entry_id, fields in self.entries[stream]
                if propagation.entry_key(entry_id) > after
            ]
        return out

    def group_positions(self, streams):
        return {s: dict(self.positions[s]) for s in streams}

    def pending(self, requests):
        self.pending_requests.extend(requests)
        return [dict(self.pendings.get((s, g), {})) for s, g, _ in requests]


def test_poll_tracks_only_our_events_and_measures_their_acknowledgement():
    reads = FakeReads()
    tracker = propagation.Tracker({STREAM: ["recommendations"]})
    cursors = {STREAM: "5-0"}
    commits = {"ours-1": 100.0, "ours-2": 100.0}
    clock = iter([100.010, 100.025]).__next__

    reads.entries[STREAM] = [
        ("4-0", {"event_id": "before-the-run"}),
        ("6-0", {"event_id": "ours-1"}),
        ("7-0", {"event_id": "someone-elses"}),
        ("8-0", {"event_id": "ours-2"}),
        # A relay that crashed between XADD and its UPDATE appends again.
        ("9-0", {"event_id": "ours-2"}),
    ]
    reads.positions[STREAM] = {"recommendations": "8-0"}
    reads.pendings[(STREAM, "recommendations")] = {"8-0": 1}
    propagation.poll(reads, tracker, cursors, commits, clock)

    assert cursors[STREAM] == "9-0"
    assert set(tracker.entries[STREAM]) == {"6-0", "8-0", "9-0"}
    assert reads.pending_requests == [(STREAM, "recommendations", "6-0")]
    assert tracker.entries[STREAM]["6-0"].acked == {"recommendations": 100.010}

    reads.positions[STREAM] = {"recommendations": "9-0"}
    reads.pendings[(STREAM, "recommendations")] = {}
    propagation.poll(reads, tracker, cursors, commits, clock)
    assert tracker.done()
    (row,) = tracker.results()
    assert row["count"] == 3
    assert row["max_ms"] == 25.0


def test_poll_skips_a_group_that_does_not_exist_yet():
    reads = FakeReads()
    tracker = propagation.Tracker({STREAM: ["rfm-engine"]})
    reads.entries[STREAM] = [("6-0", {"event_id": "ours"})]
    propagation.poll(reads, tracker, {STREAM: "0-0"}, {"ours": 1.0}, lambda: 2.0)
    assert reads.pending_requests == []
    assert not tracker.done()


# --- contracts and bounds -------------------------------------------------------

CONTRACT = """# `stream:sales.v1.recorded`

## Consumer behaviour

| Group | Effect |
|---|---|
| `recommendations` | Invalidates its cache. |

## Propagation bound

Some prose.

| Group | p95 (ms) | Max (ms) |
|---|---:|---:|
| `recommendations` | 250 | 1000 |
| `rfm-engine` | 500 | 2000 |

## Failure and compatibility

| `not-a-bound` | 1 | 2 |
"""


def test_bounds_are_read_from_their_own_section_only():
    assert propagation.parse_bounds(CONTRACT) == {
        "recommendations": propagation.Bound(250, 1000),
        "rfm-engine": propagation.Bound(500, 2000),
    }


def test_a_group_with_two_bounds_is_refused():
    text = CONTRACT.replace("| `rfm-engine` |", "| `recommendations` |")
    with pytest.raises(ValueError, match="two bounds"):
        propagation.parse_bounds(text)


def test_contract_names_map_to_stream_names():
    assert (
        propagation.stream_of(Path("users.access_changed.v1.md"))
        == "stream:users.v1.access_changed"
    )
    with pytest.raises(ValueError):
        propagation.stream_of(Path("README.md"))


def test_every_stream_contract_states_a_bound_for_every_listed_group():
    contracts = propagation.load_contracts()
    assert len(contracts) == len(list(propagation.CONTRACTS_DIR.glob("*.v1.md")))
    for stream, contract in contracts.items():
        assert contract.bounds, f"{contract.path.name} states no bound"
        text = contract.path.read_text(encoding="utf-8")
        for group, bound in contract.bounds.items():
            assert (
                f"`{group}`" in text.split(propagation.BOUND_HEADING)[0]
            ), f"{stream}: bound for {group}, a group the contract never names"
            assert 0 < bound.p95_ms <= bound.max_ms


def test_every_stream_with_an_outbox_has_a_load_or_is_known_undeployed():
    contracts = propagation.load_contracts()
    undeployed = {
        "stream:engagement.v1.interaction_recorded",
        "stream:rfm.v1.profile_changed",
        "stream:models.v1.publication_recorded",
        "stream:preferences.v1.consent_changed",
        "stream:sales.v1.reversed",
        "stream:segments.v1.assignment_changed",
    }
    assert set(contracts) - set(propagation.TOUCHES) == undeployed
    assert set(propagation.TOUCHES) <= set(contracts)


def test_touches_are_parameterized_and_change_no_business_value():
    for stream, touch in propagation.TOUCHES.items():
        statements = (touch.pick, *touch.writes)
        for statement in statements:
            assert "{" not in statement and "'%" not in statement, stream
        if not touch.append_only and stream != "stream:preferences.v1.changed":
            (write,) = touch.writes
            assert write.startswith("UPDATE ")
            column = write.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
            target, _, value = column.partition(" = ")
            assert target == value, f"{stream} changes {target}"


def test_active_accounts_are_touched_only_on_request():
    users = "stream:users.v1.access_changed"
    assert "NOT is_active" in propagation.touch_for(users).pick
    revoking = propagation.touch_for(users, allow_session_revocation=True)
    assert "NOT is_active" not in revoking.pick
    assert "r.code <> 'ADMIN'" in revoking.pick
    assert propagation.touch_for(STREAM, True) is propagation.TOUCHES[STREAM]


# --- the check ------------------------------------------------------------------


def _contracts(tmp_path: Path) -> dict:
    (tmp_path / "sales.recorded.v1.md").write_text(CONTRACT, encoding="utf-8")
    return propagation.load_contracts(tmp_path)


def _row(group="recommendations", p95=100.0, maximum=400.0, unacked=0, count=200):
    return {
        "stream": STREAM,
        "group": group,
        "count": count,
        "p50_ms": 10.0,
        "p95_ms": p95,
        "max_ms": maximum,
        "unacknowledged": unacked,
    }


def test_a_run_within_its_bounds_passes(tmp_path):
    report = {"results": [_row(), _row("rfm-engine", 499.9, 2000.0)], "errors": []}
    assert propagation.check(report, _contracts(tmp_path)) == []


def test_a_run_over_its_bound_fails(tmp_path):
    report = {"results": [_row(p95=250.1, maximum=1000.1)]}
    assert propagation.check(report, _contracts(tmp_path)) == [
        f"{STREAM} recommendations: p95 250.1 ms > bound 250 ms",
        f"{STREAM} recommendations: max 1000.1 ms > bound 1000 ms",
    ]


def test_lost_entries_and_unbounded_groups_fail(tmp_path):
    report = {
        "results": [_row(unacked=3, count=0), _row("analytics-kpis")],
        "not_relayed": 2,
        "errors": ["stream:x: OperationalError: boom"],
    }
    failures = propagation.check(report, _contracts(tmp_path))
    assert failures == [
        "load error: stream:x: OperationalError: boom",
        "2 committed events never relayed",
        f"{STREAM} recommendations: 3 entries never acknowledged",
        f"{STREAM} analytics-kpis: measured, but its contract states no bound",
    ]


def test_a_contract_without_a_bound_table_fails(tmp_path):
    (tmp_path / "stock.changed.v1.md").write_text("# no bound\n", encoding="utf-8")
    contracts = _contracts(tmp_path)
    assert propagation.check({"results": []}, contracts) == [
        "stock.changed.v1.md: no ## Propagation bound table"
    ]


def test_check_command_exits_one_on_a_failure(tmp_path, capsys):
    _contracts(tmp_path)
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"results": [_row(p95=999.0)]}), encoding="utf-8")
    args = ["check", str(report), "--contracts", str(tmp_path)]
    assert propagation.main(args) == 1
    assert "FAIL:" in capsys.readouterr().out
    report.write_text(json.dumps({"results": [_row()]}), encoding="utf-8")
    assert propagation.main(args) == 0


def test_the_recorded_local_run_is_within_the_contracts_bounds():
    """The committed report of docs/evidence is re-checked on every run."""
    report = ROOT / "docs" / "evidence" / "f20-06-stream-propagation-local.json"
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["environment"] == "local"
    assert data["results"], "the local run measured nothing"
    assert propagation.check(data, propagation.load_contracts()) == []


# --- planning -------------------------------------------------------------------


class FakeConnection:
    def __init__(self, schemas):
        self.schemas = schemas

    def execute(self, statement, params):
        present = params[0].removesuffix(".outbox") in self.schemas

        class Row:
            def fetchone(self_inner):
                return (present,)

        return Row()

    def rollback(self):
        pass


class FakeGroups:
    def __init__(self, groups):
        self.groups = groups

    def existing_groups(self, stream):
        return self.groups.get(stream, [])


def test_plan_measures_deployed_groups_and_says_why_it_skips_the_rest():
    contracts = propagation.load_contracts()
    schemas = {t.schema for t in propagation.TOUCHES.values()}
    connection = FakeConnection(schemas - {"users_events"})
    reads = FakeGroups(
        {
            STREAM: ["recommendations"],
            "stream:products.v1.changed": ["products", "recommendations"],
        }
    )
    groups, skipped = propagation.plan_streams(
        contracts, reads, connection, None, allow_append=False
    )
    assert groups == {
        STREAM: ["recommendations"],
        "stream:products.v1.changed": ["products", "recommendations"],
    }
    reasons = {(s["stream"], s["group"]): s["reason"] for s in skipped}
    assert reasons[(STREAM, "rfm-engine")] == "consumer not deployed: no group"
    assert reasons[("stream:users.v1.access_changed", "auth")].startswith(
        "producer not deployed"
    )
    assert reasons[("stream:rfm.v1.profile_changed", "model-registry")].startswith(
        "producer not deployed"
    )
    assert reasons[("stream:segments.v1.run_recorded", "segments")].startswith(
        "opt-in with --allow-append"
    )


def test_plan_can_be_limited_to_named_streams():
    contracts = propagation.load_contracts()
    schemas = {t.schema for t in propagation.TOUCHES.values()}
    groups, skipped = propagation.plan_streams(
        contracts,
        FakeGroups({STREAM: ["recommendations", "rfm-engine"]}),
        FakeConnection(schemas),
        [STREAM],
        allow_append=False,
    )
    assert groups == {STREAM: ["recommendations", "rfm-engine"]}
    assert skipped == []
