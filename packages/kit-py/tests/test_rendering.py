from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from flask import Flask
from lxml import etree

from mosaiq_kit import Collection, NoContent, Page, Representation, money
from mosaiq_kit.rendering import render_representation


def test_money_is_explicit_and_bare_decimal_remains_a_number() -> None:
    assert money(Decimal("1.2")) == "1.20"
    app = Flask("render-json")
    with app.app_context():
        response = render_representation(
            Representation(
                "Value", {"amount": money("1.2"), "coordinate": Decimal("1.25")}
            ),
            "json",
            "https://example.invalid/value",
        )
    assert json.loads(response.data) == {"amount": "1.20", "coordinate": 1.25}


def test_timestamps_are_utc_rfc3339_and_dates_are_full_dates() -> None:
    app = Flask("render-time")
    value = datetime(
        2026,
        10,
        15,
        8,
        30,
        4,
        123456,
        tzinfo=timezone(timedelta(hours=-4)),
    )
    with app.app_context():
        response = render_representation(
            Representation("Value", {"at": value, "day": date(2026, 10, 15)}),
            "json",
            "https://example.invalid/value",
        )
    assert response.json == {
        "at": "2026-10-15T12:30:04.123456Z",
        "day": "2026-10-15",
    }


def test_naive_timestamp_is_refused() -> None:
    app = Flask("render-naive")
    with app.app_context(), pytest.raises(ValueError):
        render_representation(
            Representation("Value", {"at": datetime(2026, 10, 15)}),
            "json",
            "https://example.invalid/value",
        )


def test_absent_value_is_null_in_json_and_omitted_in_xml() -> None:
    app = Flask("render-none")
    namespace = "https://example.invalid/value"
    value = Representation("Value", {"present": "yes", "absent": None})
    with app.app_context():
        json_response = render_representation(value, "json", namespace)
        xml_response = render_representation(value, "xml", namespace)
    assert json_response.json["absent"] is None
    root = etree.fromstring(xml_response.data)
    assert root.find(f"{{{namespace}}}absent") is None


def test_collection_and_page_shapes() -> None:
    app = Flask("render-collections")
    namespace = "https://example.invalid/value"
    with app.app_context():
        collection = render_representation(
            Collection("Values", "Value", "values", [{"id": 1}, {"id": 2}]),
            "json",
            namespace,
        )
        page = render_representation(
            Page(
                "Values",
                "Value",
                "values",
                [{"id": 1}],
                page=2,
                size=10,
                total=15,
            ),
            "xml",
            namespace,
        )
    assert collection.json == {"values": [{"id": 1}, {"id": 2}]}
    root = etree.fromstring(page.data)
    assert len(root.findall(f"{{{namespace}}}Value")) == 1
    page_info = root.find("{https://mosaiq.maxthecoder.online/xml/common/v1}PageInfo")
    assert page_info is not None


def test_example_xml_page_validates_against_the_service_xsd(
    client, reader_headers, contracts_dir
) -> None:
    response = client.get(
        "/v1/notes", headers={**reader_headers, "Accept": "application/xml"}
    )
    schema = etree.XMLSchema(
        etree.parse(str(contracts_dir / "kit-example/kit-example.xsd"))
    )
    assert schema.validate(etree.fromstring(response.data)), schema.error_log


def test_empty_xml_page_uses_the_requested_size_and_validates(
    client, reader_headers, contracts_dir
) -> None:
    response = client.get(
        "/v1/notes?page=2&size=7",
        headers={**reader_headers, "Accept": "application/xml"},
    )
    schema = etree.XMLSchema(
        etree.parse(str(contracts_dir / "kit-example/kit-example.xsd"))
    )
    root = etree.fromstring(response.data)
    page_info = root.find("{https://mosaiq.maxthecoder.online/xml/common/v1}PageInfo")
    assert page_info is not None
    assert (
        page_info.findtext("{https://mosaiq.maxthecoder.online/xml/common/v1}size")
        == "7"
    )
    assert schema.validate(root), schema.error_log


def test_xml_error_validates_as_the_common_error(
    client, writer_headers, contracts_dir
) -> None:
    response = client.post(
        "/v1/notes",
        headers={**writer_headers, "Accept": "application/xml"},
        data=b"{",
        content_type="application/json",
    )
    schema = etree.XMLSchema(etree.parse(str(contracts_dir / "common/v1/common.xsd")))
    root = etree.fromstring(response.data)
    assert root.tag == "{https://mosaiq.maxthecoder.online/xml/common/v1}Error"
    assert schema.validate(root), schema.error_log


def test_xsi_type_is_attached_to_the_root() -> None:
    app = Flask("render-type")
    namespace = "https://example.invalid/value"
    with app.app_context():
        response = render_representation(
            Representation("Value", {"id": 1}, xsi_type="SpecialValue"),
            "xml",
            namespace,
        )
    root = etree.fromstring(response.data)
    assert root.get("{http://www.w3.org/2001/XMLSchema-instance}type") == "SpecialValue"


def test_no_content_is_204() -> None:
    app = Flask("render-empty")
    with app.app_context():
        response = render_representation(
            NoContent(), "json", "https://example.invalid/value"
        )
    assert response.status_code == 204
    assert response.data == b""
    assert "Content-Type" not in response.headers
