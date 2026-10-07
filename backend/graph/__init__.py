from .state import AgentState, Diagnosis, Scope, ToolCallRecord, TurnRecord, new_turn_input

__all__ = ["AgentState", "Diagnosis", "Scope", "ToolCallRecord", "TurnRecord", "build_graph", "new_turn_input"]


def __getattr__(name):
    # `build` imports the node functions in `agent.nodes`, which import
    # `graph.state` — loading it eagerly here made `import agent.nodes`
    # fail with a circular import unless `graph` happened to load first.
    if name == "build_graph":
        from .build import build_graph

        return build_graph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
