"""Settings of model-registry-service (ADR-0042 rule 2)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "contracts"


@dataclass(frozen=True, slots=True)
class Settings:
    """Read once at start."""

    contracts_dir: Path

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            contracts_dir=Path(
                os.environ.get("MOSAIQ_CONTRACTS_DIR", str(DEFAULT_CONTRACTS_DIR))
            )
        )
