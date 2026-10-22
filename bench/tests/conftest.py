"""The products read cases reuse the shared conformance suite's fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.conformance.conftest import (  # noqa: E402,F401
    client,
    contract,
    sessions,
    signer,
)
