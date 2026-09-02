"""Injected-failure eval harness (docs/architecture.md §9, plan §6).

Turns "the agent works" into a number: each scenario in `scenarios.py`
pairs a manifest that breaks in a known way with a golden label stating
the true root cause, and `runner.py` scores the agent's diagnosis against
it.
"""

from .scenarios import SCENARIOS, SCENARIOS_BY_ID, Scenario

__all__ = ["SCENARIOS", "SCENARIOS_BY_ID", "Scenario"]
