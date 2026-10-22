"""Fixtures: the console against the real registry, with no poller thread."""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app import create_app

REGISTRY = Path(__file__).resolve().parents[3] / "contracts" / "registry.yaml"


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    return create_app(
        {
            "REGISTRY": REGISTRY,
            "SNAPSHOT": tmp_path / "containers.json",
            "START_POLLER": False,
        }
    )
