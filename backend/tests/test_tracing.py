"""Tracing config: per-surface projects, trace identity, and tests never tracing."""

import os
import uuid

import pytest

from observability.tracing import init_tracing

TRACING_VARS = (
    "LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY",
    "LANGSMITH_PROJECT", "LANGCHAIN_PROJECT", "LANGSMITH_EVAL_PROJECT",
)


@pytest.fixture
def env(monkeypatch, tmp_path):
    for var in TRACING_VARS:
        monkeypatch.delenv(var, raising=False)
    empty = tmp_path / ".env"
    empty.write_text("")
    return empty


def test_off_without_key(env, monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert init_tracing("LANGSMITH_PROJECT", "default", env_file=env) is None


def test_off_without_flag(env, monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "k")
    assert init_tracing("LANGSMITH_PROJECT", "default", env_file=env) is None


def test_eval_gets_its_own_project_even_when_one_is_set(env, monkeypatch):
    # A LANGSMITH_PROJECT in .env must not pull eval runs into it.
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "k")
    monkeypatch.setenv("LANGSMITH_PROJECT", "production")
    assert init_tracing("LANGSMITH_EVAL_PROJECT", "agent-eval", env_file=env) == "agent-eval"
    assert os.environ["LANGSMITH_PROJECT"] == os.environ["LANGCHAIN_PROJECT"] == "agent-eval"


def test_api_uses_configured_project(env, monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "1")  # legacy names still count
    monkeypatch.setenv("LANGCHAIN_API_KEY", "k")
    monkeypatch.setenv("LANGSMITH_PROJECT", "production")
    assert init_tracing("LANGSMITH_PROJECT", "fallback", env_file=env) == "production"


def test_env_file_is_loaded(env, monkeypatch):
    env.write_text("LANGSMITH_TRACING=true\nLANGSMITH_API_KEY=k\n")
    assert init_tracing("LANGSMITH_PROJECT", "from-default", env_file=env) == "from-default"


def test_turn_config_groups_turns_into_a_thread():
    from api.main import turn_config

    config = turn_config("thread-1", "req-1")
    assert config["configurable"] == {"thread_id": "thread-1"}
    assert config["metadata"] == {"thread_id": "thread-1", "request_id": "req-1"}
    assert isinstance(config["run_id"], uuid.UUID)
    assert turn_config("thread-1", "req-2")["run_id"] != config["run_id"]


def test_the_test_suite_never_traces():
    from langsmith.utils import tracing_is_enabled

    import api.main  # loads .env as a side effect

    assert api.main.TRACING_PROJECT is None
    assert not tracing_is_enabled()
