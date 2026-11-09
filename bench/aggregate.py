# ruff: noqa: E501
"""Turns a bench run's raw k6 JSON summaries and samples into the report's tables.

    python3 bench/aggregate.py rates  <run-dir> <percent>   # prints each cell's saturation rates
    python3 bench/aggregate.py rate   <run-dir> <fmt> <scenario>   # the measured rate, one integer
    python3 bench/aggregate.py report <run-dir>             # Markdown tables for the report

<run-dir> is bench/results/<run>, written by bench/run.sh, or a directory whose
subdirectories are such runs (CI runs one shard per scenario and format on its
own runner; `report` reads them all). The saturation criterion is stated in
bench/README.md and implemented in `sustained`.

A run holds:
    facts.txt                          what the run was, and where
    calibration/cal<k>-<build>-<fmt>-<scn>.json   one k6 summary per calibration pass
                                       (a run before F15-03 has <build>-<fmt>-<scn>.json, one pass)
    runs/rep<n>-<build>-<fmt>-<scn>.json          one k6 summary per measured run
    runs/rep<n>-<build>-<fmt>-<scn>.sample        its CPU, RSS and PSS (an older run has a .rss, RSS only)
    coldstart/<build>-<i>.txt          cold start and idle memory
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

BUILDS = ("flask", "rust")
LABEL = {"flask": "Flask", "rust": "Rust twin"}
# The saturation criterion (bench/README.md): a calibration step is sustained
# when all four hold.
MAX_DROPPED = 0.01  # at most 1% of the step's arrivals dropped (k6 had no VU free)
MAX_ERRORS = 0.01  # at most 1% failed requests and failed checks
MAX_P99_MS = 1000.0  # p99 latency within one second
SCENARIO_ORDER = ["list", "search", "filter", "detail"]


def facts(run: Path) -> dict[str, str]:
    result = {}
    for line in (run / "facts.txt").read_text().splitlines():
        key, _, value = line.partition("=")
        result[key] = value
    return result


def settings(info: dict[str, str]) -> dict[str, str]:
    """The run's `reps=5 warmup=30 ... step=10 passes=5` line, as a mapping."""
    result = {}
    for token in ("reps=" + info["reps"]).split():
        key, _, value = token.partition("=")
        result[key] = value
    return result


def shards(run: Path) -> list[Path]:
    """The run itself, or the runs one level below it (one per CI shard)."""
    if (run / "facts.txt").exists():
        return [run]
    found = sorted(p for p in run.iterdir() if (p / "facts.txt").exists())
    if not found:
        raise SystemExit(f"{run}: no facts.txt here or in a subdirectory")
    return found


def calibration_name(path: Path) -> tuple[str, str, str]:
    """(build, format, scenario) of a calibration file, with or without `cal<k>-`."""
    parts = path.stem.split("-")
    if len(parts) == 4 and parts[0].startswith("cal"):
        parts = parts[1:]
    build, fmt, scn = parts
    return build, fmt, scn


def calibration_files(shard: Path, build: str, fmt: str, scn: str) -> list[Path]:
    folder = shard / "calibration"
    old = folder / f"{build}-{fmt}-{scn}.json"
    new = sorted(
        folder.glob(f"cal*-{build}-{fmt}-{scn}.json"),
        key=lambda p: int(p.stem.split("-")[0][3:]),
    )
    return ([old] if old.exists() else []) + new


def locate(run: Path, fmt: str, scn: str) -> Path:
    """The shard that calibrated this format and scenario."""
    for shard in shards(run):
        if calibration_files(shard, BUILDS[0], fmt, scn):
            return shard
    raise SystemExit(f"{run}: no calibration for {scn} {fmt}")


def values(summary: dict, name: str) -> dict:
    metric = summary.get("metrics", {}).get(name)
    if metric is None:
        return {}
    return metric.get("values", metric)


def number(mapping: dict, *keys: str) -> float:
    for key in keys:
        if key in mapping:
            return float(mapping[key])
    return 0.0


def cells(run: Path) -> list[tuple[str, str]]:
    """Every (scenario, format) the run calibrated, scenarios in the report's order."""
    found = {
        calibration_name(path)[:0:-1]
        for shard in shards(run)
        for path in (shard / "calibration").glob("*.json")
    }
    return sorted(
        found,
        key=lambda c: (
            SCENARIO_ORDER.index(c[0]) if c[0] in SCENARIO_ORDER else 9,
            c[0],
            c[1],
        ),
    )


def ramp(summary: dict, rates: list[int], step: int) -> dict:
    """One calibration pass of one build: each step, and the saturation rate."""
    steps = []
    saturation = 0
    still_sustained = True
    for i, rate in enumerate(rates):
        tag = f"scenario:step{i}"
        requests = number(values(summary, f"http_reqs{{{tag}}}"), "count")
        dropped = number(values(summary, f"dropped_iterations{{{tag}}}"), "count")
        failed = number(values(summary, f"http_req_failed{{{tag}}}"), "rate", "value")
        checks = number(values(summary, f"checks{{{tag}}}"), "rate", "value")
        p99 = number(values(summary, f"http_req_duration{{{tag}}}"), "p(99)")
        sustained = (
            requests > 0
            and dropped <= MAX_DROPPED * rate * step
            and failed <= MAX_ERRORS
            and (1 - checks) <= MAX_ERRORS
            and p99 <= MAX_P99_MS
        )
        steps.append((rate, requests / step, dropped, failed, p99, sustained))
        if sustained and still_sustained:
            saturation = rate
        else:
            still_sustained = False
    return {"saturation": saturation, "steps": steps}


def calibration(run: Path, build: str, fmt: str, scn: str) -> dict:
    """The saturation rate of one build, format and scenario over its calibration passes.

    `saturation` is the median of the passes (the lower middle one when their
    number is even, so it is always a rate the ramp offered); `saturations`
    are the passes' own, in order, and `passes` their steps.
    """
    shard = locate(run, fmt, scn)
    info = facts(shard)
    rates = [int(r) for r in info["cal_rates"].split(",")]
    step = int(settings(info).get("step", "10"))
    passes = [
        ramp(json.loads(path.read_text()), rates, step)
        for path in calibration_files(shard, build, fmt, scn)
    ]
    if not passes:
        raise SystemExit(f"{shard}: no calibration for {build} {scn} {fmt}")
    saturations = [p["saturation"] for p in passes]
    return {
        "saturation": statistics.median_low(saturations),
        "saturations": saturations,
        "passes": passes,
    }


def measured_rate(run: Path, fmt: str, scn: str, percent: float) -> int:
    lower = min(calibration(run, b, fmt, scn)["saturation"] for b in BUILDS)
    if lower == 0:
        # Not even the lowest step was sustained: a broken build or stack, not a rate.
        raise SystemExit(f"{scn} {fmt}: a build sustained no calibration step")
    return max(1, int(lower * percent / 100))


def spread_ints(samples: list[int]) -> str:
    if len(samples) == 1:
        return str(samples[0])
    return f"{statistics.median_low(samples)} ({min(samples)}-{max(samples)})"


def cmd_rates(run: Path, percent: float) -> None:
    print(
        f"saturation rates (requests/s), median of the calibration passes with the range, and the measured rate at {percent:g}% of the lower median"
    )
    for scn, fmt in cells(run):
        cal = {b: calibration(run, b, fmt, scn) for b in BUILDS}
        rate = measured_rate(run, fmt, scn, percent)
        print(
            f"{scn:7} {fmt:4} flask={spread_ints(cal['flask']['saturations'])} "
            f"rust={spread_ints(cal['rust']['saturations'])} measured={rate}"
        )


def read_sample(path: Path) -> dict[str, float]:
    result = {}
    for line in path.read_text().splitlines():
        key, _, value = line.partition("=")
        if key:
            result[key] = float(value)
    return result


def runs(run: Path, build: str, fmt: str, scn: str) -> list[dict]:
    result = []
    shard = locate(run, fmt, scn)
    measure_s = float(settings(facts(shard))["measure"])
    for path in sorted((shard / "runs").glob(f"rep*-{build}-{fmt}-{scn}.json")):
        summary = json.loads(path.read_text())
        tag = "{scenario:measure}"
        duration = values(summary, "http_req_duration" + tag)
        count = number(values(summary, "http_reqs" + tag), "count")
        if count == 0:
            # A missing metric reads as 0; never let it into the medians.
            raise SystemExit(f"{path}: no requests in the measured phase")
        row = {
            "name": path.stem,
            "p50": number(duration, "med"),
            "p95": number(duration, "p(95)"),
            "p99": number(duration, "p(99)"),
            "requests": count,
            "failed": number(values(summary, "http_req_failed" + tag), "rate", "value"),
            "checks": number(values(summary, "checks" + tag), "rate", "value"),
            "dropped": number(values(summary, "dropped_iterations" + tag), "count"),
            "rss": None,
            "pss": None,
            "cpu_pct": None,
            "cpu_us_per_req": None,
            "throttled_pct": None,
        }
        sample, legacy = path.with_suffix(".sample"), path.with_suffix(".rss")
        if sample.exists():
            data = read_sample(sample)
            window = data["window_s"]
            # CPU time the container used over the window, as a share of one CPU,
            # and per request (the requests the window held: the measured phase's
            # count scaled to the window).
            in_window = count * window / measure_s
            row.update(
                rss=data["rss_peak_kb"],
                pss=data["pss_peak_kb"],
                cpu_pct=data["cpu_usage_usec"] / (window * 1e6) * 100,
                cpu_us_per_req=data["cpu_usage_usec"] / in_window,
                throttled_pct=(
                    data["cpu_nr_throttled"] / data["cpu_nr_periods"] * 100
                    if data["cpu_nr_periods"]
                    else 0.0
                ),
            )
        elif legacy.exists():
            row["rss"] = int(legacy.read_text())
        result.append(row)
    return result


def spread(samples: list[float], unit: str = "", digits: int = 1) -> str:
    if not samples:
        return "n/a"
    med = statistics.median(samples)
    low, high = min(samples), max(samples)
    return f"{med:.{digits}f}{unit} ({low:.{digits}f}-{high:.{digits}f})"


def verdict(a: list[float], b: list[float]) -> str:
    """Flask against Rust: ranges that overlap are no difference."""
    if not a or not b:
        return "n/a"
    if min(a) <= max(b) and min(b) <= max(a):
        return "no difference"
    ratio = (
        statistics.median(b) / statistics.median(a)
        if statistics.median(a)
        else float("inf")
    )
    percent = abs(1 - ratio) * 100
    # Below 1% a whole number would read "Rust 0% higher" for ranges that do not overlap.
    shown = f"{percent:.0f}" if percent >= 1 else f"{percent:.1f}"
    return f"Rust {shown}% {'lower' if ratio < 1 else 'higher'}"


def column(rows: list[dict], key: str, scale: float = 1.0) -> list[float]:
    return [r[key] * scale for r in rows if r[key] is not None]


def print_table(
    data: dict, columns: list[tuple[str, str, int]], extra: dict | None = None
) -> None:
    """A two-build table with a Difference row; `extra` adds a column of preformatted cells."""
    extra = extra or {}
    heads = [c[0] for c in columns] + list(extra.get("heads", []))
    print("| Build | n | " + " | ".join(heads) + " |")
    print("|---|---|" + "---|" * len(heads))
    for build in BUILDS:
        cols = [spread(data[build][key], digits=d) for _, key, d in columns]
        cols += list(extra.get("cells", {}).get(build, []))
        print(f"| {LABEL[build]} | {data[build]['n']} | " + " | ".join(cols) + " |")
    cols = [verdict(data["flask"][key], data["rust"][key]) for _, key, _ in columns]
    cols += list(extra.get("difference", []))
    print("| Difference | | " + " | ".join(cols) + " |\n")


def sat_cell(values_: list[int]) -> str:
    return spread_ints(values_)


def print_machines(run: Path) -> None:
    print("### Machines\n")
    print(
        "| Shard | CPU | cores (threads per core) | kernel | runner image | memory limit | cgroup read-back (Flask) | cgroup read-back (Rust twin) |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for shard in shards(run):
        info = facts(shard)
        print(
            f"| {shard.name} | {info['cpu']} | {info['cores']} ({info.get('threads_per_core', 'n/a')}) "
            f"| {info['os']} | {info.get('runner', 'n/a')} | {info['memory_limit']} "
            f"| {info.get('cgroup_flask', 'n/a')} | {info.get('cgroup_rust', 'n/a')} |"
        )
    print()
    for key in (
        "products_image",
        "twin_image",
        "k6_image_digest",
        "postgres",
        "commit",
        "dirty",
    ):
        distinct = sorted({facts(shard).get(key, "n/a") for shard in shards(run)})
        print(
            f"- {key}: "
            + (distinct[0] if len(distinct) == 1 else "DIFFERS: " + "; ".join(distinct))
        )
    print()


def cmd_report(run: Path) -> None:
    first = facts(shards(run)[0])
    percent = float(settings(first)["fraction"])
    print(
        "Each cell is the median over the repetitions (or calibration passes), with the min-max range in parentheses."
    )
    print(
        "Latency in milliseconds. Rates in requests per second. Error rate is the share of"
    )
    print(
        "requests that were not an expected status (404 is expected for the detail scenario's"
    )
    print("unknown ids).\n")
    print_machines(run)
    latency = [
        ("p50 (ms)", "p50", 1),
        ("p95 (ms)", "p95", 1),
        ("p99 (ms)", "p99", 1),
        ("RPS", "rps", 1),
        ("error rate (%)", "err", 3),
    ]
    resources = [
        ("CPU (% of one CPU)", "cpu", 1),
        ("CPU (us per request)", "cpu_req", 0),
        ("throttled periods (%)", "throttled", 1),
        ("RSS peak (MiB)", "rss", 1),
        ("PSS peak (MiB)", "pss", 1),
    ]
    have_resources = False
    for scn, fmt in cells(run):
        shard = locate(run, fmt, scn)
        measure_s = float(settings(facts(shard))["measure"])
        cal = {b: calibration(run, b, fmt, scn) for b in BUILDS}
        rate = measured_rate(run, fmt, scn, percent)
        data = {}
        for build in BUILDS:
            rows = runs(run, build, fmt, scn)
            data[build] = {
                "p50": column(rows, "p50"),
                "p95": column(rows, "p95"),
                "p99": column(rows, "p99"),
                "rps": [r["requests"] / measure_s for r in rows],
                "err": column(rows, "failed", 100),
                "cpu": column(rows, "cpu_pct"),
                "cpu_req": column(rows, "cpu_us_per_req"),
                "throttled": column(rows, "throttled_pct"),
                "rss": column(rows, "rss", 1 / 1024),
                "pss": column(rows, "pss", 1 / 1024),
                "sat": cal[build]["saturations"],
                "n": len(rows),
            }
        print(f"### {scn}, {fmt.upper()} (offered {rate} req/s)\n")
        sat = {
            "heads": ["saturation (req/s)"],
            "cells": {b: [sat_cell(data[b]["sat"])] for b in BUILDS},
            "difference": [verdict(data["flask"]["sat"], data["rust"]["sat"])],
        }
        print_table(data, latency, sat)
        # A build that sustained the ramp's top step in some pass has a
        # saturation rate that is a lower bound, not a measurement.
        top = max(int(r) for r in facts(shard)["cal_rates"].split(","))
        for build in BUILDS:
            if top in data[build]["sat"]:
                print(
                    f"{LABEL[build]} sustained the top of the ramp ({top} req/s) in "
                    f"{data[build]['sat'].count(top)} of {len(data[build]['sat'])} passes: "
                    "its saturation rate is a lower bound.\n"
                )
        if any(data[b]["cpu"] for b in BUILDS):
            have_resources = True
            print_table(data, resources)
    # The tables' RPS rounds small drops away and their error rate leaves the
    # format checks out, so every measured run with either is listed here.
    print("### Dropped arrivals and failed checks in the measured runs\n")
    flagged = [
        f"- `{r['name']}`: {r['requests']:.0f} requests, dropped arrivals: {r['dropped']:.0f},"
        f" failed checks: {(1 - r['checks']) * 100:.3f}%"
        for scn, fmt in cells(run)
        for build in BUILDS
        for r in runs(run, build, fmt, scn)
        if r["dropped"] or r["checks"] < 1
    ]
    print("\n".join(flagged) if flagged else "None.")
    print()
    print("### Memory, image size and cold start\n")
    sizes = {
        "flask": int(first["products_image_size_bytes"]),
        "rust": int(first["twin_image_size_bytes"]),
    }
    idle_pss_known = any(
        "idle_pss_kb" in dict(p.split("=") for p in f.read_text().split())
        for shard in shards(run)
        for f in (shard / "coldstart").glob("*.txt")
    )
    heads = ["RSS idle (MiB)"] + (["PSS idle (MiB)"] if idle_pss_known else [])
    heads += ["RSS under load (MiB)"] + (
        ["PSS under load (MiB)"] if have_resources else []
    )
    print("| Build | " + " | ".join(heads) + " | image (MB) | cold start (ms) |")
    print("|---|" + "---|" * (len(heads) + 2))
    for build in BUILDS:
        cold = [
            dict(p.split("=") for p in f.read_text().split())
            for shard in shards(run)
            for f in sorted((shard / "coldstart").glob(f"{build}-*.txt"))
        ]
        rows = [r for scn, fmt in cells(run) for r in runs(run, build, fmt, scn)]
        cells_ = [spread([int(c["idle_rss_kb"]) / 1024 for c in cold])]
        if idle_pss_known:
            cells_.append(
                spread(
                    [int(c["idle_pss_kb"]) / 1024 for c in cold if "idle_pss_kb" in c]
                )
            )
        cells_.append(spread(column(rows, "rss", 1 / 1024)))
        if have_resources:
            cells_.append(spread(column(rows, "pss", 1 / 1024)))
        ms = [float(c["cold_start_ms"]) for c in cold]
        print(
            f"| {LABEL[build]} | "
            + " | ".join(cells_)
            + f" | {sizes[build] / 1e6:.1f} | {spread(ms, digits=0)} |"
        )
    print(
        "\nRSS and PSS are the sums over every process in the container (`docker top`, `/proc/<pid>/smaps_rollup`),"
    )
    print(
        "idle after a 10 s settle following a start, and under load the peak of the samples taken inside each measured"
    )
    print(
        "phase. The ranges are over every cold start of the build, and over every measured run of the build (all"
    )
    print("scenarios, formats and repetitions; every shard).")
    print("\n### Calibration\n")
    for scn, fmt in cells(run):
        for build in BUILDS:
            cal = calibration(run, build, fmt, scn)
            if len(cal["passes"]) == 1:
                line = ", ".join(
                    f"{r}:{'ok' if ok else 'X'}({ach:.0f}/s p99 {p99:.0f}ms)"
                    for r, ach, _, _, p99, ok in cal["passes"][0]["steps"]
                    if ach > 0
                )
                print(
                    f"- {scn} {fmt} {LABEL[build]}: saturation {cal['saturation']}; {line}"
                )
                continue
            # Several passes: the saturation of each, then how many passes
            # sustained each step (an unbroken run from the bottom is what
            # counts, so a step can be sustained and still lie above it).
            per_step = ", ".join(
                f"{rate}:{sum(p['steps'][i][5] for p in cal['passes'])}/{len(cal['passes'])}"
                for i, (rate, *_rest) in enumerate(cal["passes"][0]["steps"])
                if any(p["steps"][i][1] > 0 for p in cal["passes"])
            )
            print(
                f"- {scn} {fmt} {LABEL[build]}: saturation per pass {cal['saturations']}, "
                f"median {cal['saturation']}, range {min(cal['saturations'])}-{max(cal['saturations'])}; "
                f"passes that sustained each step: {per_step}"
            )


def main(argv: list[str]) -> int:
    try:
        command, run = argv[1], Path(argv[2])
        if command == "rates":
            cmd_rates(run, float(argv[3]))
        elif command == "rate":
            percent = float(settings(facts(locate(run, argv[3], argv[4])))["fraction"])
            print(measured_rate(run, argv[3], argv[4], percent))
        elif command == "report":
            cmd_report(run)
        else:
            raise IndexError
    except IndexError:
        # A wrong command or a missing argument; a missing key in the run's
        # data raises with its name instead of printing this.
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
