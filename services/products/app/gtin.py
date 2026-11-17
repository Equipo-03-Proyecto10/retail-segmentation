"""The GTIN rule of the products contract: digits only, a GTIN-8, -12, -13 or
-14, with a valid check digit (GS1).

sql/01_schema.sql holds the same rule as ``products.gtin_is_valid``, which the
``product_barcode_gtin_check`` CHECK uses as the backstop. The service refuses
first so that a bad check digit is 422 ``invalid_barcode`` before any write;
``tests/test_details.py`` compares the two on the same inputs.
"""

from __future__ import annotations

import re

_GTIN = re.compile(r"[0-9]{8}|[0-9]{12,14}")


def check_digit(body: str) -> int:
    """From the right of the body the digits weigh 3, 1, 3, 1, ..."""

    total = sum(
        int(digit) * (3 if position % 2 == 0 else 1)
        for position, digit in enumerate(reversed(body))
    )
    return (10 - total % 10) % 10


def is_valid(code: str) -> bool:
    return _GTIN.fullmatch(code) is not None and check_digit(code[:-1]) == int(code[-1])
