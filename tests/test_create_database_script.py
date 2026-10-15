"""00_create_database.sql never prints a password, and runs twice on one cluster.

A `SELECT set_config(..., :'app_password', ...)` returns the value as a result
row, and psql prints it to whoever runs the script — on the instance, into the
operator's terminal. The password reaches the DO block through `SET` instead,
and so does every service role's (F13-07, #434).

The deploy and an operator may both run the script, so a second run on the
same cluster must re-apply role attributes and passwords instead of failing on
what the first one created. Those tests need a scratch cluster and are marked
`postgres`.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "sql/00_create_database.sql"
SCRIPT = SCRIPT_PATH.read_text()


def _statements_with_the_password() -> list[str]:
    return [
        line.strip()
        for line in SCRIPT.splitlines()
        if ":'app_password'" in line and not line.lstrip().startswith("--")
    ]


def test_the_password_is_only_ever_set_never_selected() -> None:
    assert _statements_with_the_password() == [
        "SET mosaiq.app_password = :'app_password';"
    ]


def test_the_password_leaves_the_session_after_the_role_is_set() -> None:
    assert SCRIPT.index("END\n$$;") < SCRIPT.index("RESET mosaiq.app_password;")


def test_service_passwords_are_only_ever_set_never_selected() -> None:
    uses = [
        line.strip()
        for line in SCRIPT.splitlines()
        if re.search(r":'svc_[a-z_]+_password'", line)
        and not line.lstrip().startswith("--")
    ]
    assert len(uses) == 16
    for use in uses:
        assert re.fullmatch(
            r"SET mosaiq\.(svc_[a-z_]+)_password = :'\1_password';", use
        ), use


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("localhost", 0))
        return probe.getsockname()[1]


@pytest.fixture
def scratch_cluster(tmp_path: Path, postgres_binaries: dict[str, str]):
    data = tmp_path / "data"
    subprocess.run(
        [postgres_binaries["initdb"], "-D", str(data), "-U", "postgres", "-A", "trust"],
        check=True,
        capture_output=True,
    )
    port = _free_port()
    # TCP only: a pytest tmp path is too long for a Unix socket.
    options = f"-c unix_socket_directories='' -c listen_addresses=localhost -p {port}"
    pg_ctl = postgres_binaries["pg_ctl"]
    log = str(tmp_path / "log")
    subprocess.run(
        [pg_ctl, "-D", str(data), "-l", log, "-o", options, "-w", "start"],
        check=True,
        capture_output=True,
    )
    env = dict(os.environ, PGHOST="localhost", PGPORT=str(port), PGUSER="postgres")
    env["PGDATABASE"] = "postgres"
    try:
        yield env
    finally:
        subprocess.run(
            [pg_ctl, "-D", str(data), "-m", "immediate", "stop"], capture_output=True
        )


def _psql(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", *args],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


@pytest.mark.postgres
def test_script_runs_twice_on_one_cluster(scratch_cluster: dict[str, str]) -> None:
    for run in (1, 2):
        result = _psql(scratch_cluster, "-f", str(SCRIPT_PATH))
        assert result.returncode == 0, f"run {run}: {result.stderr}"
        # Each fallback password is its role's name, so a printed one would
        # show up here.
        assert "svc_" not in result.stdout


@pytest.mark.postgres
def test_creates_the_service_and_definer_roles(
    scratch_cluster: dict[str, str],
) -> None:
    assert _psql(scratch_cluster, "-f", str(SCRIPT_PATH)).returncode == 0
    counts = _psql(
        scratch_cluster,
        "-d",
        "retail",
        "-tAc",
        "SELECT count(*) FILTER (WHERE rolname LIKE 'svc\\_%' AND rolcanlogin"
        "  AND has_database_privilege(oid, 'retail', 'CONNECT')),"
        " count(*) FILTER (WHERE rolname LIKE 'definer\\_%' AND NOT rolcanlogin),"
        " count(*) FILTER (WHERE (rolname LIKE 'svc\\_%' OR rolname LIKE 'definer\\_%')"
        "  AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication"
        "       OR rolbypassrls))"
        " FROM pg_roles",
    )
    assert counts.stdout.strip() == "16|10|0"
