"""Structured logging: JSON shape, context binding, and the events nodes emit."""

import json
import logging

from agent import nodes
from graph.state import AgentState, Scope
from observability.logging import ConsoleFormatter, JsonFormatter, bind


def record(msg="evt", **extra) -> logging.LogRecord:
    rec = logging.LogRecord("test", logging.INFO, __file__, 1, msg, None, None)
    rec.__dict__.update(extra)
    return rec


def test_json_line_carries_event_extras_and_bound_context():
    with bind(request_id="r1", thread_id="t1"):
        line = JsonFormatter().format(record("tool.call", tool="describe", duration_ms=12))
    payload = json.loads(line)
    assert payload["event"] == "tool.call"
    assert payload["level"] == "info"
    assert {k: payload[k] for k in ("request_id", "thread_id", "tool", "duration_ms")} == {
        "request_id": "r1", "thread_id": "t1", "tool": "describe", "duration_ms": 12,
    }


def test_context_does_not_leak_after_bind():
    with bind(request_id="r1"):
        pass
    assert "request_id" not in json.loads(JsonFormatter().format(record()))


def test_console_renders_the_same_fields():
    with bind(thread_id="t1"):
        line = ConsoleFormatter().format(record("chat.turn.end", route="followup"))
    assert "chat.turn.end" in line and "thread_id=t1" in line and "route=followup" in line


async def test_execute_tool_logs_the_call(monkeypatch, caplog):
    from langchain_core.tools import tool

    @tool
    def t(namespace: str | None = None) -> str:
        """Fake."""
        return "{}"

    monkeypatch.setattr(nodes, "TOOLS_BY_NAME", {"t": t})
    caplog.set_level(logging.INFO, logger=nodes.__name__)
    await nodes.execute_tool(AgentState(user_request="q", scope=Scope(namespace="ns"), pending_tool_call={"name": "t", "args": {}}))
    [rec] = [r for r in caplog.records if r.getMessage() == "tool.call"]
    assert rec.tool == "t" and rec.tool_args == {"namespace": "ns"} and rec.namespace_filled is True
    assert rec.tool_error is None and isinstance(rec.duration_ms, int)


def test_noisy_libraries_are_quieted_even_at_debug():
    from observability.logging import LogSettings, configure_logging

    configure_logging(LogSettings(level="DEBUG", format="json"))
    try:
        # sse_starlette logs response chunks (the reply text) at DEBUG.
        for name in ("sse_starlette", "aiosqlite", "httpx", "anthropic"):
            assert not logging.getLogger(name).isEnabledFor(logging.DEBUG), name
    finally:
        logging.getLogger().handlers = []
        logging.getLogger().setLevel(logging.WARNING)
