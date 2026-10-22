# ruff: noqa: E501
"""Turns a bench run's raw k6 JSON summaries into the report's tables.

    python3 bench/aggregate.py rates  <run-dir> <percent>   # prints each cell's saturation rates
    python3 bench/aggregate.py rate   <run-dir> <fmt> <scenario>   # the measured rate, one integer
    python3 bench/aggregate.py report <run-dir>             # Markdown tables for the report

<run-dir> is bench/results/<run>, written by bench/run.sh. The saturation
criterion is stated in bench/README.md and implemented in `sustained`.
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


def facts(run: Path) -> dict[str, str]:
    result = {}
    for line in (run / "facts.txt").read_text().splitlines():
        key, _, value = line.partition("=")
        result[key] = value
    return result


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


def cells(run: Path) -> tuple[list[str], list[str]]:
    names = sorted(p.stem for p in (run / "calibration").glob("*.json"))
    formats = sorted({n.split("-")[1] for n in names})
    scenarios = []
    for name in names:
        scn = name.split("-", 2)[2]
        if scn not in scenarios:
            scenarios.append(scn)
    order = ["list", "search", "filter", "detail"]
    return formats, sorted(scenarios, key=lambda s: order.index(s) if s in order else 9)


def calibration(run: Path, build: str, fmt: str, scn: str) -> dict:
    """The saturation rate of one build, format and scenario."""
    info = facts(run)
    rates = [int(r) for r in info["cal_rates"].split(",")]
    step = int(info["reps"].split("step=")[1]) if "step=" in info["reps"] else 10
    summary = json.loads(
        (run / "calibration" / f"{build}-{fmt}-{scn}.json").read_text()
    )
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


def measured_rate(run: Path, fmt: str, scn: str, percent: float) -> int:
    lower = min(calibration(run, b, fmt, scn)["saturation"] for b in BUILDS)
    return max(1, int(lower * percent / 100))


def cmd_rates(run: Path, percent: float) -> None:
    formats, scenarios = cells(run)
    print(
        f"saturation rates (requests/s) and the measured rate at {percent:g}% of the lower"
    )
    for scn in scenarios:
        for fmt in formats:
            sat = {b: calibration(run, b, fmt, scn)["saturation"] for b in BUILDS}
            rate = measured_rate(run, fmt, scn, percent)
            print(
                f"{scn:7} {fmt:4} flask={sat['flask']:5} rust={sat['rust']:5} measured={rate}"
            )


def runs(run: Path, build: str, fmt: str, scn: str) -> list[dict]:
    result = []
    for path in sorted((run / "runs").glob(f"rep*-{build}-{fmt}-{scn}.json")):
        summary = json.loads(path.read_text())
        tag = "{scenario:measure}"
        duration = values(summary, "http_req_duration" + tag)
        count = number(values(summary, "http_reqs" + tag), "count")
        rss = path.with_suffix(".rss")
        result.append(
            {
                "p50": number(duration, "med"),
                "p95": number(duration, "p(95)"),
                "p99": number(duration, "p(99)"),
                "requests": count,
                "failed": number(
                    values(summary, "http_req_failed" + tag), "rate", "value"
                ),
                "checks": number(values(summary, "checks" + tag), "rate", "value"),
                "dropped": number(values(summary, "dropped_iterations" + tag), "count"),
                "rss": int(rss.read_text()) if rss.exists() else None,
            }
        )
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
    return f"Rust {abs(1 - ratio) * 100:.0f}% {'lower' if ratio < 1 else 'higher'}"


def cmd_report(run: Path) -> None:
    info = facts(run)
    formats, scenarios = cells(run)
    percent = float(info["reps"].split("fraction=")[1].split()[0])
    measure_s = float(info["reps"].split("measure=")[1].split()[0])
    print(
        "Each cell is the median over the repetitions, with the min-max range in parentheses."
    )
    print(
        "Latency in milliseconds. Rates in requests per second. Error rate is the share of"
    )
    print(
        "requests that were not an expected status (404 is expected for the detail scenario's"
    )
    print("unknown ids).\n")
    metrics = [
        ("p50 (ms)", "p50", 1),
        ("p95 (ms)", "p95", 1),
        ("p99 (ms)", "p99", 1),
        ("RPS", "rps", 1),
        ("error rate (%)", "err", 3),
    ]
    for scn in scenarios:
        for fmt in formats:
            sat = {b: calibration(run, b, fmt, scn)["saturation"] for b in BUILDS}
            rate = measured_rate(run, fmt, scn, percent)
            data = {}
            for build in BUILDS:
                rows = runs(run, build, fmt, scn)
                data[build] = {
                    "p50": [r["p50"] for r in rows],
                    "p95": [r["p95"] for r in rows],
                    "p99": [r["p99"] for r in rows],
                    "rps": [r["requests"] / measure_s for r in rows],
                    "err": [r["failed"] * 100 for r in rows],
                    "n": len(rows),
                }
            print(f"### {scn}, {fmt.upper()} (offered {rate} req/s)\n")
            print(
                "| Build | n | "
                + " | ".join(m[0] for m in metrics)
                + " | saturation (req/s) |"
            )
            print("|---|---|" + "---|" * (len(metrics) + 1))
            for build in BUILDS:
                cols = [spread(data[build][key], digits=d) for _, key, d in metrics]
                print(
                    f"| {LABEL[build]} | {data[build]['n']} | "
                    + " | ".join(cols)
                    + f" | {sat[build]} |"
                )
            cols = [
                verdict(data["flask"][key], data["rust"][key]) for _, key, _ in metrics
            ]
            sat_verdict = (
                "no difference"
                if sat["flask"] == sat["rust"]
                else (
                    f"Rust {abs(sat['rust'] / sat['flask'] - 1) * 100:.0f}% "
                    f"{'higher' if sat['rust'] > sat['flask'] else 'lower'}"
                    if sat["flask"]
                    else "n/a"
                )
            )
            print("| Difference | | " + " | ".join(cols) + f" | {sat_verdict} |\n")
    print("### Memory, image size and cold start\n")
    print(
        "| Build | RSS idle (MiB) | RSS under load (MiB) | image (MB) | cold start (ms) |"
    )
    print("|---|---|---|---|---|")
    size = {
        "flask": int(info["products_image_size_bytes"]),
        "rust": int(info["twin_image_size_bytes"]),
    }
    for build in BUILDS:
        cold = [
            dict(p.split("=") for p in f.read_text().split())
            for f in sorted((run / "coldstart").glob(f"{build}-*.txt"))
        ]
        idle = [int(c["idle_rss_kb"]) / 1024 for c in cold]
        ms = [float(c["cold_start_ms"]) for c in cold]
        load = [
            r["rss"] / 1024
            for fmt in formats
            for scn in scenarios
            for r in runs(run, build, fmt, scn)
            if r["rss"]
        ]
        print(
            f"| {LABEL[build]} | {spread(idle)} | {spread(load)} | {size[build] / 1e6:.1f} | {spread(ms, digits=0)} |"
        )
    print(
        "\nRSS under load is the peak of samples taken every 10 s during each measured run;"
    )
    print(
        "its range is over every measured run of the build (all scenarios, formats and repetitions)."
    )
    print("\n### Calibration steps\n")
    for scn in scenarios:
        for fmt in formats:
            for build in BUILDS:
                cal = calibration(run, build, fmt, scn)
                line = ", ".join(
                    f"{r}:{'ok' if ok else 'X'}({ach:.0f}/s p99 {p99:.0f}ms)"
                    for r, ach, _, _, p99, ok in cal["steps"]
                    if ach > 0
                )
                print(
                    f"- {scn} {fmt} {LABEL[build]}: saturation {cal['saturation']}; {line}"
                )


def main(argv: list[str]) -> int:
    try:
        command, run = argv[1], Path(argv[2])
        if command == "rates":
            cmd_rates(run, float(argv[3]))
        elif command == "rate":
            percent = float(facts(run)["reps"].split("fraction=")[1].split()[0])
            print(measured_rate(run, argv[3], argv[4], percent))
        elif command == "report":
            cmd_report(run)
        else:
            raise IndexError
    except (IndexError, KeyError):
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
