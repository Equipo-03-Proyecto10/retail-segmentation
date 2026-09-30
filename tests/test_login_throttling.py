"""Authentication throttling covers both account and client buckets."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from web.app import create_app
from web.config import Config
from web.services.auth import LoginResult
from web.services.login_throttle import LoginThrottle, account_key, client_key


def test_sliding_window_blocks_at_the_threshold_and_expires() -> None:
    now = [100.0]
    throttle = LoginThrottle(3, 60, clock=lambda: now[0])
    key = "account:test"

    for _ in range(2):
        assert not throttle.check([key]).blocked
        throttle.record_failure([key])
    assert not throttle.check([key]).blocked
    throttle.record_failure([key])

    decision = throttle.check([key])
    assert decision.blocked
    assert 1 <= decision.retry_after <= 60
    now[0] += 61
    assert not throttle.check([key]).blocked


def test_an_account_and_client_bucket_are_independent() -> None:
    throttle = LoginThrottle(2, 60)
    account = account_key("Person@Example.com")
    assert account == account_key(" person@example.COM ")
    assert account is not None
    client = client_key("203.0.113.10")

    throttle.record_failure([account])
    throttle.record_failure([account])
    assert throttle.check([account]).blocked
    assert not throttle.check([client]).blocked


def test_success_clears_failures() -> None:
    throttle = LoginThrottle(1, 60)
    key = "client:203.0.113.11"
    throttle.record_failure([key])
    assert throttle.check([key]).blocked
    throttle.clear([key])
    assert not throttle.check([key]).blocked


def _app(max_attempts: int = 3) -> object:
    return create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused",
            login_throttle_max_attempts=max_attempts,
            login_throttle_window_seconds=60,
        ),
        database_connector=Mock(return_value=MagicMock()),
    )


def test_repeated_failures_return_a_retryable_html_response(monkeypatch) -> None:
    app = _app()
    monkeypatch.setattr(
        "web.routes.auth.authenticate", lambda *_args: LoginResult(success=False)
    )
    client = app.test_client()

    responses = [
        client.post(
            "/login",
            data={"email": "person@example.test", "password": "wrong"},
            environ_base={"REMOTE_ADDR": "203.0.113.12"},
        )
        for _ in range(4)
    ]

    assert [response.status_code for response in responses[:3]] == [401, 401, 401]
    assert responses[3].status_code == 429
    assert responses[3].headers["Retry-After"].isdigit()
    assert "Too many sign-in attempts" in responses[3].get_data(as_text=True)


def test_a_correct_password_is_still_throttled_until_the_window_expires(
    monkeypatch,
) -> None:
    app = _app()
    monkeypatch.setattr(
        "web.routes.auth.authenticate", lambda *_args: LoginResult(success=False)
    )
    client = app.test_client()
    for _ in range(3):
        client.post(
            "/login",
            data={"email": "person@example.test", "password": "wrong"},
            environ_base={"REMOTE_ADDR": "203.0.113.13"},
        )

    monkeypatch.setattr(
        "web.routes.auth.authenticate",
        lambda *_args: LoginResult(success=True, user=MagicMock()),
    )
    response = client.post(
        "/login",
        data={"email": "person@example.test", "password": "correct"},
        environ_base={"REMOTE_ADDR": "203.0.113.13"},
    )
    assert response.status_code == 429


def test_success_for_one_account_does_not_clear_the_client_bucket(monkeypatch) -> None:
    app = _app()
    client = app.test_client()
    refused = LoginResult(success=False)
    monkeypatch.setattr("web.routes.auth.authenticate", lambda *_args: refused)

    for _ in range(2):
        assert (
            client.post(
                "/login",
                data={"email": "first@example.test", "password": "wrong"},
                environ_base={"REMOTE_ADDR": "203.0.113.14"},
            ).status_code
            == 401
        )

    user = SimpleNamespace(
        user_id="user-2", role_id=7, role_code="CUSTOMER", name="User"
    )
    monkeypatch.setattr(
        "web.routes.auth.authenticate",
        lambda *_args: LoginResult(success=True, user=user),
    )
    monkeypatch.setattr("web.routes.auth.start_session", lambda *_args: "session-2")
    assert (
        client.post(
            "/login",
            data={"email": "second@example.test", "password": "correct"},
            environ_base={"REMOTE_ADDR": "203.0.113.14"},
        ).status_code
        == 302
    )

    monkeypatch.setattr("web.routes.auth.authenticate", lambda *_args: refused)
    response = client.post(
        "/login",
        data={"email": "second@example.test", "password": "wrong"},
        environ_base={"REMOTE_ADDR": "203.0.113.14"},
    )
    assert response.status_code == 401
    response = client.post(
        "/login",
        data={"email": "second@example.test", "password": "wrong"},
        environ_base={"REMOTE_ADDR": "203.0.113.14"},
    )
    assert response.status_code == 429


def test_twenty_five_failures_do_not_make_the_next_correct_password_succeed(
    monkeypatch,
) -> None:
    app = _app(max_attempts=5)
    client = app.test_client()
    monkeypatch.setattr(
        "web.routes.auth.authenticate", lambda *_args: LoginResult(success=False)
    )
    for _ in range(25):
        client.post(
            "/login",
            data={"email": "reproduction@example.test", "password": "wrong"},
            environ_base={"REMOTE_ADDR": "203.0.113.15"},
        )

    monkeypatch.setattr(
        "web.routes.auth.authenticate",
        lambda *_args: LoginResult(success=True, user=MagicMock()),
    )
    response = client.post(
        "/login",
        data={"email": "reproduction@example.test", "password": "correct"},
        environ_base={"REMOTE_ADDR": "203.0.113.15"},
    )
    assert response.status_code == 429
