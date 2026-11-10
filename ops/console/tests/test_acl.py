"""The ops_console Redis line, and every command its two readers send (#764).

The console (`app/redis_stats.py`) and the propagation tool
(`ops/propagation/propagation.py`) both sign in as `ops_console`. Their tests
use fakes, which never cross the ACL, so this file drives their real redis-py
calls against a client that records the command each call sends. It fails
when either reader sends a command the line does not grant.
"""

from __future__ import annotations

import importlib.util
import inspect as pyinspect
import sys
from pathlib import Path

import pytest

import redis
from app import redis_stats
from redis.client import Pipeline

ACL = Path(__file__).resolve().parents[1] / "redis" / "ops-console.acl"
TOOL = Path(__file__).resolve().parents[3] / "ops" / "propagation" / "propagation.py"
STREAM = "stream:products.v1.changed"


def _grants() -> set[str]:
    tokens = ACL.read_text(encoding="utf-8").split()
    return {token[1:].lower() for token in tokens if token.startswith("+")}


def test_acl_grants_only_the_documented_read_commands() -> None:
    tokens = ACL.read_text(encoding="utf-8").split()
    assert _grants() == {
        "ping",
        "info",
        "scan",
        "xinfo|groups",
        "xpending",
        "xrange",
        "xrevrange",
        "exists",
        "time",
    }
    assert "%R~stream:*" in tokens
    assert not any(token.startswith("%W") or token.startswith("~") for token in tokens)


# --- the commands each reader sends ---------------------------------------------

# What redis-py would return once parsed, per command, so the readers run on.
REPLIES = {
    "ping": True,
    "info": {"used_memory_human": "1M", "maxmemory_human": "64M"},
    "time": (1_700_000_000, 0),
    "scan": (0, [STREAM.encode()]),
    "exists": 1,
    "xinfo|groups": [
        {
            "name": b"recommendations",
            "last-delivered-id": b"5-0",
            "lag": 1,
            "pending": 1,
        }
    ],
    "xpending": {"pending": 1, "min": b"5-0", "max": b"5-0", "consumers": []},
    "xrange": [(b"6-0", {b"event_id": b"e"})],
    "xrevrange": [(b"6-0", {b"event_id": b"e"})],
}


def _name(args: tuple) -> str:
    # redis-py sends a subcommand either inside the first argument
    # ("XINFO GROUPS") or as the second; the ACL spells both "xinfo|groups".
    words = str(args[0]).lower().split() + [str(a).lower() for a in args[1:2]]
    if words[0] in {"xinfo", "client", "config", "acl"}:
        return f"{words[0]}|{words[1]}"
    return words[0]


def _reply(args: tuple):
    name = _name(args)
    if name == "xpending" and len(args) > 3:  # XPENDING with a range
        return [
            {
                "message_id": b"5-0",
                "consumer": b"c",
                "time_since_delivered": 1,
                "times_delivered": 1,
            }
        ]
    return REPLIES[name]


class Recording(redis.Redis):
    """A real redis-py client that records each command instead of sending it."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    def execute_command(self, *args, **options):
        self.sent.append(_name(args))
        return _reply(args)

    def pipeline(self, transaction: bool = True, shard_hint=None):
        client = self

        class RecordingPipeline(Pipeline):
            def execute(self, raise_on_error: bool = True):
                stack = [command[0] for command in self.command_stack]
                self.reset()
                if transaction:
                    client.sent.append("multi")
                client.sent.extend(_name(args) for args in stack)
                return [_reply(args) for args in stack]

        return RecordingPipeline(
            self.connection_pool, self.response_callbacks, transaction, shard_hint
        )


def _tool():
    spec = importlib.util.spec_from_file_location("propagation_under_acl", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_console_sends_only_granted_commands() -> None:
    client = Recording()
    instance = redis_stats.inspect("redis-state", client)
    assert instance.up, instance.error
    assert set(client.sent) <= _grants(), set(client.sent) - _grants()
    assert {"xpending", "time", "xinfo|groups"} <= set(client.sent)


def test_the_propagation_tool_sends_only_granted_commands() -> None:
    tool = _tool()
    client = Recording()
    reads = tool.RedisReads(client)
    calls = {
        "existing_groups": lambda: reads.existing_groups(STREAM),
        "last_id": lambda: reads.last_id(STREAM),
        "new_entries": lambda: reads.new_entries({STREAM: "5-0"}),
        "group_positions": lambda: reads.group_positions([STREAM]),
        "pending": lambda: reads.pending([(STREAM, "recommendations", "5-0")]),
    }
    public = {
        name
        for name, _ in pyinspect.getmembers(tool.RedisReads, pyinspect.isfunction)
        if not name.startswith("_")
    }
    # A new reader method must be added here, so its commands are checked too.
    assert public == set(calls)
    for call in calls.values():
        call()
    assert set(client.sent) <= _grants(), set(client.sent) - _grants()
    assert {"exists", "xrevrange", "xrange", "xpending"} <= set(client.sent)


def test_the_recorder_catches_an_ungranted_command() -> None:
    # A write is recorded as what it is, and is not granted.
    client = Recording()
    with pytest.raises(KeyError):
        client.xadd(STREAM, {"f": "v"})
    assert client.sent == ["xadd"]
    assert "xadd" not in _grants()
