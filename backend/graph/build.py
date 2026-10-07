"""Graph assembly: wires the nodes in agent.nodes into the state machine
described in docs/architecture.md §7.

    intake -> gather_context -> plan
    plan --(need more evidence)--> execute_tool --> plan   # bounded loop
    plan --(enough evidence)-----> diagnose -> ground -> recommend -> finalize -> END

`intake` can also short-circuit straight to `finalize` when the request is
too ambiguous to investigate (see `graph.state.Scope.needs_clarification`).
`finalize` archives the turn into conversation state, which a checkpointer
persists across turns under the caller's `thread_id`.
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph

from agent.nodes import (
    diagnose,
    execute_tool,
    finalize,
    gather_context,
    ground,
    intake,
    plan,
    recommend,
    route_after_intake,
    route_after_plan,
)
from graph.state import AgentState


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Compile the graph. With a checkpointer, callers must pass a `thread_id`
    in `configurable` and build each turn's input with `new_turn_input`."""
    graph = StateGraph(AgentState)

    graph.add_node("intake", intake)
    graph.add_node("gather_context", gather_context)
    graph.add_node("plan", plan)
    graph.add_node("execute_tool", execute_tool)
    graph.add_node("diagnose", diagnose)
    graph.add_node("ground", ground)
    graph.add_node("recommend", recommend)
    graph.add_node("finalize", finalize)

    graph.set_entry_point("intake")
    graph.add_conditional_edges(
        "intake", route_after_intake, {"gather_context": "gather_context", "finalize": "finalize"}
    )
    graph.add_edge("gather_context", "plan")
    graph.add_conditional_edges("plan", route_after_plan, {"execute_tool": "execute_tool", "diagnose": "diagnose"})
    graph.add_edge("execute_tool", "plan")
    graph.add_edge("diagnose", "ground")
    graph.add_edge("ground", "recommend")
    graph.add_edge("recommend", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)
