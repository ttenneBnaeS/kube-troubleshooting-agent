"""The read-only safety boundary, enforced statically (docs/architecture.md §5).

Read-only is a property of which Kubernetes client methods the tool layer
calls, not of anything a prompt says. This scans every module in `tools/`
for calls on a client API object and fails on any verb that isn't a read,
so a mutating call can't land without this test being deliberately edited.
"""

import ast
from pathlib import Path

import pytest

import tools

TOOLS_DIR = Path(tools.__file__).parent
# Verbs the generated kubernetes client uses for reads. Everything else it
# exposes (create_/delete_/patch_/replace_/connect_ ...) mutates or execs.
READ_PREFIXES = ("list_", "read_", "get_")
# Getters that build API clients rather than call the API.
CLIENT_GETTERS = {"get_core_v1_api", "get_apps_v1_api", "get_networking_v1_api"}
MUTATING_PREFIXES = ("create_", "delete_", "patch_", "replace_", "connect_")


def _api_method_calls(path: Path) -> list[tuple[int, str]]:
    """Method names called on `get_*_api()` results or `api`-named variables."""
    tree = ast.parse(path.read_text())
    calls = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        receiver = node.func.value
        on_getter = (
            isinstance(receiver, ast.Call)
            and isinstance(receiver.func, ast.Name)
            and receiver.func.id in CLIENT_GETTERS
        )
        on_api_var = isinstance(receiver, ast.Name) and receiver.id == "api"
        if on_getter or on_api_var:
            calls.append((node.lineno, node.func.attr))
    return calls


TOOL_MODULES = sorted(TOOLS_DIR.glob("*.py"))


@pytest.mark.parametrize("path", TOOL_MODULES, ids=lambda p: p.name)
def test_tool_modules_only_read(path):
    bad = [f"{path.name}:{line} {name}" for line, name in _api_method_calls(path) if not name.startswith(READ_PREFIXES)]
    assert not bad, f"non-read Kubernetes API calls in the tool layer: {bad}"


def test_no_mutating_verb_appears_anywhere_in_tools():
    # Belt and braces for calls the receiver heuristic above can't see
    # (an API object passed under another name): no attribute access with
    # a mutating client verb is allowed in tools/ at all.
    hits = []
    for path in TOOL_MODULES:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and node.attr.startswith(MUTATING_PREFIXES):
                hits.append(f"{path.name}:{node.lineno} {node.attr}")
    assert not hits, f"mutating client verbs referenced in tools/: {hits}"


def test_the_scan_sees_the_real_calls():
    # Guard against the heuristic silently matching nothing: the tool
    # layer is known to make these reads.
    seen = {name for path in TOOL_MODULES for _, name in _api_method_calls(path)}
    assert {"list_namespaced_pod", "read_namespaced_pod_log", "read_namespaced_secret"} <= seen


def test_no_tool_exposes_a_mutating_kubectl_verb():
    from tools import TOOLS

    verbs = ("apply", "delete", "edit", "scale", "rollout", "patch", "create", "exec")
    names = [t.name for t in TOOLS]
    assert not [n for n in names if any(v in n for v in verbs)], names
