"""MCP server exposing the troubleshooting agent as a single `investigate` tool.

Why the agent rather than its seven read-only tools: plenty of Kubernetes
MCP servers already hand a client raw cluster reads. What this project
adds is the investigation on top: a bounded plan/execute loop, a
structured diagnosis with stated confidence, and a fix grounded in cited
docs, measured against the eval suite. Exposing that lets it run from
the assistant an engineer already uses instead of a separate web UI.

Read-only end to end: the graph's tools can't mutate the cluster
(enforced by tests/test_read_only.py), and the tool is annotated so.
"""

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations

from .investigate import Investigation, run_investigation

server = MCPServer(
    name="kube-troubleshooter",
    title="Kubernetes Troubleshooting Agent",
    instructions=(
        "Use `investigate` to diagnose a failing Kubernetes workload. It inspects the cluster read-only and "
        "returns a root cause with its evidence and a suggested fix. It never changes the cluster: any fix is "
        "for the user to review and run. A run takes roughly 20-60 seconds."
    ),
)


@server.tool(
    title="Investigate a Kubernetes failure",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        # Same question, possibly different path to the answer: the agent
        # is model-driven. No side effects either way.
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def investigate(request: str, ctx: Context, namespace: str | None = None) -> Investigation:
    """Diagnose why something in a Kubernetes cluster is failing.

    Describe the symptom in plain language, the way a user would ("the checkout pods keep restarting",
    "requests to search-api time out but every pod looks healthy"). Don't name a suspected cause unless the
    user did; the agent works it out from cluster evidence. Pass `namespace` if known.

    The agent sweeps pods and events, then gathers more evidence step by step (describe, logs, events,
    service endpoints, network policies) until it can diagnose. It returns the root cause with confidence
    and evidence, a suggested fix, and the trail of what it checked. It only reads from the cluster and
    applies nothing; present the fix to the user rather than running it. If the request is too vague to
    start, the result asks a clarifying question instead.
    """

    async def progress(step: int, message: str) -> None:
        await ctx.report_progress(step, None, message)

    return await run_investigation(request, namespace, progress)
