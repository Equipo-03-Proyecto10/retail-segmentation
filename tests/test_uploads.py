"""Regression checks for the image-upload PR and its integration hotfix."""

import struct
import zlib
from dataclasses import replace
from io import BytesIO
from unittest.mock import MagicMock, Mock

import pytest
from werkzeug.datastructures import FileStorage

from web.app import create_app
from web.config import Config
from web.services import uploads
from web.services.uploads import (
    MAX_IMAGE_SIDE_PIXELS,
    UploadRejected,
    delete_legacy_image,
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


def _upload(content: bytes, mime: str, config) -> uploads.ValidatedImage:
    """The validation every upload passes before anything is written."""
    return uploads._validate(
        FileStorage(BytesIO(content), filename="../a", content_type=mime), config
    )


@pytest.mark.parametrize(
    "mime,content",
    [
        ("image/jpeg", b"\xff\xd8\xffimage bytes"),
        ("image/png", _png()),
        ("image/webp", b"RIFF\x04\x00\x00\x00WEBPimage bytes"),
    ],
)
def test_an_accepted_image_is_validated_whole(config, mime, content):
    image = _upload(content, mime, config)

    assert image.data == content
    assert image.content_type == mime


@pytest.mark.parametrize(
    "content,mime",
    [
        (b"", "image/png"),
        (b"pdf", "application/pdf"),
        (b"GIF89a", "image/gif"),
        (b"x" * 11, "image/png"),
        (b"text", "image/png"),
        (b"\xff\xd8\xff", "image/png"),
    ],
)
def test_a_rejected_file_never_reaches_the_store(config, content, mime):
    store = Mock()

    with pytest.raises(UploadRejected):
        save_product_image(
            FileStorage(BytesIO(content), filename="a", content_type=mime),
            replace(config, max_upload_bytes=10),
            owner_id="11111111-1111-1111-1111-000000000001",
            store=store,
        )

    store.put.assert_not_called()


def test_deleting_a_legacy_image_stays_inside_the_upload_directory(config, tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    delete_legacy_image(
        "../outside", replace(config, upload_dir=str(tmp_path / "uploads"))
    )
    assert outside.read_bytes() == b"keep"


def test_a_legacy_image_is_still_served_from_the_upload_directory(
    config, monkeypatch, tmp_path
):
    # An image uploaded before the file store, and not yet migrated (ADR-0091).
    monkeypatch.chdir(tmp_path)
    config = replace(config, upload_dir="uploads")
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "0123456789abcdef0123456789abcdef.png").write_bytes(_png())
    client = create_app(
        config, database_connector=Mock(return_value=MagicMock())
    ).test_client()
    with client.session_transaction() as session:
        session.update(user_id="u-1", role_code="ADMIN", name="Admin")

    response = client.get("/admin/products/image/0123456789abcdef0123456789abcdef.png")

    assert response.status_code == 200
    assert response.data == _png()
    assert response.headers["X-Content-Type-Options"] == "nosniff"


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


def _refuse_png(content: bytes, config, match: str) -> None:
    """The whole upload path refuses the file, and the store never sees it."""
    store = Mock()
    with pytest.raises(UploadRejected, match=match):
        save_product_image(
            FileStorage(BytesIO(content), filename="a.png", content_type="image/png"),
            config,
            owner_id="11111111-1111-1111-1111-000000000001",
            store=store,
        )
    store.put.assert_not_called()


def test_a_png_at_the_size_limit_is_accepted(config):
    image = _upload(
        _png(MAX_IMAGE_SIDE_PIXELS, MAX_IMAGE_SIDE_PIXELS), "image/png", config
    )

    assert image.content_type == "image/png"


@pytest.mark.parametrize(
    "width,height",
    [(60000, 60000), (MAX_IMAGE_SIDE_PIXELS + 1, 1), (1, MAX_IMAGE_SIDE_PIXELS + 1)],
)
def test_a_png_declaring_huge_dimensions_is_refused(config, width, height):
    # #595: a few hundred bytes that declare 60000×60000 pixels.
    _refuse_png(_png(width, height), config, "pixels or fewer")


def test_a_png_with_html_appended_is_refused(config):
    # #595: a valid image followed by a page; served as image/png it runs
    # nothing today, but no later step should ever receive it.
    trailer = b"<html><script>alert(1)</script></html>"

    _refuse_png(_png(trailer=trailer), config, "extra data after its end")


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
def test_a_damaged_png_is_refused(config, content):
    _refuse_png(content, config, "damaged or incomplete")
