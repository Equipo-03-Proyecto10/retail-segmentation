"""Prints one bench access token (ADR-0042) for the k6 runs.

Signs an ANALYST token with the bench key and opens its session in redis-state,
as the conformance suite does; both live for an hour. Reads
CONFORMANCE_PRIVATE_KEY, CONFORMANCE_KID, CONFORMANCE_REDIS_STATE and
REDIS_OPS_PASSWORD.

    python3 bench/mint_token.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conformance.harness import Sessions, Signer  # noqa: E402


def main() -> int:
    signer = Signer(
        Path(os.environ["CONFORMANCE_PRIVATE_KEY"]), os.environ["CONFORMANCE_KID"]
    )
    sessions = Sessions(os.environ["CONFORMANCE_REDIS_STATE"])
    sid = sessions.open()
    print(signer.sign(signer.claims("ANALYST", sid, exp=int(time.time()) + 3300)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
