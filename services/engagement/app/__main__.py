"""`python -m app rebuild-rollup --from YYYY-MM-DD --to YYYY-MM-DD`."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date

from mosaiq_kit import ApiError

from .events import EventStore
from .live import LiveCounters
from .rollup import rebuild


def parse_day(text: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise argparse.ArgumentTypeError(f"{text!r} is not a YYYY-MM-DD date")
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app")
    commands = parser.add_subparsers(dest="command", required=True)
    rollup = commands.add_parser(
        "rebuild-rollup",
        help="recompute engagement_daily from engagement_event for UTC days",
    )
    rollup.add_argument("--from", dest="first", type=parse_day, required=True)
    rollup.add_argument("--to", dest="last", type=parse_day, required=True)
    arguments = parser.parse_args(argv)
    if arguments.last < arguments.first:
        parser.error("--to is before --from")
    store = EventStore.from_environment()
    if store.client is None:
        parser.error("MONGO_URI is not set")
    try:
        result = rebuild(
            store, LiveCounters.from_environment(), arguments.first, arguments.last
        )
    except ApiError as error:
        print(f"rebuild-rollup: {error.message}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
