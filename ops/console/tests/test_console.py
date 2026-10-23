import json
import re

from app.host import read_snapshot
from app.probes import DOWN, NOT_READY, UP, Probe
from app.redis_stats import Group, Instance


def record(app, probes, now=1000.0, redis=()):
    state = app.extensions["ops_console"]["state"]
    poller = app.extensions["ops_console"]["poller"]
    state.record(
        now,
        probes,
        {"stores": "1.4.0"},
        read_snapshot(poller.config["SNAPSHOT"], now),
        list(redis),
    )
    return state


def test_the_registry_is_read_whole(app):
    state = app.extensions["ops_console"]["state"]
    assert len(state.rows) >= 10
    assert state.rows["stores"].service.port == 5005


def test_uptime_is_the_share_of_ready_samples(app):
    up = Probe(UP, UP, None, 3.0, None)
    down = Probe(DOWN, DOWN, None, None, "ConnectError")
    for i, p in enumerate([up, up, up, down]):
        state = record(app, {"stores": p}, now=1000.0 + i)
    assert state.rows["stores"].uptime_percent == 75.0


def test_history_is_bounded_to_the_configured_hour(app):
    up = Probe(UP, UP, None, 3.0, None)
    for i in range(400):
        state = record(app, {"stores": up}, now=float(i))
    assert len(state.rows["stores"].history) == 360


def test_page_lists_unhealthy_services_first(app):
    names = list(app.extensions["ops_console"]["state"].rows)
    probes = {n: Probe(UP, UP, None, 2.0, None) for n in names}
    probes["stores"] = Probe(UP, NOT_READY, "schema_not_ready", 2.0, "HTTP 503")
    record(
        app,
        probes,
        redis=[
            Instance(
                "redis-state",
                True,
                "1.2M",
                "64.00M",
                None,
                [Group("stream:stores.v1.changed", "stock", 3, 1)],
            )
        ],
    )
    html = app.test_client().get("/").get_data(as_text=True)
    assert f"{len(names) - 1}/{len(names)} ready" in html
    listed = re.findall(r'<td class="mono">([a-z-]+)</td>\s*<td class="num">\d+', html)
    assert listed[0] == "stores"
    assert listed[1:] == [n for n in names if n != "stores"]
    assert "schema_not_ready" in html
    assert "stream:stores.v1.changed" in html


def test_status_json(app):
    record(app, {"stores": Probe(UP, UP, None, 2.0, None)})
    body = json.loads(app.test_client().get("/status.json").get_data())
    stores = next(s for s in body["services"] if s["name"] == "stores")
    assert stores["probe"]["ready"] == UP
    assert stores["contract_version"] == "1.4.0"
    assert body["snapshot"]["stale"] is True


def test_page_renders_before_the_first_poll(app):
    response = app.test_client().get("/")
    assert response.status_code == 200
    assert "not polled yet" in response.get_data(as_text=True)
