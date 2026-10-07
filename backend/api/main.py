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

# Nodes whose LLM output is user-facing prose, streamed token by token.
# Every other LLM node returns structured output with nothing to stream.
PROSE_NODES = frozenset({"recommend", "answer_followup"})


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
            streamed_reply = False
            # "messages" mode surfaces the prose nodes' tokens as they're
            # generated; "values" mode gives the full state after each
            # node, so a turn that streamed nothing (`intake` asked a
            # clarifying question) can send the reply `finalize` recorded.
            async for stream_mode, chunk in graph.astream(
                new_turn_input(req.message), config=config, stream_mode=["messages", "values"]
            ):
                if stream_mode == "messages":
                    message, metadata = chunk
                    # `.text` drops thinking blocks; `.content` would be a
                    # list of blocks whenever the model thinks first.
                    if metadata.get("langgraph_node") in PROSE_NODES and message.text:
                        streamed_reply = True
                        yield {"event": "token", "data": message.text}
                elif stream_mode == "values":
                    final_state = chunk

            if not streamed_reply:
                turns = _field(final_state, "turns") or []
                reply = _field(turns[-1], "reply") if turns else None
                yield {"event": "token", "data": reply or DEFAULT_CLARIFYING_QUESTION}
        except Exception as exc:
            yield {"event": "error", "data": str(exc)}
            return
        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_stream())
