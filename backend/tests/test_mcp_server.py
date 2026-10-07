"""The MCP `investigate` tool, over the SDK's in-process client and the stubbed graph."""

import pytest
from mcp import Client

from mcp_server import investigate
from mcp_server.server import server


@pytest.fixture(autouse=True)
def fresh_graph():
    # The compiled graph is cached per process; rebuild it so a test sees
    # (and leaves behind) only its own stubs.
    investigate._graph.cache_clear()
    yield
    investigate._graph.cache_clear()


async def test_tool_is_listed_read_only_with_an_output_schema():
    async with Client(server) as client:
        [tool] = (await client.list_tools()).tools
    assert tool.name == "investigate"
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert set(tool.input_schema["required"]) == {"request"}
    assert "diagnosis" in tool.output_schema["properties"]


async def test_investigation_returns_diagnosis_fix_and_trail(stub_nodes):
    progress = []

    async def on_progress(value, total, message):
        progress.append(message)

    async with Client(server) as client:
        result = await client.call_tool(
            "investigate", {"request": "pod won't start", "namespace": "ns1"}, progress_callback=on_progress
        )

    assert not result.is_error
    out = result.structured_content
    assert out["status"] == "diagnosed"
    assert out["diagnosis"]["confidence"] == "high"
    assert out["recommendation"].startswith("fix for pod won't start")
    assert out["tool_calls"] == 2 and out["hit_step_limit"] is False
    assert [s["type"] for s in out["trail"]] == ["sweep", "tool", "tool", "diagnosis", "docs"]
    # One progress message per node, in graph order.
    assert progress[0] == "Resolving what to investigate"
    assert progress[-1] == "Wrote the suggested fix"
    assert progress.count("Gathered evidence") == 2


async def test_namespace_reaches_the_request(stub_nodes, monkeypatch):
    seen = []
    import graph.build as build

    original = build.intake

    async def spy(state):
        seen.append(state.user_request)
        return await original(state)

    monkeypatch.setattr(build, "intake", spy)
    async with Client(server) as client:
        await client.call_tool("investigate", {"request": "it's broken", "namespace": "payments"})
    assert seen == ["it's broken\n\n(Namespace: payments)"]


async def test_vague_request_returns_a_clarifying_question(stub_nodes):
    async with Client(server) as client:
        result = await client.call_tool("investigate", {"request": "vague"})
    out = result.structured_content
    assert out["status"] == "needs_clarification"
    assert out["clarifying_question"] == "Which namespace?"
    assert out["diagnosis"] is None and out["trail"] == []


async def test_each_call_is_a_fresh_investigation(stub_nodes):
    # No server-side memory: a second call must not inherit the first's turn.
    async with Client(server) as client:
        await client.call_tool("investigate", {"request": "first"})
        second = await client.call_tool("investigate", {"request": "what was it again"})
    # With memory, "again" would route to a follow-up; fresh threads can't.
    assert second.structured_content["status"] == "diagnosed"
    assert second.structured_content["tool_calls"] == 2
