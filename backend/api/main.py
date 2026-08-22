import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from sse_starlette.sse import EventSourceResponse

from api.schemas import ChatRequest
from models.config import ModelTier, get_chat_model
from prompts import load_prompt
from tools import TOOLS

app = FastAPI(title="Kubernetes Troubleshooting Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

chat_model = get_chat_model(ModelTier.REASONING).bind_tools(TOOLS)
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    messages: list[BaseMessage] = [SystemMessage(content=load_prompt("chat_v2"))]
    for m in req.history:
        role_cls = HumanMessage if m.role == "user" else AIMessage
        messages.append(role_cls(content=m.content))
    messages.append(HumanMessage(content=req.message))

    async def event_stream():
        try:
            # One tool-call round trip (the Week 4 LangGraph loop replaces
            # this with a proper bounded plan/execute cycle): let the model
            # decide whether it needs a tool, run it if so, then stream the
            # final answer.
            probe = await chat_model.ainvoke(messages)
            if probe.tool_calls:
                messages.append(probe)
                for call in probe.tool_calls:
                    result = await asyncio.to_thread(TOOLS_BY_NAME[call["name"]].invoke, call["args"])
                    messages.append(ToolMessage(content=result, tool_call_id=call["id"]))

            async for chunk in chat_model.astream(messages):
                if chunk.text:
                    yield {"event": "token", "data": chunk.text}
        except Exception as exc:
            yield {"event": "error", "data": str(exc)}
            return
        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_stream())
