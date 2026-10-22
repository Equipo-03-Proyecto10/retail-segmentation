import httpx

from app.probes import DOWN, NOT_READY, UP, contract_version, probe
from app.registry import Service

STORES = Service("stores", 5005, "core", "rule", ("RN-53",))


def client(routes: dict[str, httpx.Response | Exception]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        outcome = routes[request.url.path]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_live_and_ready():
    result = probe(
        client(
            {"/health/live": httpx.Response(204), "/health/ready": httpx.Response(204)}
        ),
        STORES,
        "{name}",
    )
    assert (result.live, result.ready, result.error) == (UP, UP, None)
    assert result.latency_ms is not None


def test_schema_not_ready_is_reported_with_its_code():
    result = probe(
        client(
            {
                "/health/live": httpx.Response(204),
                "/health/ready": httpx.Response(503, json={"code": "schema_not_ready"}),
            }
        ),
        STORES,
        "{name}",
    )
    assert (result.live, result.ready, result.ready_code) == (
        UP,
        NOT_READY,
        "schema_not_ready",
    )


def test_unreachable_service_is_down():
    error = httpx.ConnectError("refused")
    result = probe(client({"/health/live": error}), STORES, "{name}")
    assert (result.live, result.ready, result.error) == (DOWN, DOWN, "ConnectError")


def test_probe_uses_the_host_template():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(204)

    probe(httpx.Client(transport=httpx.MockTransport(handler)), STORES, "127.0.0.1")
    assert seen[0] == "http://127.0.0.1:5005/health/live"


def test_contract_version_reads_info_version():
    yaml_text = "openapi: 3.1.0\ninfo:\n  title: stores\n  version: 1.4.0\n"
    assert (
        contract_version(
            client({"/openapi.yaml": httpx.Response(200, text=yaml_text)}),
            STORES,
            "{name}",
        )
        == "1.4.0"
    )
    assert (
        contract_version(
            client({"/openapi.yaml": httpx.Response(404)}), STORES, "{name}"
        )
        is None
    )
