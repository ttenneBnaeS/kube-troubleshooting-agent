from anthropic import AsyncAnthropic
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from api.schemas import ChatRequest
from models.config import ModelTier, settings
from prompts import load_prompt

app = FastAPI(title="Kubernetes Troubleshooting Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = AsyncAnthropic(api_key=settings.anthropic_api_key)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    messages = [{"role": m.role, "content": m.content} for m in req.history]
    messages.append({"role": "user", "content": req.message})

    async def event_stream():
        try:
            async with client.messages.stream(
                model=settings.model_for(ModelTier.REASONING),
                max_tokens=settings.max_tokens,
                system=load_prompt("chat_v1"),
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield {"event": "token", "data": text}
        except Exception as exc:
            yield {"event": "error", "data": str(exc)}
            return
        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_stream())
