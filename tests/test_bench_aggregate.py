# ruff: noqa: E501
"""bench/aggregate.py: the calibration passes, CPU and PSS, and the shard layout (F15-03).

The tables are the evidence of the comparison, so their arithmetic is pinned
here on small synthetic runs; nothing here starts Docker or k6.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "bench_aggregate", ROOT / "bench" / "aggregate.py"
)
aggregate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aggregate)

RATES = [10, 20, 40]
STEP = 10


def summary(tag_values: dict[str, dict[str, float]]) -> dict:
    return {
        "metrics": {name: {"values": vals} for name, vals in tag_values.items()},
    }


def calibration_summary(sustained_steps: int) -> dict:
    """A k6 summary whose first `sustained_steps` steps hold and the rest do not."""
    metrics: dict[str, dict[str, float]] = {}
    for i, rate in enumerate(RATES):
        tag = f"scenario:step{i}"
        ok = i < sustained_steps
        metrics[f"http_reqs{{{tag}}}"] = {"count": rate * STEP}
        metrics[f"dropped_iterations{{{tag}}}"] = {"count": 0 if ok else rate * STEP}
        metrics[f"http_req_failed{{{tag}}}"] = {"rate": 0.0}
        metrics[f"checks{{{tag}}}"] = {"rate": 1.0}
        metrics[f"http_req_duration{{{tag}}}"] = {"p(99)": 5.0}
    return summary(metrics)


def measured_summary(p50: float, requests: int) -> dict:
    tag = "{scenario:measure}"
    return summary(
        {
            "http_req_duration" + tag: {"med": p50, "p(95)": p50 * 2, "p(99)": p50 * 3},
            "http_reqs" + tag: {"count": requests},
            "http_req_failed" + tag: {"rate": 0.0},
            "checks" + tag: {"rate": 1.0},
            "dropped_iterations" + tag: {"count": 0},
        }
    )


def write_run(
    run: Path,
    *,
    sustained: dict[str, list[int]],
    fmt: str = "json",
    scn: str = "detail",
    sample: bool = True,
) -> None:
    """A run with one calibration pass per entry of `sustained[build]` and 3 repetitions."""
    run.mkdir(parents=True)
    (run / "facts.txt").write_text(
        "\n".join(
            [
                "cpu=Test CPU",
                "cores=4",
                "threads_per_core=2",
                "os=Test OS kernel 6.0 x86_64",
                "memory_limit=160m",
                "products_image_size_bytes=2000000",
                "twin_image_size_bytes=1000000",
                "reps=3 warmup=15 measure=60 fraction=70 step=10 passes=5 interval=10 margin=5",
                "cal_rates=" + ",".join(str(r) for r in RATES),
            ]
        )
        + "\n"
    )
    for sub in ("calibration", "runs", "coldstart"):
        (run / sub).mkdir()
    for build, passes in sustained.items():
        for k, steps in enumerate(passes, start=1):
            (run / "calibration" / f"cal{k}-{build}-{fmt}-{scn}.json").write_text(
                json.dumps(calibration_summary(steps))
            )
        for rep in (1, 2, 3):
            stem = f"rep{rep}-{build}-{fmt}-{scn}"
            p50 = 2.0 if build == "flask" else 1.0
            (run / "runs" / f"{stem}.json").write_text(
                json.dumps(measured_summary(p50, 60 * 20))
            )
            if sample:
                # 50 s window; Flask 10 CPU-seconds (20%), the twin 2.5 (5%).
                usec = 10_000_000 if build == "flask" else 2_500_000
                (run / "runs" / f"{stem}.sample").write_text(
                    f"window_s=50.0\nsamples=5\nrss_peak_kb={102400 if build == 'flask' else 20480}\n"
                    f"pss_peak_kb={81920 if build == 'flask' else 18432}\n"
                    f"cpu_usage_usec={usec}\ncpu_user_usec={usec - 1000}\ncpu_system_usec=1000\n"
                    f"cpu_nr_periods=500\ncpu_nr_throttled={50 if build == 'flask' else 0}\n"
                    "cpu_throttled_usec=0\n"
                )
        for i in (1, 2):
            (run / "coldstart" / f"{build}-{i}.txt").write_text(
                f"cold_start_ms=1000 idle_rss_kb=51200 idle_pss_kb={40960 if build == 'flask' else 10240}\n"
            )


def test_saturation_is_the_median_of_the_passes_and_keeps_the_range(tmp_path):
    run = tmp_path / "run"
    write_run(run, sustained={"flask": [1, 2, 1, 1, 3], "rust": [3, 3, 3, 3, 3]})
    flask = aggregate.calibration(run, "flask", "json", "detail")
    assert flask["saturations"] == [10, 20, 10, 10, 40]
    assert flask["saturation"] == 10
    rust = aggregate.calibration(run, "rust", "json", "detail")
    assert rust["saturation"] == 40
    # The measured rate is 70% of the lower of the two medians.
    assert aggregate.measured_rate(run, "json", "detail", 70) == 7


def test_an_even_number_of_passes_still_gives_a_rate_the_ramp_offered(tmp_path):
    run = tmp_path / "run"
    write_run(run, sustained={"flask": [1, 3], "rust": [3, 3]})
    assert aggregate.calibration(run, "flask", "json", "detail")["saturation"] == 10


def test_report_gives_cpu_pss_and_the_calibration_range(tmp_path, capsys):
    run = tmp_path / "run"
    write_run(run, sustained={"flask": [2, 2, 1, 2, 2], "rust": [3, 3, 3, 3, 3]})
    aggregate.cmd_report(run)
    out = capsys.readouterr().out
    # 10 s of CPU in a 50 s window is 20% of one CPU; 1200 requests in 60 s is
    # 1000 in the window, so 10,000 us each for Flask.
    assert (
        "| Flask | 3 | 20.0 (20.0-20.0) | 10000 (10000-10000) | 10.0 (10.0-10.0) |"
        in out
    )
    assert "| Rust twin | 3 | 5.0 (5.0-5.0) | 2500 (2500-2500) | 0.0 (0.0-0.0) |" in out
    assert "| Difference | | Rust 75% lower | Rust 75% lower |" in out
    # PSS sits beside RSS, 80 and 18 MiB, against 100 and 20.
    assert "80.0 (80.0-80.0)" in out and "18.0 (18.0-18.0)" in out
    assert "100.0 (100.0-100.0)" in out
    # The saturation column carries a median and a range: 20 (10-20) against 40.
    assert "20 (10-20)" in out
    assert "40 (40-40)" in out
    assert "Rust 100% higher" in out
    assert "| Flask | 40.0 (40.0-40.0) |" not in out
    assert "saturation per pass [20, 20, 10, 20, 20], median 20, range 10-20" in out
    # The summary row gives Flask / twin medians, and `=` only where ranges overlap.
    assert (
        "| detail JSON | 14 | 2.0 / 1.0 | 4.0 / 2.0 | 6.0 / 3.0 | 20.0 / 5.0 "
        "| 10000 / 2500 | 100 / 20 | 80 / 18 | 20 / 40 |"
    ) in out


def test_overlapping_ranges_are_reported_as_no_difference(tmp_path, capsys):
    run = tmp_path / "run"
    write_run(run, sustained={"flask": [2, 3, 2, 3, 3], "rust": [3, 3, 3, 3, 2]})
    aggregate.cmd_report(run)
    out = capsys.readouterr().out
    saturation = [line for line in out.splitlines() if line.startswith("| Difference")][
        0
    ]
    assert saturation.rstrip().endswith("| no difference |")


def test_shards_are_read_as_one_report(tmp_path, capsys):
    root = tmp_path / "results"
    write_run(
        root / "detail-json",
        sustained={"flask": [1] * 5, "rust": [3] * 5},
        fmt="json",
        scn="detail",
    )
    write_run(
        root / "list-xml",
        sustained={"flask": [2] * 5, "rust": [2] * 5},
        fmt="xml",
        scn="list",
    )
    assert aggregate.cells(root) == [("list", "xml"), ("detail", "json")]
    assert aggregate.measured_rate(root, "json", "detail", 70) == 7
    assert aggregate.measured_rate(root, "xml", "list", 70) == 14
    aggregate.cmd_report(root)
    out = capsys.readouterr().out
    assert "### list, XML (offered 14 req/s)" in out
    assert "### detail, JSON (offered 7 req/s)" in out
    assert "| detail-json | Test CPU | 4 (2) |" in out
    # Idle memory is over every cold start of every shard: 4 per build.
    assert "| Flask | 50.0 (50.0-50.0) | 40.0 (40.0-40.0) |" in out


def test_a_run_before_f15_03_still_reports(capsys):
    """F15-02's committed results have one calibration, RSS only and no CPU."""
    legacy = ROOT / "bench" / "results" / "2026-10-22-pi5"
    assert aggregate.calibration(legacy, "flask", "json", "list")["saturation"] == 160
    assert aggregate.measured_rate(legacy, "json", "list", 70) == 112
    aggregate.cmd_report(legacy)
    out = capsys.readouterr().out
    assert "| Flask | 5 | 7.2 (7.2-7.3) |" in out
    assert "| 116.2 (116.2-116.3) | 120.1 (120.0-120.1) | 281.3 |" in out
    assert "CPU (% of one CPU)" not in out


def test_a_run_with_no_requests_is_refused(tmp_path):
    run = tmp_path / "run"
    write_run(run, sustained={"flask": [3], "rust": [3]})
    (run / "runs" / "rep1-flask-json-detail.json").write_text(
        json.dumps(measured_summary(1.0, 0))
    )
    with pytest.raises(SystemExit):
        aggregate.runs(run, "flask", "json", "detail")


def test_a_saturation_at_the_top_of_the_ramp_is_a_lower_bound(tmp_path, capsys):
    run = tmp_path / "run"
    write_run(run, sustained={"flask": [1] * 5, "rust": [3] * 5})
    aggregate.cmd_report(run)
    out = capsys.readouterr().out
    assert "Rust twin sustained the top of the ramp (40 req/s) in 5 of 5 passes" in out
    assert "Flask sustained the top of the ramp" not in out


def test_a_difference_under_one_percent_keeps_its_decimal():
    assert aggregate.verdict([100.0, 100.1], [100.4, 100.5]) == "Rust 0.4% higher"
    assert aggregate.verdict([100.0, 101.0], [50.0, 51.0]) == "Rust 50% lower"
