"""The HTTP surface: SSE chat stream and thread rehydration, over a stubbed graph."""

import json

import pytest
from fastapi.testclient import TestClient

import api.config
from api.main import app


@pytest.fixture
def client(stub_nodes, tmp_path, monkeypatch):
    monkeypatch.setattr(api.config.settings, "checkpoint_db_path", str(tmp_path / "checkpoints.db"))
    with TestClient(app) as c:
        yield c


def chat(client, message, thread_id="t1") -> list[tuple[str, str]]:
    response = client.post("/api/chat", json={"message": message, "thread_id": thread_id})
    assert response.status_code == 200
    # Same framing the frontend uses: CRLF-normalized, blank-line separated,
    # every `data:` line of an event joined.
    events = []
    for raw in response.text.replace("\r\n", "\n").split("\n\n"):
        lines = raw.split("\n")
        name = next((ln[6:].strip() for ln in lines if ln.startswith("event:")), None)
        data = "\n".join(ln[5:].removeprefix(" ") for ln in lines if ln.startswith("data:"))
        if name:
            events.append((name, data))
    return events


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_investigation_streams_trail_then_done(client):
    events = chat(client, "pod won't start")
    names = [n for n, _ in events]
    steps = [json.loads(d)["type"] for n, d in events if n == "step"]
    assert steps == ["sweep", "tool", "tool", "diagnosis", "docs"]
    assert names[-1] == "done"
    assert "error" not in names


def test_clarifying_question_is_sent_when_nothing_streams(client):
    events = chat(client, "vague")
    assert ("token", "Which namespace?") in events
    assert [n for n, _ in events if n == "step"] == []


def test_thread_rehydration(client):
    chat(client, "pod won't start", thread_id="t2")
    chat(client, "which key again", thread_id="t2")
    body = client.get("/api/threads/t2").json()
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    first, second = body["messages"][1], body["messages"][3]
    assert first["content"] == "fix for pod won't start"
    assert [s["type"] for s in first["trail"]] == ["sweep", "tool", "tool", "diagnosis", "docs"]
    assert second["trail"] == [{"type": "followup"}]


def test_unknown_thread_is_empty_not_an_error(client):
    assert client.get("/api/threads/never-used").json() == {"thread_id": "never-used", "messages": []}


def test_chat_requires_thread_id(client):
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 422
