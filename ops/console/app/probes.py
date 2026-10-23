"""HTTP probes of a service's meta routes, and nothing else (ADR-0055)."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import yaml

from .registry import Service

UP = "up"
DOWN = "down"
NOT_READY = "not_ready"


@dataclass(frozen=True)
class Probe:
    live: str
    ready: str
    ready_code: str | None
    latency_ms: float | None
    error: str | None


def base_url(service: Service, host_template: str) -> str:
    return f"http://{host_template.format(name=service.name)}:{service.port}"


def _get(client: httpx.Client, url: str) -> tuple[httpx.Response | None, float, str]:
    started = time.perf_counter()
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        return None, (time.perf_counter() - started) * 1000, type(exc).__name__
    return response, (time.perf_counter() - started) * 1000, ""


def _error_code(response: httpx.Response) -> str | None:
    try:
        return response.json().get("code")
    except ValueError:
        return None


def probe(client: httpx.Client, service: Service, host_template: str) -> Probe:
    """Liveness and readiness of one service; latency is the readiness call's."""
    base = base_url(service, host_template)
    live_response, _, live_error = _get(client, f"{base}/health/live")
    if live_response is None:
        return Probe(DOWN, DOWN, None, None, live_error)
    live = UP if live_response.status_code == 204 else DOWN

    ready_response, latency, ready_error = _get(client, f"{base}/health/ready")
    if ready_response is None:
        return Probe(live, DOWN, None, None, ready_error)
    if ready_response.status_code == 204:
        return Probe(live, UP, None, round(latency, 1), None)
    code = _error_code(ready_response)
    return Probe(
        live,
        NOT_READY if ready_response.status_code == 503 else DOWN,
        code,
        round(latency, 1),
        f"HTTP {ready_response.status_code}",
    )


def contract_version(
    client: httpx.Client, service: Service, host_template: str
) -> str | None:
    """The `info.version` of the OpenAPI document the service is serving."""
    response, _, _ = _get(client, f"{base_url(service, host_template)}/openapi.yaml")
    if response is None or response.status_code != 200:
        return None
    try:
        document = yaml.safe_load(response.text)
    except yaml.YAMLError:
        return None
    if not isinstance(document, dict):
        return None
    return str(document.get("info", {}).get("version") or "") or None
