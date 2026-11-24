"""`stored_file` holds its shape in the database itself (F29-13, ADR-0091).

What these tests state is what the schema refuses and what the seed contains,
against a database loaded with the three scripts. Every statement runs in a
transaction that is rolled back, so the shared test database keeps its rows.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
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


# ---------- a product's name for its picture is its stored file's (#1205) ----------


def _linked(owner, content_type: str, extension: str, image_path_extension: str):
    file_id = str(uuid4())
    _insert(
        owner,
        _row(
            file_id=file_id,
            content_type=content_type,
            object_path=f"product-image/{file_id}.{extension}",
        ),
    )
    product_id = _a_product(owner)
    return file_id, product_id, f"{file_id}.{image_path_extension}"


def test_a_png_cannot_be_published_under_a_jpg_name(owner) -> None:
    # The CHECK only sees the product's row; the extension must also be the one the
    # referenced file's content type implies, so a constraint trigger reads it.
    file_id, product_id, name = _linked(owner, "image/png", "png", "jpg")

    with pytest.raises(CheckViolation) as refusal:
        with owner.transaction():
            owner.execute(
                "UPDATE product SET image_file_id = %s, image_path = %s"
                " WHERE product_id = %s",
                (file_id, name, product_id),
            )

    assert refusal.value.diag.constraint_name == "product_image_file_matches"


@pytest.mark.parametrize(
    "content_type,extension",
    [("image/jpeg", "jpg"), ("image/png", "png"), ("image/webp", "webp")],
)
def test_each_type_is_published_under_its_own_extension_only(
    owner, content_type: str, extension: str
) -> None:
    file_id, product_id, name = _linked(owner, content_type, extension, extension)
    owner.execute(
        "UPDATE product SET image_file_id = %s, image_path = %s WHERE product_id = %s",
        (file_id, name, product_id),
    )
    others = {"jpg", "png", "webp"} - {extension}
    for other in sorted(others):
        with pytest.raises(CheckViolation):
            with owner.transaction():
                owner.execute(
                    "UPDATE product SET image_path = %s WHERE product_id = %s",
                    (f"{file_id}.{other}", product_id),
                )


def test_the_name_is_checked_on_insert_too(owner) -> None:
    file_id = str(uuid4())
    _insert(owner, _row(file_id=file_id))  # image/jpeg, .jpg

    with pytest.raises(CheckViolation) as refusal:
        with owner.transaction():
            owner.execute(
                "INSERT INTO product (product_id, sku, name, category_id, list_price,"
                " image_path, image_file_id) VALUES (919150, 'SCHEMA-INS', 'x', 1, 1,"
                " %s, %s)",
                (f"{file_id}.png", file_id),
            )

    assert refusal.value.diag.constraint_name == "product_image_file_matches"


# ---------- the history cannot be rewritten by the web role (#1205) ----------


def _as_web(owner: psycopg.Connection) -> None:
    owner.execute("SET LOCAL ROLE retail_app")


def test_the_web_role_cannot_delete_a_stored_file_row(owner) -> None:
    file_id = str(uuid4())
    _insert(owner, _row(file_id=file_id))
    _as_web(owner)

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with owner.transaction():
            owner.execute("DELETE FROM stored_file WHERE file_id = %s", (file_id,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with owner.transaction():
            owner.execute("DELETE FROM stored_file WHERE status = 'DELETED'")


@pytest.mark.parametrize(
    "column,value",
    [
        ("object_path", "'product-image/x.jpg'"),
        ("privacy", "'PRIVATE'"),
        ("sha256", "repeat('b', 64)"),
        ("size_bytes", "1"),
        ("owner_user_id", "gen_random_uuid()"),
        ("metadata", "'{}'::jsonb"),
    ],
)
def test_the_web_role_may_not_rewrite_what_a_row_says_about_its_object(
    owner, column: str, value: str
) -> None:
    file_id = str(uuid4())
    _insert(owner, _row(file_id=file_id))
    _as_web(owner)

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with owner.transaction():
            owner.execute(
                f"UPDATE stored_file SET {column} = {value} WHERE file_id = %s",  # noqa: S608
                (file_id,),
            )


def test_a_removed_file_is_never_brought_back_even_by_the_columns_the_web_may_update(
    owner,
) -> None:
    file_id = str(uuid4())
    _insert(owner, _row(file_id=file_id, status="DELETED", deleted_at="2026-01-01Z"))
    _as_web(owner)

    for target in ("STORED", "PENDING_DELETE"):
        with pytest.raises(CheckViolation) as refusal:
            with owner.transaction():
                owner.execute(
                    "UPDATE stored_file SET status = %s, deleted_at = NULL"
                    " WHERE file_id = %s",
                    (target, file_id),
                )
        assert refusal.value.diag.constraint_name == "stored_file_forward_lifecycle"


def test_a_file_moves_forward_through_its_lifecycle(owner) -> None:
    file_id = str(uuid4())
    _insert(owner, _row(file_id=file_id))
    _as_web(owner)

    owner.execute(
        "UPDATE stored_file SET status = 'PENDING_DELETE' WHERE file_id = %s",
        (file_id,),
    )
    owner.execute(
        "UPDATE stored_file SET status = 'DELETED', deleted_at = now()"
        " WHERE file_id = %s",
        (file_id,),
    )
    with pytest.raises(CheckViolation):
        with owner.transaction():
            owner.execute(
                "UPDATE stored_file SET status = 'PENDING_DELETE', deleted_at = NULL"
                " WHERE file_id = %s",
                (file_id,),
            )


def test_not_even_the_owner_changes_a_files_identity(owner) -> None:
    file_id = str(uuid4())
    _insert(owner, _row(file_id=file_id))

    with pytest.raises(CheckViolation) as refusal:
        with owner.transaction():
            owner.execute(
                "UPDATE stored_file SET privacy = 'PRIVATE' WHERE file_id = %s",
                (file_id,),
            )

    assert refusal.value.diag.constraint_name == "stored_file_identity_immutable"


# ---------- a live database is upgraded in place, not rebuilt (#1205) ----------

_SCHEMA = Path(__file__).resolve().parents[1] / "sql" / "01_schema.sql"


def _store_block() -> str:
    """The block the way an operator extracts it (deploy/README.md)."""
    text = _SCHEMA.read_text(encoding="utf-8")
    found = re.search(
        r"^-- BEGIN FILE STORE.*?^-- END FILE STORE$", text, re.MULTILINE | re.DOTALL
    )
    assert found, "the FILE STORE block is missing"
    return found.group(0)


def test_a_database_that_predates_the_file_store_keeps_its_images_and_gains_the_store(
    owner,
) -> None:
    # The state of the instance before this release: no stored_file, no
    # image_file_id, and products whose pictures are named in image_path, which is
    # all that deploy/migrate-uploads-1189.py has to go on.
    owner.execute("DROP TRIGGER trg_product_image_file_matches ON product")
    owner.execute("ALTER TABLE product DROP COLUMN image_file_id")
    owner.execute("ALTER TABLE sales_load DROP COLUMN source_file_id")
    owner.execute("DROP TABLE file_access")
    owner.execute("DROP TABLE stored_file")
    legacy = _a_product(owner)
    owner.execute(
        "UPDATE product SET image_path = %s WHERE product_id = %s",
        ("0123456789abcdef0123456789abcdef.jpg", legacy),
    )

    for _ in range(2):  # re-runnable
        owner.execute(_store_block())

    assert owner.execute(
        "SELECT image_path, image_file_id FROM product WHERE product_id = %s",
        (legacy,),
    ).fetchone() == ("0123456789abcdef0123456789abcdef.jpg", None)
    assert owner.execute("SELECT count(*) FROM stored_file").fetchone()[0] == 0
    # The private-file pieces of #1190 come back with it.
    assert (
        owner.execute(
            "SELECT count(*) FROM information_schema.columns"
            " WHERE table_name = 'sales_load' AND column_name = 'source_file_id'"
        ).fetchone()[0]
        == 1
    )
    assert owner.execute("SELECT count(*) FROM file_access").fetchone()[0] == 0
    # What the migration selects is still there to be selected.
    assert (
        owner.execute(
            "SELECT count(*) FROM product WHERE image_path IS NOT NULL"
            " AND image_file_id IS NULL AND product_id = %s",
            (legacy,),
        ).fetchone()[0]
        == 1
    )
    # And the new rules are in force again.
    file_id = str(uuid4())
    _insert(
        owner,
        _row(
            file_id=file_id,
            content_type="image/png",
            object_path=f"product-image/{file_id}.png",
        ),
    )
    with pytest.raises(CheckViolation):
        with owner.transaction():
            owner.execute(
                "UPDATE product SET image_file_id = %s, image_path = %s"
                " WHERE product_id = %s",
                (file_id, f"{file_id}.jpg", legacy),
            )
    _as_web(owner)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with owner.transaction():
            owner.execute("DELETE FROM stored_file")


def test_the_block_is_the_only_definition_of_the_file_store() -> None:
    text = _SCHEMA.read_text(encoding="utf-8")
    product = text.split("CREATE TABLE product (", 1)[1].split("\n);", 1)[0]

    assert text.count("CREATE TABLE IF NOT EXISTS stored_file (") == 1
    assert "CREATE TABLE stored_file" not in text
    assert "image_file_id UUID" not in product
    assert "CREATE OR REPLACE TRIGGER trg_product_image_file_matches" in _store_block()


def test_a_service_that_writes_products_and_cannot_read_stored_file_is_unaffected(
    owner,
) -> None:
    # products-service writes `product` as svc_products, which holds no privilege on
    # stored_file. The image check must not run for a product with no picture.
    owner.execute("SET LOCAL ROLE svc_products")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with owner.transaction():
            owner.execute("SELECT count(*) FROM stored_file")

    owner.execute("UPDATE product SET name = name WHERE product_id = 1")
    assert owner.execute(
        "SELECT count(*) FROM product WHERE product_id = 1"
    ).fetchone() == (1,)


# ---------- a private file kind and the record of its requests (F29-14) ----------


def test_a_csv_is_a_stored_file_and_pairs_only_with_its_own_extension(owner) -> None:
    file_id = str(uuid4())
    _insert(
        owner,
        _row(
            file_id=file_id,
            content_type="text/csv",
            privacy="PRIVATE",
            object_path=f"sales-import/{file_id}.csv",
        ),
    )
    other = str(uuid4())
    _refused(
        owner,
        _row(
            file_id=other,
            content_type="text/csv",
            object_path=f"sales-import/{other}.png",
        ),
        "stored_file_object_path_check",
    )
    third = str(uuid4())
    _refused(
        owner,
        _row(
            file_id=third,
            content_type="image/png",
            object_path=f"sales-import/{third}.csv",
        ),
        "stored_file_object_path_check",
    )


def _private_csv(owner: psycopg.Connection) -> str:
    file_id = str(uuid4())
    _insert(
        owner,
        _row(
            file_id=file_id,
            content_type="text/csv",
            privacy="PRIVATE",
            object_path=f"sales-import/{file_id}.csv",
        ),
    )
    return file_id


def _access(owner: psycopg.Connection, file_id: str, **changes) -> None:
    row = {
        "file_id": file_id,
        "actor": ADMIN_ID,
        "role": "ADMIN",
        "outcome": "ISSUED",
        "at": "2026-11-23T12:00:00Z",
        "expires": "2026-11-23T12:10:00Z",
    }
    row.update(changes)
    owner.execute(
        "INSERT INTO file_access (file_id, actor_user_id, role_code, outcome,"
        " occurred_at, expires_at) VALUES (%(file_id)s, %(actor)s, %(role)s,"
        " %(outcome)s, %(at)s, %(expires)s)",
        row,
    )


def test_an_issued_url_and_a_refusal_are_both_recorded(owner) -> None:
    file_id = _private_csv(owner)

    _access(owner, file_id)
    _access(owner, file_id, outcome="REFUSED", role="ANALYST", expires=None)


@pytest.mark.parametrize(
    "changes,constraint",
    [
        # Two CHECKs refuse an unknown outcome; PostgreSQL names the first it meets.
        ({"outcome": "GRANTED"}, "file_access_expiry_check"),
        ({"expires": None}, "file_access_expiry_check"),  # issued, no expiry
        ({"expires": "2026-11-23T12:15:01Z"}, "file_access_expiry_check"),  # >15 min
        ({"expires": "2026-11-23T12:00:00Z"}, "file_access_expiry_check"),  # not after
        ({"expires": "2026-11-23T11:59:00Z"}, "file_access_expiry_check"),
        (
            {"outcome": "REFUSED", "expires": "2026-11-23T12:10:00Z"},
            "file_access_expiry_check",  # a refusal issued nothing
        ),
    ],
    ids=[
        "unknown-outcome",
        "issued-without-expiry",
        "over-fifteen-minutes",
        "expires-at-request",
        "expired-before-request",
        "refused-with-url-expiry",
    ],
)
def test_the_record_of_a_request_refuses_what_cannot_be_true(
    owner, changes, constraint
) -> None:
    file_id = _private_csv(owner)

    with pytest.raises(CheckViolation) as refusal:
        with owner.transaction():
            _access(owner, file_id, **changes)

    assert refusal.value.diag.constraint_name == constraint


def test_fifteen_minutes_exactly_is_accepted(owner) -> None:
    _access(owner, _private_csv(owner), expires="2026-11-23T12:15:00Z")


def test_the_web_can_add_to_the_record_of_requests_but_never_change_it(owner) -> None:
    file_id = _private_csv(owner)
    owner.execute("SET LOCAL ROLE retail_app")
    _access(owner, file_id)

    for statement in (
        "UPDATE file_access SET role_code = 'X'",
        "DELETE FROM file_access",
        "TRUNCATE file_access",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with owner.transaction():
                owner.execute(statement)

    assert owner.execute("SELECT count(*) FROM file_access").fetchone()[0] >= 1


def test_a_sales_load_keeps_at_most_one_source_file_and_a_file_one_load(owner) -> None:
    file_id = _private_csv(owner)
    owner.execute("UPDATE sales_load SET source_file_id = NULL WHERE load_id IN (1, 2)")

    owner.execute(
        "UPDATE sales_load SET source_file_id = %s WHERE load_id = 1", (file_id,)
    )
    with pytest.raises(UniqueViolation):
        with owner.transaction():
            owner.execute(
                "UPDATE sales_load SET source_file_id = %s WHERE load_id = 2",
                (file_id,),
            )


def test_the_seed_gives_every_load_a_source_file_and_every_file_one_request(
    owner,
) -> None:
    with owner.cursor() as cursor:
        cursor.execute(
            "SELECT count(*), count(*) FILTER (WHERE f.status = 'DELETED'),"
            " count(*) FILTER (WHERE f.privacy = 'PRIVATE'),"
            " count(*) FILTER"
            " (WHERE f.object_path = 'sales-import/' || f.file_id || '.csv')"
            " FROM sales_load l JOIN stored_file f ON f.file_id = l.source_file_id"
            " WHERE f.file_id::text LIKE '44444444-4444-4444-4444-%'"
        )
        assert cursor.fetchone() == (30, 30, 30, 30)
        cursor.execute(
            "SELECT count(*), count(*) FILTER (WHERE outcome = 'ISSUED'),"
            " count(*) FILTER (WHERE outcome = 'REFUSED' AND role_code = 'ANALYST'),"
            " count(DISTINCT file_id) FROM file_access"
        )
        assert cursor.fetchone() == (30, 24, 6, 30)
        cursor.execute("SELECT count(*) FROM stored_file")
        assert cursor.fetchone()[0] == 60
