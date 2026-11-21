"""The two copies of the hand-computed RFM fixture are one fixture (F29-01, #886).

A service never imports `web/` or reaches outside its own directory (ADR-0045), so
rfm-engine's test reads its own copy of `tests/fixtures/rfm-hand/fixture.json`. Both
tests are only worth anything if they check the same hand values; this fails when
either copy is edited alone.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "tests/fixtures/rfm-hand/fixture.json"
ENGINE = ROOT / "services/rfm-engine/tests/fixtures/rfm-hand/fixture.json"


def test_rfm_engine_checks_the_same_fixture_as_the_web() -> None:
    assert ENGINE.read_bytes() == WEB.read_bytes()
