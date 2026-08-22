import json

from langchain_core.tools import tool

from .retriever import search_docs


@tool
def search_k8s_docs_tool(query: str) -> str:
    """Search official Kubernetes/kubectl documentation for grounding on a concept, error, or remediation (e.g. "CrashLoopBackOff", "readiness probe failure", "NetworkPolicy denying traffic"). Returns relevant doc excerpts with source URLs to cite in a recommendation."""
    return json.dumps(search_docs(query))
