"""Redis Streams propagation, from outbox commit to consumer acknowledgement.

F20-06 (#764). For every stream contracted in `contracts/streams/` and each of
its consumer groups, this measures how long an event takes from the commit of
the outbox row that raises it to the moment the group acknowledges its entry,
and compares the figures with the bound each contract states.

    propagation.py measure --out report.json [--check]
    propagation.py check report.json
    propagation.py backlog --stream S --group G --out backlog.json \
        --ready-file F --go-file F

`ops/propagation/run.sh` runs each of these in a container on the services'
network, which is how Redis and the PostgreSQL socket are reached both on a
laptop and on the instance. `check` needs nothing beyond the standard library.

Clock. Both ends are read on this process's monotonic clock, so no clock is
compared with another:

- the commit time is taken when `COMMIT` returns, so it is at most one round
  trip later than the real commit, and a latency is understated by that much;
- the acknowledgement time is the first poll that sees the entry acknowledged:
  the group's `last-delivered-id` has passed it and it is not in the group's
  pending entries list. A latency is therefore overstated by at most one poll
  interval, which the report states.

Load. A change is a touch that fires the stream's capture trigger and changes
no business value (`SET name = name`), except where the trigger captures only
inserts and deletes: a preference is deleted and re-inserted in one
transaction, and a segmentation run is appended only with `--allow-append`,
because a run is a business record. The outbox rows of a touch are read in the
touch's own transaction: they share its `now()`.

Every statement is parameterized; identifiers are composed with
`psycopg.sql.Identifier`, never with string formatting.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# In the checkout; run.sh mounts them at /contracts and passes --contracts.
CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent / "contracts" / "streams"
BOUND_HEADING = "## Propagation bound"
_BOUND_ROW = re.compile(
    r"^\|\s*`(?P<group>[a-z][a-z0-9-]*)`\s*\|\s*(?P<p95>\d+)\s*\|\s*(?P<max>\d+)\s*\|"
)
_CONTRACT_NAME = re.compile(
    r"^(?P<producer>[a-z][a-z0-9_]*)\.(?P<event>[a-z][a-z0-9_]*)\.v(?P<v>\d+)\.md$"
)


# --- contracts and bounds ------------------------------------------------------


@dataclass(frozen=True)
class Bound:
    p95_ms: int
    max_ms: int


@dataclass(frozen=True)
class StreamContract:
    stream: str
    path: Path
    bounds: dict[str, Bound]

    @property
    def groups(self) -> list[str]:
        return list(self.bounds)


def parse_bounds(text: str) -> dict[str, Bound]:
    """The `## Propagation bound` table of a stream contract, by group."""
    bounds: dict[str, Bound] = {}
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line.strip() == BOUND_HEADING
            continue
        if not in_section:
            continue
        match = _BOUND_ROW.match(line.strip())
        if match:
            group = match["group"]
            if group in bounds:
                raise ValueError(f"group {group} has two bounds")
            bounds[group] = Bound(int(match["p95"]), int(match["max"]))
    return bounds


def stream_of(path: Path) -> str:
    match = _CONTRACT_NAME.match(path.name)
    if not match:
        raise ValueError(f"{path.name} is not a stream contract name")
    return f"stream:{match['producer']}.v{match['v']}.{match['event']}"


def load_contracts(directory: Path = CONTRACTS_DIR) -> dict[str, StreamContract]:
    contracts = {}
    for path in sorted(directory.glob("*.v*.md")):
        if not _CONTRACT_NAME.match(path.name):
            continue
        stream = stream_of(path)
        contracts[stream] = StreamContract(
            stream, path, parse_bounds(path.read_text(encoding="utf-8"))
        )
    return contracts


# --- statistics ----------------------------------------------------------------


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile: the smallest value with q% of values at or below."""
    if not values:
        raise ValueError("percentile of no values")
    if not 0 < q <= 100:
        raise ValueError("q must be in (0, 100]")
    ordered = sorted(values)
    rank = math.ceil(q / 100 * len(ordered))
    return ordered[rank - 1]


def summarize(latencies_ms: Sequence[float]) -> dict[str, float | int]:
    return {
        "count": len(latencies_ms),
        "p50_ms": round(percentile(latencies_ms, 50), 1),
        "p95_ms": round(percentile(latencies_ms, 95), 1),
        "max_ms": round(max(latencies_ms), 1),
    }


# --- acknowledgement tracking ---------------------------------------------------


def entry_key(entry_id: str) -> tuple[int, int]:
    milliseconds, _, sequence = entry_id.partition("-")
    return int(milliseconds), int(sequence or 0)


@dataclass
class Entry:
    entry_id: str
    event_id: str
    committed: float
    acked: dict[str, float] = field(default_factory=dict)


class Tracker:
    """Which tracked stream entries each group has acknowledged, and when."""

    def __init__(self, groups: Mapping[str, Iterable[str]]):
        self.groups = {stream: list(names) for stream, names in groups.items()}
        self.entries: dict[str, dict[str, Entry]] = {s: {} for s in self.groups}
        self.max_deliveries: dict[tuple[str, str], int] = {}

    def add(self, stream: str, entry_id: str, event_id: str, committed: float) -> None:
        self.entries[stream].setdefault(entry_id, Entry(entry_id, event_id, committed))

    def outstanding(self, stream: str, group: str) -> list[Entry]:
        return [e for e in self.entries[stream].values() if group not in e.acked]

    def oldest_outstanding(self, stream: str, group: str) -> str | None:
        ids = [e.entry_id for e in self.outstanding(stream, group)]
        return min(ids, key=entry_key) if ids else None

    def observe(
        self,
        stream: str,
        group: str,
        last_delivered: str,
        pending: Mapping[str, int],
        now: float,
    ) -> None:
        """Mark acknowledged every entry the group has delivered and not pending.

        `pending` maps the group's pending entry ids to their delivery counts.
        """
        delivered = entry_key(last_delivered)
        for count in pending.values():
            key = (stream, group)
            self.max_deliveries[key] = max(self.max_deliveries.get(key, 0), count)
        for entry in self.outstanding(stream, group):
            if entry_key(entry.entry_id) <= delivered and entry.entry_id not in pending:
                entry.acked[group] = now

    def done(self) -> bool:
        return all(
            not self.outstanding(stream, group)
            for stream, groups in self.groups.items()
            for group in groups
        )

    def results(self) -> list[dict[str, Any]]:
        rows = []
        for stream, groups in sorted(self.groups.items()):
            entries = list(self.entries[stream].values())
            for group in sorted(groups):
                latencies = [
                    (e.acked[group] - e.committed) * 1000
                    for e in entries
                    if group in e.acked
                ]
                row: dict[str, Any] = {"stream": stream, "group": group}
                if latencies:
                    row.update(summarize(latencies))
                else:
                    row.update(count=0, p50_ms=None, p95_ms=None, max_ms=None)
                row["unacknowledged"] = len(entries) - len(latencies)
                rows.append(row)
        return rows


class StreamReads(Protocol):
    """The Redis reads a poll makes; `RedisReads` is the real one."""

    def new_entries(
        self, cursors: Mapping[str, str]
    ) -> dict[str, list[tuple[str, dict[str, str]]]]: ...

    def group_positions(self, streams: Iterable[str]) -> dict[str, dict[str, str]]: ...

    def pending(
        self, requests: Sequence[tuple[str, str, str]]
    ) -> list[dict[str, int]]: ...


def poll(
    reads: StreamReads,
    tracker: Tracker,
    cursors: dict[str, str],
    commits: Mapping[str, float],
    now: Callable[[], float],
) -> None:
    """One poll: pick up new entries of ours, then each group's acknowledgements."""
    for stream, entries in reads.new_entries(cursors).items():
        for entry_id, fields in entries:
            cursors[stream] = entry_id
            event_id = fields.get("event_id", "")
            if event_id in commits:
                tracker.add(stream, entry_id, event_id, commits[event_id])
    streams = [s for s, entries in tracker.entries.items() if entries]
    if not streams:
        return
    positions = reads.group_positions(streams)
    requests = []
    for stream in streams:
        for group in tracker.groups[stream]:
            oldest = tracker.oldest_outstanding(stream, group)
            if oldest is not None and group in positions.get(stream, {}):
                requests.append((stream, group, oldest))
    if not requests:
        return
    observed = now()
    for (stream, group, _oldest), pending in zip(
        requests, reads.pending(requests), strict=True
    ):
        tracker.observe(stream, group, positions[stream][group], pending, observed)


class RedisReads:
    """`StreamReads` on redis-py, one pipelined round trip per step."""

    def __init__(self, client: Any):
        self.client = client

    @staticmethod
    def _text(value: Any) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)

    def new_entries(self, cursors):
        pipe = self.client.pipeline(transaction=False)
        names = list(cursors)
        for stream in names:
            pipe.xrange(stream, min="(" + cursors[stream], max="+", count=1000)
        out = {}
        for stream, rows in zip(names, pipe.execute(), strict=True):
            out[stream] = [
                (
                    self._text(entry_id),
                    {self._text(k): self._text(v) for k, v in fields.items()},
                )
                for entry_id, fields in rows
            ]
        return out

    def group_positions(self, streams):
        pipe = self.client.pipeline(transaction=False)
        streams = list(streams)
        for stream in streams:
            pipe.xinfo_groups(stream)
        out = {}
        for stream, rows in zip(streams, pipe.execute(), strict=True):
            out[stream] = {
                self._text(row["name"]): self._text(row["last-delivered-id"])
                for row in rows
            }
        return out

    def pending(self, requests):
        pipe = self.client.pipeline(transaction=False)
        for stream, group, oldest in requests:
            pipe.xpending_range(stream, group, min=oldest, max="+", count=10_000)
        return [
            {self._text(p["message_id"]): int(p["times_delivered"]) for p in rows}
            for rows in pipe.execute()
        ]

    def existing_groups(self, stream: str) -> list[str]:
        if not self.client.exists(stream):
            return []
        return [self._text(row["name"]) for row in self.client.xinfo_groups(stream)]

    def last_id(self, stream: str) -> str:
        rows = self.client.xrevrange(stream, count=1)
        return self._text(rows[0][0]) if rows else "0-0"


# --- load -----------------------------------------------------------------------


@dataclass(frozen=True)
class Touch:
    """How to raise one event on a stream without changing a business value."""

    schema: str
    pick: str
    writes: tuple[str, ...]
    append_only: bool = False
    note: str = ""


TOUCHES: dict[str, Touch] = {
    "stream:sales.v1.recorded": Touch(
        "sales_events",
        "SELECT transaction_id FROM public.transaction "
        "ORDER BY transaction_id LIMIT %s",
        ("UPDATE public.transaction SET total = total WHERE transaction_id = %s",),
    ),
    "stream:products.v1.changed": Touch(
        "products_events",
        "SELECT product_id FROM public.product ORDER BY product_id LIMIT %s",
        ("UPDATE public.product SET name = name WHERE product_id = %s",),
    ),
    "stream:categories.v1.changed": Touch(
        "categories_events",
        "SELECT category_id FROM public.category ORDER BY category_id LIMIT %s",
        ("UPDATE public.category SET name = name WHERE category_id = %s",),
    ),
    "stream:stock.v1.changed": Touch(
        "stock_events",
        "SELECT store_id, product_id FROM public.inventory "
        "ORDER BY store_id, product_id LIMIT %s",
        (
            "UPDATE public.inventory SET quantity_on_hand = quantity_on_hand "
            "WHERE store_id = %s AND product_id = %s",
        ),
    ),
    "stream:stores.v1.changed": Touch(
        "stores_events",
        "SELECT store_id FROM public.store ORDER BY store_id LIMIT %s",
        ("UPDATE public.store SET name = name WHERE store_id = %s",),
    ),
    # Inactive accounts only: auth ends every session of a touched account.
    "stream:users.v1.access_changed": Touch(
        "users_events",
        "SELECT user_id FROM public.app_user WHERE NOT is_active "
        "ORDER BY user_id LIMIT %s",
        ("UPDATE public.app_user SET is_active = is_active WHERE user_id = %s",),
        note="needs an inactive account",
    ),
    # Captures INSERT and DELETE only: one touch is two events.
    "stream:preferences.v1.changed": Touch(
        "preferences_events",
        "SELECT customer_id, category_id FROM public.customer_interest_category "
        "ORDER BY customer_id, category_id LIMIT %s",
        (
            "DELETE FROM public.customer_interest_category "
            "WHERE customer_id = %s AND category_id = %s",
            "INSERT INTO public.customer_interest_category (customer_id, category_id) "
            "VALUES (%s, %s)",
        ),
    ),
    # Captures INSERT only, and a run is a business record (RN-05): opt-in.
    "stream:segments.v1.run_recorded": Touch(
        "segments_events",
        "SELECT 'RFM_RULES', 90, 'SEEDED' FROM generate_series(1, %s)",
        (
            "INSERT INTO public.segmentation_run (method, window_days, data_origin) "
            "VALUES (%s, %s, %s)",
        ),
        append_only=True,
        note="appends empty segmentation runs; local stack only",
    ),
}

# With --allow-session-revocation: any account but the administrator, whose
# sessions auth then ends. For a stack with no inactive account (the seed has
# none); never on the instance.
USERS_ANY_ACCOUNT = Touch(
    "users_events",
    "SELECT u.user_id FROM public.app_user AS u "
    "JOIN public.role AS r ON r.role_id = u.role_id "
    "WHERE r.code <> 'ADMIN' ORDER BY u.user_id LIMIT %s",
    TOUCHES["stream:users.v1.access_changed"].writes,
    note="ends the sessions of non-administrator accounts; local stack only",
)


def touch_for(stream: str, allow_session_revocation: bool = False) -> Touch:
    if allow_session_revocation and stream == "stream:users.v1.access_changed":
        return USERS_ANY_ACCOUNT
    return TOUCHES[stream]


def _schema_exists(connection: Any, schema: str) -> bool:
    row = connection.execute(
        "SELECT to_regclass(%s) IS NOT NULL", (schema + ".outbox",)
    ).fetchone()
    connection.rollback()
    return bool(row and row[0])


def touch_once(connection: Any, stream: str, touch: Touch, target: tuple) -> list[str]:
    """Run one touch in its own transaction; return its outbox event ids."""
    from psycopg import sql

    with connection.transaction():
        for statement in touch.writes:
            connection.execute(statement, target)
        rows = connection.execute(
            sql.SQL(
                "SELECT event_id::text FROM {}.outbox "
                "WHERE occurred_at = now() AND stream = %s"
            ).format(sql.Identifier(touch.schema)),
            (stream,),
        ).fetchall()
    return [row[0] for row in rows]


def produce(
    database_url: str,
    stream: str,
    touch: Touch,
    events: int,
    rate: float,
    commits: dict[str, float],
    lock: threading.Lock,
    errors: list[str],
) -> None:
    """Raise `events` touches on one stream at `rate` per second."""
    import psycopg

    try:
        with psycopg.connect(database_url) as connection:
            targets = connection.execute(touch.pick, (max(events, 1),)).fetchall()
            connection.commit()
            if not targets:
                errors.append(f"{stream}: no row to touch ({touch.note or 'empty'})")
                return
            start = time.perf_counter()
            for index in range(events):
                due = start + index / rate
                delay = due - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                target = tuple(targets[index % len(targets)])
                event_ids = touch_once(connection, stream, touch, target)
                committed = time.perf_counter()
                with lock:
                    for event_id in event_ids:
                        commits[event_id] = committed
    except Exception as error:  # reported, never swallowed
        errors.append(f"{stream}: {type(error).__name__}: {error}")


def _redis(url: str) -> Any:
    import redis

    return redis.Redis.from_url(url, socket_timeout=5)


def plan_streams(
    contracts: Mapping[str, StreamContract],
    reads: Any,
    connection: Any,
    selected: Sequence[str] | None,
    allow_append: bool,
) -> tuple[dict[str, list[str]], list[dict[str, str]]]:
    """Which (stream, group) pairs can be measured, and why the rest cannot."""
    groups: dict[str, list[str]] = {}
    skipped: list[dict[str, str]] = []

    def skip(stream: str, names: Iterable[str], reason: str) -> None:
        for name in names:
            skipped.append({"stream": stream, "group": name, "reason": reason})

    for stream, contract in sorted(contracts.items()):
        if selected and stream not in selected:
            continue
        touch = TOUCHES.get(stream)
        if touch is None:
            skip(stream, contract.groups, "producer not deployed: not captured yet")
            continue
        if not _schema_exists(connection, touch.schema):
            skip(stream, contract.groups, "producer not deployed: no outbox")
            continue
        if touch.append_only and not allow_append:
            skip(stream, contract.groups, f"opt-in with --allow-append: {touch.note}")
            continue
        live = set(reads.existing_groups(stream))
        present = [g for g in contract.groups if g in live]
        skip(
            stream,
            [g for g in contract.groups if g not in live],
            "consumer not deployed: no group",
        )
        if present:
            groups[stream] = present
    return groups, skipped


def measure(args: argparse.Namespace) -> dict[str, Any]:
    import psycopg

    contracts = load_contracts(Path(args.contracts))
    client = _redis(args.redis_url)
    reads = RedisReads(client)
    with psycopg.connect(args.database_url) as connection:
        groups, skipped = plan_streams(
            contracts, reads, connection, args.stream, args.allow_append
        )
    tracker = Tracker(groups)
    cursors = {stream: reads.last_id(stream) for stream in groups}
    commits: dict[str, float] = {}
    lock = threading.Lock()
    errors: list[str] = []
    producers = [
        threading.Thread(
            target=produce,
            args=(
                args.database_url,
                stream,
                touch_for(stream, args.allow_session_revocation),
                args.events,
                args.rate,
                commits,
                lock,
                errors,
            ),
            daemon=True,
        )
        for stream in groups
    ]
    started = time.time()
    for thread in producers:
        thread.start()
    deadline = None
    while True:
        with lock:
            snapshot = dict(commits)
        poll(reads, tracker, cursors, snapshot, time.perf_counter)
        producing = any(thread.is_alive() for thread in producers)
        if not producing:
            if deadline is None:
                deadline = time.perf_counter() + args.timeout
            seen = sum(len(entries) for entries in tracker.entries.values())
            if seen >= len(snapshot) and tracker.done():
                break
            if time.perf_counter() > deadline:
                break
        time.sleep(args.poll_ms / 1000)
    seen_events = {e.event_id for es in tracker.entries.values() for e in es.values()}
    return {
        "kind": "measure",
        "environment": args.environment,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "events_per_stream": args.events,
        "rate_per_second": args.rate,
        "poll_interval_ms": args.poll_ms,
        "results": tracker.results(),
        "not_relayed": len(set(commits) - seen_events),
        "skipped": skipped,
        "errors": errors,
    }


# --- backlog and restart --------------------------------------------------------


def _wait_for_file(path: Path, timeout: float, poll_s: float = 0.002) -> float:
    deadline = time.perf_counter() + timeout
    while not path.exists():
        if time.perf_counter() > deadline:
            raise TimeoutError(f"{path} did not appear within {timeout:.0f} s")
        time.sleep(poll_s)
    return time.perf_counter()


def backlog(args: argparse.Namespace) -> dict[str, Any]:
    """Build a backlog for a stopped group, then time its catch-up after restart.

    The consumer is stopped before this runs. Its events are raised and relayed
    while it is down; with `--duplicates`, some outbox rows are marked
    unrelayed again so the relay appends them twice, as a relay that crashed
    between XADD and its UPDATE would. Then `--ready-file` is written and this
    waits for `--go-file`, created right after the consumer is started.
    """
    import psycopg
    from psycopg import sql

    stream, group = args.stream, args.group
    touch = touch_for(stream, args.allow_session_revocation)
    client = _redis(args.redis_url)
    reads = RedisReads(client)
    if group not in reads.existing_groups(stream):
        raise SystemExit(f"{stream} has no group {group}")
    tracker = Tracker({stream: [group]})
    cursors = {stream: reads.last_id(stream)}
    commits: dict[str, float] = {}
    errors: list[str] = []
    produce(
        args.database_url,
        stream,
        touch,
        args.events,
        args.rate,
        commits,
        threading.Lock(),
        errors,
    )
    if errors:
        raise SystemExit("; ".join(errors))
    if args.producer_stopped and args.duplicates:
        raise SystemExit("--duplicates needs a running relay")
    duplicated = sorted(commits)[: args.duplicates]
    if duplicated:
        with psycopg.connect(args.database_url) as connection:
            connection.execute(
                sql.SQL(
                    "UPDATE {}.outbox SET relayed_at = NULL WHERE event_id = ANY(%s)"
                ).format(sql.Identifier(touch.schema)),
                (duplicated,),
            )
    expected = len(commits) + len(duplicated)

    def seen() -> int:
        return sum(len(e) for e in tracker.entries.values())

    deadline = time.perf_counter() + args.timeout
    # A consumer that is also the stream's producer stops its relay too: then
    # the backlog waits in the outbox, and the catch-up includes the relay.
    while not args.producer_stopped and seen() < expected:
        poll(reads, tracker, cursors, commits, time.perf_counter)
        if time.perf_counter() > deadline:
            raise SystemExit("the backlog was not relayed in time")
        time.sleep(args.poll_ms / 1000)
    poll(reads, tracker, cursors, commits, time.perf_counter)
    acked_while_stopped = sum(
        1 for e in tracker.entries[stream].values() if group in e.acked
    )
    if acked_while_stopped:
        raise SystemExit(
            f"{acked_while_stopped} entries were acknowledged while {group} "
            "was meant to be stopped"
        )
    Path(args.ready_file).write_text("ready\n", encoding="utf-8")
    restarted = _wait_for_file(Path(args.go_file), args.timeout)
    deadline = restarted + args.timeout
    while (seen() < expected or not tracker.done()) and time.perf_counter() < deadline:
        poll(reads, tracker, cursors, commits, time.perf_counter)
        time.sleep(args.poll_ms / 1000)
    entries = list(tracker.entries[stream].values())
    acked = [e for e in entries if group in e.acked]
    acked_events = {e.event_id for e in acked}
    report = {
        "kind": "backlog",
        "environment": args.environment,
        "stream": stream,
        "group": group,
        "events": len(commits),
        "producer_stopped": args.producer_stopped,
        "duplicate_entries": len(duplicated),
        "entries": len(entries),
        "acknowledged_entries": len(acked),
        "lost_events": sorted(set(commits) - acked_events),
        "max_deliveries_seen": tracker.max_deliveries.get((stream, group), 0),
        "catch_up_s": (
            round(max(e.acked[group] for e in acked) - restarted, 3) if acked else None
        ),
        "poll_interval_ms": args.poll_ms,
    }
    return report


# --- check ----------------------------------------------------------------------


def check(
    report: Mapping[str, Any], contracts: Mapping[str, StreamContract]
) -> list[str]:
    """Every failure of a measured run against the contracts' bounds."""
    failures = []
    for message in report.get("errors", []):
        failures.append(f"load error: {message}")
    if report.get("not_relayed"):
        failures.append(f"{report['not_relayed']} committed events never relayed")
    for _stream, contract in sorted(contracts.items()):
        if not contract.bounds:
            failures.append(f"{contract.path.name}: no {BOUND_HEADING} table")
    for row in report.get("results", []):
        stream, group = row["stream"], row["group"]
        label = f"{stream} {group}"
        contract = contracts.get(stream)
        bound = contract.bounds.get(group) if contract else None
        if bound is None:
            failures.append(f"{label}: measured, but its contract states no bound")
            continue
        if row["unacknowledged"]:
            failures.append(
                f"{label}: {row['unacknowledged']} entries never acknowledged"
            )
        if row["count"] == 0:
            continue
        if row["p95_ms"] > bound.p95_ms:
            failures.append(
                f"{label}: p95 {row['p95_ms']} ms > bound {bound.p95_ms} ms"
            )
        if row["max_ms"] > bound.max_ms:
            failures.append(
                f"{label}: max {row['max_ms']} ms > bound {bound.max_ms} ms"
            )
    return failures


def table(report: Mapping[str, Any]) -> str:
    lines = [
        "| Stream | Group | Events | p50 (ms) | p95 (ms) | Max (ms) | Unacked |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["results"]:
        lines.append(
            f"| `{row['stream']}` | `{row['group']}` | {row['count']} | "
            f"{row['p50_ms']} | {row['p95_ms']} | {row['max_ms']} | "
            f"{row['unacknowledged']} |"
        )
    for row in report.get("skipped", []):
        lines.append(
            f"| `{row['stream']}` | `{row['group']}` | — | — | — | — | "
            f"skipped: {row['reason']} |"
        )
    return "\n".join(lines)


# --- command line ---------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    import os

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def connected(command: argparse.ArgumentParser) -> None:
        command.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
        command.add_argument("--redis-url", default=os.environ.get("REDIS_URL"))
        command.add_argument("--environment", default="local")
        command.add_argument("--rate", type=float, default=20.0, help="events/s")
        command.add_argument("--poll-ms", type=float, default=5.0)
        command.add_argument("--timeout", type=float, default=60.0)
        command.add_argument(
            "--allow-session-revocation",
            action="store_true",
            help="touch active non-administrator accounts (local stack only)",
        )
        command.add_argument("--contracts", default=str(CONTRACTS_DIR))
        command.add_argument("--out", required=True)

    m = sub.add_parser("measure", help="controlled load on every stream")
    connected(m)
    m.add_argument("--events", type=int, default=200, help="events per stream")
    m.add_argument("--stream", action="append", help="limit to these streams")
    m.add_argument("--allow-append", action="store_true")
    m.add_argument("--check", action="store_true", help="then check the bounds")

    b = sub.add_parser("backlog", help="catch-up of a restarted consumer")
    connected(b)
    b.add_argument("--stream", required=True, choices=sorted(TOUCHES))
    b.add_argument("--group", required=True)
    b.add_argument("--events", type=int, default=500)
    b.add_argument("--duplicates", type=int, default=0)
    b.add_argument(
        "--producer-stopped",
        action="store_true",
        help="the stopped service also relays this stream",
    )
    b.add_argument("--ready-file", required=True)
    b.add_argument("--go-file", required=True)

    c = sub.add_parser("check", help="compare a report with the contracts' bounds")
    c.add_argument("report")
    c.add_argument("--contracts", default=str(CONTRACTS_DIR))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "check":
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        failures = check(report, load_contracts(Path(args.contracts)))
        for failure in failures:
            print("FAIL:", failure)
        return 1 if failures else 0
    if not args.database_url or not args.redis_url:
        print("DATABASE_URL and REDIS_URL are required", file=sys.stderr)
        return 2
    report = measure(args) if args.command == "measure" else backlog(args)
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report["kind"] == "measure":
        print(table(report))
        if args.check:
            failures = check(report, load_contracts(Path(args.contracts)))
            for failure in failures:
                print("FAIL:", failure)
            return 1 if failures else 0
        return 1 if report["errors"] else 0
    print(json.dumps(report, indent=2))
    return 1 if report["lost_events"] or report["catch_up_s"] is None else 0


if __name__ == "__main__":
    raise SystemExit(main())
