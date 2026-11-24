"""Turn one Locust run into the report's tables (F29-15, #1191).

    python3 bench/locust/report.py bench/locust/results/<run>

Reads the run's ``requests.csv`` and ``phases.csv`` (or their ``.gz``) (written by
locustfile.py) and prints Markdown. Standard library only, so it runs without
Locust installed.

Definitions, the same as the k6 bench's calibration (bench/README.md):
- a step is measured over its steady window, from ``SETTLE_SECONDS`` after the
  step starts to its end, so the users still being spawned are left out;
- a step is **sustained** when its p99 is at most 1,000 ms and at most 1% of
  its requests failed;
- the **saturation point** is the first step that is not sustained, and the
  capacity is the last sustained step before it;
- **recovery** is the time from the drop to the end of the first 10-second
  window whose p95 is back within 1.2 times the first step's p95, with no
  failure.
"""

from __future__ import annotations

import csv
import gzip
import io
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

SETTLE_SECONDS = 10.0
P99_LIMIT_MS = 1000.0
ERROR_LIMIT = 0.01
RECOVERY_WINDOW = 10.0
RECOVERY_FACTOR = 1.2
SLOWEST = 5


@dataclass
class Request:
    t: float
    cls: str
    name: str
    ms: float
    ok: bool
    status: int
    error: str


@dataclass
class Phase:
    name: str
    users: int
    start: float
    end: float


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile; NaN for no values."""
    if not values:
        return math.nan
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def open_csv(run: Path, name: str) -> io.TextIOBase:
    """A run's CSV, or its gzip: the repository ignores *.csv (AGENTS.md)."""
    if (run / name).exists():
        return (run / name).open(newline="")
    return gzip.open(run / (name + ".gz"), "rt", newline="")


def load(run: Path) -> tuple[list[Request], list[Phase]]:
    with open_csv(run, "requests.csv") as handle:
        requests = [
            Request(
                float(r["t"]),
                r["class"],
                r["name"],
                float(r["ms"]),
                r["ok"] == "1",
                int(r["status"] or 0),
                r["error"],
            )
            for r in csv.DictReader(handle)
        ]
    with open_csv(run, "phases.csv") as handle:
        rows = list(csv.DictReader(handle))
    phases = []
    for row, following in zip(rows, rows[1:], strict=False):
        if row["phase"] in {"sign-in", "end"}:
            continue
        phases.append(
            Phase(
                row["phase"], int(row["users"]), float(row["t"]), float(following["t"])
            )
        )
    return requests, phases


@dataclass
class Summary:
    count: int
    failures: int
    seconds: float
    mean: float
    p50: float
    p95: float
    p99: float
    max: float

    @property
    def rps(self) -> float:
        return self.count / self.seconds if self.seconds > 0 else math.nan

    @property
    def error_rate(self) -> float:
        return self.failures / self.count if self.count else 0.0

    @property
    def sustained(self) -> bool:
        return (
            self.count > 0
            and self.p99 <= P99_LIMIT_MS
            and self.error_rate <= ERROR_LIMIT
        )


def summarise(requests: list[Request], seconds: float) -> Summary:
    times = [r.ms for r in requests]
    return Summary(
        count=len(requests),
        failures=sum(not r.ok for r in requests),
        seconds=seconds,
        mean=sum(times) / len(times) if times else math.nan,
        p50=percentile(times, 50),
        p95=percentile(times, 95),
        p99=percentile(times, 99),
        max=max(times) if times else math.nan,
    )


def between(requests: list[Request], start: float, end: float) -> list[Request]:
    return [r for r in requests if start <= r.t < end]


def ms(value: float) -> str:
    return "—" if math.isnan(value) else f"{value:,.0f}"


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def steady(phase: Phase) -> tuple[float, float]:
    start = phase.start + SETTLE_SECONDS
    return (start, phase.end) if start < phase.end else (phase.start, phase.end)


def report(run: Path) -> str:
    requests, phases = load(run)
    steps = [p for p in phases if p.name.startswith("step-")]
    recovery = next((p for p in phases if p.name == "recovery"), None)
    out: list[str] = []

    out += [
        "### Per step (steady window)",
        "",
        "| Phase | Users | Requests | req/s | Mean ms | p50 ms | p95 ms | p99 ms "
        "| Max ms | Errors | Sustained |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    summaries = {}
    for phase in steps + ([recovery] if recovery else []):
        start, end = steady(phase)
        s = summarise(between(requests, start, end), end - start)
        summaries[phase.name] = s
        out.append(
            f"| {phase.name} | {phase.users} | {s.count:,} | {s.rps:.1f} "
            f"| {ms(s.mean)} | {ms(s.p50)} | {ms(s.p95)} | {ms(s.p99)} | {ms(s.max)} "
            f"| {s.failures:,} ({pct(s.error_rate)}) "
            f"| {'yes' if s.sustained else 'no'} |"
        )

    out += ["", "### Saturation", ""]
    broken = next((p for p in steps if not summaries[p.name].sustained), None)
    if broken is None:
        top = steps[-1] if steps else None
        out.append(
            f"Every step was sustained, up to {top.users if top else 0} users: "
            "the platform did not saturate within this ramp."
        )
    else:
        index = steps.index(broken)
        s = summaries[broken.name]
        capacity = steps[index - 1].users if index else 0
        why = []
        if s.p99 > P99_LIMIT_MS:
            why.append(f"p99 {ms(s.p99)} ms > {P99_LIMIT_MS:,.0f} ms")
        if s.error_rate > ERROR_LIMIT:
            why.append(f"errors {pct(s.error_rate)} > {pct(ERROR_LIMIT)}")
        out.append(
            f"Saturation at **{broken.users} users** ({broken.name}: "
            f"{'; '.join(why)}). The last sustained step was **{capacity} users**."
        )

    out += ["", "### Recovery after the drop", ""]
    if recovery is None or not steps:
        out.append("The run has no recovery phase.")
    else:
        start, end = steady(steps[0])
        baseline = summarise(between(requests, start, end), end - start).p95
        threshold = baseline * RECOVERY_FACTOR
        out += [
            f"Baseline: {steps[0].name}'s p95, {ms(baseline)} ms; threshold "
            f"{ms(threshold)} ms with no failure, in {RECOVERY_WINDOW:.0f}-second "
            "windows from the drop.",
            "",
            "| Window (s after drop) | Requests | p95 ms | Errors | Recovered |",
            "|---|---:|---:|---:|---|",
        ]
        recovered_at = None
        t = recovery.start
        while t + RECOVERY_WINDOW <= recovery.end + 1e-6:
            window = between(requests, t, t + RECOVERY_WINDOW)
            s = summarise(window, RECOVERY_WINDOW)
            ok = bool(window) and s.p95 <= threshold and s.failures == 0
            offset = t - recovery.start
            out.append(
                f"| {offset:.0f}–{offset + RECOVERY_WINDOW:.0f} | {s.count} "
                f"| {ms(s.p95)} | {s.failures} | {'yes' if ok else 'no'} |"
            )
            if ok and recovered_at is None:
                recovered_at = offset + RECOVERY_WINDOW
            t += RECOVERY_WINDOW
        out.append("")
        out.append(
            f"Recovered **{recovered_at:.0f} s** after the drop."
            if recovered_at is not None
            else "It did **not** recover within the recovery phase."
        )

    measured = [r for r in requests if steps and steps[0].start <= r.t < phases[-1].end]
    by_name: dict[str, list[Request]] = defaultdict(list)
    for r in measured:
        by_name[r.name].append(r)
    span = (phases[-1].end - steps[0].start) if steps else 0.0
    rows = sorted(
        ((name, summarise(rs, span)) for name, rs in by_name.items()),
        key=lambda item: item[1].p95,
        reverse=True,
    )
    out += [
        "",
        f"### The {SLOWEST} slowest endpoints (whole run, by p95)",
        "",
        "| Endpoint | Requests | Mean ms | p95 ms | p99 ms | Errors |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, s in rows[:SLOWEST]:
        out.append(
            f"| `{name}` | {s.count:,} | {ms(s.mean)} | {ms(s.p95)} | {ms(s.p99)} "
            f"| {s.failures:,} ({pct(s.error_rate)}) |"
        )

    by_class: dict[str, list[Request]] = defaultdict(list)
    for r in measured:
        by_class[r.cls].append(r)
    out += [
        "",
        "### Per user class (whole run)",
        "",
        "| Class | Requests | req/s | Mean ms | p95 ms | p99 ms | Errors |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for cls in sorted(by_class):
        s = summarise(by_class[cls], span)
        out.append(
            f"| {cls} | {s.count:,} | {s.rps:.1f} | {ms(s.mean)} | {ms(s.p95)} "
            f"| {ms(s.p99)} | {s.failures:,} ({pct(s.error_rate)}) |"
        )

    failures: dict[tuple[str, str], int] = defaultdict(int)
    for r in measured:
        if not r.ok:
            failures[(r.name, r.error or str(r.status))] += 1
    out += ["", "### Errors", ""]
    if not failures:
        out.append("No request failed.")
    else:
        out += ["| Endpoint | Error | Count |", "|---|---|---:|"]
        for (name, error), count in sorted(failures.items(), key=lambda i: -i[1]):
            out.append(f"| `{name}` | {error} | {count:,} |")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    sys.stdout.write(report(Path(argv[0])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
