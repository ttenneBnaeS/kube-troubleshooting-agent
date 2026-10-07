"""The compiled graph across turns, with model and cluster nodes stubbed (see `stub_nodes`)."""

from langgraph.checkpoint.memory import InMemorySaver

from graph import build_graph, new_turn_input
from graph.state import checkpoint_serializer


async def run(graph, request, thread="t"):
    return await graph.ainvoke(new_turn_input(request), config={"configurable": {"thread_id": thread}})


async def test_run_state_resets_between_turns(stub_nodes):
    graph = build_graph(InMemorySaver(serde=checkpoint_serializer()))
    first = await run(graph, "first problem")
    second = await run(graph, "second problem")

    # Without the reset, step_count would carry over and the loop guard
    # would trip before turn 2's first tool call.
    for s in (first, second):
        assert s["step_count"] == 2
        assert [r.tool_name for r in s["investigation_log"]] == ["initial_sweep", "get_pod_status_tool", "get_pod_status_tool"]
        assert s["loop_guard_triggered"] is False
    assert second["diagnosis"].root_cause == "cause of second problem"
    assert len(second["turns"]) == 2 and len(second["messages"]) == 4


async def test_three_routes(stub_nodes):
    graph = build_graph(InMemorySaver(serde=checkpoint_serializer()))
    await run(graph, "what was it again")  # follow-up flag on a first turn
    await run(graph, "vague")
    final = await run(graph, "tell me again")
    routes = [t.route for t in final["turns"]]
    assert routes == ["investigate", "clarify", "followup"]
    assert final["turns"][2].reply == "from 2 earlier turn(s)"
    assert final["turns"][2].investigation_log == []
    assert final["messages"][3].content == "Which namespace?"


async def test_threads_are_isolated(stub_nodes):
    graph = build_graph(InMemorySaver(serde=checkpoint_serializer()))
    await run(graph, "a", thread="one")
    other = await run(graph, "b", thread="two")
    assert len(other["turns"]) == 1


async def test_rationale_reaches_the_turn_record(stub_nodes):
    graph = build_graph(InMemorySaver(serde=checkpoint_serializer()))
    final = await run(graph, "x")
    assert [r.rationale for r in final["turns"][0].investigation_log[1:]] == ["look at p0", "look at p1"]
