"""bench/locust/report.py's definitions, on a synthetic run (F29-15, #1191)."""

from __future__ import annotations

import csv
import gzip
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "locust_report", ROOT / "bench/locust/report.py"
)
report = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = report
spec.loader.exec_module(report)


def write_run(directory: Path, *, gz: bool = False) -> Path:
    """Two steps of 30 s, then a 40 s recovery.

    Step 1 answers in 20 ms. Step 2 answers in 1,500 ms with 5% failures, so it
    is the saturation point. The recovery's first 10 s are still slow; the
    window after them is back to 20 ms.
    """
    rows = []
    for second in range(30):
        for k in range(10):
            rows.append([1000 + second + k / 10, "web", "GET /a", 20.0, 1, 200, ""])
    for second in range(30):
        for k in range(10):
            ok = 0 if k == 0 and second % 2 == 0 else 1
            rows.append([1030 + second + k / 10, "web", "GET /b", 1500.0, ok, 503, ""])
    for second in range(40):
        for k in range(5):
            ms = 900.0 if second < 10 else 20.0
            rows.append([1060 + second + k / 5, "web", "GET /a", ms, 1, 200, ""])
    phases = [
        [990, "sign-in", 0],
        [1000, "step-1", 10],
        [1030, "step-2", 20],
        [1060, "recovery", 5],
        [1100, "end", 0],
    ]

    def dump(name: str, header: list[str], data: list[list]) -> None:
        path = directory / (name + (".gz" if gz else ""))
        opener = gzip.open if gz else open
        with opener(path, "wt", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(data)

    dump("requests.csv", ["t", "class", "name", "ms", "ok", "status", "error"], rows)
    dump("phases.csv", ["t", "phase", "users"], phases)
    return directory


def test_percentile_is_nearest_rank():
    assert report.percentile([1, 2, 3, 4], 50) == 2
    assert report.percentile([1, 2, 3, 4], 99) == 4
    assert report.percentile(list(range(1, 101)), 95) == 95


def test_saturation_is_the_first_step_not_sustained(tmp_path):
    text = report.report(write_run(tmp_path))
    assert "Saturation at **20 users**" in text
    assert "The last sustained step was **10 users**" in text
    assert "p99 1,500 ms > 1,000 ms" in text


def test_recovery_ends_at_the_first_window_back_under_the_threshold(tmp_path):
    text = report.report(write_run(tmp_path))
    assert "Recovered **20 s** after the drop." in text


def test_the_steady_window_leaves_the_spawn_out(tmp_path):
    requests, phases = report.load(write_run(tmp_path))
    start, end = report.steady(phases[0])
    assert (start, end) == (1010.0, 1030.0)


def test_gzipped_runs_read_the_same(tmp_path):
    plain = tmp_path / "plain"
    zipped = tmp_path / "zipped"
    plain.mkdir()
    zipped.mkdir()
    assert report.report(write_run(plain)) == report.report(write_run(zipped, gz=True))
