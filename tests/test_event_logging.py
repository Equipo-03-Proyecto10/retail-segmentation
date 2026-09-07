"""Operational events are traceable without leaking credentials."""

import logging
from io import BytesIO
from unittest.mock import MagicMock, Mock

from werkzeug.datastructures import FileStorage

from web.app import create_app
from web.config import Config
from web.services import auth
from web.services.segmentation import run
from web.services.uploads import UploadRejected, save_product_image


def configuration(**overrides):
    return Config.from_env(
        {
            "DATABASE_URL": "private-connection",
            "FLASK_SECRET_KEY": "private-key",
            **overrides,
        }
    )


def test_startup_logs_safe_configuration_and_configures_service_loggers(caplog):
    create_app(configuration(), database_connector=Mock(return_value=MagicMock()))
    assert "application_started" in caplog.text
    assert "proxy_hops=0" in caplog.text
    assert "private-connection" not in caplog.text
    assert "private-key" not in caplog.text
    assert logging.getLogger("web.services.auth").getEffectiveLevel() == logging.INFO


def test_repeated_factory_calls_do_not_duplicate_handlers(capsys):
    for _ in range(2):
        create_app(
            configuration(LOG_LEVEL="WARNING"),
            database_connector=Mock(return_value=MagicMock()),
        )
    logger = logging.getLogger("web.services.auth")
    logger.info("below-threshold")
    logger.warning("one-event-only")
    output = capsys.readouterr().err
    assert output.count("one-event-only") == 1
    assert "below-threshold" not in output


def test_sign_in_and_out_events_share_context_without_passwords(caplog, monkeypatch):
    user = Mock(user_id="user-1", role_id=2, role_code="ANALYST", name="User")
    user.name = "User"
    monkeypatch.setattr(auth, "get_user_by_email", Mock(return_value=user))
    monkeypatch.setattr(auth, "_hasher", Mock(verify=Mock(return_value=True)))
    app = create_app(configuration(), database_connector=Mock(return_value=MagicMock()))
    client = app.test_client()
    response = client.post(
        "/login", data=dict(email="user@example.com\nforged", password="never-log-this")
    )
    assert response.status_code == 302
    assert client.post("/logout").status_code == 302
    records = {record.getMessage().split()[0]: record for record in caplog.records}
    assert records["login_succeeded"].reference != "-"
    assert records["logout_succeeded"].actor == "'user-1'"
    assert "never-log-this" not in caplog.text
    assert "\\nforged" in caplog.text
    assert "\nforged" not in caplog.text


def test_segment_completion_is_logged_only_after_commit(caplog):
    create_app(configuration(), database_connector=Mock(return_value=MagicMock()))
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (
        30,
        20,
        10,
        4,
        2,
    )

    def commit():
        assert "segment_run_started" in caplog.text
        assert "segment_run_succeeded" not in caplog.text

    connection.commit.side_effect = commit
    run(connection, 180)
    assert "processed=30 assigned=20 unmatched=10 reassigned=4 cleared=2" in caplog.text


def test_upload_rejection_logs_reason_without_file_contents(caplog):
    create_app(configuration(), database_connector=Mock(return_value=MagicMock()))
    file = FileStorage(
        BytesIO(b"private file content"),
        filename="secret.png",
        content_type="image/png",
    )
    try:
        save_product_image(file, configuration())
    except UploadRejected:
        pass
    assert "upload_refused" in caplog.text
    assert "does not match" in caplog.text
    assert "private file content" not in caplog.text
    assert "secret.png" not in caplog.text
