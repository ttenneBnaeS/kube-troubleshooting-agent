"""`uv run --directory backend python -m mcp_server` — serve `investigate` over stdio.

Run from `backend/` (that's what `--directory` does): backend config reads
`.env` relative to the working directory.
"""

import sys

from observability.tracing import init_tracing

# Before LangChain is imported (see observability.tracing).
init_tracing("LANGSMITH_PROJECT", "kube-troubleshooting-agent")

from observability import configure_logging

# stderr: over stdio, stdout is the MCP protocol stream, and one stray
# log line there corrupts it.
configure_logging(stream=sys.stderr)

from .server import server

server.run("stdio")
