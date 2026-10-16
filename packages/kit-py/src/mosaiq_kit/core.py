"""Contract-driven Flask integration."""

from __future__ import annotations

import logging
import os
import re
import secrets
import time
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

import redis
from flask import Flask, Response, g, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.routing import BaseConverter

from .auth import Authenticator
from .contracts import Contract, Operation
from .database import Database
from .logging import set_request_context
from .rendering import render_error, render_representation
from .types import ApiError, Collection, NoContent, Page, Representation

LOGGER = logging.getLogger(__name__)
_CORRELATION = re.compile(r"^[0-9A-Za-z-]{1,64}$")
_ResponseValue = Representation | Collection | Page | NoContent
_Handler = TypeVar("_Handler", bound=Callable[..., _ResponseValue])


class Kit:
    """Install shared mechanisms around a service's staged contract."""

    def __init__(
        self,
        app: Flask,
        *,
        service: str,
        suffix: str,
        contracts_dir: str | Path,
        ready_checks: list[Callable[[], bool | None]] | None = None,
    ):
        self.app = app
        self.service = service
        self.suffix = suffix
        self.contract = Contract(contracts_dir, service)
        self.ready_checks = ready_checks or []
        self.auth = Authenticator(suffix)
        self.db = Database()
        self.rate_limit_enabled = self._env_bool("RATE_LIMIT_ENABLED", True)
        self.rate_limit_requests = self._env_int("RATE_LIMIT_REQUESTS", 120, 1)
        self.rate_limit_window = self._env_int("RATE_LIMIT_WINDOW_SECONDS", 60, 1)
        self._pattern_converters: dict[str, str] = {}
        app.config["MAX_CONTENT_LENGTH"] = 65_536
        app.before_request(self._start_request)
        app.after_request(self._finish_request)
        app.register_error_handler(ApiError, self._api_error)
        app.register_error_handler(RequestEntityTooLarge, self._too_large)
        app.register_error_handler(HTTPException, self._http_error)
        app.register_error_handler(Exception, self._unexpected_error)
        self._register_meta_routes()

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        value = os.environ.get(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _env_int(name: str, default: int, minimum: int) -> int:
        try:
            value = int(os.environ.get(name, str(default)))
        except ValueError:
            return default
        return value if value >= minimum else default

    @property
    def principal(self) -> dict[str, Any] | None:
        return getattr(g, "principal", None)

    @property
    def body(self) -> Any:
        return getattr(g, "body", None)

    @property
    def page(self) -> int:
        return getattr(g, "page", 1)

    @property
    def size(self) -> int:
        return getattr(g, "size", 20)

    def _start_request(self) -> None:
        supplied = request.headers.get("X-Correlation-ID", "")
        g.correlation_id = (
            supplied if _CORRELATION.fullmatch(supplied) else secrets.token_hex(16)
        )
        g.response_format = self._preferred_response_format()
        g.principal = None
        set_request_context(g.correlation_id)

    @staticmethod
    def _finish_request(response: Response) -> Response:
        response.headers["X-Correlation-ID"] = getattr(g, "correlation_id", "-")
        return response

    def operation(self, operation_id: str) -> Callable[[_Handler], _Handler]:
        """Register the Flask rule declared by an OpenAPI operation."""

        operation = self.contract.operation(operation_id)
        if operation.protected and not operation.roles:
            raise ValueError(
                f"Protected operation {operation_id!r} has no x-mosaiq-roles."
            )
        if operation.request_schema is not None and (
            not operation.xml_name or not operation.xml_namespace
        ):
            raise ValueError(
                f"Operation {operation_id!r} has a request body without an XML root."
            )

        def decorator(handler: _Handler) -> _Handler:
            @wraps(handler)
            def wrapped(**path_values: Any) -> Response:
                return self._dispatch(operation, handler, path_values)

            self.app.add_url_rule(
                self._flask_rule(operation),
                endpoint="mosaiq_operation_" + operation_id,
                view_func=wrapped,
                methods=[operation.method],
                provide_automatic_options=False,
            )
            return handler

        return decorator

    def _flask_rule(self, operation: Operation) -> str:
        parameters = {
            parameter.get("name"): parameter
            for parameter in operation.parameters
            if parameter.get("in") == "path"
        }

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            schema = parameters.get(name, {}).get("schema", {})
            if schema.get("type") == "integer":
                options = []
                if "minimum" in schema:
                    options.append("min=" + str(schema["minimum"]))
                if "maximum" in schema:
                    options.append("max=" + str(schema["maximum"]))
                arguments = "(" + ",".join(options) + ")" if options else ""
                return f"<int{arguments}:{name}>"
            if schema.get("format") == "uuid":
                return f"<uuid:{name}>"
            if "pattern" in schema:
                converter = self._pattern_converter(str(schema["pattern"]))
                return f"<{converter}:{name}>"
            return f"<string:{name}>"

        return re.sub(r"\{([^}]+)\}", replace, operation.path)

    def _pattern_converter(self, pattern: str) -> str:
        try:
            return self._pattern_converters[pattern]
        except KeyError:
            pass
        name = "mosaiq_pattern_" + str(len(self._pattern_converters))
        regex = pattern.removeprefix("^").removesuffix("$")
        converter = type(
            "_GeneratedPatternConverter",
            (BaseConverter,),
            {"regex": regex},
        )
        self.app.url_map.converters[name] = converter
        self._pattern_converters[pattern] = name
        return name

    def _dispatch(
        self,
        operation: Operation,
        handler: Callable[..., _ResponseValue],
        path_values: dict[str, Any],
    ) -> Response:
        raw = self._capped_body()
        if operation.protected:
            principal = self.auth.verify(request.headers.get("Authorization"))
            g.principal = principal
            set_request_context(g.correlation_id, str(principal["sub"]))
            if operation.roles and principal.get("role") not in operation.roles:
                raise ApiError(
                    403,
                    "forbidden_role",
                    "The authenticated role cannot perform this operation.",
                )
        self._rate_limit()
        g.response_format = self._negotiate()
        if operation.request_schema is not None:
            media_type = request.mimetype
            if media_type not in {"application/json", "application/xml"}:
                raise ApiError(
                    415,
                    "unsupported_media_type",
                    "The request format is unsupported.",
                )
            if media_type == "application/json":
                body = self.contract.parse_json(raw)
            else:
                body = self.contract.parse_xml(raw, operation)
            self.contract.validate(body, operation.request_schema)
            g.body = body
        if operation.paginated:
            g.page, g.size = self._pagination()
        result = handler(**path_values)
        if not isinstance(result, Representation | Collection | Page | NoContent):
            raise TypeError("A kit operation returned an unsupported response type.")
        return render_representation(
            result,
            g.response_format,
            self.contract.namespace,
            request_size=self.size,
        )

    @staticmethod
    def _capped_body() -> bytes:
        content_length = request.content_length
        if content_length is not None and content_length > 65_536:
            raise ApiError(413, "payload_too_large", "The request body exceeds 64 KiB.")
        try:
            raw = request.get_data(cache=True)
        except RequestEntityTooLarge:
            raise ApiError(
                413, "payload_too_large", "The request body exceeds 64 KiB."
            ) from None
        if len(raw) > 65_536:
            raise ApiError(413, "payload_too_large", "The request body exceeds 64 KiB.")
        return raw

    @staticmethod
    def _preferred_response_format() -> str:
        accept = request.headers.get("Accept")
        if not accept or accept.strip() == "*/*":
            return "json"
        best = request.accept_mimetypes.best_match(
            ["application/json", "application/xml"]
        )
        if best == "application/xml" and request.accept_mimetypes[best] > 0:
            return "xml"
        return "json"

    @staticmethod
    def _negotiate() -> str:
        accept = request.headers.get("Accept")
        if not accept or accept.strip() == "*/*":
            return "json"
        best = request.accept_mimetypes.best_match(
            ["application/json", "application/xml"]
        )
        if best is None or request.accept_mimetypes[best] == 0:
            raise ApiError(
                406,
                "not_acceptable",
                "The requested response format is unsupported.",
            )
        return "xml" if best == "application/xml" else "json"

    def _rate_limit(self) -> None:
        if not self.rate_limit_enabled:
            return
        caller = (
            str(g.principal["sub"])
            if g.principal
            else request.headers.get("X-Real-IP") or request.remote_addr or "unknown"
        )
        now = int(time.time())
        window_index = now // self.rate_limit_window
        key = self.suffix + ":rl:" + caller + ":" + str(window_index)
        try:
            pipe = self.auth.redis.pipeline(transaction=False)
            pipe.set(key, 0, ex=2 * self.rate_limit_window, nx=True)
            pipe.incr(key)
            _created, count = pipe.execute()
        except redis.RedisError as error:
            LOGGER.warning("Rate-limit storage failed: %r", error)
            return
        if int(count) > self.rate_limit_requests:
            retry_after = self.rate_limit_window - (now % self.rate_limit_window)
            raise ApiError(
                429,
                "rate_limited",
                "The request limit was exceeded.",
                headers={"Retry-After": str(max(1, retry_after))},
            )

    @staticmethod
    def _pagination() -> tuple[int, int]:
        details = []
        values: dict[str, int] = {}
        for name, default, minimum, maximum in (
            ("page", 1, 1, None),
            ("size", 20, 1, 100),
        ):
            raw = request.args.get(name)
            if raw is not None and re.fullmatch(r"[0-9]+", raw) is None:
                details.append({"field": name, "code": "invalid_type"})
                continue
            value = default if raw is None else int(raw)
            if value < minimum or (maximum is not None and value > maximum):
                details.append({"field": name, "code": "out_of_range"})
            values[name] = value
        if details:
            raise ApiError(
                422,
                "invalid_pagination",
                "Pagination values are outside their allowed ranges.",
                details,
            )
        return values["page"], values["size"]

    def _register_meta_routes(self) -> None:
        self.app.add_url_rule(
            "/health/live",
            endpoint="mosaiq_health_live",
            view_func=lambda: Response(status=204),
            methods=["GET"],
            provide_automatic_options=False,
        )
        self.app.add_url_rule(
            "/health/ready",
            endpoint="mosaiq_health_ready",
            view_func=self._ready,
            methods=["GET"],
            provide_automatic_options=False,
        )
        self.app.add_url_rule(
            "/openapi.yaml",
            endpoint="mosaiq_openapi",
            view_func=lambda: Response(
                self.contract.openapi_bytes, content_type="application/yaml"
            ),
            methods=["GET"],
            provide_automatic_options=False,
        )
        self.app.add_url_rule(
            "/schema.xsd",
            endpoint="mosaiq_schema",
            view_func=lambda: Response(
                self.contract.xsd_bytes, content_type="application/xml"
            ),
            methods=["GET"],
            provide_automatic_options=False,
        )
        for path in sorted(
            (self.contract.root / self.service).glob(f"{self.service}.v*.xsd")
        ):
            version = path.name.removeprefix(self.service + ".").removesuffix(".xsd")
            self.app.add_url_rule(
                f"/schema.{version}.xsd",
                endpoint="mosaiq_schema_" + version,
                view_func=self._file_view(path),
                methods=["GET"],
                provide_automatic_options=False,
            )

    @staticmethod
    def _file_view(path: Path) -> Callable[[], Response]:
        payload = path.read_bytes()
        return lambda: Response(payload, content_type="application/xml")

    def _ready(self) -> Response:
        try:
            ready = all(check() is not False for check in self.ready_checks)
        except Exception as error:  # readiness collapses every dependency failure
            LOGGER.warning("Readiness check failed: %r", error)
            ready = False
        if not ready:
            raise ApiError(
                503,
                "schema_not_ready",
                "The required service schema has not been applied.",
            )
        return Response(status=204)

    def _api_error(self, error: ApiError) -> Response:
        return render_error(
            error.code,
            error.message,
            getattr(g, "correlation_id", "-"),
            error.details,
            error.status,
            getattr(g, "response_format", "json"),
            error.headers,
        )

    def _too_large(self, _error: RequestEntityTooLarge) -> Response:
        return self._api_error(
            ApiError(413, "payload_too_large", "The request body exceeds 64 KiB.")
        )

    def _http_error(self, error: HTTPException) -> Response:
        code = {
            404: "not_found",
            405: "method_not_allowed",
        }.get(error.code or 500, "http_error")
        api_error = ApiError(error.code or 500, code, error.description)
        response = self._api_error(api_error)
        if error.code == 405 and getattr(error, "valid_methods", None):
            response.headers["Allow"] = ", ".join(error.valid_methods)
        return response

    def _unexpected_error(self, error: Exception) -> Response:
        LOGGER.exception("Unhandled service error: %r", error)
        return self._api_error(
            ApiError(
                500, "internal_error", "The service could not complete the request."
            )
        )
