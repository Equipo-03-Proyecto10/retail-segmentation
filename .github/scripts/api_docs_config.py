#!/usr/bin/env python3
"""Generate the API documentation page's service list from the registry (#1192).

    python3 .github/scripts/api_docs_config.py           # rewrite it
    python3 .github/scripts/api_docs_config.py --check   # exit 1 if stale

docs/api-docs/swagger-config.json names one entry per service of
contracts/registry.yaml, in registry order, each pointing at the contract that
service serves itself, /api/<name>/openapi.yaml. It holds URLs only, never a
copy of a contract, so a redeployed service shows its new contract with
nothing here to update (ADR-0090). tests/test_api_docs.py runs the check.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "contracts/registry.yaml"
OUTPUT = ROOT / "docs/api-docs/swagger-config.json"


def generate() -> str:
    services = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["services"]
    config = {
        "urls": [
            {"name": entry["name"], "url": f"/api/{entry['name']}/openapi.yaml"}
            for entry in services
        ],
        "urls.primaryName": services[0]["name"],
    }
    return json.dumps(config, indent=2) + "\n"


def main(argv: list[str]) -> int:
    text = generate()
    if argv == ["--check"]:
        if OUTPUT.read_text(encoding="utf-8") != text:
            print(f"{OUTPUT.relative_to(ROOT)} is stale: run {Path(__file__).name}")
            return 1
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
