"""Graph assembly: wires the nodes in agent.nodes into the state machine
described in docs/architecture.md §7.

    intake -> gather_context -> plan
    plan --(need more evidence)--> execute_tool --> plan   # bounded loop
    plan --(enough evidence)-----> diagnose -> recommend -> END

`intake` can also short-circuit straight to END when the request is too
ambiguous to investigate (see `graph.state.Scope.needs_clarification`).
"""

from langgraph.graph import END, StateGraph

from agent.nodes import (
    diagnose,
    execute_tool,
    gather_context,
    intake,
    plan,
    recommend,
    route_after_intake,
    route_after_plan,
)
from graph.state import AgentState


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("intake", intake)
    graph.add_node("gather_context", gather_context)
    graph.add_node("plan", plan)
    graph.add_node("execute_tool", execute_tool)
    graph.add_node("diagnose", diagnose)
    graph.add_node("recommend", recommend)

    graph.set_entry_point("intake")
    graph.add_conditional_edges("intake", route_after_intake, {"gather_context": "gather_context", "end": END})
    graph.add_edge("gather_context", "plan")
    graph.add_conditional_edges("plan", route_after_plan, {"execute_tool": "execute_tool", "diagnose": "diagnose"})
    graph.add_edge("execute_tool", "plan")
    graph.add_edge("diagnose", "recommend")
    graph.add_edge("recommend", END)

    return graph.compile()


troubleshooting_graph = build_graph()
