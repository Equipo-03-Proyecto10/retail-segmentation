"""What every service image answers, read from its own contract (F13-09).

ADR-0035 fixes the representations and the shared error codes, ADR-0036 the
token checks and ADR-0037 how each Redis failure is felt. The operations and
their `x-mosaiq-roles` come from the contract, so the same cases run against
every service and against both kits' example images.

The two Redis cases stop a container and come last, in file order.
"""

from __future__ import annotations

import os

import pytest

from tests.conformance import tokens
from tests.conformance.harness import (
    Client,
    Contract,
    Operation,
    Sessions,
    Signer,
    container_ctl,
    wait_for,
)


def _v1(contract: Contract) -> list[Operation]:
    return [op for op in contract.operations if op.path.startswith("/v")]


@pytest.fixture(scope="module")
def probe(contract: Contract) -> Operation:
    """A protected operation that needs no body, a GET when there is one."""

    candidates = [op for op in _v1(contract) if op.protected and op.roles]
    if not candidates:
        pytest.skip("the contract declares no protected operation")
    return min(
        candidates,
        key=lambda op: (op.method != "GET", op.body, len(op.required()), op.path),
    )


@pytest.fixture(scope="module")
def writer(contract: Contract) -> Operation:
    candidates = [op for op in _v1(contract) if op.protected and op.roles and op.body]
    if not candidates:
        pytest.skip("the contract declares no operation that takes a body")
    return min(candidates, key=lambda op: (len(op.required()), op.path, op.method))


def reads(contract: Contract) -> list[Operation]:
    """Every protected GET that needs no parameter, so it can answer 200."""

    return [
        op
        for op in _v1(contract)
        if op.method == "GET" and op.protected and op.roles and not op.required()
    ]


def live(signer: Signer, sessions: Sessions, role: str) -> str:
    return signer.sign(signer.claims(role, sessions.open()))


def accepted(status: int) -> bool:
    return status not in (401, 403, 404, 405, 406, 415, 503) and status < 500


# Meta routes ----------------------------------------------------------------


def test_the_served_contract_is_the_repository_file_byte_for_byte(
    client: Client, contract: Contract
) -> None:
    served = client.call("GET", "/openapi.yaml", accept=None)
    assert served.status == 200
    assert served.body == (contract.directory / "openapi.yaml").read_bytes()


def test_the_served_schemas_are_the_repository_files_byte_for_byte(
    client: Client, contract: Contract
) -> None:
    schemas = {"/schema.xsd": contract.xsd}
    for versioned in contract.directory.glob(contract.directory.name + ".v*.xsd"):
        schemas["/schema." + versioned.name.split(".", 1)[1]] = versioned
    for route, path in schemas.items():
        served = client.call("GET", route, accept=None)
        assert served.status == 200, route
        assert served.body == path.read_bytes(), route


def test_liveness_answers_without_a_token(client: Client) -> None:
    assert client.call("GET", "/health/live", accept=None).status in (200, 204)


# Tokens (ADR-0036) -------------------------------------------------------------


def test_a_live_token_of_an_allowed_role_is_accepted(
    client: Client, probe: Operation, signer: Signer, sessions: Sessions
) -> None:
    # Without this, every 401 below could be a token the image never accepts.
    token = live(signer, sessions, probe.allowed_role)
    reply = client.call(probe.method, probe.url(), token=token)
    assert accepted(reply.status), (reply.status, reply.body)


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_no_token_is_invalid_token(
    client: Client, probe: Operation, accept: str
) -> None:
    reply = client.call(probe.method, probe.url(), accept=accept)
    assert (reply.status, reply.code()) == (401, "invalid_token")


def _forged(case: str, signer: Signer, sid: str, role: str) -> str:
    claims = signer.claims(role, sid)
    if case == "alg_none":
        return tokens.unsigned({"alg": "none", "typ": "JWT", "kid": signer.kid}, claims)
    if case == "hs256_keyed_with_the_public_key":
        header = {"alg": "HS256", "typ": "JWT", "kid": signer.kid}
        return tokens.sign_hs256(header, claims, tokens.public_pem(signer.private_key))
    if case == "wrong_audience":
        return signer.sign(claims | {"aud": "another-api"})
    if case == "wrong_issuer":
        return signer.sign(claims | {"iss": "https://issuer.invalid/"})
    if case == "expired":
        return signer.sign(
            claims | {"iat": claims["iat"] - 1200, "exp": claims["iat"] - 600}
        )
    if case == "tampered_payload":
        header, _payload, signature = signer.sign(claims).split(".")
        other = signer.sign(claims | {"role": "ADMIN", "sub": claims["sub"][::-1]})
        return ".".join((header, other.split(".")[1], signature))
    if case == "unknown_kid":
        return signer.sign(claims, kid="conformance-unknown-kid")
    raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    [
        "alg_none",
        "hs256_keyed_with_the_public_key",
        "wrong_audience",
        "wrong_issuer",
        "expired",
        "tampered_payload",
        "unknown_kid",
    ],
)
def test_a_forged_or_stale_token_is_invalid_token(
    client: Client, probe: Operation, signer: Signer, sessions: Sessions, case: str
) -> None:
    token = _forged(case, signer, sessions.open(), probe.allowed_role)
    reply = client.call(probe.method, probe.url(), token=token)
    assert (reply.status, reply.code()) == (401, "invalid_token"), case


def test_a_token_whose_liveness_key_is_gone_is_session_revoked(
    client: Client, probe: Operation, signer: Signer, sessions: Sessions
) -> None:
    sid = sessions.open()
    token = signer.sign(signer.claims(probe.allowed_role, sid))
    assert accepted(client.call(probe.method, probe.url(), token=token).status)
    sessions.revoke(sid)
    reply = client.call(probe.method, probe.url(), token=token)
    assert (reply.status, reply.code()) == (401, "session_revoked")


def test_a_role_outside_each_operation_is_forbidden_role(
    client: Client, contract: Contract, signer: Signer, sessions: Sessions
) -> None:
    operations = [op for op in _v1(contract) if op.protected and op.roles]
    assert operations, "the contract declares no protected operation"
    for op in operations:
        token = live(signer, sessions, op.outside_role)
        reply = client.call(op.method, op.url(), token=token)
        assert (reply.status, reply.code()) == (403, "forbidden_role"), (
            op.operation_id,
            op.outside_role,
        )


# Representations (ADR-0035) ----------------------------------------------------


def test_every_parameterless_read_answers_json_and_valid_xml(
    client: Client, contract: Contract, signer: Signer, sessions: Sessions
) -> None:
    for op in reads(contract):
        token = live(signer, sessions, op.allowed_role)
        assert client.call("GET", op.path, token=token).status == 200, op.operation_id
        if op.xml_reply:
            # Client.call validates the body against the service's XSD.
            xml = client.call("GET", op.path, token=token, accept="application/xml")
            assert xml.status == 200, op.operation_id


def test_an_unsupported_accept_is_406(
    client: Client, probe: Operation, signer: Signer, sessions: Sessions
) -> None:
    token = live(signer, sessions, probe.allowed_role)
    reply = client.call(probe.method, probe.url(), token=token, accept="text/csv")
    assert reply.status == 406
    if reply.body:
        assert reply.code() == "not_acceptable"


def test_an_unsupported_content_type_is_415(
    client: Client, writer: Operation, signer: Signer, sessions: Sessions
) -> None:
    token = live(signer, sessions, writer.allowed_role)
    reply = client.call(
        writer.method, writer.url(), token=token, content_type="text/csv", body=b"a,b\n"
    )
    assert (reply.status, reply.code()) == (415, "unsupported_media_type")


def test_a_body_over_64_kib_is_payload_too_large(
    client: Client,
    contract: Contract,
    probe: Operation,
    signer: Signer,
    sessions: Sessions,
) -> None:
    candidates = [op for op in _v1(contract) if op.protected and op.roles and op.body]
    target = candidates[0] if candidates else probe
    token = live(signer, sessions, target.allowed_role)
    reply = client.call(
        target.method,
        target.url(),
        token=token,
        content_type="application/json",
        body=b'{"pad": "' + b"a" * 66_560 + b'"}',
    )
    assert (reply.status, reply.code()) == (413, "payload_too_large")


def _billion_laughs() -> bytes:
    entities = ['<!ENTITY lol0 "lol">'] + [
        f'<!ENTITY lol{i} "{f"&lol{i - 1};" * 10}">' for i in range(1, 10)
    ]
    return (
        '<?xml version="1.0"?>\n<!DOCTYPE r [\n'
        + "\n".join(entities)
        + "\n]>\n<r>&lol9;</r>"
    ).encode()


XML_ATTACKS = {
    "doctype": b'<?xml version="1.0"?>\n<!DOCTYPE r [<!ELEMENT r ANY>]>\n<r/>',
    "billion_laughs": _billion_laughs(),
    "external_entity": (
        b'<?xml version="1.0"?>\n'
        b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>\n<r>&x;</r>'
    ),
}


@pytest.mark.parametrize("attack", sorted(XML_ATTACKS))
def test_a_dangerous_xml_body_is_malformed_body(
    client: Client, writer: Operation, signer: Signer, sessions: Sessions, attack: str
) -> None:
    token = live(signer, sessions, writer.allowed_role)
    reply = client.call(
        writer.method,
        writer.url(),
        token=token,
        content_type="application/xml",
        body=XML_ATTACKS[attack],
    )
    assert (reply.status, reply.code()) == (400, "malformed_body"), attack


def test_size_101_is_invalid_pagination(
    client: Client, contract: Contract, signer: Signer, sessions: Sessions
) -> None:
    paged = [op for op in _v1(contract) if op.protected and op.roles and op.paginated]
    if not paged:
        pytest.skip("the contract declares no paginated operation")
    for op in paged:
        token = live(signer, sessions, op.allowed_role)
        url = op.url()
        reply = client.call(
            op.method, url + ("&" if "?" in url else "?") + "size=101", token=token
        )
        assert (reply.status, reply.code()) == (
            422,
            "invalid_pagination",
        ), op.operation_id


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_an_unknown_route_is_not_found_in_both_formats(
    client: Client, accept: str
) -> None:
    reply = client.call("GET", "/v1/conformance-no-such-resource", accept=accept)
    assert reply.media_type == accept
    assert (reply.status, reply.code()) == (404, "not_found")


def test_an_undeclared_method_is_method_not_allowed(
    client: Client,
    contract: Contract,
    probe: Operation,
    signer: Signer,
    sessions: Sessions,
) -> None:
    declared = {op.method for op in contract.operations if op.path == probe.path}
    method = next(m for m in ("DELETE", "PUT", "PATCH", "POST") if m not in declared)
    token = live(signer, sessions, probe.allowed_role)
    reply = client.call(method, probe.url(), token=token)
    assert (reply.status, reply.code()) == (405, "method_not_allowed")


# Redis failures (ADR-0037); each restarts what it stopped -----------------------


def test_cached_reads_answer_200_with_redis_cache_stopped(
    client: Client, contract: Contract, signer: Signer, sessions: Sessions
) -> None:
    operations = reads(contract)
    if not operations:
        pytest.skip("the contract declares no parameterless read")
    container = os.environ["CONFORMANCE_REDIS_CACHE"]
    container_ctl("stop", container)
    try:
        for op in operations:
            token = live(signer, sessions, op.allowed_role)
            reply = client.call("GET", op.path, token=token)
            assert reply.status == 200, (op.operation_id, reply.status, reply.body)
    finally:
        container_ctl("start", container)


def test_authentication_fails_closed_with_redis_state_stopped(
    client: Client, probe: Operation, signer: Signer, sessions: Sessions
) -> None:
    token = live(signer, sessions, probe.allowed_role)
    assert accepted(client.call(probe.method, probe.url(), token=token).status)
    container_ctl("stop", sessions.container)
    try:
        reply = client.call(probe.method, probe.url(), token=token)
        assert (reply.status, reply.code()) == (503, "auth_unavailable")
    finally:
        container_ctl("start", sessions.container)
        wait_for(lambda: sessions.redis("PING") == "PONG", "redis-state")
