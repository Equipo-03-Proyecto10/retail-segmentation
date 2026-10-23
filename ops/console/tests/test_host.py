import json
from pathlib import Path

from app.host import read_snapshot

PS = {
    "Names": "mosaiq-stores-stores-1",
    "Labels": "com.docker.compose.project=mosaiq-stores,"
    "com.docker.compose.service=stores",
    "State": "running",
    "Status": "Up 2 hours",
    "Image": "ghcr.io/equipo-03-proyecto10/mosaiq-stores:0.3.1",
}
STATS = {
    "Name": "mosaiq-stores-stores-1",
    "CPUPerc": "0.42%",
    "MemUsage": "12.5MiB / 48MiB",
    "MemPerc": "26.04%",
}


def write(path: Path, taken_at: float) -> Path:
    path.write_text(
        json.dumps(
            {
                "taken_at": taken_at,
                "ps": [PS],
                "stats": [STATS],
                "restarts": {"mosaiq-stores-stores-1": 2},
            }
        )
    )
    return path


def test_containers_are_keyed_by_compose_project(tmp_path):
    snapshot = read_snapshot(write(tmp_path / "c.json", 1000), now=1010)
    container = snapshot.containers["mosaiq-stores"]
    assert not snapshot.stale
    assert (container.state, container.restarts) == ("running", 2)
    assert (container.cpu_percent, container.mem_percent) == (0.42, 26.04)
    assert (container.mem_used, container.mem_limit) == ("12.5MiB", "48MiB")


def test_an_old_snapshot_is_stale(tmp_path):
    assert read_snapshot(write(tmp_path / "c.json", 1000), now=1061).stale


def test_a_missing_or_broken_snapshot_is_stale_not_fatal(tmp_path):
    missing = read_snapshot(tmp_path / "absent.json")
    assert missing.stale and missing.error == "no snapshot yet"
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert read_snapshot(broken).error == "unreadable snapshot: JSONDecodeError"
