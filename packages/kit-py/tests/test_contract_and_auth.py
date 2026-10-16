from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from mosaiq_kit.auth import JwksStore
from mosaiq_kit.contracts import Contract

REPOSITORY = Path(__file__).resolve().parents[3]


def test_unknown_key_reloads_at_most_once_per_thirty_seconds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "jwks.json"
    path.write_text('{"keys": []}')
    store = JwksStore(path)
    store._reload_requested = False
    reloads = []
    original = store._reload

    def counted_reload() -> None:
        reloads.append(True)
        original()

    monkeypatch.setattr(store, "_reload", counted_reload)
    monkeypatch.setattr("mosaiq_kit.auth.time.monotonic", lambda: 100.0)
    with pytest.raises(KeyError):
        store.key("missing")
    with pytest.raises(KeyError):
        store.key("missing")
    assert len(reloads) == 1


def test_explicit_reload_is_always_honored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = REPOSITORY / "packages/kit-vectors/jwks.json"
    path = tmp_path / "jwks.json"
    path.write_bytes(source.read_bytes())
    store = JwksStore(path)
    first = store.key("kit-vector-key")
    reloads = []
    original = store._reload

    def counted_reload() -> None:
        reloads.append(True)
        original()

    monkeypatch.setattr(store, "_reload", counted_reload)
    store.request_reload()
    second = store.key("kit-vector-key")
    assert first.public_numbers() == second.public_numbers()
    assert len(reloads) == 1


def test_polymorphic_request_uses_the_shared_xml_root_and_typed_values() -> None:
    contract = Contract(REPOSITORY / "contracts", "engagement")
    operation = contract.operation("createEngagementEvent")
    assert operation.xml_name == "EngagementEventRequest"
    body = contract.parse_xml(
        (REPOSITORY / "contracts/engagement/examples/qr-scan.request.xml").read_bytes(),
        operation,
    )
    contract.validate(body, operation.request_schema)
    assert isinstance(body["store_id"], int)
    assert isinstance(body["product_id"], int)


def test_secure_xml_parser_options_are_explicit() -> None:
    source = inspect.getsource(Contract.parse_xml)
    assert "resolve_entities=False" in source
    assert "no_network=True" in source
    assert "load_dtd=False" in source
    assert "huge_tree=False" in source


def test_project_version_matches_the_first_release() -> None:
    pyproject = (REPOSITORY / "packages/kit-py/pyproject.toml").read_text()
    readme = (REPOSITORY / "packages/kit-py/README.md").read_text()
    assert 'name = "mosaiq-kit"' in pyproject
    assert 'version = "0.1.0"' in pyproject
    assert "kit-py/v0.1.0" in readme
