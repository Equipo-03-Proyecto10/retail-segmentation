"""Planned operations (ADR-0073): excluded from cases, guarded once answered."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("yaml")

from tests.conformance.harness import load_contract  # noqa: E402
from tests.conformance.test_conformance import planned_gaps  # noqa: E402

DOC = """\
openapi: 3.0.3
paths:
  /v1/things:
    get:
      operationId: listThings
      security: [{BearerAuth: []}]
      x-mosaiq-roles: [ADMIN]
      responses: {"200": {description: ok}}
    post:
      operationId: addThing
      security: [{BearerAuth: []}]
      x-mosaiq-roles: [ADMIN]
      x-mosaiq-status: planned
      responses: {"201": {description: ok}}
"""


def _contract(tmp_path: Path, text: str = DOC):
    (tmp_path / "openapi.yaml").write_text(text)
    return load_contract(tmp_path)


def test_a_planned_operation_leaves_every_case_and_is_listed(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    assert [o.operation_id for o in contract.operations] == ["listThings"]
    assert [o.operation_id for o in contract.planned] == ["addThing"]


def test_an_unknown_status_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _contract(tmp_path, DOC.replace("status: planned", "status: later"))


@pytest.mark.parametrize("status", [404, 405])
def test_an_unanswered_planned_operation_passes_the_guard(
    tmp_path: Path, status: int
) -> None:
    assert planned_gaps(_contract(tmp_path), lambda m, p: status) == []


@pytest.mark.parametrize("status", [401, 403, 422, 200])
def test_an_answered_planned_operation_fails_the_guard(
    tmp_path: Path, status: int
) -> None:
    gaps = planned_gaps(_contract(tmp_path), lambda m, p: status)
    assert gaps and "addThing" in gaps[0]


def test_an_xml_reply_is_validated_against_the_xsd_of_its_namespace() -> None:
    """engagement serves /v1 and /v2 in two namespaces (#799)."""

    root = Path(__file__).resolve().parents[1] / "contracts/engagement"
    contract = load_contract(root)
    v2 = "https://mosaiq.maxthecoder.online/xml/engagement/v2"
    assert contract.xsd_for(v2) == root / "engagement.v2.xsd"
    v1 = "https://mosaiq.maxthecoder.online/xml/engagement/v1"
    assert contract.xsd_for(v1) == root / "engagement.xsd"
    common = "https://mosaiq.maxthecoder.online/xml/common/v1"
    assert contract.xsd_for(common) == root / "engagement.xsd"
