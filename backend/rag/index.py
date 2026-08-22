"""Build/rebuild the Qdrant collection from the corpus.

Run directly to (re)index: `uv run python -m rag.index`. The corpus is
small and static, so indexing just recreates the collection from scratch
each time rather than tracking incremental updates.
"""

import time

from langchain_core.documents import Document
from langchain_qdrant import QdrantVectorStore
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter
from langchain_voyageai import VoyageAIEmbeddings
from voyageai.error import RateLimitError

from .config import settings
from .loader import load_corpus

# Voyage's free tier (no payment method on file) caps requests at 3 RPM /
# 10K TPM, far below what a single from_documents() call would send for
# this corpus. Neither VoyageAIEmbeddings nor QdrantVectorStore backs off
# for that, so we embed in small, spaced-out batches ourselves.
_EMBED_BATCH_SIZE = 30
_EMBED_DELAY_SECONDS = 21


def get_embeddings() -> VoyageAIEmbeddings:
    return VoyageAIEmbeddings(model=settings.embedding_model, voyage_api_key=settings.voyage_api_key)


def build_index() -> int:
    """Chunk the corpus and (re)populate the Qdrant collection. Returns the chunk count."""
    docs = load_corpus()
    splitter = RecursiveCharacterTextSplitter.from_language(
        Language.MARKDOWN, chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap
    )

    chunks = [
        Document(
            page_content=piece,
            metadata={"title": doc.title, "source_url": doc.source_url, "file": doc.file, "chunk": i},
        )
        for doc in docs
        for i, piece in enumerate(splitter.split_text(doc.content))
    ]

    embeddings = get_embeddings()
    vectorstore: QdrantVectorStore | None = None
    for i in range(0, len(chunks), _EMBED_BATCH_SIZE):
        batch = chunks[i : i + _EMBED_BATCH_SIZE]
        _add_batch_with_retry(vectorstore, batch, embeddings)
        if vectorstore is None:
            vectorstore = QdrantVectorStore.from_existing_collection(
                embedding=embeddings, url=settings.qdrant_url, collection_name=settings.collection_name
            )
        if i + _EMBED_BATCH_SIZE < len(chunks):
            time.sleep(_EMBED_DELAY_SECONDS)

    return len(chunks)


def _add_batch_with_retry(
    vectorstore: QdrantVectorStore | None, batch: list[Document], embeddings: VoyageAIEmbeddings
) -> None:
    try:
        if vectorstore is None:
            QdrantVectorStore.from_documents(
                batch,
                embedding=embeddings,
                url=settings.qdrant_url,
                collection_name=settings.collection_name,
                force_recreate=True,
            )
        else:
            vectorstore.add_documents(batch)
    except RateLimitError:
        time.sleep(65)
        _add_batch_with_retry(vectorstore, batch, embeddings)


if __name__ == "__main__":
    docs = load_corpus()
    chunk_count = build_index()
    print(
        f"Indexed {chunk_count} chunks from {len(docs)} docs into "
        f"Qdrant collection '{settings.collection_name}' at {settings.qdrant_url}"
    )
