from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from sse_starlette.sse import EventSourceResponse

from api.config import settings
from api.schemas import ChatRequest
from graph import build_graph, new_turn_input
from graph.state import DEFAULT_CLARIFYING_QUESTION, checkpoint_serializer


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The checkpointer holds an open aiosqlite connection, so it has to be
    # entered for the app's lifetime rather than built at import time.
    Path(settings.checkpoint_db_path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(settings.checkpoint_db_path) as conn:
        checkpointer = AsyncSqliteSaver(conn, serde=checkpoint_serializer())
        app.state.graph = build_graph(checkpointer)
        yield


app = FastAPI(title="Kubernetes Troubleshooting Agent", lifespan=lifespan)

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
    graph = app.state.graph
    config = {"configurable": {"thread_id": req.thread_id}}

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
            async for stream_mode, chunk in graph.astream(
                new_turn_input(req.message), config=config, stream_mode=["messages", "values"]
            ):
                if stream_mode == "messages":
                    message, metadata = chunk
                    # `.text` drops thinking blocks; `.content` would be a
                    # list of blocks whenever the model thinks first.
                    if metadata.get("langgraph_node") == "recommend" and message.text:
                        streamed_recommendation = True
                        yield {"event": "token", "data": message.text}
                elif stream_mode == "values":
                    final_state = chunk

            if not streamed_recommendation:
                scope = _field(final_state, "scope")
                question = _field(scope, "clarifying_question") if scope else None
                yield {"event": "token", "data": question or DEFAULT_CLARIFYING_QUESTION}
        except Exception as exc:
            yield {"event": "error", "data": str(exc)}
            return
        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_stream())
