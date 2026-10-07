from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    # Conversation history lives server-side in the checkpointer, keyed by
    # this id; the client generates it and resends it each turn.
    thread_id: str
