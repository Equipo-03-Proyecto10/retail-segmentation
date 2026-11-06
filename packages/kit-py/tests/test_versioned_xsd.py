"""A versioned XSD beside the service's own (`<service>.v2.xsd`): each
operation is validated and rendered in the namespace its schema names (#799).
"""

from __future__ import annotations

import copy
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from conftest import EXAMPLE_CONTRACT, REPOSITORY, TEST_SUFFIX
from flask import Flask
from lxml import etree

from mosaiq_kit import ApiError, Kit, Representation
from mosaiq_kit.contracts import Contract

V1 = "https://mosaiq.maxthecoder.online/xml/kit-example/v1"
V2 = "https://mosaiq.maxthecoder.online/xml/kit-example/v2"
ENGAGEMENT_V1 = "https://mosaiq.maxthecoder.online/xml/engagement/v1"
ENGAGEMENT_V2 = "https://mosaiq.maxthecoder.online/xml/engagement/v2"
CONTRACTS = REPOSITORY / "contracts"


@pytest.fixture(scope="module")
def versioned_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """kit-example plus a /v2/notes/{note_id} whose Note lives in a v2 XSD."""

    target = tmp_path_factory.mktemp("versioned")
    shutil.copytree(EXAMPLE_CONTRACT, target / "kit-example")
    shutil.copytree(CONTRACTS / "common/v1", target / "common/v1")
    service = target / "kit-example"
    xsd = (service / "kit-example.xsd").read_text().replace(V1, V2)
    (service / "kit-example.v2.xsd").write_text(xsd)
    document = yaml.safe_load((service / "openapi.yaml").read_text())
    item = copy.deepcopy(document["paths"]["/v1/notes/{note_id}"])
    item["get"]["operationId"] = "getNoteV2"
    for media in item["get"]["responses"]["200"]["content"].values():
        media["schema"] = {"$ref": "#/components/schemas/NoteV2"}
    document["paths"]["/v2/notes/{note_id}"] = item
    note = copy.deepcopy(document["components"]["schemas"]["Note"])
    note["xml"]["namespace"] = V2
    document["components"]["schemas"]["NoteV2"] = note
    (service / "openapi.yaml").write_text(yaml.safe_dump(document, sort_keys=False))
    return target


def test_each_versioned_xsd_is_loaded_under_its_namespace(
    versioned_dir: Path,
) -> None:
    contract = Contract(versioned_dir, "kit-example")
    assert set(contract.xml_schemas) == {V1, V2}
    assert contract.namespace == V1
    assert contract.operation("getNote").response_namespace == V1
    assert contract.operation("getNoteV2").response_namespace == V2


def test_a_repeated_namespace_is_refused(tmp_path: Path) -> None:
    shutil.copytree(EXAMPLE_CONTRACT, tmp_path / "kit-example")
    shutil.copytree(CONTRACTS / "common/v1", tmp_path / "common/v1")
    service = tmp_path / "kit-example"
    shutil.copy(service / "kit-example.xsd", service / "kit-example.v2.xsd")
    with pytest.raises(ValueError, match="repeats namespace"):
        Contract(tmp_path, "kit-example")


def test_a_v2_request_validates_against_the_v2_xsd() -> None:
    contract = Contract(CONTRACTS, "engagement")
    operation = contract.operation("createEngagementEventV2")
    assert operation.xml_namespace == ENGAGEMENT_V2
    assert operation.response_namespace == ENGAGEMENT_V2
    assert contract.operation("createEngagementEvent").response_namespace in (
        None,
        ENGAGEMENT_V1,
    )
    body = contract.parse_xml(
        (CONTRACTS / "engagement/examples/v2/cart-add.request.xml").read_bytes(),
        operation,
    )
    contract.validate(body, operation.request_schema)
    assert body["type"] == "CART_ADD"
    assert isinstance(body["quantity"], int)


def test_a_v1_body_is_not_accepted_by_a_v2_operation() -> None:
    contract = Contract(CONTRACTS, "engagement")
    raw = (CONTRACTS / "engagement/examples/qr-scan.request.xml").read_bytes()
    with pytest.raises(ApiError) as error:
        contract.parse_xml(raw, contract.operation("createEngagementEventV2"))
    assert error.value.status == 422


def test_a_v2_operation_renders_in_its_own_namespace(
    configured_environment: None,
    versioned_dir: Path,
    reader_headers: dict[str, str],
) -> None:
    application = Flask(__name__)
    application.testing = True
    kit = Kit(
        application,
        service="kit-example",
        suffix=TEST_SUFFIX,
        contracts_dir=versioned_dir,
        ready_checks=[lambda: True],
    )

    def note(note_id: int) -> Representation:
        return Representation(
            "Note",
            {
                "note_id": note_id,
                "title": "One",
                "amount": "12.50",
                "created_by": None,
                "created_at": datetime(2026, 10, 15, 12, 30, tzinfo=UTC),
            },
        )

    kit.operation("getNote")(note)
    kit.operation("getNoteV2")(note)
    client = application.test_client()
    headers = {**reader_headers, "Accept": "application/xml"}
    try:
        for path, namespace in (("/v1/notes/1", V1), ("/v2/notes/1", V2)):
            response = client.get(path, headers=headers)
            assert response.status_code == 200, response.data
            root = etree.fromstring(response.data)
            assert root.tag == f"{{{namespace}}}Note"
            assert (
                Contract(versioned_dir, "kit-example")
                .xml_schemas[namespace]
                .validate(root)
            )
    finally:
        kit.db.close()
