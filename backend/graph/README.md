`AgentState` and the graph assembly, per `docs/architecture.md` §6-7.

- `state.py` — `AgentState` (Pydantic), plus `Scope`, `ToolCallRecord`,
  `Diagnosis`, and `LOOP_GUARD_MAX` (the plan→execute_tool loop cap).
- `build.py` — wires `agent.nodes` into the state graph: `intake` →
  `gather_context` → `plan` ⇄ `execute_tool` → `diagnose` → `recommend` →
  `END`, with `intake` able to short-circuit straight to `END` when the
  request is too ambiguous to scope. Exports the compiled
  `troubleshooting_graph`, imported by `backend/api/main.py`.

No checkpointer is wired in yet — conversational memory across turns is
still the "client resends full history" approach from Weeks 1-3
(`AgentState.messages`, seeded from `ChatRequest.history`), not LangGraph
checkpointing. That's Week 6 per the plan.
