"""Regression checks for the image-upload PR and its integration hotfix."""

import struct
import zlib
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from werkzeug.datastructures import FileStorage

from web.app import create_app
from web.config import Config
from web.services.uploads import (
    MAX_IMAGE_SIDE_PIXELS,
    UploadRejected,
    delete_product_image,
    save_product_image,
)


def _chunk(kind: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + kind
        + body
        + struct.pack(">I", zlib.crc32(kind + body))
    )


def _png(width: int = 1, height: int = 1, *, trailer: bytes = b"") -> bytes:
    """A well-formed one-pixel-row PNG, with the IHDR declaring any size."""
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", pixels)
        + _chunk(b"IEND", b"")
        + trailer
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


@pytest.mark.parametrize(
    "mime,content",
    [
        ("image/jpeg", b"\xff\xd8\xffimage bytes"),
        ("image/png", _png()),
        ("image/webp", b"RIFF\x04\x00\x00\x00WEBPimage bytes"),
    ],
)
def test_upload_round_trip(config, mime, content):
    saved = save_product_image(
        FileStorage(BytesIO(content), filename="../a", content_type=mime), config
    )
    assert Path(saved.absolute_path).read_bytes() == content
    assert "/" not in saved.relative_path
    delete_product_image(saved.relative_path, config)
    assert not Path(saved.absolute_path).exists()


@pytest.mark.parametrize(
    "content,mime",
    [
        (b"", "image/png"),
        (b"pdf", "application/pdf"),
        (b"x" * 11, "image/png"),
        (b"text", "image/png"),
        (b"\xff\xd8\xff", "image/png"),
    ],
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
        FileStorage(
            BytesIO(_png()),
            filename="a.png",
            content_type="image/png",
        ),
        config,
    )
    client = create_app(
        config, database_connector=Mock(return_value=MagicMock())
    ).test_client()
    with client.session_transaction() as session:
        session.update(user_id="u-1", role_code="ADMIN", name="Admin")
    response = client.get(f"/admin/products/image/{saved.relative_path}")
    assert response.status_code == 200
    assert response.data == _png()


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


def _save_png(content: bytes, config) -> None:
    save_product_image(
        FileStorage(BytesIO(content), filename="a.png", content_type="image/png"),
        config,
    )


def test_a_png_at_the_size_limit_is_accepted(config, tmp_path):
    _save_png(_png(MAX_IMAGE_SIDE_PIXELS, MAX_IMAGE_SIDE_PIXELS), config)

    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize(
    "width,height",
    [(60000, 60000), (MAX_IMAGE_SIDE_PIXELS + 1, 1), (1, MAX_IMAGE_SIDE_PIXELS + 1)],
)
def test_a_png_declaring_huge_dimensions_is_refused(config, tmp_path, width, height):
    # #595: a few hundred bytes that declare 60000×60000 pixels.
    with pytest.raises(UploadRejected, match="pixels or fewer"):
        _save_png(_png(width, height), config)

    assert list(tmp_path.iterdir()) == []


def test_a_png_with_html_appended_is_refused(config, tmp_path):
    # #595: a valid image followed by a page; served as image/png it runs
    # nothing today, but no later step should ever receive it.
    trailer = b"<html><script>alert(1)</script></html>"

    with pytest.raises(UploadRejected, match="extra data after its end"):
        _save_png(_png(trailer=trailer), config)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "content",
    [
        b"\x89PNG\r\n\x1a\nimage bytes",
        _png()[:-12],
        _png()[:26] + b"\x01" + _png()[27:],
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IDAT", b"") + _chunk(b"IEND", b""),
    ],
    ids=["no-chunks", "no-iend", "bad-crc", "ihdr-not-first"],
)
def test_a_damaged_png_is_refused(config, tmp_path, content):
    with pytest.raises(UploadRejected, match="damaged or incomplete"):
        _save_png(content, config)

    assert list(tmp_path.iterdir()) == []
