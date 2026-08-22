K8s/kubectl doc corpus, embedding, and retrieval, per `docs/architecture.md` §8.

- `corpus/` — curated `.md` pages (YAML frontmatter: `title`, `source_url`)
  fetched from official Kubernetes docs, scoped to the failure-scenario
  catalog (Section 7 of the plan) rather than a full site crawl.
- `loader.py` — parses corpus frontmatter/content.
- `index.py` — chunks the corpus (`RecursiveCharacterTextSplitter`,
  markdown-aware) and (re)populates a Qdrant collection. Run directly to
  rebuild: `uv run python -m rag.index`. Embeddings are Voyage AI
  (`VOYAGE_API_KEY` env var) — Anthropic has no embeddings API of its own.
- `retriever.py` — `search_docs(query, k)`, returns normalized
  `{title, source_url, content, score}` results, no LangChain dependency.
- `langchain_tool.py` — `@tool`-wrapped `search_k8s_docs_tool` for
  Anthropic tool calling, bound alongside the cluster tools in
  `backend/api/main.py`.

Requires a running Qdrant (`docker run -p 6333:6333 -p 6334:6334 -v
qdrant_storage:/qdrant/storage qdrant/qdrant`) and a populated collection
(`uv run python -m rag.index`) before the search tool works at all — if
the collection doesn't exist yet, Qdrant returns a 404 and `search_docs`
raises `UnexpectedResponse`, which the chat endpoint's tool execution
will surface as a caught error, not a silent empty result.

Retrieval grounds `diagnose`/`recommend` output in real docs; it does not
do the diagnosis itself — see the plan's "don't oversell RAG" framing.
