"""JSON and XML rendering from one representation."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from flask import Response
from lxml import etree

from .types import Collection, NoContent, Page, Representation

COMMON_NAMESPACE = "https://mosaiq.maxthecoder.online/xml/common/v1"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"


def wire_value(value: Any) -> Any:
    """Convert rich Python values to their common JSON/XML wire values."""

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("A wire datetime must be timezone-aware.")
        utc = value.astimezone(UTC)
        timespec = "microseconds" if utc.microsecond else "seconds"
        return utc.isoformat(timespec=timespec).replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: wire_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [wire_value(item) for item in value]
    return value


def _text(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    value = wire_value(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(value, "f").rstrip("0").rstrip(".")
    return str(value)


def _append(parent: etree._Element, key: str, value: Any, namespace: str) -> None:
    if value is None:
        return
    if isinstance(value, list):
        for member in value:
            _append(parent, key, member, namespace)
        return
    child = etree.SubElement(parent, etree.QName(namespace, key))
    if isinstance(value, dict):
        for nested_key, nested_value in value.items():
            _append(child, nested_key, nested_value, namespace)
    else:
        child.text = _text(value)


def _xml_root(name: str, namespace: str, xsi_type: str | None = None) -> etree._Element:
    namespace_map = {None: namespace, "common": COMMON_NAMESPACE}
    if xsi_type:
        namespace_map["xsi"] = XSI_NAMESPACE
    root = etree.Element(etree.QName(namespace, name), nsmap=namespace_map)
    if xsi_type:
        root.set(etree.QName(XSI_NAMESPACE, "type"), xsi_type)
    return root


def _response(
    body: bytes | str, status: int, mimetype: str, headers: dict[str, str] | None
) -> Response:
    response = Response(body, status=status, content_type=mimetype)
    if headers:
        response.headers.update(headers)
    return response


def render_representation(
    value: Representation | Collection | Page | NoContent,
    response_format: str,
    namespace: str,
    request_size: int = 20,
) -> Response:
    """Render a public response object."""

    if isinstance(value, NoContent):
        response = Response(status=204)
        response.headers.pop("Content-Type", None)
        if value.headers:
            response.headers.update(value.headers)
        return response

    if isinstance(value, Representation):
        json_data = wire_value(value.data)
        if response_format == "json":
            return _response(
                json.dumps(json_data, separators=(",", ":")),
                value.status,
                "application/json",
                value.headers,
            )
        root = _xml_root(value.root, namespace, value.xsi_type)
        for key, item in value.data.items():
            _append(root, key, item, namespace)
        return _response(
            etree.tostring(root, xml_declaration=True, encoding="UTF-8"),
            value.status,
            "application/xml",
            value.headers,
        )

    if isinstance(value, Page):
        size = value.size if value.size is not None else request_size
        json_data = {
            value.key: wire_value(value.items),
            "page_info": {"page": value.page, "size": size, "total": value.total},
        }
    else:
        json_data = {value.key: wire_value(value.items)}

    if response_format == "json":
        return _response(
            json.dumps(json_data, separators=(",", ":")),
            value.status,
            "application/json",
            value.headers,
        )

    root = _xml_root(value.root, namespace)
    for item in value.items:
        child = etree.SubElement(root, etree.QName(namespace, value.item))
        for key, member in item.items():
            _append(child, key, member, namespace)
    if isinstance(value, Page):
        page_info = etree.SubElement(root, etree.QName(COMMON_NAMESPACE, "PageInfo"))
        _append(page_info, "page", value.page, COMMON_NAMESPACE)
        _append(page_info, "size", size, COMMON_NAMESPACE)
        _append(page_info, "total", value.total, COMMON_NAMESPACE)
    return _response(
        etree.tostring(root, xml_declaration=True, encoding="UTF-8"),
        value.status,
        "application/xml",
        value.headers,
    )


def render_error(
    code: str,
    message: str,
    correlation_id: str,
    details: list[dict[str, str]],
    status: int,
    response_format: str,
    headers: dict[str, str] | None = None,
) -> Response:
    """Render the common error type."""

    data = {
        "code": code,
        "message": message,
        "correlation_id": correlation_id,
        "details": details,
    }
    if response_format == "json":
        return _response(
            json.dumps(data, separators=(",", ":")),
            status,
            "application/json",
            headers,
        )
    root = _xml_root("Error", COMMON_NAMESPACE)
    _append(root, "code", code, COMMON_NAMESPACE)
    _append(root, "message", message, COMMON_NAMESPACE)
    _append(root, "correlation_id", correlation_id, COMMON_NAMESPACE)
    _append(root, "details", details, COMMON_NAMESPACE)
    return _response(
        etree.tostring(root, xml_declaration=True, encoding="UTF-8"),
        status,
        "application/xml",
        headers,
    )
