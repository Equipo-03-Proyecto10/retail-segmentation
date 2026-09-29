"""web/db/search.ilike_pattern (#295): a user's own % or _ must match
literally, not be read back as an ILIKE wildcard."""

from __future__ import annotations

from web.db.search import ilike_pattern


def test_a_percent_sign_is_escaped() -> None:
    assert ilike_pattern("%") == "%\\%%"


def test_an_underscore_is_escaped() -> None:
    assert ilike_pattern("_") == "%\\_%"


def test_a_backslash_is_escaped_first_so_it_cannot_unescape_what_follows() -> None:
    assert ilike_pattern("\\%") == "%\\\\\\%%"


def test_a_plain_term_is_still_wrapped_for_a_substring_match() -> None:
    assert ilike_pattern("ada") == "%ada%"
