"""Every deployed service refuses every bad token (#459, ADR-0036).

The cases run against each service's probe: a protected operation from its
own contract, so a service added to the registry is covered as it deploys.
Each forged token starts from a live token auth signed for a role the
operation allows, so the one thing wrong with it is the thing the case names.

A wrong `iss`, a wrong `aud` and an expired `exp` need a valid signature, so
only a run that holds the signing key can make them: the local stack, with its
throwaway key (MOSAIQ_SIGNING_KEY). The instance's key never leaves auth.
There, the expired case waits for a real token to run out instead, and the
other two are covered by the local run and by every image's conformance suite.
"""

from __future__ import annotations

import time

import pytest

from tests.negative.stack import (
    Evidence,
    Service,
    Stack,
    claims_of,
    header_of,
    hs256_with_public_key,
    registry_names,
    resigned,
    run_last,
    signing_key,
    tampered_signature,
    unsigned,
)

NAMES = registry_names()


@pytest.fixture(scope="session")
def jwks(deployed_stack: Stack) -> dict[str, dict]:
    reply = deployed_stack.edge.call("GET", "/api/auth/v1/jwks.json")
    assert reply.status == 200, reply.outcome()
    return {key["kid"]: key for key in reply.json()["keys"]}


@pytest.fixture(scope="session")
def first_token(deployed_stack: Stack) -> str:
    """The ADMIN access token as it was signed when the run began."""
    return deployed_stack.sessions["ADMIN"].access


def _call(stack: Stack, service: Service, token: str | None):
    probe = service.probe()
    return stack.edge.call(probe.method, service.prefix + probe.url(), token=token)


# first_token is taken here, at the first case, so it is old by the last one.
@pytest.mark.usefixtures("first_token")
@pytest.mark.parametrize("name", NAMES)
def test_a_missing_or_forged_token_is_invalid_token(
    deployed_stack: Stack,
    services: dict[str, Service],
    evidence: Evidence,
    jwks: dict[str, dict],
    name: str,
) -> None:
    service = services[name]
    role = service.probe().allowed_role
    live = deployed_stack.token(role)
    jwk = jwks[header_of(live)["kid"]]
    cases = {
        "no token": None,
        "a tampered signature": tampered_signature(live),
        "alg: none": unsigned(live),
        "HS256 keyed with the public key": hs256_with_public_key(live, jwk),
    }
    key = signing_key()
    if key:
        now = int(time.time())
        cases |= {
            "a wrong iss": resigned(live, key, iss="https://issuer.invalid/"),
            "a wrong aud": resigned(live, key, aud="another-api"),
            "an expired token": resigned(live, key, iat=now - 1200, exp=now - 600),
        }
    else:
        for case in ("a wrong iss", "a wrong aud"):
            evidence.not_run(
                name,
                case,
                "It needs auth's private key, which never leaves auth: the local "
                "stack's run and each image's CI conformance suite cover it.",
            )

    # The live token itself must pass, or every 401 below proves nothing.
    baseline = _call(deployed_stack, service, live)
    assert baseline.status not in (401, 403), (name, baseline.outcome())

    held = True
    for case, token in cases.items():
        reply = _call(deployed_stack, service, token)
        held &= evidence.check(name, case, reply, 401, "invalid_token")
    assert held, [r for r in evidence.rows if r.area == name and not r.held]


@run_last
def test_an_access_token_past_its_exp_is_invalid_token(
    deployed_stack: Stack,
    services: dict[str, Service],
    evidence: Evidence,
    first_token: str,
) -> None:
    """Without the key, wait for a token auth signed to expire, then use it."""
    if signing_key():
        pytest.skip("covered by the re-signed expired token in the case above")
    wait = claims_of(first_token)["exp"] + 5 - time.time()
    if wait > 0:
        time.sleep(wait)
    held = True
    for name in NAMES:
        reply = _call(deployed_stack, services[name], first_token)
        held &= evidence.check(
            name, "a token auth signed, after its exp", reply, 401, "invalid_token"
        )
    assert held, [r for r in evidence.rows if not r.held]
