#!/usr/bin/env python3
"""Render the service template into one service's directory (ADR-0040, F13-08).

    python3 deploy/templates/service/render.py NAME PORT {python,rust} OUTDIR
        [--mem-limit 160m]

writes OUTDIR/compose.yaml, OUTDIR/nginx/NAME.conf and OUTDIR/Dockerfile.
PORT is the service's port in contracts/registry.yaml. An implementation story
renders into services/<name>/ once and commits the result; nothing in deploy/
lists services, so this script takes the name and port as arguments.

The template marks the lines that only auth carries with a leading `#auth# `:
they are kept, unmarked, for auth and dropped for every other service.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUTH_MARK = "#auth#"
NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
# ADR-0040's memory budget: Flask 1 worker x 4 threads at 0.5 CPU, Rust two
# tokio workers at 0.25. A service with a larger line in that table passes
# --mem-limit.
KINDS = {
    "python": {"cpus": "0.5", "mem_limit": "160m", "dockerfile": "Dockerfile.python"},
    "rust": {"cpus": "0.25", "mem_limit": "48m", "dockerfile": "Dockerfile.rust"},
}


def _substitute(text: str, values: dict[str, str], auth: bool) -> str:
    lines = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(AUTH_MARK):
            if not auth:
                continue
            kept = stripped[len(AUTH_MARK) :].removeprefix(" ")
            line = line[: len(line) - len(stripped)] + kept
        lines.append(line)
    rendered = "".join(lines)
    for key, value in values.items():
        rendered = rendered.replace(f"__{key}__", value)
    leftover = re.search(r"__[A-Z_]+__", rendered)
    if leftover:
        raise ValueError(f"unrendered placeholder {leftover.group(0)}")
    return rendered


def render_nginx(name: str, port: int, auth: bool | None = None) -> str:
    """The NGINX include of one service, as the instance installs it.

    auth defaults to whether the service is auth; the local gateway passes
    False, since it carries no sign-in zone.
    """
    template = (HERE / "nginx.conf").read_text(encoding="utf-8")
    values = {"NAME": name, "PORT": str(port)}
    return _substitute(template, values, auth=name == "auth" if auth is None else auth)


def render(
    name: str, port: int, kind: str, outdir: Path, mem_limit: str | None
) -> None:
    if not NAME.match(name):
        raise ValueError(f"invalid service name {name!r}")
    if not 1024 <= port <= 65535:
        raise ValueError(f"port {port} is outside 1024-65535")
    settings = KINDS[kind]
    values = {
        "NAME": name,
        "PORT": str(port),
        "CPUS": settings["cpus"],
        "MEM_LIMIT": mem_limit or settings["mem_limit"],
    }
    auth = name == "auth"
    compose = (HERE / "compose.yaml").read_text(encoding="utf-8")
    dockerfile = (HERE / settings["dockerfile"]).read_text(encoding="utf-8")
    (outdir / "nginx").mkdir(parents=True, exist_ok=True)
    (outdir / "compose.yaml").write_text(
        _substitute(compose, values, auth), encoding="utf-8"
    )
    (outdir / "Dockerfile").write_text(
        _substitute(dockerfile, values, auth), encoding="utf-8"
    )
    (outdir / "nginx" / f"{name}.conf").write_text(
        render_nginx(name, port), encoding="utf-8"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name")
    parser.add_argument("port", type=int)
    parser.add_argument("kind", choices=sorted(KINDS))
    parser.add_argument("outdir", type=Path)
    parser.add_argument("--mem-limit", help="e.g. 384m; defaults by kind")
    args = parser.parse_args(argv)
    try:
        render(args.name, args.port, args.kind, args.outdir, args.mem_limit)
    except ValueError as error:
        print(f"render: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
