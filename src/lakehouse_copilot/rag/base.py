"""The retriever contract shared by the local and Azure implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from lakehouse_copilot.catalog.docs import Chunk


@dataclass(frozen=True)
class SearchHit:
    chunk: Chunk
    score: float


class Retriever(Protocol):
    name: str

    def search(self, query: str, k: int = 5) -> list[SearchHit]:
        """Return the k most relevant catalog chunks, best first."""
        ...
