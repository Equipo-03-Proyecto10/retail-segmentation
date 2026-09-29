"""#345: the seeded segment bands must be able to produce every label.

The seed once generated bands whose monetary minimum was always 3, so 65 of the
125 (R, F, M) triples matched no rule and AT_RISK and HIBERNATING could never be
assigned. There is no PostgreSQL in the unit suite, so the seed is read as text:
the bands are literal rows, and what matters about them is arithmetic. CI's
"SQL scripts run clean from empty" job loads the same file into a real database.
"""

from __future__ import annotations

import itertools
import re
from collections import Counter
from pathlib import Path

_SEED = Path("sql/02_seed_30_per_table.sql").read_text(encoding="utf-8")
_LABELS = ["CHAMPION", "LOYAL", "POTENTIAL", "AT_RISK", "HIBERNATING", "LOST"]
_ROW = re.compile(r"\(\s*(\d+),\s*'(RULE_\d+)',\s*((?:\d+,\s*){5}\d+)\s*\)")


def _bands() -> dict[int, tuple[int, ...]]:
    block = _SEED[_SEED.index("INSERT INTO segment_rule") :]
    block = block[: block.index(";")]
    return {
        int(rule_id): tuple(int(v) for v in values.split(","))
        for rule_id, _code, values in _ROW.findall(block)
    }


def _label_of(segment_id: int) -> str:
    """`segment.label_code` is the vocabulary cycled by id (see the seed)."""
    return _LABELS[(segment_id - 1) % 6]


def _matches(band: tuple[int, ...], r: int, f: int, m: int) -> bool:
    r_min, r_max, f_min, f_max, m_min, m_max = band
    return r_min <= r <= r_max and f_min <= f <= f_max and m_min <= m <= m_max


def test_the_seed_carries_thirty_literal_bands() -> None:
    assert sorted(_bands()) == list(range(1, 31))


def test_every_triple_matches_exactly_one_band() -> None:
    """A partition: no triple falls to the fallback and none is ambiguous, so
    the lowest-segment_id tie-break never has to decide."""
    bands = _bands()
    uncovered, ambiguous = [], []
    for triple in itertools.product(range(1, 6), repeat=3):
        hits = [i for i, band in bands.items() if _matches(band, *triple)]
        if not hits:
            uncovered.append(triple)
        elif len(hits) > 1:
            ambiguous.append((triple, hits))

    assert uncovered == [], f"{len(uncovered)} triples match no band: {uncovered[:5]}"
    assert ambiguous == [], f"overlapping bands: {ambiguous[:3]}"


def test_every_label_is_assigned_to_some_triple() -> None:
    bands = _bands()
    reached = Counter(
        _label_of(next(i for i, band in bands.items() if _matches(band, *triple)))
        for triple in itertools.product(range(1, 6), repeat=3)
    )

    assert set(reached) == set(_LABELS), f"unreachable: {set(_LABELS) - set(reached)}"


def test_every_segment_is_reachable() -> None:
    """A band nothing lands in is filler, which the seed minimum is not for."""
    bands = _bands()
    hit = {
        next(i for i, band in bands.items() if _matches(band, *triple))
        for triple in itertools.product(range(1, 6), repeat=3)
    }

    assert hit == set(bands)


def test_the_better_the_scores_the_better_the_label() -> None:
    """Sanity of meaning: the top triple is a champion, the bottom one is lost,
    and a recent, frequent customer is never called at risk."""
    bands = _bands()

    def label(r: int, f: int, m: int) -> str:
        return _label_of(next(i for i, b in bands.items() if _matches(b, r, f, m)))

    assert label(5, 5, 5) == "CHAMPION"
    assert label(1, 1, 1) == "LOST"
    assert label(1, 5, 5) == "AT_RISK"
    assert label(5, 5, 1) == "CHAMPION"
