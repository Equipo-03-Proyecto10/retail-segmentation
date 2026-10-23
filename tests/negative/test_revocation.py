"""A signed-out session is refused by every service on its very next request (#459).

One real session, signed out through auth, calls each registered service's
probe. Before the sign-out each call must pass authentication, so the 401
after it is the revocation. Every service checks the liveness key on Redis
before it looks at the role (ADR-0037), so one session serves every probe.

On the local stack, customers-service, which runs no relay and no consumer,
also shows that its refusal read no database: PostgreSQL logs every
statement of svc_customers for the duration of the case, and none appears
between two marker statements around the refused request. That needs
MOSAIQ_DB_CONTAINER and MOSAIQ_CUSTOMERS_CONTAINER, the local stack's
containers, and changes logging on that disposable database only.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid

import pytest

from tests.negative.stack import Evidence, Service, Stack, registry_names, require

NAMES = registry_names()


def _probe(stack: Stack, service: Service, token: str):
    probe = service.probe()
    return stack.edge.call(probe.method, service.prefix + probe.url(), token=token)


def test_a_signed_out_session_is_refused_everywhere(
    deployed_stack: Stack, services: dict[str, Service], evidence: Evidence
) -> None:
    session = deployed_stack.fresh_session("ADMIN")
    token = session.token()
    for name in NAMES:
        before = _probe(deployed_stack, services[name], token)
        assert before.status != 401, (name, before.outcome())

    require(session.sign_out(), 204)

    held = True
    for name in NAMES:
        reply = _probe(deployed_stack, services[name], token)
        held &= evidence.check(
            name, "the next request after sign-out", reply, 401, "session_revoked"
        )
    assert held, [r for r in evidence.rows if not r.held]


def _psql(container: str, *statements: str) -> str:
    # Each -c runs on its own, in one session: ALTER SYSTEM refuses a
    # transaction block, and SET lasts for the commands after it.
    command = ["docker", "exec", container, "psql", "-X", "-q", "-U", "postgres"]
    command += ["-d", "retail", "-v", "ON_ERROR_STOP=1"]
    for statement in statements:
        command += ["-c", statement]
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout


def _mark(container: str) -> str:
    """Log a bound marker value, so the log can be cut between two of them."""
    marker = "f18-04-mark-" + uuid.uuid4().hex
    script = "SET log_statement = 'all';\nSELECT $1::text \\bind " + marker + " \\g\n"
    command = ["docker", "exec", "-i", container, "psql", "-X", "-q", "-U", "postgres"]
    command += ["-d", "retail", "-v", "ON_ERROR_STOP=1", "-f", "-"]
    subprocess.run(command, input=script, capture_output=True, text=True, check=True)
    return marker


def _restart(container: str, stack: Stack) -> None:
    subprocess.run(["docker", "restart", container], capture_output=True, check=True)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        reply = stack.edge.call("GET", "/api/customers/health/ready", accept=None)
        if 200 <= reply.status < 300:
            return
        time.sleep(1)
    pytest.fail("customers-service did not get ready again within 90 s")


def _statements_between(container: str, first: str, second: str) -> list[str]:
    logs = subprocess.run(
        ["docker", "logs", "--since", "15m", container],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = (logs.stdout + logs.stderr).splitlines()
    start = next(i for i, line in enumerate(lines) if first in line)
    end = next(i for i, line in enumerate(lines) if second in line)
    return [
        line
        for line in lines[start:end]
        if "user=svc_customers" in line and "statement:" in line
    ]


def test_customers_refuses_a_revoked_token_with_redis_alone(
    deployed_stack: Stack, services: dict[str, Service], evidence: Evidence
) -> None:
    db = os.environ.get("MOSAIQ_DB_CONTAINER")
    customers = os.environ.get("MOSAIQ_CUSTOMERS_CONTAINER")
    if deployed_stack.is_instance or not db or not customers:
        evidence.not_run(
            "customers",
            "no statement logged for a revoked token",
            "It changes the database's logging, so it runs on the local stack "
            "only, with MOSAIQ_DB_CONTAINER and MOSAIQ_CUSTOMERS_CONTAINER.",
        )
        pytest.skip("local stack only, with its container names")

    service = services["customers"]
    _psql(
        db,
        "ALTER SYSTEM SET log_line_prefix = '%m [%p] user=%u '",
        "SELECT pg_reload_conf()",
        "ALTER ROLE svc_customers SET log_statement = 'all'",
    )
    try:
        # The role's setting reaches new connections only: restart the pool.
        _restart(customers, deployed_stack)
        session = deployed_stack.fresh_session("ADMIN")
        token = session.token()

        # Control: an accepted read logs its statements, so the log is on.
        first = _mark(db)
        accepted = _probe(deployed_stack, service, token)
        second = _mark(db)
        logged = _statements_between(db, first, second)

        require(session.sign_out(), 204)
        third = _mark(db)
        refused = _probe(deployed_stack, service, token)
        time.sleep(1)
        fourth = _mark(db)
        silent = _statements_between(db, third, fourth)
    finally:
        _psql(
            db,
            "ALTER ROLE svc_customers RESET log_statement",
            "ALTER SYSTEM RESET log_line_prefix",
            "SELECT pg_reload_conf()",
        )
        _restart(customers, deployed_stack)

    held = evidence.record(
        "customers",
        "statements logged for an accepted read (control)",
        "at least one",
        f"{len(logged)}",
        bool(logged) and 200 <= accepted.status < 300,
    )
    held &= evidence.check(
        "customers", "the revoked token's request", refused, 401, "session_revoked"
    )
    held &= evidence.record(
        "customers", "statements logged for that request", "0", f"{len(silent)}"
    )
    assert held, (accepted.outcome(), logged[:3], refused.outcome(), silent[:3])
