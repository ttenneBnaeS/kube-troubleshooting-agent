import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from observability.tracing import flush_traces, init_tracing

# Before LangChain is imported: it reads tracing config from the process
# environment, which this populates from .env.
TRACING_PROJECT = init_tracing("LANGSMITH_PROJECT", "kube-troubleshooting-agent")

import aiosqlite
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from sse_starlette.sse import EventSourceResponse

from api import trail
from api.config import settings
from api.schemas import ChatRequest
from graph import build_graph, new_turn_input
from graph.state import (
    DEFAULT_CLARIFYING_QUESTION,
    Diagnosis,
    ReferenceDoc,
    ToolCallRecord,
    TurnRecord,
    checkpoint_serializer,
)
from observability import bind, configure_logging, get_logger

configure_logging()
log = get_logger(__name__)

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
        log.info(
            "api.started",
            extra={"checkpoint_db": settings.checkpoint_db_path, "tracing_project": TRACING_PROJECT},
        )
        yield
    if TRACING_PROJECT:
        flush_traces()


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


def _steps_for_update(node: str, update) -> list[dict]:
    """Trail steps for one node's state update ("updates" stream mode).

    Updates carry model instances or plain dicts depending on the path, so
    each is re-validated into its model before serializing.
    """
    if not update:
        return []
    if node in ("gather_context", "execute_tool"):
        log = _field(update, "investigation_log") or []
        return [trail.log_step(ToolCallRecord.model_validate(log[-1]))] if log else []
    if node == "diagnose":
        diagnosis = _field(update, "diagnosis")
        return [trail.diagnosis_step(Diagnosis.model_validate(diagnosis))] if diagnosis else []
    if node == "ground":
        docs = [ReferenceDoc.model_validate(d) for d in _field(update, "reference_docs") or []]
        error = _field(update, "grounding_error")
        return [trail.docs_step(docs, error)] if docs or error else []
    if node == "answer_followup":
        return [trail.followup_step()]
    return []


@app.get("/api/threads/{thread_id}")
async def get_thread(thread_id: str) -> dict:
    """A thread's turns as chat messages, each assistant reply with its trail.

    Built from the archived `TurnRecord`s rather than `messages`, because
    that's where each turn's evidence lives. An unknown id is an empty
    conversation, not an error: the client mints ids before first use.
    """
    snapshot = await app.state.graph.aget_state({"configurable": {"thread_id": thread_id}})
    turns = [TurnRecord.model_validate(t) for t in _field(snapshot.values, "turns") or []]
    messages = []
    for turn in turns:
        messages.append({"role": "user", "content": turn.user_request})
        messages.append({"role": "assistant", "content": turn.reply, "trail": trail.turn_trail(turn)})
    return {"thread_id": thread_id, "messages": messages}


def turn_config(thread_id: str, request_id: str) -> dict:
    """LangGraph config for one chat turn: checkpoint thread plus trace identity.

    `thread_id` in metadata is what LangSmith groups a conversation's turns
    into a thread by. The root `run_id` is minted here rather than left to
    LangChain so it can be logged before the run starts.
    """
    return {
        "configurable": {"thread_id": thread_id},
        "run_id": uuid.uuid4(),
        "run_name": "chat_turn",
        "tags": ["api"],
        "metadata": {"thread_id": thread_id, "request_id": request_id},
    }


@app.post("/api/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    graph = app.state.graph
    request_id = uuid.uuid4().hex[:12]
    config = turn_config(req.thread_id, request_id)

    async def event_stream():
        # Bound inside the generator: it runs in the response task, so a
        # context set in the endpoint body wouldn't reach the graph's logs.
        context = {"request_id": request_id, "thread_id": req.thread_id}
        if TRACING_PROJECT:
            # The trace's root run id, so a log line leads to its trace.
            context["trace_id"] = str(config["run_id"])
        with bind(**context):
            async for event in _turn_events(graph, req, config):
                yield event

    return EventSourceResponse(event_stream())


async def _turn_events(graph, req: ChatRequest, config: dict):
    started = last_node_at = time.monotonic()
    log.info("chat.turn.start", extra={"message_chars": len(req.message)})
    try:
        final_state = None
        streamed_reply = False
        # "messages" mode surfaces the prose nodes' tokens as they're
        # generated; "updates" mode gives each node's output as it
        # finishes, which becomes the investigation trail (`event:
        # step`, JSON); "values" mode gives the full state after each
        # node, so a turn that streamed nothing (`intake` asked a
        # clarifying question) can send the reply `finalize` recorded.
        async for stream_mode, chunk in graph.astream(
            new_turn_input(req.message), config=config, stream_mode=["messages", "updates", "values"]
        ):
            if stream_mode == "messages":
                message, metadata = chunk
                # `.text` drops thinking blocks; `.content` would be a
                # list of blocks whenever the model thinks first.
                if metadata.get("langgraph_node") in PROSE_NODES and message.text:
                    streamed_reply = True
                    yield {"event": "token", "data": message.text}
            elif stream_mode == "updates":
                now = time.monotonic()
                for node, update in chunk.items():
                    # The graph runs one node at a time, so the gap
                    # since the previous update is this node's runtime.
                    log.debug("graph.node", extra={"node": node, "duration_ms": round((now - last_node_at) * 1000)})
                    for step in _steps_for_update(node, update):
                        yield {"event": "step", "data": json.dumps(step)}
                last_node_at = now
            elif stream_mode == "values":
                final_state = chunk

        if not streamed_reply:
            turns = _field(final_state, "turns") or []
            reply = _field(turns[-1], "reply") if turns else None
            yield {"event": "token", "data": reply or DEFAULT_CLARIFYING_QUESTION}
    except Exception as exc:
        # Previously this reached only the client, as an SSE error event,
        # and left no trace server-side.
        log.exception("chat.turn.failed", extra={"duration_ms": round((time.monotonic() - started) * 1000)})
        yield {"event": "error", "data": str(exc)}
        return
    _log_turn_end(final_state, started)
    yield {"event": "done", "data": ""}


def _log_turn_end(final_state, started: float) -> None:
    turns = _field(final_state, "turns") or []
    turn = TurnRecord.model_validate(turns[-1]) if turns else None
    log.info(
        "chat.turn.end",
        extra={
            "route": turn.route if turn else None,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "tool_calls": sum(1 for r in turn.investigation_log if r.tool_name != trail.INITIAL_SWEEP) if turn else 0,
            "confidence": turn.diagnosis.confidence if turn and turn.diagnosis else None,
            "loop_guard": bool(turn and turn.loop_guard_triggered),
            "reply_chars": len(turn.reply) if turn else 0,
        },
    )
