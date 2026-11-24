"""`stored_file` holds its shape in the database itself (F29-13, ADR-0091).

What these tests state is what the schema refuses and what the seed contains,
against a database loaded with the three scripts. Every statement runs in a
transaction that is rolled back, so the shared test database keeps its rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import (
    CheckViolation,
    ForeignKeyViolation,
    RestrictViolation,
    UniqueViolation,
)

pytestmark = pytest.mark.postgres

ADMIN_ID = "11111111-1111-1111-1111-000000000001"
SHA = "a" * 64


@pytest.fixture
def owner(owner_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(owner_url) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


def _row(**changes) -> dict:
    """A row the schema accepts; each test changes one thing."""
    file_id = changes.pop("file_id", str(uuid4()))
    row = {
        "file_id": file_id,
        "object_path": f"product-image/{file_id}.jpg",
        "content_type": "image/jpeg",
        "size_bytes": 1234,
        "sha256": SHA,
        "privacy": "PUBLIC",
        "status": "STORED",
        "owner_user_id": ADMIN_ID,
        "deleted_at": None,
        "metadata": "{}",
    }
    row.update(changes)
    return row


def _insert(connection: psycopg.Connection, row: dict) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stored_file (file_id, object_path, content_type,"
            " size_bytes, sha256, privacy, status, owner_user_id, deleted_at,"
            " metadata)"
            " VALUES (%(file_id)s, %(object_path)s, %(content_type)s,"
            " %(size_bytes)s, %(sha256)s, %(privacy)s, %(status)s,"
            " %(owner_user_id)s, %(deleted_at)s, %(metadata)s::jsonb)",
            row,
        )


def _refused(connection: psycopg.Connection, row: dict, constraint: str) -> None:
    with pytest.raises(CheckViolation) as refusal:
        with connection.transaction():
            _insert(connection, row)
    assert refusal.value.diag.constraint_name == constraint


def test_a_well_formed_row_is_accepted_and_defaults_to_stored(owner) -> None:
    row = _row()
    row.pop("status")
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stored_file (file_id, object_path, content_type,"
            " size_bytes, sha256, privacy, owner_user_id)"
            " VALUES (%(file_id)s, %(object_path)s, %(content_type)s,"
            " %(size_bytes)s, %(sha256)s, %(privacy)s, %(owner_user_id)s)"
            " RETURNING status, created_at IS NOT NULL, metadata",
            row,
        )
        assert cursor.fetchone() == ("STORED", True, {})


@pytest.mark.parametrize("content_type", ["image/jpeg", "image/png", "image/webp"])
def test_each_accepted_type_pairs_with_its_own_extension(owner, content_type) -> None:
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
    file_id = str(uuid4())

    _insert(
        owner,
        _row(
            file_id=file_id,
            content_type=content_type,
            object_path=f"product-image/{file_id}.{extension[content_type]}",
        ),
    )


@pytest.mark.parametrize(
    "content_type", ["image/gif", "application/pdf", "text/html", "image/svg+xml", ""]
)
def test_a_content_type_outside_the_three_images_is_refused(
    owner, content_type
) -> None:
    _refused(owner, _row(content_type=content_type), "stored_file_content_type_check")


@pytest.mark.parametrize("size", [0, -1, 5 * 1024 * 1024 + 1])
def test_a_size_outside_one_byte_to_five_megabytes_is_refused(owner, size) -> None:
    _refused(owner, _row(size_bytes=size), "stored_file_size_check")


def test_exactly_five_megabytes_is_accepted(owner) -> None:
    _insert(owner, _row(size_bytes=5 * 1024 * 1024))


@pytest.mark.parametrize(
    "sha256",
    [
        "A" * 64,  # upper case
        "a" * 63,  # one short
        "g" * 64,  # not hexadecimal
        "a" * 63 + " ",
        "",
    ],
)
def test_a_sha256_that_is_not_sixty_four_lowercase_hex_digits_is_refused(
    owner, sha256
) -> None:
    # CHAR(64) pads a short value, so the pattern is what refuses it.
    _refused(owner, _row(sha256=sha256), "stored_file_sha256_check")


@pytest.mark.parametrize("privacy", ["public", "SECRET", "", "PUBLIC "])
def test_a_privacy_other_than_public_or_private_is_refused(owner, privacy) -> None:
    _refused(owner, _row(privacy=privacy), "stored_file_privacy_check")


def test_private_is_accepted(owner) -> None:
    _insert(owner, _row(privacy="PRIVATE"))


@pytest.mark.parametrize("status", ["stored", "ARCHIVED", ""])
def test_a_status_outside_the_three_is_refused(owner, status) -> None:
    _refused(owner, _row(status=status), "stored_file_status_check")


def test_a_deletion_time_is_set_exactly_when_the_file_is_deleted(owner) -> None:
    _refused(
        owner,
        _row(status="DELETED", deleted_at=None),
        "stored_file_deleted_at_check",
    )
    _refused(
        owner,
        _row(status="STORED", deleted_at="2026-01-01T00:00:00Z"),
        "stored_file_deleted_at_check",
    )
    _refused(
        owner,
        _row(status="PENDING_DELETE", deleted_at="2026-01-01T00:00:00Z"),
        "stored_file_deleted_at_check",
    )
    _insert(owner, _row(status="DELETED", deleted_at="2026-01-01T00:00:00Z"))
    _insert(owner, _row(status="PENDING_DELETE"))


@pytest.mark.parametrize("metadata", ["[]", '"text"', "1", "null"])
def test_free_metadata_is_one_json_object(owner, metadata) -> None:
    _refused(owner, _row(metadata=metadata), "stored_file_metadata_check")


@pytest.mark.parametrize(
    "path",
    [
        "../escape.jpg",  # a path out of the folder
        "holiday photo.jpg",  # the user's own name
        "product-image/holiday photo.jpg",
        "product-image/{other}.jpg",  # another file's id
        "product-image/{file_id}.gif",  # an extension no type uses
        "product-image/{file_id}.png",  # the wrong extension for image/jpeg
        "product-image/{file_id}.jpg.exe",
        "/product-image/{file_id}.jpg",
        "Product-Image/{file_id}.jpg",
        "{file_id}.jpg",  # no kind
    ],
)
def test_an_object_name_that_is_not_kind_slash_id_dot_extension_is_refused(
    owner, path
) -> None:
    file_id = str(uuid4())
    _refused(
        owner,
        _row(file_id=file_id, object_path=path.format(file_id=file_id, other=uuid4())),
        "stored_file_object_path_check",
    )


def test_two_files_cannot_share_an_identity_and_so_not_an_object_name(owner) -> None:
    # The name contains the file's id, so a second row could only repeat the
    # first's id: the primary key refuses it (object_path is UNIQUE as well, as
    # the second candidate key).
    file_id = str(uuid4())
    _insert(owner, _row(file_id=file_id))

    with pytest.raises(UniqueViolation):
        with owner.transaction():
            _insert(owner, _row(file_id=file_id))


def test_the_same_content_may_be_stored_twice_as_two_files(owner) -> None:
    # sha256 is not a key: two uploads of one picture are two files (4NF).
    _insert(owner, _row())
    _insert(owner, _row())


def test_a_file_needs_an_owner_that_exists(owner) -> None:
    with pytest.raises(ForeignKeyViolation):
        with owner.transaction():
            _insert(owner, _row(owner_user_id=str(uuid4())))


# ---------- the product's reference ----------


def _a_product(owner: psycopg.Connection) -> int:
    product_id = 919101
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO product (product_id, sku, name, category_id, list_price)"
            " VALUES (%s, %s, 'Schema scratch', 1, 1)",
            (product_id, f"SCHEMA-{uuid4().hex[:12]}"),
        )
    return product_id


def test_a_product_names_its_file_by_that_files_id(owner) -> None:
    file_id = str(uuid4())
    _insert(owner, _row(file_id=file_id))
    product_id = _a_product(owner)

    with owner.cursor() as cursor:
        cursor.execute(
            "UPDATE product SET image_file_id = %s, image_path = %s"
            " WHERE product_id = %s",
            (file_id, f"{file_id}.jpg", product_id),
        )
        assert cursor.rowcount == 1


@pytest.mark.parametrize(
    "image_path", [None, "photo.jpg", "{other}.jpg", "{file_id}.gif", "{file_id}"]
)
def test_a_product_with_a_file_must_be_published_under_that_files_name(
    owner, image_path
) -> None:
    file_id = str(uuid4())
    _insert(owner, _row(file_id=file_id))
    product_id = _a_product(owner)
    path = image_path and image_path.format(file_id=file_id, other=uuid4())

    with pytest.raises(CheckViolation) as refusal:
        with owner.transaction():
            owner.execute(
                "UPDATE product SET image_file_id = %s, image_path = %s"
                " WHERE product_id = %s",
                (file_id, path, product_id),
            )

    assert refusal.value.diag.constraint_name == "product_image_name_check"


def test_a_file_belongs_to_one_product_at_most(owner) -> None:
    file_id = str(uuid4())
    _insert(owner, _row(file_id=file_id))
    first = _a_product(owner)
    second = first + 10
    owner.execute(
        "INSERT INTO product (product_id, sku, name, category_id, list_price)"
        " VALUES (%s, %s, 'Second', 1, 1)",
        (second, f"SCHEMA-{uuid4().hex[:12]}"),
    )
    update = (
        "UPDATE product SET image_file_id = %s, image_path = %s WHERE product_id = %s"
    )
    owner.execute(update, (file_id, f"{file_id}.jpg", first))

    with pytest.raises(UniqueViolation):
        with owner.transaction():
            owner.execute(update, (file_id, f"{file_id}.jpg", second))


def test_a_product_cannot_point_at_a_file_that_does_not_exist(owner) -> None:
    product_id = _a_product(owner)
    ghost = str(uuid4())

    with pytest.raises(ForeignKeyViolation):
        with owner.transaction():
            owner.execute(
                "UPDATE product SET image_file_id = %s, image_path = %s"
                " WHERE product_id = %s",
                (ghost, f"{ghost}.jpg", product_id),
            )


def test_a_file_a_product_uses_cannot_be_deleted_from_under_it(owner) -> None:
    file_id = str(uuid4())
    _insert(owner, _row(file_id=file_id))
    product_id = _a_product(owner)
    owner.execute(
        "UPDATE product SET image_file_id = %s, image_path = %s WHERE product_id = %s",
        (file_id, f"{file_id}.jpg", product_id),
    )

    with pytest.raises(RestrictViolation):
        with owner.transaction():
            owner.execute("DELETE FROM stored_file WHERE file_id = %s", (file_id,))


def test_a_legacy_product_keeps_its_old_file_name_until_it_is_migrated(owner) -> None:
    product_id = _a_product(owner)

    owner.execute(
        "UPDATE product SET image_path = '0123456789abcdef0123456789abcdef.png'"
        " WHERE product_id = %s",
        (product_id,),
    )


# ---------- the seed ----------


def test_the_seed_gives_the_table_thirty_rows_of_replaced_pictures(owner) -> None:
    with owner.cursor() as cursor:
        cursor.execute(
            "SELECT count(*), count(*) FILTER (WHERE status = 'DELETED'),"
            " count(*) FILTER (WHERE deleted_at > created_at),"
            " count(DISTINCT object_path), count(DISTINCT content_type)"
            " FROM stored_file WHERE file_id::text LIKE '33333333-3333-3333-3333-%'"
        )
        total, deleted, ended_after_created, paths, types = cursor.fetchone()

    assert total == 30
    assert (deleted, ended_after_created, paths, types) == (30, 30, 30, 3)


def test_no_seeded_product_points_at_an_object_no_bucket_holds(owner) -> None:
    with owner.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM product AS p JOIN stored_file AS f"
            " ON f.file_id = p.image_file_id"
            " WHERE f.file_id::text LIKE '33333333-3333-3333-3333-%'"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT count(*) FROM product WHERE image_path IS NOT NULL"
            " AND product_id <= 40"
        )
        assert cursor.fetchone()[0] == 0
