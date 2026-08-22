import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from sse_starlette.sse import EventSourceResponse

from api.schemas import ChatRequest
from models.config import ModelTier, get_chat_model
from prompts import load_prompt
from rag import search_k8s_docs_tool
from tools import TOOLS

app = FastAPI(title="Kubernetes Troubleshooting Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ALL_TOOLS = [*TOOLS, search_k8s_docs_tool]
chat_model = get_chat_model(ModelTier.REASONING).bind_tools(ALL_TOOLS)
# No tools bound: used only for the closing streamed answer, so a model
# that still wants another tool call at the round cap is forced to answer
# in text from whatever evidence it already has, instead of streaming
# nothing (see the loop guard in docs/architecture.md §3.4/§7).
final_chat_model = get_chat_model(ModelTier.REASONING)
TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}
MAX_TOOL_ROUNDS = 5


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    messages: list[BaseMessage] = [SystemMessage(content=load_prompt("chat_v3"))]
    for m in req.history:
        role_cls = HumanMessage if m.role == "user" else AIMessage
        messages.append(role_cls(content=m.content))
    messages.append(HumanMessage(content=req.message))

    async def event_stream():
        try:
            # Bounded plan/execute-style loop (the Week 4 LangGraph loop
            # replaces this with the real graph, but the loop-guard idea is
            # the same as docs/architecture.md §3.4/§7): let the model call
            # tools across multiple rounds, capped so a run can't loop
            # forever, then force a final text answer from whatever
            # evidence it gathered.
            for _ in range(MAX_TOOL_ROUNDS):
                probe = await chat_model.ainvoke(messages)
                if not probe.tool_calls:
                    break
                messages.append(probe)
                for call in probe.tool_calls:
                    result = await asyncio.to_thread(TOOLS_BY_NAME[call["name"]].invoke, call["args"])
                    messages.append(ToolMessage(content=result, tool_call_id=call["id"]))

            async for chunk in final_chat_model.astream(messages):
                if chunk.text:
                    yield {"event": "token", "data": chunk.text}
        except Exception as exc:
            yield {"event": "error", "data": str(exc)}
            return
        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_stream())
