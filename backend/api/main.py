from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sse_starlette.sse import EventSourceResponse

from api.schemas import ChatRequest
from models.config import ModelTier, get_chat_model
from prompts import load_prompt

app = FastAPI(title="Kubernetes Troubleshooting Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

chat_model = get_chat_model(ModelTier.REASONING)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    messages: list[BaseMessage] = [SystemMessage(content=load_prompt("chat_v1"))]
    for m in req.history:
        role_cls = HumanMessage if m.role == "user" else AIMessage
        messages.append(role_cls(content=m.content))
    messages.append(HumanMessage(content=req.message))

    async def event_stream():
        try:
            async for chunk in chat_model.astream(messages):
                if chunk.text:
                    yield {"event": "token", "data": chunk.text}
        except Exception as exc:
            yield {"event": "error", "data": str(exc)}
            return
        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_stream())
