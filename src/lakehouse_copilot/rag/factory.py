"""Pick the retriever from settings.

RETRIEVER=local   TF-IDF keyword search (default, free, offline)
RETRIEVER=hybrid  TF-IDF + embeddings merged with RRF, on your laptop
RETRIEVER=azure   Azure AI Search: hybrid + semantic ranker
"""

from __future__ import annotations

from lakehouse_copilot.catalog import load_catalog
from lakehouse_copilot.catalog.docs import build_chunks
from lakehouse_copilot.config import Settings, get_settings
from lakehouse_copilot.rag.base import Retriever


def get_retriever(settings: Settings | None = None) -> Retriever:
    settings = settings or get_settings()
    if settings.retriever == "azure":
        from lakehouse_copilot.rag.azure import AzureSearchRetriever

        return AzureSearchRetriever.from_settings(settings)
    chunks = build_chunks(load_catalog())
    if settings.retriever == "hybrid":
        from lakehouse_copilot.rag.embeddings import get_embedder
        from lakehouse_copilot.rag.hybrid import HybridRetriever

        cache = settings.lakehouse_dir / "_index" / "embeddings.jsonl"
        return HybridRetriever(chunks, get_embedder(settings), cache)
    from lakehouse_copilot.rag.local import TfidfRetriever

    return TfidfRetriever(chunks)
