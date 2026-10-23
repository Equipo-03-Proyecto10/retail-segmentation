"""ops-console: one page over the services' health (ADR-0055).

Not a service: it implements no business rule, is not in the registry, calls
only the meta routes, and binds loopback, reached through an SSH tunnel.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict
from pathlib import Path

from flask import Flask, jsonify, render_template

from .poller import Poller
from .registry import load_registry
from .state import State


def _config_from_env() -> dict:
    env = os.environ.get
    return {
        "REGISTRY": Path(env("OPS_CONSOLE_REGISTRY", "/app/registry.yaml")),
        "SNAPSHOT": Path(
            env("OPS_CONSOLE_SNAPSHOT", "/run/mosaiq/ops/containers.json")
        ),
        "HOST_TEMPLATE": env("OPS_CONSOLE_HOST_TEMPLATE", "{name}"),
        "POLL_SECONDS": float(env("OPS_CONSOLE_POLL_SECONDS", "10")),
        "PROBE_TIMEOUT": float(env("OPS_CONSOLE_PROBE_TIMEOUT", "2")),
        "HISTORY_SECONDS": int(env("OPS_CONSOLE_HISTORY_SECONDS", "3600")),
        "REDIS_URLS": {
            "redis-state": env("REDIS_STATE_URL", "redis://redis-state:6379/0"),
            "redis-cache": env("REDIS_CACHE_URL", "redis://redis-cache:6379/0"),
        },
        "REDIS_USERNAME": env("OPS_CONSOLE_REDIS_USERNAME", "ops_console"),
        "REDIS_PASSWORD": env("REDIS_OPS_CONSOLE_PASSWORD", ""),
        "START_POLLER": env("OPS_CONSOLE_START_POLLER", "1") == "1",
    }


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(_config_from_env())
    app.config.update(overrides or {})

    services = load_registry(app.config["REGISTRY"])
    history = max(1, int(app.config["HISTORY_SECONDS"] / app.config["POLL_SECONDS"]))
    state = State(services, history)
    poller = Poller(state, app.config)
    app.extensions["ops_console"] = {"state": state, "poller": poller}
    if app.config["START_POLLER"]:
        poller.start()

    def view() -> dict:
        with state.lock:
            # Unhealthy first, registry order within each group (sort is stable).
            rows = sorted(state.rows.values(), key=lambda r: r.healthy)
            return {
                "rows": [
                    {
                        "row": r,
                        "history": list(r.history),
                        "container": state.snapshot.containers.get(
                            f"mosaiq-{r.service.name}"
                        ),
                    }
                    for r in rows
                ],
                "ready": sum(1 for r in rows if r.healthy),
                "total": len(rows),
                "polled_at": state.polled_at,
                "snapshot": state.snapshot,
                "redis": list(state.redis),
                "now": time.time(),
            }

    @app.get("/")
    def index():
        return render_template(
            "index.html", refresh=int(app.config["POLL_SECONDS"]), **view()
        )

    @app.get("/status.json")
    def status():
        data = view()
        return jsonify(
            polled_at=data["polled_at"],
            ready=data["ready"],
            total=data["total"],
            services=[
                {
                    "name": item["row"].service.name,
                    "port": item["row"].service.port,
                    "probe": item["row"].probe and asdict(item["row"].probe),
                    "contract_version": item["row"].contract_version,
                    "uptime_percent": item["row"].uptime_percent,
                    "container": item["container"] and asdict(item["container"]),
                }
                for item in data["rows"]
            ],
            snapshot={
                "taken_at": data["snapshot"].taken_at,
                "stale": data["snapshot"].stale,
                "error": data["snapshot"].error,
            },
            redis=[asdict(i) for i in data["redis"]],
        )

    @app.template_filter("sparkline")
    def sparkline(history: list, width: int = 120, height: int = 24) -> str:
        """SVG polyline points of readiness latency; gaps drop to the baseline."""
        if len(history) < 2:
            return ""
        top = max((s.latency_ms or 0) for s in history) or 1
        step = width / (len(history) - 1)

        def y(sample) -> float:
            if not sample.ready:
                return height
            return height - (sample.latency_ms or 0) / top * (height - 2) - 1

        return " ".join(f"{i * step:.1f},{y(s):.1f}" for i, s in enumerate(history))

    @app.template_filter("ago")
    def ago(at: float | None) -> str:
        if at is None:
            return "never"
        seconds = int(time.time() - at)
        return f"{seconds} s ago" if seconds < 120 else f"{seconds // 60} min ago"

    return app
