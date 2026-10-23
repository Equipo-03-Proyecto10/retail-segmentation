"""The boundaries under the services refuse what they must (#459).

- redis-state stopped (local stack, MOSAIQ_REDIS_STATE_CONTAINER): every
  protected route answers 503 auth_unavailable and never 200, and liveness
  stays up (ADR-0037).
- The database (MOSAIQ_DB_EXEC, a command prefix that runs its arguments as
  the database owner, in a directory holding this tree's contracts/db/): the
  grant and boundary checks print nothing, and two service roles are refused
  another boundary's table and function (ADR-0034, ADR-0044). The direct
  INSERT of a second administrator, in a rolled-back transaction, runs on the
  local stack's disposable database only.
- Redis (MOSAIQ_CHECK_REDIS=1): deploy/infra/check-redis.sh prints nothing,
  with its own environment (REDIS_OPS_PASSWORD and the CLI variables).
- The network (MOSAIQ_PROBE_HOST, from outside the instance): no service,
  Redis or MongoDB port accepts a connection.
"""

from __future__ import annotations

import os
import shlex
import socket
import subprocess
import time

import pytest

from tests.negative.stack import (
    ROOT,
    Evidence,
    Service,
    Stack,
    env_bool,
    registry_names,
    run_last,
)

NAMES = registry_names()


@run_last
def test_redis_state_stopped_fails_closed(
    deployed_stack: Stack, services: dict[str, Service], evidence: Evidence
) -> None:
    container = os.environ.get("MOSAIQ_REDIS_STATE_CONTAINER")
    if deployed_stack.is_instance or not container:
        evidence.not_run(
            "redis-state",
            "stopped",
            "It stops a shared container, so it runs on the local stack only, "
            "with MOSAIQ_REDIS_STATE_CONTAINER.",
        )
        pytest.skip("local stack only, with its container name")

    # Tokens first: nothing can be signed in or refreshed once it is stopped.
    probes = {}
    for name in NAMES:
        probe = services[name].probe()
        probes[name] = (probe, deployed_stack.token(probe.allowed_role))
    subprocess.run(["docker", "stop", container], capture_output=True, check=True)
    held = True
    try:
        for name, (probe, token) in probes.items():
            prefix = services[name].prefix
            reply = deployed_stack.edge.call(
                probe.method, prefix + probe.url(), token=token
            )
            held &= evidence.check(
                name,
                "a protected route, redis-state stopped",
                reply,
                503,
                "auth_unavailable",
            )
            live = deployed_stack.edge.call("GET", prefix + "/health/live", accept=None)
            held &= evidence.record(
                name,
                "/health/live, redis-state stopped",
                "2xx",
                str(live.status),
                200 <= live.status < 300,
            )
    finally:
        subprocess.run(["docker", "start", container], capture_output=True, check=True)
        _wait_for_sessions(deployed_stack)
        # Redis replays its AOF refusing auth's transactions, so the run's
        # sessions do not come back (#714). Runs last for that reason.
        deployed_stack.forget_sessions()
    assert held, [r for r in evidence.rows if not r.held]


def _wait_for_sessions(stack: Stack) -> None:
    """Until auth can read redis-state again: any answer but 503."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if stack.sessions["ADMIN"].current().status != 503:
            return
        time.sleep(1)
    pytest.fail("sessions did not answer again within 60 s of redis-state starting")


def _db(*arguments: str, script: str | None = None) -> subprocess.CompletedProcess:
    prefix = os.environ.get("MOSAIQ_DB_EXEC")
    if not prefix:
        pytest.skip("MOSAIQ_DB_EXEC is not set; the database checks are disabled")
    return subprocess.run(
        shlex.split(prefix) + list(arguments),
        input=script,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def _psql(script: str) -> subprocess.CompletedProcess:
    return _db(
        "psql",
        "-X",
        "-q",
        "-v",
        "ON_ERROR_STOP=1",
        "-d",
        "retail",
        "-f",
        "-",
        script=script,
    )


def test_the_grant_and_boundary_checks_print_nothing(
    deployed_stack: Stack, evidence: Evidence
) -> None:
    grants = _db("bash", "contracts/db/check_grants.sh")
    boundaries = _db(
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-d",
        "retail",
        "-f",
        "contracts/db/check_boundaries.sql",
    )
    held = True
    for case, result in (
        ("contracts/db/check_grants.sh", grants),
        ("contracts/db/check_boundaries.sql", boundaries),
    ):
        output = (result.stdout + result.stderr).strip()
        held &= evidence.record(
            "database",
            case,
            "exit 0, prints nothing",
            f"exit {result.returncode}, "
            + (f"{len(output.splitlines())} lines" if output else "prints nothing"),
            result.returncode == 0 and not output,
        )
    assert held, (grants.stdout, grants.stderr, boundaries.stdout, boundaries.stderr)


# Whole fixed scripts: a role name cannot be a bound parameter, and nothing is
# composed into SQL text.
@pytest.mark.parametrize(
    ("case", "script", "refusal"),
    [
        (
            "as svc_sales: SELECT 1 FROM public.inventory",
            "BEGIN;\nSET LOCAL ROLE svc_sales;\n"
            "SELECT 1 FROM public.inventory LIMIT 1;\nROLLBACK;\n",
            "permission denied for table inventory",
        ),
        (
            "as svc_stock: SELECT api_stock.consume_v1(1::smallint, 1, 1)",
            "BEGIN;\nSET LOCAL ROLE svc_stock;\n"
            "SELECT api_stock.consume_v1(1::smallint, 1, 1);\nROLLBACK;\n",
            "permission denied for function consume_v1",
        ),
    ],
)
def test_a_service_role_is_refused_another_boundary(
    deployed_stack: Stack, evidence: Evidence, case: str, script: str, refusal: str
) -> None:
    result = _psql(script)
    first = (
        result.stderr.strip().splitlines()[0] if result.stderr.strip() else "accepted"
    )
    held = evidence.record(
        "database",
        case,
        refusal,
        first,
        result.returncode != 0 and refusal in result.stderr,
    )
    assert held, (result.returncode, result.stderr)


def test_a_direct_insert_of_a_second_administrator_is_refused(
    deployed_stack: Stack, evidence: Evidence
) -> None:
    if deployed_stack.is_instance:
        evidence.not_run(
            "database",
            "a direct INSERT of a second administrator",
            "It writes to the live accounts table, even if rolled back, so it "
            "runs on the local stack's disposable database only.",
        )
        pytest.skip("local stack only")
    result = _psql(
        "BEGIN;\n"
        "INSERT INTO app_user (role_id, name, email, password_hash)"
        " VALUES (1, 'F18-04 probe', 'f18-04-probe@example.test', 'not-a-hash');\n"
        "ROLLBACK;\n"
    )
    held = evidence.record(
        "database",
        "a direct INSERT of a second administrator, as postgres",
        "refused by ux_app_user_single_administrator",
        result.stderr.strip().splitlines()[0] if result.stderr.strip() else "accepted",
        result.returncode != 0 and "ux_app_user_single_administrator" in result.stderr,
    )
    assert held, (result.returncode, result.stderr)


def test_the_redis_acl_checks_print_nothing(
    deployed_stack: Stack, evidence: Evidence
) -> None:
    if not env_bool("MOSAIQ_CHECK_REDIS"):
        evidence.not_run(
            "redis",
            "deploy/infra/check-redis.sh",
            "It checks the instance's mosaiq-infra project, so the instance run "
            "covers it (MOSAIQ_CHECK_REDIS).",
        )
        pytest.skip("MOSAIQ_CHECK_REDIS is not set")
    result = subprocess.run(
        ["bash", "deploy/infra/check-redis.sh"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    output = (result.stdout + result.stderr).strip()
    held = evidence.record(
        "redis",
        "deploy/infra/check-redis.sh (ACL DRYRUN per user, settings)",
        "exit 0, prints nothing",
        f"exit {result.returncode}, "
        + (f"{len(output.splitlines())} lines" if output else "prints nothing"),
        result.returncode == 0 and not output,
    )
    assert held, output


INTERNAL_PORTS = [*range(5001, 5016), 6379, 27017]


def _connect(host: str, port: int, seconds: float) -> str:
    try:
        with socket.create_connection((host, port), timeout=seconds):
            return "open"
    except TimeoutError:
        return f"filtered (no answer in {seconds:g} s)"
    except ConnectionRefusedError:
        return "closed (refused)"
    except OSError as error:
        return f"unreachable ({error.strerror or error})"


def test_the_probe_reaches_the_instance(outside_evidence: Evidence) -> None:
    """A port the firewall allows answers, so a filtered port means something.

    Without this, a probe from a machine with no route to the instance would
    report every port filtered. SSH is open to the world (docs/infra.md).
    """
    host = os.environ["MOSAIQ_PROBE_HOST"]
    port = int(os.environ.get("MOSAIQ_PROBE_CONTROL_PORT", "22"))
    held = outside_evidence.record(
        "network",
        f"control: tcp/{port} on {host}, which the firewall allows",
        "open",
        _connect(host, port, 6),
    )
    assert held, "the probe cannot reach the instance from here"


@pytest.mark.parametrize("port", INTERNAL_PORTS)
def test_internal_ports_are_closed_from_outside(
    outside_evidence: Evidence, port: int
) -> None:
    host = os.environ["MOSAIQ_PROBE_HOST"]
    actual = _connect(host, port, 4)
    held = outside_evidence.record(
        "network",
        f"tcp/{port} on {host}",
        "closed or filtered",
        actual,
        actual != "open",
    )
    assert held, actual
