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
    # How long a training may run before it is closed as `timed_out` (F22-12).
    training_timeout_seconds: int
    # How often the drift monitor tries a pass; 0 turns it off (F23-09). A pass
    # writes one report per day at most: its window ends at 03:00 UTC.
    drift_interval_seconds: int

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            contracts_dir=Path(
                os.environ.get("MOSAIQ_CONTRACTS_DIR", str(DEFAULT_CONTRACTS_DIR))
            ),
            training_timeout_seconds=int(
                os.environ.get("TRAINING_TIMEOUT_SECONDS", "900")
            ),
            drift_interval_seconds=int(
                os.environ.get("DRIFT_INTERVAL_SECONDS", "3600")
            ),
        )
