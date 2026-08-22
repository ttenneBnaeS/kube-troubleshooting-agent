from functools import lru_cache

from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from .config import settings
from .index import get_embeddings


@lru_cache(maxsize=1)
def _vectorstore() -> QdrantVectorStore:
    client = QdrantClient(url=settings.qdrant_url)
    # validate_collection_config would otherwise embed a dummy string on
    # every construction just to check vector-size compatibility — a real
    # Voyage API call we don't need, since index.py already built this
    # collection with the same embedding model. Caching the instance
    # avoids paying that cost (or reconnecting) on every search_docs call.
    return QdrantVectorStore(
        client=client,
        collection_name=settings.collection_name,
        embedding=get_embeddings(),
        validate_collection_config=False,
    )


def search_docs(query: str, k: int | None = None) -> list[dict]:
    """Top-k relevant doc chunks for a query, each with its source title/URL and similarity score."""
    results = _vectorstore().similarity_search_with_score(query, k=k or settings.top_k)
    return [
        {
            "title": doc.metadata.get("title"),
            "source_url": doc.metadata.get("source_url"),
            "content": doc.page_content,
            "score": float(score),
        }
        for doc, score in results
    ]
