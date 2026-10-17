"""Retrieval over the data catalog (the R in RAG)."""

from lakehouse_copilot.rag.base import Retriever, SearchHit
from lakehouse_copilot.rag.factory import get_retriever

__all__ = ["Retriever", "SearchHit", "get_retriever"]
