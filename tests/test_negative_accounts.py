"""The negative suite's local accounts hold the roles they claim (#754).

`tests/negative/stack.py` signs in as one seeded account per role. If an
account's seeded role differs from the role the suite names, `test_auth.py`
compares the wrong role and `test_roles.py` calls with the wrong outside role,
so the mapping is checked against the seed itself.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.conformance.harness import ROLE_CODES
from tests.negative.stack import local_accounts

SEED = Path(__file__).resolve().parents[1] / "sql/02_seed_30_per_table.sql"


def _seeded_role_codes() -> dict[int, str]:
    """`role_id` -> code, as the seed inserts them."""
    text = SEED.read_text(encoding="utf-8")
    block = text[text.index("INSERT INTO role") :]
    block = block[: block.index(";")]
    return {
        int(role_id): code
        for role_id, code in re.findall(r"^\((\d+),'([A-Z_]+)'", block, re.M)
    }


def _seeded_role_id(number: int) -> int:
    """The role the seed gives user `number`, from its two app_user inserts."""
    text = SEED.read_text(encoding="utf-8")
    assert "CASE WHEN n = 1 THEN 1 ELSE 2 + ((n - 2) % 6) END" in text
    assert "       n - 23,\n" in text and "FROM generate_series(31,33) n;" in text
    if number == 1:
        return 1
    if 2 <= number <= 30:
        return 2 + ((number - 2) % 6)
    assert 31 <= number <= 33, number
    return number - 23


def test_there_is_one_local_account_for_every_role() -> None:
    assert set(local_accounts()) == set(ROLE_CODES)


def test_each_local_account_holds_the_role_the_suite_names() -> None:
    codes = _seeded_role_codes()
    for role, account in local_accounts().items():
        number = int(account.user_id.rsplit("-", 1)[1])
        assert account.email.split("@")[0] in {"admin", f"user{number}"}
        assert codes[_seeded_role_id(number)] == role, (role, account.email)
