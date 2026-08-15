"""Versioned prompt templates, loaded by name (e.g. `load_prompt("chat_v1")`)."""

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


@lru_cache
def load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.md").read_text().strip()
