"""Staged OpenAPI and XSD loading, routing metadata, and body validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from lxml import etree

from .types import ApiError

_FORBIDDEN_XML = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


_XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("A JSON object contains a duplicate member.")
        result[key] = value
    return result


@dataclass(slots=True)
class Operation:
    operation_id: str
    path: str
    method: str
    document: dict[str, Any]
    protected: bool
    roles: tuple[str, ...]
    parameters: tuple[dict[str, Any], ...]
    request_schema: dict[str, Any] | None
    xml_name: str | None
    xml_namespace: str | None

    @property
    def paginated(self) -> bool:
        return any(
            parameter.get("in") == "query" and parameter.get("name") in {"page", "size"}
            for parameter in self.parameters
        )


class Contract:
    """One service's staged OpenAPI and XML Schema."""

    def __init__(self, contracts_dir: str | Path, service: str):
        self.root = Path(contracts_dir)
        self.service = service
        self.openapi_path = self.root / service / "openapi.yaml"
        self.xsd_path = self.root / service / f"{service}.xsd"
        self.openapi_bytes = self.openapi_path.read_bytes()
        self.xsd_bytes = self.xsd_path.read_bytes()
        self.document = yaml.safe_load(self.openapi_bytes)
        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            huge_tree=False,
        )
        xsd_document = etree.parse(str(self.xsd_path), parser)
        self.namespace = xsd_document.getroot().get("targetNamespace")
        if not self.namespace:
            raise ValueError("The service XSD has no targetNamespace.")
        self.xml_schema = etree.XMLSchema(xsd_document)
        self.operations = self._load_operations()

    def _resolve_ref(self, reference: str) -> Any:
        if not reference.startswith("#/"):
            raise ValueError(
                f"Only local contract references are supported: {reference!r}"
            )
        value: Any = self.document
        for part in reference[2:].split("/"):
            value = value[part.replace("~1", "/").replace("~0", "~")]
        return value

    def resolve(self, value: Any, seen: frozenset[str] = frozenset()) -> Any:
        """Resolve local references into a standalone schema or parameter."""

        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                if reference in seen:
                    return {}
                resolved = self.resolve(
                    self._resolve_ref(reference), seen | {reference}
                )
                additions = {key: item for key, item in value.items() if key != "$ref"}
                if additions:
                    return {**resolved, **self.resolve(additions, seen)}
                return resolved
            return {key: self.resolve(item, seen) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item, seen) for item in value]
        return value

    def _load_operations(self) -> dict[str, Operation]:
        operations: dict[str, Operation] = {}
        default_security = self.document.get("security")
        for path, path_item in self.document.get("paths", {}).items():
            path_parameters = [
                self.resolve(item) for item in path_item.get("parameters", [])
            ]
            for method, raw in path_item.items():
                if method.lower() not in {
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "head",
                }:
                    continue
                operation_id = raw.get("operationId")
                if not operation_id:
                    continue
                parameters = path_parameters + [
                    self.resolve(item) for item in raw.get("parameters", [])
                ]
                security = raw.get("security", default_security)
                protected = security != []
                roles = tuple(raw.get("x-mosaiq-roles", ()))
                request_schema = None
                xml_name = None
                xml_namespace = None
                raw_request_body = raw.get("requestBody", {})
                request_body_source = self._request_body_source(raw_request_body)
                source_content = request_body_source.get("content", {})
                source_media = source_content.get(
                    "application/json"
                ) or source_content.get("application/xml")
                component_name = self._component_name(
                    source_media.get("schema", {}) if source_media else {}
                )
                request_body = self.resolve(raw_request_body)
                content = request_body.get("content", {})
                media = content.get("application/json") or content.get(
                    "application/xml"
                )
                if media and "schema" in media:
                    request_schema = self.resolve(media["schema"])
                    xml = self._shared_xml(request_schema)
                    if xml is not None:
                        xml_name = xml.get("name") or component_name
                        xml_namespace = xml.get("namespace") or self.namespace
                    elif component_name:
                        xml_name = component_name
                        xml_namespace = self.namespace
                operations[operation_id] = Operation(
                    operation_id=operation_id,
                    path=path,
                    method=method.upper(),
                    document=raw,
                    protected=protected,
                    roles=roles,
                    parameters=tuple(parameters),
                    request_schema=request_schema,
                    xml_name=xml_name,
                    xml_namespace=xml_namespace,
                )
        return operations

    def _request_body_source(self, value: Any) -> dict[str, Any]:
        seen = set()
        while isinstance(value, dict) and "$ref" in value:
            reference = value["$ref"]
            if reference in seen:
                return {}
            seen.add(reference)
            additions = {key: item for key, item in value.items() if key != "$ref"}
            value = {**self._resolve_ref(reference), **additions}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _component_name(schema: Any) -> str | None:
        if not isinstance(schema, dict):
            return None
        reference = schema.get("$ref")
        prefix = "#/components/schemas/"
        if not isinstance(reference, str) or not reference.startswith(prefix):
            return None
        name = reference.removeprefix(prefix)
        if not name:
            return None
        return name.replace("~1", "/").replace("~0", "~")

    @staticmethod
    def _shared_xml(schema: dict[str, Any]) -> dict[str, Any] | None:
        if isinstance(schema.get("xml"), dict):
            return schema["xml"]
        candidates = schema.get("oneOf") or schema.get("anyOf") or []
        values = [item.get("xml") for item in candidates if item.get("xml")]
        if values and all(value == values[0] for value in values):
            return values[0]
        return None

    def operation(self, operation_id: str) -> Operation:
        try:
            return self.operations[operation_id]
        except KeyError:
            raise ValueError(f"Unknown OpenAPI operationId: {operation_id}") from None

    def parse_json(self, raw: bytes) -> Any:
        try:
            return json.loads(
                raw,
                object_pairs_hook=_strict_object,
                parse_float=Decimal,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"Invalid JSON constant: {value}")
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise ApiError(
                400, "malformed_body", "The request body cannot be parsed safely."
            ) from None

    def parse_xml(self, raw: bytes, operation: Operation) -> Any:
        if b"\x00" in raw or _FORBIDDEN_XML.search(raw):
            raise ApiError(
                400, "malformed_body", "The request body cannot be parsed safely."
            )
        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            huge_tree=False,
        )
        try:
            # fromstring: the body is bytes, never a path or a URL to open.
            root = etree.fromstring(raw, parser)
        except (etree.XMLSyntaxError, ValueError):
            raise ApiError(
                400, "malformed_body", "The request body cannot be parsed safely."
            ) from None
        docinfo = root.getroottree().docinfo
        if docinfo.doctype or docinfo.internalDTD is not None:
            raise ApiError(
                400, "malformed_body", "The request body cannot be parsed safely."
            )
        if any(isinstance(node, etree._Entity) for node in root.iter()):
            raise ApiError(
                400, "malformed_body", "The request body cannot be parsed safely."
            )
        expected = etree.QName(operation.xml_namespace, operation.xml_name)
        if root.tag != str(expected):
            raise ApiError(
                422,
                "invalid_body",
                "The request body violates the service schema.",
                [{"field": "$", "code": "invalid"}],
            )
        if not self.xml_schema.validate(root):
            details = []
            for error in self.xml_schema.error_log:
                detail = self._xsd_detail(error)
                if detail not in details:
                    details.append(detail)
            raise ApiError(
                422,
                "invalid_body",
                "The request body violates the service schema.",
                details or [{"field": "$", "code": "invalid"}],
            )
        return self._element_to_value(root, operation.request_schema or {})

    @staticmethod
    def _xsd_detail(error: etree._LogEntry) -> dict[str, str]:
        message = error.message
        element = re.search(r"Element '(?:\{[^}]+\})?([^']+)'", message)
        field = element.group(1) if element else "$"
        code = "invalid"
        if "Missing child element" in message:
            missing = re.search(
                r"Expected is(?: one of)? \( (?:\{[^}]+\})?([\w.-]+)", message
            )
            field = missing.group(1) if missing else field
            code = "required"
        elif "This element is not expected" in message:
            code = "unexpected_field"
        elif "[facet 'pattern']" in message:
            code = "invalid_format"
        elif "[facet 'minLength']" in message:
            code = "too_short"
        elif "[facet 'maxLength']" in message:
            code = "too_long"
        elif any(
            facet in message
            for facet in (
                "[facet 'minInclusive']",
                "[facet 'maxInclusive']",
                "[facet 'minExclusive']",
                "[facet 'maxExclusive']",
            )
        ):
            code = "out_of_range"
        elif "[facet 'enumeration']" in message:
            code = "not_allowed"
        elif "not a valid value of the atomic type" in message:
            code = "invalid_type"
        return {"field": field, "code": code}

    def _merged_properties(self, schema: dict[str, Any]) -> dict[str, Any]:
        properties = dict(schema.get("properties", {}))
        for keyword in ("allOf", "oneOf", "anyOf"):
            for branch in schema.get(keyword, []):
                properties.update(self._merged_properties(branch))
        return properties

    def _schema_type(self, schema: dict[str, Any]) -> str | None:
        schema_type = schema.get("type")
        if isinstance(schema_type, list):
            return next((item for item in schema_type if item != "null"), None)
        if schema_type:
            return schema_type
        for keyword in ("oneOf", "anyOf"):
            for branch in schema.get(keyword, []):
                branch_type = self._schema_type(branch)
                if branch_type and branch_type != "null":
                    return branch_type
        return None

    def _nullable(self, schema: dict[str, Any]) -> bool:
        schema_type = schema.get("type")
        if schema_type == "null" or (
            isinstance(schema_type, list) and "null" in schema_type
        ):
            return True
        return any(
            self._nullable(branch)
            for keyword in ("oneOf", "anyOf")
            for branch in schema.get(keyword, [])
        )

    def _required_names(self, schema: dict[str, Any]) -> set[str]:
        names = set(schema.get("required", []))
        for branch in schema.get("allOf", []):
            names |= self._required_names(branch)
        return names

    def _element_to_value(self, element: etree._Element, schema: dict[str, Any]) -> Any:
        # xsi:nil="true" is the XML spelling of JSON null.
        if element.get(_XSI_NIL) in {"true", "1"}:
            return None
        schema_type = self._schema_type(schema)
        if schema_type == "object" or len(element):
            properties = self._merged_properties(schema)
            result: dict[str, Any] = {}
            grouped: dict[str, list[etree._Element]] = {}
            for child in element:
                grouped.setdefault(etree.QName(child).localname, []).append(child)
            for name, children in grouped.items():
                child_schema = properties.get(name, {})
                if self._schema_type(child_schema) == "array":
                    item_schema = child_schema.get("items", {})
                    result[name] = [
                        self._element_to_value(child, item_schema) for child in children
                    ]
                else:
                    result[name] = self._element_to_value(children[0], child_schema)
            # "An absent value is omitted in XML and is null in JSON": a member
            # that is required and nullable, and omitted, reads as null.
            for name in self._required_names(schema):
                if name not in result and self._nullable(properties.get(name, {})):
                    result[name] = None
            return result
        text = element.text or ""
        if schema_type == "integer":
            try:
                return int(text)
            except ValueError:
                return text
        if schema_type == "number":
            try:
                return Decimal(text)
            except (InvalidOperation, ValueError):
                return text
        if schema_type == "boolean":
            if text in {"true", "1"}:
                return True
            if text in {"false", "0"}:
                return False
        return text

    def validate(self, value: Any, schema: dict[str, Any]) -> None:
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
                value
            ),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if not errors:
            return
        details = []
        for error in errors:
            field = ".".join(str(part) for part in error.absolute_path) or "$"
            if error.validator == "required":
                missing = re.search(r"'([^']+)'", error.message)
                field = missing.group(1) if missing else field
            elif error.validator == "additionalProperties":
                extra = re.search(r"'([^']+)'", error.message)
                field = extra.group(1) if extra else field
            code = {
                "required": "required",
                "additionalProperties": "unexpected_field",
                "type": "invalid_type",
                "minLength": "too_short",
                "maxLength": "too_long",
                "minimum": "out_of_range",
                "maximum": "out_of_range",
                "exclusiveMinimum": "out_of_range",
                "exclusiveMaximum": "out_of_range",
                "format": "invalid_format",
                "pattern": "invalid_format",
                "enum": "not_allowed",
                "const": "not_allowed",
                "uniqueItems": "duplicate",
            }.get(error.validator, "invalid")
            detail = {"field": field, "code": code}
            if detail not in details:
                details.append(detail)
        raise ApiError(
            422,
            "invalid_body",
            "The request body violates the service schema.",
            details,
        )
