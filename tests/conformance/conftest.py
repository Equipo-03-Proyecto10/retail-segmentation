"""Fixtures of the conformance suite; harness.py lists what it reads."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conformance.harness import (
    BASE_URL,
    SERVICE,
    Client,
    Contract,
    Sessions,
    Signer,
    load_contract,
)


def pytest_collection_modifyitems(config, items) -> None:
    if BASE_URL and SERVICE:
        return
    skip = pytest.mark.skip(reason="CONFORMANCE_BASE_URL and CONFORMANCE_SERVICE")
    for item in items:
        if Path(str(item.fspath)).parent == Path(__file__).parent:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def contract() -> Contract:
    return load_contract()


@pytest.fixture(scope="session")
def client(contract: Contract) -> Client:
    return Client(BASE_URL, contract)


@pytest.fixture(scope="session")
def sessions() -> Sessions:
    return Sessions(os.environ["CONFORMANCE_REDIS_STATE"])


@pytest.fixture(scope="session")
def signer() -> Signer:
    return Signer(
        Path(os.environ["CONFORMANCE_PRIVATE_KEY"]), os.environ["CONFORMANCE_KID"]
    )
