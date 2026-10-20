"""The conformance suite's client, contract reader and Redis controls (F13-09).

The suite runs against one running image. tests/conformance/stack.sh starts it
with redis-state, redis-cache, a PostgreSQL built by the three scripts and a
throwaway signing key, and writes the variables read here:

    CONFORMANCE_BASE_URL       the image's published address
    CONFORMANCE_SERVICE        the service name, as in contracts/registry.yaml
    CONFORMANCE_CONTRACT_DIR   its contract; default contracts/<service>/
    CONFORMANCE_PRIVATE_KEY    the throwaway key's PEM file
    CONFORMANCE_KID            that key's id in the mounted JWKS
    CONFORMANCE_REDIS_STATE    the redis-state container
    CONFORMANCE_REDIS_CACHE    the redis-cache container
    REDIS_OPS_PASSWORD         the ops user of both, which writes liveness keys

Without CONFORMANCE_BASE_URL and CONFORMANCE_SERVICE every case is skipped, so
the quality job's plain `pytest` collects the suite and runs none of it.
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from tests.conformance import tokens

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = os.environ.get("CONFORMANCE_BASE_URL", "")
SERVICE = os.environ.get("CONFORMANCE_SERVICE", "")
ISSUER = os.environ.get("CONFORMANCE_ISSUER", "https://auth.invalid/")
AUDIENCE = "mosaiq-api"
# Every token names a seeded account, so an operation that answers for its
# caller (users' GET /v1/users/me) finds one: each stack loads the seed.
SUBJECT = "11111111-1111-1111-1111-000000000001"
# The seven role codes of contracts/auth/token-claims.v1.md.
ROLE_CODES = (
    "ADMIN",
    "ANALYST",
    "STORE_MANAGER",
    "MARKETING",
    "INVENTORY_PLANNER",
    "AUDITOR",
    "CUSTOMER",
)
METHODS = ("get", "post", "put", "patch", "delete")


@dataclass(frozen=True)
class Operation:
    operation_id: str
    method: str
    path: str
    roles: tuple[str, ...]
    protected: bool
    parameters: tuple[dict, ...]
    body: bool
    xml_reply: bool

    def required(self) -> list[dict]:
        return [p for p in self.parameters if p.get("required") or p["in"] == "path"]

    @property
    def paginated(self) -> bool:
        return any(p["in"] == "query" and p["name"] == "size" for p in self.parameters)

    @property
    def allowed_role(self) -> str:
        return "ADMIN" if "ADMIN" in self.roles else self.roles[0]

    @property
    def outside_role(self) -> str:
        for role in ROLE_CODES:
            if role not in self.roles:
                return role
        return "CONFORMANCE_OUTSIDER"

    def url(self) -> str:
        """The path with a well-formed value in each path parameter.

        The value reaches no row on purpose: it serves the cases decided before
        a handler runs, such as authentication, role and negotiation.
        """

        path = self.path
        query = []
        for parameter in self.required():
            value = sample(parameter.get("schema", {}))
            if parameter["in"] == "path":
                path = path.replace("{" + parameter["name"] + "}", value)
            elif parameter["in"] == "query":
                query.append(parameter["name"] + "=" + value)
        return path + ("?" + "&".join(query) if query else "")


def sample(schema: dict) -> str:
    if "enum" in schema:
        return str(schema["enum"][0])
    if "example" in schema:
        return str(schema["example"])
    if schema.get("examples"):
        return str(schema["examples"][0])
    if schema.get("type") == "integer":
        return str(max(int(schema.get("minimum", 1)), 1))
    if schema.get("format") == "uuid":
        return "00000000-0000-4000-8000-000000000001"
    if schema.get("format") == "date":
        return "2026-01-01"
    return "conformance"


@dataclass
class Contract:
    directory: Path
    document: dict
    operations: list[Operation] = field(default_factory=list)

    @property
    def xsd(self) -> Path:
        return self.directory / (self.directory.name + ".xsd")

    def resolve(self, item: dict) -> dict:
        while "$ref" in item:
            node: Any = self.document
            for part in item["$ref"].removeprefix("#/").split("/"):
                node = node[part]
            item = node
        return item


def contract_dir() -> Path:
    directory = Path(
        os.environ.get("CONFORMANCE_CONTRACT_DIR") or ROOT / "contracts" / SERVICE
    )
    return directory if directory.is_absolute() else ROOT / directory


def load_contract(directory: Path) -> Contract:
    # Imported here: the database job's pytest collects this package without
    # PyYAML, and only a run against an image reads a contract.
    import yaml

    document = yaml.safe_load((directory / "openapi.yaml").read_text())
    contract = Contract(directory, document)
    root_security = contract.document.get("security", [])
    for path, item in contract.document["paths"].items():
        shared = [contract.resolve(p) for p in item.get("parameters", [])]
        for method in METHODS:
            if method not in item:
                continue
            operation = item[method]
            security = operation.get("security", root_security)
            reply = contract.resolve(
                operation.get("responses", {}).get("200")
                or operation.get("responses", {}).get("201")
                or {}
            )
            contract.operations.append(
                Operation(
                    operation_id=operation["operationId"],
                    method=method.upper(),
                    path=path,
                    roles=tuple(operation.get("x-mosaiq-roles", ())),
                    protected=bool(security),
                    parameters=tuple(
                        shared
                        + [contract.resolve(p) for p in operation.get("parameters", [])]
                    ),
                    body="requestBody" in operation,
                    xml_reply="application/xml" in reply.get("content", {}),
                )
            )
    return contract


@dataclass
class Reply:
    status: int
    headers: http.client.HTTPMessage
    body: bytes

    @property
    def media_type(self) -> str:
        return (self.headers.get("Content-Type") or "").split(";")[0].strip()

    def json(self) -> Any:
        return json.loads(self.body)

    def code(self) -> str | None:
        if self.media_type == "application/json":
            return self.json().get("code")
        if self.media_type == "application/xml":
            return _xpath(self.body, "code")
        return None


def _xpath(document: bytes, element: str) -> str:
    return (
        subprocess.run(
            [
                "xmllint",
                "--nonet",
                "--xpath",
                f'string(//*[local-name()="{element}"])',
                "-",
            ],
            input=document,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )


class Client:
    """Every request checks what every response must carry.

    `X-Correlation-ID` is echoed, an error carries the shared envelope with the
    same id, and an XML response validates against the service's XSD.
    """

    def __init__(self, base_url: str, contract: Contract):
        self.base = urlsplit(base_url)
        self.contract = contract

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        accept: str | None = "application/json",
        content_type: str | None = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Reply:
        correlation = "conformance-" + uuid.uuid4().hex[:16]
        headers = {"X-Correlation-ID": correlation} | (headers or {})
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if accept is not None:
            headers["Accept"] = accept
        if content_type is not None:
            headers["Content-Type"] = content_type
        connection = http.client.HTTPConnection(
            self.base.hostname, self.base.port, timeout=15
        )
        try:
            try:
                connection.request(method, path, body=body, headers=headers)
            except (BrokenPipeError, ConnectionResetError):
                # The service may answer an oversized body before reading it.
                pass
            response = connection.getresponse()
            reply = Reply(response.status, response.headers, response.read())
        finally:
            connection.close()
        where = f"{method} {path} -> {reply.status}"
        assert reply.headers.get("X-Correlation-ID") == correlation, where
        if reply.media_type == "application/xml" and not path.startswith("/schema"):
            self.validate_xml(reply.body, where)
        if reply.status >= 400 and reply.body:
            envelope(reply, correlation, where)
        return reply

    def validate_xml(self, document: bytes, where: str) -> None:
        result = subprocess.run(
            ["xmllint", "--noout", "--nonet", "--schema", str(self.contract.xsd), "-"],
            input=document,
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"{where}: XML invalid against {self.contract.xsd.name}: "
            f"{result.stderr.decode()}\n{document.decode(errors='replace')}"
        )


def envelope(reply: Reply, correlation: str, where: str) -> None:
    if reply.media_type == "application/json":
        error = reply.json()
        assert set(error) == {"code", "message", "correlation_id", "details"}, where
        assert error["correlation_id"] == correlation, where
        assert isinstance(error["details"], list), where
    elif reply.media_type == "application/xml":
        assert _xpath(reply.body, "correlation_id") == correlation, where
    else:
        pytest.fail(f"{where}: an error answered {reply.media_type!r}")


class Sessions:
    """Liveness keys on redis-state, written as its ops user (ADR-0037)."""

    def __init__(self, container: str):
        self.container = container

    def redis(self, *command: str) -> str:
        # redis-cli reads the password from the environment, never from argv.
        result = subprocess.run(
            ["docker", "exec", "-e", "REDISCLI_AUTH", self.container, "redis-cli"]
            + ["--no-auth-warning", "--user", "ops", *command],
            capture_output=True,
            text=True,
            check=True,
            env=os.environ | {"REDISCLI_AUTH": os.environ["REDIS_OPS_PASSWORD"]},
        )
        return result.stdout.strip()

    def open(self) -> str:
        sid = str(uuid.uuid4())
        assert self.redis("SET", "pub:auth:v1:live:" + sid, "1", "EX", "3600") == "OK"
        return sid

    def revoke(self, sid: str) -> None:
        assert self.redis("DEL", "pub:auth:v1:live:" + sid) == "1"


class Signer:
    def __init__(self, private_key: Path, kid: str):
        self.private_key = private_key
        self.kid = kid

    def claims(self, role: str, sid: str, **overrides: Any) -> dict:
        now = int(time.time())
        return {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": SUBJECT,
            "sid": sid,
            "role": role,
            "jti": str(uuid.uuid4()),
            "iat": now,
            "exp": now + 600,
        } | overrides

    def sign(self, claims: dict, *, kid: str | None = None) -> str:
        header = {"alg": "RS256", "typ": "JWT", "kid": kid or self.kid}
        return tokens.sign_rs256(header, claims, self.private_key)


def container_ctl(action: str, container: str) -> None:
    subprocess.run(["docker", action, container], capture_output=True, check=True)


def wait_for(probe, what: str, seconds: int = 60) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if probe():
                return
        except (subprocess.CalledProcessError, OSError):
            pass
        time.sleep(1)
    pytest.fail(f"{what} did not come back within {seconds} s")
