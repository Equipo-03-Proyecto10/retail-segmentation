"""docker/distributed/auth-keys.sh is safe to re-run (#943).

The script writes next to itself, so the test copies it, with the helper it
calls, into a scratch tree and never touches the real key directory.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(script)], capture_output=True, text=True)


def test_second_run_succeeds_and_keeps_the_key_pair(tmp_path: Path) -> None:
    script = tmp_path / "docker/distributed/auth-keys.sh"
    helper = tmp_path / "tests/conformance/tokens.py"
    script.parent.mkdir(parents=True)
    helper.parent.mkdir(parents=True)
    shutil.copy(ROOT / "docker/distributed/auth-keys.sh", script)
    shutil.copy(ROOT / "tests/conformance/tokens.py", helper)
    keys = script.parent / "keys"

    first = _run(script)
    assert first.returncode == 0, first.stderr
    private = (keys / "auth-local-dev.pem").read_bytes()
    jwks = (keys / "jwks.json").read_bytes()

    second = _run(script)
    assert second.returncode == 0, second.stderr
    assert (keys / "auth-local-dev.pem").read_bytes() == private
    assert (keys / "jwks.json").read_bytes() == jwks
    assert sorted(p.name for p in keys.iterdir()) == [
        "auth-local-dev.pem",
        "jwks.json",
    ]
    for name in ("auth-local-dev.pem", "jwks.json"):
        mode = stat.S_IMODE((keys / name).stat().st_mode)
        assert mode == 0o444, (name, oct(mode))
