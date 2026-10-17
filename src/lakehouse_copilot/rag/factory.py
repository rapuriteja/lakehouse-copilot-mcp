"""Pick the retriever from settings: RETRIEVER=local (default) or RETRIEVER=azure."""

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
    from lakehouse_copilot.rag.local import TfidfRetriever

    return TfidfRetriever(build_chunks(load_catalog()))
