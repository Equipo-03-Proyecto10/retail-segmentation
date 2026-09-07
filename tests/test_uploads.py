"""Regression checks for the image-upload PR and its integration hotfix."""

from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from werkzeug.datastructures import FileStorage

from web.app import create_app
from web.config import Config
from web.services.uploads import (
    UploadRejected,
    delete_product_image,
    save_product_image,
)


@pytest.fixture
def config(tmp_path):
    return Config(
        secret_key="test",
        session_cookie_secure=False,
        environment="testing",
        port=5000,
        log_level="INFO",
        database_url="unused",
        upload_dir=str(tmp_path),
    )


@pytest.mark.parametrize("mime", ["image/jpeg", "image/png", "image/webp"])
def test_upload_round_trip(config, mime):
    saved = save_product_image(
        FileStorage(BytesIO(b"image bytes"), filename="../a", content_type=mime), config
    )
    assert Path(saved.absolute_path).read_bytes() == b"image bytes"
    assert "/" not in saved.relative_path
    delete_product_image(saved.relative_path, config)
    assert not Path(saved.absolute_path).exists()


@pytest.mark.parametrize(
    "content,mime",
    [(b"", "image/png"), (b"pdf", "application/pdf"), (b"x" * 11, "image/png")],
)
def test_rejection_writes_no_file(config, content, mime):
    with pytest.raises(UploadRejected):
        save_product_image(
            FileStorage(BytesIO(content), filename="a", content_type=mime),
            replace(config, max_upload_bytes=10),
        )
    assert not list(Path(config.upload_dir).iterdir())


def test_deletion_stays_inside_upload_directory(config, tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    delete_product_image(
        "../outside", replace(config, upload_dir=str(tmp_path / "uploads"))
    )
    assert outside.read_bytes() == b"keep"


def test_image_route_uses_same_relative_directory_as_storage(
    config, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    config = replace(config, upload_dir="uploads")
    saved = save_product_image(
        FileStorage(BytesIO(b"image"), filename="a.png", content_type="image/png"),
        config,
    )
    client = create_app(
        config, database_connector=Mock(return_value=MagicMock())
    ).test_client()
    with client.session_transaction() as session:
        session.update(user_id="u-1", role_code="ADMIN", name="Admin")
    response = client.get(f"/admin/products/image/{saved.relative_path}")
    assert response.status_code == 200
    assert response.data == b"image"


def test_rejected_creation_does_not_insert_product(config, monkeypatch):
    from web.routes import admin

    create = Mock()
    monkeypatch.setattr(admin, "create_product", create)
    monkeypatch.setattr(admin, "list_all_categories", Mock(return_value=[]))
    client = create_app(
        config, database_connector=Mock(return_value=MagicMock())
    ).test_client()
    with client.session_transaction() as session:
        session.update(user_id="u-1", role_code="ADMIN", name="Admin")
    response = client.post(
        "/admin/products/new",
        data={
            "product_id": "1",
            "sku": "sku",
            "name": "Product",
            "category_id": "1",
            "list_price": "10",
            "image": (BytesIO(b"pdf"), "a.pdf", "application/pdf"),
        },
    )
    assert response.status_code == 400
    create.assert_not_called()
    assert b'name="product_id"' in response.data
