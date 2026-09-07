"""Provisioning the real administrator on the instance (#107, F4-06).

The procedure is run against a real database in
docs/evidence/f4-06-real-administrator.md — including the two states a deployed
instance can be in, and the check that the published password stops opening
anything. What these tests hold in place is the part that must never drift:
where the password may come from, and what the command refuses.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from flask import Flask
from flask.cli import ScriptInfo

from web.app import create_app
from web.cli import (
    MINIMUM_PASSWORD_LENGTH,
    PASSWORD_VARIABLE,
    PUBLISHED_PASSWORD,
    _read_password,
    provision_administrator,
)
from web.config import Config

_GOOD_PASSWORD = "a-real-one-not-in-the-repo"


@pytest.fixture
def app() -> Flask:
    return create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused-by-test",
        ),
        database_connector=MagicMock(return_value=MagicMock()),
    )


# ---------- where the password may come from ----------


def test_the_password_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PASSWORD_VARIABLE, _GOOD_PASSWORD)

    assert _read_password() == _GOOD_PASSWORD


def test_without_the_variable_it_is_asked_for_and_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PASSWORD_VARIABLE, raising=False)

    with patch("web.cli.getpass", side_effect=[_GOOD_PASSWORD, _GOOD_PASSWORD]) as ask:
        assert _read_password() == _GOOD_PASSWORD

    assert ask.call_count == 2, "typed twice, because it is not echoed"


def test_two_different_typings_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PASSWORD_VARIABLE, raising=False)

    with patch("web.cli.getpass", side_effect=[_GOOD_PASSWORD, "something-else-again"]):
        with pytest.raises(Exception, match="do not match"):
            _read_password()


def test_the_password_is_not_a_command_line_option() -> None:
    """A password in argv is in the shell history and in `ps`."""
    options = {
        option.name for option in provision_administrator.params  # type: ignore[attr-defined]
    }

    assert "password" not in options
    assert options == {"name", "email", "deactivate_demo_accounts"}


def test_no_password_is_read_from_a_file_in_the_repository() -> None:
    """The whole point of the story: the credential is not in the tree."""
    source = __import__("pathlib").Path("web/cli.py").read_text()

    assert "open(" not in source
    assert "read_text" not in source
    assert PASSWORD_VARIABLE in source


# ---------- what it refuses ----------


def test_the_published_password_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PASSWORD_VARIABLE, PUBLISHED_PASSWORD)

    with pytest.raises(Exception, match="this repository publishes"):
        _read_password()


def test_a_short_password_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PASSWORD_VARIABLE, "x" * (MINIMUM_PASSWORD_LENGTH - 1))

    with pytest.raises(Exception, match=str(MINIMUM_PASSWORD_LENGTH)):
        _read_password()


def test_the_published_password_is_the_one_the_seed_actually_uses() -> None:
    """If the seed's password changes, this refusal must follow it."""
    seed = __import__("pathlib").Path("sql/02_seed_30_per_table.sql").read_text()

    assert PUBLISHED_PASSWORD in seed


def test_an_address_that_already_exists_is_refused(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PASSWORD_VARIABLE, _GOOD_PASSWORD)
    monkeypatch.setattr("web.cli.get_user_by_email", lambda _c, _e: object())
    installed = MagicMock()
    monkeypatch.setattr("web.cli.provision_account", installed)

    result = CliRunner().invoke(
        app.cli,
        ["provision-administrator", "--name", "R", "--email", "taken@udem.edu"],
        obj=ScriptInfo(create_app=lambda: app),
    )

    assert result.exit_code != 0
    assert "already has an account" in result.output
    installed.assert_not_called()


# ---------- the commands are registered ----------


def test_the_instance_commands_are_attached_to_the_application(app: Flask) -> None:
    assert {
        "provision-administrator",
        "rotate-password",
        "account-report",
    } <= set(app.cli.commands)


def test_none_of_them_is_reachable_over_http(app: Flask) -> None:
    """They run on the instance, by somebody with a shell on it."""
    paths = {rule.rule for rule in app.url_map.iter_rules()}

    assert not any("provision" in path or "rotate" in path for path in paths)
