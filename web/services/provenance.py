"""Data provenance vocabulary (ADR-0052).

Every sale and every segmentation run records where its data came from, in
``data_origin``: ``SEEDED`` for the demonstration seed, ``OBSERVED`` for
everything a real writer records (the CSV import, sales-service). The schema's
CHECK constraints hold the same two values; these constants are how the web
names them. The pages that mark figures ``Synthetic`` (#560) build on this
module.
"""

from __future__ import annotations

OBSERVED = "OBSERVED"
SEEDED = "SEEDED"

DATA_ORIGINS = (OBSERVED, SEEDED)
