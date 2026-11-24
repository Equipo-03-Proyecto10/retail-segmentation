"""Signed URLs for PRIVATE files, without a database (F29-14, ADR-0091).

The GCS signer runs against a fake client that records the call it receives: the
real cloud is not available to the tests, so what is established is that the web
asks for a V4, GET, at-most-15-minute URL, for one object, with no public ACL, and
signs as the attached account. That Google then accepts and expires it is for the
instance (docs/evidence). The local signer runs for real, against the real clock.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from tests.test_file_store import FakeBlob, FakeBucket, FakeClient
from web.config import Config
from web.db.stored_files import StoredFile
from web.services.file_urls import (
    SignedUrl,
    check_local_url,
    download_name,
    issue_url,
    kind_of,
    read_permission,
    signature,
    ttl_seconds,
)
from web.services.files import GcsFileStore, LocalFileStore, StoreUnavailable

FILE_ID = UUID("44444444-4444-4444-4444-000000000099")
SECRET = "a-secret-for-the-test"
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def _file(kind: str = "sales-import", ext: str = "csv") -> StoredFile:
    return StoredFile(
        file_id=FILE_ID,
        object_path=f"{kind}/{FILE_ID}.{ext}",
        content_type="text/csv",
        size_bytes=10,
        sha256="a" * 64,
        privacy="PRIVATE",
        status="STORED",
        owner_user_id=UUID(int=1),
        created_at=NOW,
        metadata={},
    )


def _config(**changes: Any) -> Config:
    return replace(
        Config(
            secret_key=SECRET,
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused",
        ),
        **changes,
    )


def _local_url(file_id: UUID, expires: int, sig: str) -> str:
    return f"/files/{file_id}/content?expires={expires}&signature={sig}"


# ---------- the GCS signer ----------


class SigningBlob(FakeBlob):
    def generate_signed_url(self, **kwargs: Any) -> str:
        self.bucket.client.calls.append(("generate_signed_url", self.name, kwargs))
        self.bucket.client.fail("generate_signed_url")
        return (
            f"https://storage.googleapis.com/b/{self.name}?X-Goog-Signature=SECRETSIG"
        )


class SigningBucket(FakeBucket):
    def blob(self, name: str) -> SigningBlob:
        self.client.calls.append(("blob", self.name, name))
        return SigningBlob(self, name)


class SigningClient(FakeClient):
    def bucket(self, name: str) -> SigningBucket:
        self.calls.append(("bucket", name))
        return SigningBucket(self, name)


def _gcs(client: SigningClient) -> GcsFileStore:
    return GcsFileStore(
        "the-private-bucket",
        client=client,
        signer_identity=lambda: ("files@project.iam.gserviceaccount.com", "ya29.token"),
    )


def test_the_gcs_signer_asks_for_a_v4_get_url_of_at_most_fifteen_minutes() -> None:
    client = SigningClient()
    file = _file()

    signed = issue_url(file, _gcs(client), _config(), now=NOW, local_url=_local_url)

    call = next(c for c in client.calls if c[0] == "generate_signed_url")
    assert call[1] == file.object_path
    arguments = call[2]
    assert arguments["version"] == "v4"
    assert arguments["method"] == "GET"
    assert arguments["expiration"] == timedelta(seconds=600)
    assert arguments["expiration"] <= timedelta(minutes=15)
    # Signed as the instance's attached account, through IAM signBlob: the
    # account's email and a token, never a private key.
    assert arguments["service_account_email"] == "files@project.iam.gserviceaccount.com"
    assert arguments["access_token"] == "ya29.token"
    assert "private_key" not in arguments and "credentials" not in arguments
    assert arguments["response_disposition"] == (
        f'attachment; filename="sales-import-{FILE_ID}.csv"'
    )
    assert signed.url.startswith("https://storage.googleapis.com/")
    assert signed.expires_at == NOW + timedelta(seconds=600)


def test_the_gcs_signer_never_makes_the_object_public() -> None:
    client = SigningClient()

    issue_url(_file(), _gcs(client), _config(), now=NOW, local_url=_local_url)

    # Nothing but a bucket, a blob and the signing call: no ACL, no
    # make_public, no upload.
    assert {call[0] for call in client.calls} == {
        "bucket",
        "blob",
        "generate_signed_url",
    }
    arguments = next(c for c in client.calls if c[0] == "generate_signed_url")[2]
    assert not {"acl", "predefined_acl", "make_public"} & set(arguments)


@pytest.mark.parametrize("configured", [10_000, 901, 900])
def test_no_configuration_issues_a_url_longer_than_fifteen_minutes(
    configured: int,
) -> None:
    client = SigningClient()

    signed = issue_url(
        _file(),
        _gcs(client),
        _config(signed_url_ttl_seconds=configured),
        now=NOW,
        local_url=_local_url,
    )

    arguments = next(c for c in client.calls if c[0] == "generate_signed_url")[2]
    assert arguments["expiration"] == timedelta(seconds=900)
    assert signed.expires_at - NOW == timedelta(minutes=15)


def test_the_ttl_is_never_below_one_second() -> None:
    assert ttl_seconds(_config(signed_url_ttl_seconds=0)) == 1
    assert ttl_seconds(_config(signed_url_ttl_seconds=-5)) == 1


def test_a_signer_that_fails_is_unavailable_and_logs_no_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = SigningClient()
    client.failures["generate_signed_url"] = PermissionError(
        "caller lacks iam.serviceAccounts.signBlob"
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(StoreUnavailable):
            issue_url(_file(), _gcs(client), _config(), now=NOW, local_url=_local_url)

    assert "file_store_sign_failed backend=gcs" in caplog.text
    assert "ya29.token" not in caplog.text


def test_the_url_is_not_in_the_repr_of_a_signed_url() -> None:
    signed = SignedUrl(url="https://x/?X-Goog-Signature=SECRETSIG", expires_at=NOW)

    assert "SECRETSIG" not in repr(signed) and "SECRETSIG" not in str(signed)


# ---------- the local signer, on the real clock ----------


def _issue_local(config: Config, now: datetime) -> tuple[int, str]:
    signed = issue_url(
        _file(), LocalFileStore("."), config, now=now, local_url=_local_url
    )
    query = signed.url.split("?", 1)[1]
    values = dict(part.split("=", 1) for part in query.split("&"))
    return int(values["expires"]), values["signature"]


def test_a_local_url_is_good_until_it_expires_and_then_refused() -> None:
    config = _config(signed_url_ttl_seconds=2)
    expires, sig = _issue_local(config, datetime.now(UTC))

    assert check_local_url(SECRET, FILE_ID, expires, sig, now=datetime.now(UTC)) is None

    time.sleep(2.3)

    assert (
        check_local_url(SECRET, FILE_ID, expires, sig, now=datetime.now(UTC))
        == "expired"
    )


def test_a_local_url_lives_the_configured_time_and_no_more_than_fifteen_minutes() -> (
    None
):
    expires, _ = _issue_local(_config(signed_url_ttl_seconds=99_999), NOW)

    assert expires == int((NOW + timedelta(minutes=15)).timestamp())


@pytest.mark.parametrize(
    "tamper",
    [
        lambda e, s: (e + 3600, s),  # a later expiry, the old signature
        lambda e, s: (e, s[:-1] + ("0" if s[-1] != "0" else "1")),
        lambda e, s: (e, s[:32]),  # truncated
        lambda e, s: (e, s.upper()),
        lambda e, s: (e, ""),
        lambda e, s: (None, s),
    ],
    ids=[
        "later-expiry",
        "flipped-digit",
        "truncated",
        "upper-case",
        "empty",
        "no-expiry",
    ],
)
def test_a_tampered_local_url_is_refused(tamper) -> None:
    expires, sig = _issue_local(_config(), NOW)
    forged_expires, forged_sig = tamper(expires, sig)

    refusal = check_local_url(SECRET, FILE_ID, forged_expires, forged_sig, now=NOW)

    assert refusal in {"signature", "malformed"}


def test_a_local_url_is_for_one_file_and_one_secret() -> None:
    expires, sig = _issue_local(_config(), NOW)
    other = UUID("44444444-4444-4444-4444-000000000098")

    assert check_local_url(SECRET, other, expires, sig, now=NOW) == "signature"
    assert (
        check_local_url("another-secret", FILE_ID, expires, sig, now=NOW) == "signature"
    )
    assert signature(SECRET, FILE_ID, expires) == sig


def test_a_forged_url_is_not_told_that_it_is_late() -> None:
    expires = int(NOW.timestamp()) - 10_000

    assert check_local_url(SECRET, FILE_ID, expires, "0" * 64, now=NOW) == "signature"


# ---------- who reads what ----------


def test_a_kind_without_a_reader_has_no_permission() -> None:
    assert read_permission(_file()) == "sales_ingest.execute"
    assert read_permission(_file(kind="mystery")) is None
    assert kind_of(_file()) == "sales-import"
    assert download_name(_file()) == f"sales-import-{FILE_ID}.csv"
