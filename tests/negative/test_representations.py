"""Every service refuses what it cannot represent or must not parse (#459, ADR-0035).

Each case uses a real token of a role the operation allows, so that the refusal
is the representation's and not the token's. The 65 KiB body is refused at the
edge (NGINX on the instance, the local gateway on the local stack) and, with
MOSAIQ_DIRECT_PORTS=1, by the service itself on 127.0.0.1:<port>, which only a
process on the same host can reach.
"""

from __future__ import annotations

import pytest

from tests.negative.stack import (
    Edge,
    Evidence,
    Operation,
    Service,
    Stack,
    env_bool,
    registry_names,
)

NAMES = registry_names()
OVERSIZED = b'{"pad": "' + b"a" * (65 * 1024) + b'"}'
DOCTYPE = b'<?xml version="1.0"?>\n<!DOCTYPE r [<!ELEMENT r ANY>]>\n<r/>'
ENTITY = b'<?xml version="1.0"?>\n<!DOCTYPE r [<!ENTITY x "expanded">]>\n<r>&x;</r>'


def _token(stack: Stack, op: Operation) -> str | None:
    return stack.token(op.allowed_role) if op.protected and op.roles else None


@pytest.mark.parametrize("name", NAMES)
def test_an_unsupported_accept_is_not_acceptable(
    deployed_stack: Stack, services: dict[str, Service], evidence: Evidence, name: str
) -> None:
    service = services[name]
    probe = service.probe()
    reply = deployed_stack.edge.call(
        probe.method,
        service.prefix + probe.url(),
        token=_token(deployed_stack, probe),
        accept="text/csv",
    )
    assert evidence.check(name, "Accept: text/csv", reply, 406, "not_acceptable")


@pytest.mark.parametrize("name", NAMES)
def test_bodies_that_must_be_refused_are(
    deployed_stack: Stack, services: dict[str, Service], evidence: Evidence, name: str
) -> None:
    service = services[name]
    op = service.body_operation()
    if op is None:
        evidence.not_applicable(name, "the body cases", "Its contract takes no body.")
        pytest.skip(f"{name} takes no body")
    path = service.prefix + op.url()
    token = _token(deployed_stack, op)
    where = f"{op.method} {op.path}"

    held = evidence.check(
        name,
        f"Content-Type: text/plain on {where}",
        deployed_stack.edge.call(
            op.method, path, token=token, content_type="text/plain", body=b"x"
        ),
        415,
        "unsupported_media_type",
    )
    for case, body in (("a DOCTYPE", DOCTYPE), ("an entity declaration", ENTITY)):
        held &= evidence.check(
            name,
            f"an XML body with {case} on {where}",
            deployed_stack.edge.call(
                op.method, path, token=token, content_type="application/xml", body=body
            ),
            400,
            "malformed_body",
        )
    held &= evidence.check(
        name,
        f"65 KiB on {where}, through the edge",
        deployed_stack.edge.call(
            op.method,
            path,
            token=token,
            content_type="application/json",
            body=OVERSIZED,
        ),
        413,
        "payload_too_large",
    )
    if env_bool("MOSAIQ_DIRECT_PORTS"):
        direct = Edge(f"http://127.0.0.1:{service.port}")
        held &= evidence.check(
            name,
            f"65 KiB on {where}, on 127.0.0.1:{service.port}",
            direct.call(
                op.method,
                op.url(),
                token=token,
                content_type="application/json",
                body=OVERSIZED,
            ),
            413,
            "payload_too_large",
        )
    assert held, [r for r in evidence.rows if r.area == name and not r.held]


@pytest.mark.parametrize("name", NAMES)
def test_pagination_outside_its_range_is_invalid_pagination(
    deployed_stack: Stack, services: dict[str, Service], evidence: Evidence, name: str
) -> None:
    service = services[name]
    paged = service.paginated()
    if not paged:
        evidence.not_applicable(
            name, "pagination", "Its contract has no paginated read."
        )
        pytest.skip(f"{name} has no paginated read")
    held = True
    for op in paged:
        url = service.prefix + op.url()
        joiner = "&" if "?" in url else "?"
        for query in ("page=0", "size=101"):
            reply = deployed_stack.edge.call(
                op.method, url + joiner + query, token=_token(deployed_stack, op)
            )
            held &= evidence.check(
                name,
                f"{query} on {op.method} {op.path}",
                reply,
                422,
                "invalid_pagination",
            )
    assert held, [r for r in evidence.rows if r.area == name and not r.held]
