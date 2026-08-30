from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sse_starlette.sse import EventSourceResponse

from api.schemas import ChatRequest
from graph import troubleshooting_graph

app = FastAPI(title="Kubernetes Troubleshooting Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _field(state, name, default=None):
    # `values`-mode state chunks come back as either the AgentState model
    # or a plain dict of its fields depending on LangGraph version/path —
    # accept either rather than assuming one.
    if isinstance(state, dict):
        return state.get(name, default)
    return getattr(state, name, default)


@app.post("/api/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    history: list[BaseMessage] = [
        (HumanMessage if m.role == "user" else AIMessage)(content=m.content) for m in req.history
    ]
    initial_state = {"user_request": req.message, "messages": history}

    async def event_stream():
        try:
            final_state = None
            streamed_recommendation = False
            # The graph's `recommend` node is the only one whose LLM call
            # produces user-facing prose; "messages" mode surfaces its
            # tokens as they're generated, "values" mode gives us the full
            # state after each node so we can fall back to a clarifying
            # question if `intake` short-circuited the graph before
            # `recommend` ever ran (docs/architecture.md §7).
            async for stream_mode, chunk in troubleshooting_graph.astream(
                initial_state, stream_mode=["messages", "values"]
            ):
                if stream_mode == "messages":
                    message, metadata = chunk
                    if metadata.get("langgraph_node") == "recommend" and message.content:
                        streamed_recommendation = True
                        yield {"event": "token", "data": message.content}
                elif stream_mode == "values":
                    final_state = chunk

            if not streamed_recommendation:
                scope = _field(final_state, "scope")
                question = _field(scope, "clarifying_question") if scope else None
                yield {"event": "token", "data": question or "Could you say more about what's going wrong?"}
        except Exception as exc:
            yield {"event": "error", "data": str(exc)}
            return
        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_stream())
