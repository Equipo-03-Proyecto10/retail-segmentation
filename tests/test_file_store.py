"""The file store port and its two backends (F29-13, ADR-0091), without a database.

The local backend runs for real, on a temporary directory. The GCS backend runs
against a fake client object that records the calls it receives: the real cloud
is not available to the tests, so what they establish is the calls the web makes
and how it reads the answers, not that Google accepts them. The upload, read and
delete flows against PostgreSQL are in tests/test_file_store_postgres.py.
"""

from __future__ import annotations

import logging
import os
import sys
import types
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from web.config import Config
from web.services.files import (
    GCS_DEADLINE_SECONDS,
    GCS_TIMEOUT_SECONDS,
    GcsFileStore,
    LocalFileStore,
    ObjectMissing,
    StoreUnavailable,
    build_file_store,
)

PATH = "product-image/6f1c0de2-0000-4000-8000-000000000001.jpg"
# Stands for the client's retry policy, which these tests do not load.
RETRY = "a-retry-policy"


# ---------- the local backend ----------


def test_the_local_store_round_trips_an_object(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)

    store.put(PATH, b"\xff\xd8\xffbytes", "image/jpeg")

    assert store.get(PATH) == b"\xff\xd8\xffbytes"
    assert (tmp_path / PATH).read_bytes() == b"\xff\xd8\xffbytes"
    # Nothing half-written is left beside it.
    assert [p.name for p in (tmp_path / "product-image").iterdir()] == [Path(PATH).name]


def test_the_local_store_deletes_and_deleting_twice_is_not_an_error(
    tmp_path: Path,
) -> None:
    store = LocalFileStore(tmp_path)
    store.put(PATH, b"x", "image/jpeg")

    store.delete(PATH)
    store.delete(PATH)

    with pytest.raises(ObjectMissing):
        store.get(PATH)


def test_the_local_store_never_leaves_its_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    store = LocalFileStore(tmp_path / "store")

    for attempt in ("../outside", "product-image/../../outside", "/etc/passwd"):
        with pytest.raises(StoreUnavailable):
            store.put(attempt, b"x", "image/jpeg")
        with pytest.raises(StoreUnavailable):
            store.delete(attempt)

    assert outside.read_bytes() == b"keep"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_local_store_is_unavailable_and_says_so_in_the_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with caplog.at_level(logging.ERROR):
            with pytest.raises(StoreUnavailable):
                LocalFileStore(locked).put(PATH, b"x", "image/jpeg")
    finally:
        locked.chmod(0o700)

    assert "file_store_write_failed backend=local" in caplog.text
    assert not list(locked.iterdir())


# ---------- the GCS backend, against a fake client ----------


class FakeNotFound(Exception):
    """What google.api_core.exceptions.NotFound looks like to the store: an
    exception whose `code` is the HTTP status."""

    code = 404


class FakeForbidden(Exception):
    code = 403


class FakeBlob:
    def __init__(self, bucket: FakeBucket, name: str) -> None:
        self.bucket = bucket
        self.name = name

    def upload_from_string(self, data: bytes, **kwargs: Any) -> None:
        self.bucket.client.calls.append(("upload_from_string", self.name, data, kwargs))
        self.bucket.client.fail("upload_from_string")
        self.bucket.client.objects[self.name] = data

    def download_as_bytes(self, **kwargs: Any) -> bytes:
        self.bucket.client.calls.append(("download_as_bytes", self.name, kwargs))
        self.bucket.client.fail("download_as_bytes")
        if self.name not in self.bucket.client.objects:
            raise FakeNotFound(self.name)
        return self.bucket.client.objects[self.name]

    def delete(self, **kwargs: Any) -> None:
        self.bucket.client.calls.append(("delete", self.name, kwargs))
        self.bucket.client.fail("delete")
        if self.name not in self.bucket.client.objects:
            raise FakeNotFound(self.name)
        del self.bucket.client.objects[self.name]


class FakeBucket:
    def __init__(self, client: FakeClient, name: str) -> None:
        self.client = client
        self.name = name

    def blob(self, name: str) -> FakeBlob:
        self.client.calls.append(("blob", self.name, name))
        return FakeBlob(self, name)


class FakeClient:
    """Records every call and keeps objects in a dict. `failures` maps a method
    to the exception it raises, to play the bucket being down or refusing."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.objects: dict[str, bytes] = {}
        self.failures: dict[str, Exception] = {}

    def bucket(self, name: str) -> FakeBucket:
        self.calls.append(("bucket", name))
        return FakeBucket(self, name)

    def fail(self, method: str) -> None:
        if method in self.failures:
            raise self.failures[method]


def test_gcs_put_writes_one_object_with_its_type_a_guard_and_a_timeout() -> None:
    client = FakeClient()
    store = GcsFileStore(
        "the-private-bucket", client=client, retry_policy=lambda: RETRY
    )

    store.put(PATH, b"\xff\xd8\xffbytes", "image/jpeg")

    assert ("bucket", "the-private-bucket") in client.calls
    upload = next(call for call in client.calls if call[0] == "upload_from_string")
    assert upload[1] == PATH
    assert upload[2] == b"\xff\xd8\xffbytes"
    assert upload[3] == {
        "content_type": "image/jpeg",
        # A name is used once: the write succeeds only if nothing is there.
        "if_generation_match": 0,
        "timeout": GCS_TIMEOUT_SECONDS,
        "retry": RETRY,
    }
    assert client.objects == {PATH: b"\xff\xd8\xffbytes"}


def test_gcs_get_returns_the_bytes_and_an_absent_object_is_missing() -> None:
    client = FakeClient()
    store = GcsFileStore("b", client=client, retry_policy=lambda: RETRY)
    store.put(PATH, b"data", "image/png")

    assert store.get(PATH) == b"data"
    with pytest.raises(ObjectMissing):
        store.get("product-image/absent.png")


def test_gcs_delete_removes_the_object_and_an_absent_one_is_not_an_error() -> None:
    client = FakeClient()
    store = GcsFileStore("b", client=client, retry_policy=lambda: RETRY)
    store.put(PATH, b"data", "image/png")

    store.delete(PATH)
    store.delete(PATH)

    assert client.objects == {}


@pytest.mark.parametrize(
    "method,call",
    [
        ("upload_from_string", lambda s: s.put(PATH, b"x", "image/jpeg")),
        ("download_as_bytes", lambda s: s.get(PATH)),
        ("delete", lambda s: s.delete(PATH)),
    ],
)
@pytest.mark.parametrize(
    "failure",
    [ConnectionError("unreachable"), FakeForbidden("403 caller lacks permission")],
    ids=["unreachable", "forbidden"],
)
def test_any_other_failure_of_the_bucket_is_unavailable_and_logged(
    method: str, call, failure: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    client = FakeClient()
    client.failures[method] = failure
    store = GcsFileStore(
        "the-private-bucket", client=client, retry_policy=lambda: RETRY
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(StoreUnavailable):
            call(store)

    assert "backend=gcs bucket='the-private-bucket'" in caplog.text
    assert type(failure).__name__ in caplog.text


def _install_fake_library(monkeypatch: pytest.MonkeyPatch, client_factory) -> list:
    """Put a stand-in for `google.cloud.storage` where the lazy import finds it."""
    built: list = []
    storage = types.ModuleType("google.cloud.storage")

    def Client() -> Any:  # noqa: N802 — the library's own name
        client = client_factory()
        built.append(client)
        return client

    storage.Client = Client
    cloud = types.ModuleType("google.cloud")
    cloud.storage = storage
    google = types.ModuleType("google")
    google.cloud = cloud
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage)

    class Policy:
        def with_deadline(self, seconds: float) -> tuple:
            return ("retry-until", seconds)

    retry = types.ModuleType("google.cloud.storage.retry")
    retry.DEFAULT_RETRY = Policy()
    storage.retry = retry
    monkeypatch.setitem(sys.modules, "google.cloud.storage.retry", retry)
    return built


def test_the_gcs_client_is_built_once_and_only_when_first_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Building the store, and so the application, needs neither the library nor
    # the network: the import and the client wait for the first call.
    built = _install_fake_library(monkeypatch, FakeClient)
    store = GcsFileStore("b")
    assert built == []

    store.put(PATH, b"x", "image/jpeg")
    store.get(PATH)

    assert len(built) == 1
    assert built[0].objects == {PATH: b"x"}


def test_every_call_to_the_bucket_has_a_whole_operation_deadline_shorter_than_the_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The client's `timeout` bounds one HTTP attempt; its retry policy bounds the
    # operation, 120 seconds by default: as long as gunicorn's worker and twice
    # NGINX's 60. Each call must carry a policy cut to GCS_DEADLINE_SECONDS.
    built = _install_fake_library(monkeypatch, FakeClient)
    store = GcsFileStore("b")

    store.put(PATH, b"x", "image/jpeg")
    store.get(PATH)
    store.delete(PATH)

    calls = built[0].calls
    retries = [
        call[-1]["retry"]
        for call in calls
        if call[0] in {"upload_from_string", "download_as_bytes", "delete"}
    ]
    assert retries == [("retry-until", GCS_DEADLINE_SECONDS)] * 3
    assert GCS_TIMEOUT_SECONDS <= GCS_DEADLINE_SECONDS < 60


def test_the_real_clients_default_retry_is_cut_to_the_deadline() -> None:
    # The library is a runtime requirement (web/requirements.txt), installed in CI.
    retry = pytest.importorskip("google.cloud.storage.retry")

    cut = retry.DEFAULT_RETRY.with_deadline(GCS_DEADLINE_SECONDS)

    assert retry.DEFAULT_RETRY.deadline == 120  # what an unbounded call would get
    assert cut.deadline == GCS_DEADLINE_SECONDS


def test_credentials_that_cannot_be_found_are_unavailable_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class DefaultCredentialsError(Exception):
        pass

    def no_credentials() -> Any:
        raise DefaultCredentialsError("could not find default credentials")

    _install_fake_library(monkeypatch, no_credentials)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(StoreUnavailable):
            GcsFileStore("b").get(PATH)

    assert "DefaultCredentialsError" in caplog.text


# ---------- choosing the backend ----------


def _config(**changes: Any) -> Config:
    return replace(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused",
        ),
        **changes,
    )


def test_the_configuration_chooses_the_backend(tmp_path: Path) -> None:
    local = build_file_store(_config(upload_dir=str(tmp_path)))
    gcs = build_file_store(_config(file_store_backend="gcs", gcs_bucket="a-bucket"))

    assert isinstance(local, LocalFileStore)
    assert isinstance(gcs, GcsFileStore)


def test_a_gcs_store_without_a_bucket_is_refused() -> None:
    with pytest.raises(ValueError, match="bucket"):
        build_file_store(_config(file_store_backend="gcs"))
