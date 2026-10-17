"""Local hybrid retriever: TF-IDF keyword search + embedding vector search, merged with RRF.

This is the same idea as Azure AI Search hybrid search, running on your laptop, so vector
search can be measured without any Azure resources. Chunk embeddings are cached on disk
and only recomputed when a chunk's text or the embedding model changes.

Reciprocal Rank Fusion: each list gives a document 1 / (60 + rank) points; the points are
added up. It needs no score calibration between the two very different searches.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from lakehouse_copilot.catalog.docs import Chunk
from lakehouse_copilot.rag.base import SearchHit
from lakehouse_copilot.rag.embeddings import Embedder, cosine_scores, normalise
from lakehouse_copilot.rag.local import TfidfRetriever

RRF_K = 60
CANDIDATES = 20


def chunk_text(chunk: Chunk) -> str:
    return f"{chunk.title}\n{chunk.text}"


class EmbeddingCache:
    """Vectors stored as JSON lines keyed by sha256(model + text)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.vectors: dict[str, list[float]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                self.vectors[row["key"]] = row["vector"]

    @staticmethod
    def key(model: str, text: str) -> str:
        return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()

    def embed(self, embedder: Embedder, texts: list[str]) -> list[list[float]]:
        keys = [self.key(embedder.name, t) for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in self.vectors]
        if missing:
            new = embedder.embed([texts[i] for i in missing])
            for i, vector in zip(missing, new, strict=True):
                self.vectors[keys[i]] = vector
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as f:
                for k in keys:  # keep only the current chunks
                    f.write(json.dumps({"key": k, "vector": self.vectors[k]}) + "\n")
        return [self.vectors[k] for k in keys]


class HybridRetriever:
    name = "local-hybrid"

    def __init__(self, chunks: list[Chunk], embedder: Embedder, cache_path: Path) -> None:
        self.chunks = chunks
        self.embedder = embedder
        self.keyword = TfidfRetriever(chunks)
        vectors = EmbeddingCache(cache_path).embed(embedder, [chunk_text(c) for c in chunks])
        self.matrix = normalise(vectors)
        self.position = {c.id: i for i, c in enumerate(chunks)}
        self.name = f"local-hybrid({embedder.name})"

    def search(self, query: str, k: int = 5) -> list[SearchHit]:
        keyword = [h.chunk.id for h in self.keyword.search(query, CANDIDATES)]
        scores = cosine_scores(self.embedder.embed([query])[0], self.matrix)
        vector = [self.chunks[i].id for i in np.argsort(-scores)[:CANDIDATES]]
        fused: dict[str, float] = {}
        for ranking in (keyword, vector):
            for rank, chunk_id in enumerate(ranking, start=1):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        best = sorted(fused, key=lambda cid: (-fused[cid], self.position[cid]))[:k]
        return [SearchHit(self.chunks[self.position[cid]], fused[cid]) for cid in best]
