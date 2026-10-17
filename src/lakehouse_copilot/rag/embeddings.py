"""Embeddings: turn text into vectors so that texts with similar meaning are close.

EMBEDDING_PROVIDER picks where the vectors come from:
  openrouter  OpenRouter's embeddings API (default model openai/text-embedding-3-small)
  azure       an Azure OpenAI embedding deployment
  local       a free sentence-transformers model on your laptop
              (pip install -e ".[local-embeddings]")
All three are used through the same `embed(texts)` method.
"""

from __future__ import annotations

from typing import Any, Protocol

import numpy as np

BATCH_SIZE = 64


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbedder:
    """OpenRouter, Azure OpenAI or any server with the OpenAI embeddings API."""

    def __init__(self, client: Any, model: str, dimensions: int | None = None) -> None:
        self.client = client
        self.model = model  # on Azure this is the deployment name
        self.dimensions = dimensions
        self.name = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            response = self.client.embeddings.create(model=self.model, input=batch)
            data = sorted(getattr(response, "data", None) or [], key=lambda d: d.index)
            if len(data) != len(batch):
                raise RuntimeError(
                    f"Embedding API returned {len(data)} vectors for {len(batch)} texts."
                )
            vectors += [list(d.embedding) for d in data]
        if self.dimensions and vectors and len(vectors[0]) != self.dimensions:
            raise RuntimeError(
                f"{self.model} returned {len(vectors[0])} dimensions, "
                f"EMBEDDING_DIMENSIONS is {self.dimensions}."
            )
        return vectors


class SentenceTransformerEmbedder:
    """Free local embeddings. The model downloads once from Hugging Face, then runs offline."""

    def __init__(self, model: str) -> None:
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model)
        self.name = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()


def get_embedder(settings) -> Embedder:
    if settings.embedding_provider == "local":
        return SentenceTransformerEmbedder(settings.local_embedding_model)
    if settings.embedding_provider == "azure":
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.secret("azure_openai_api_key"),
            api_version=settings.azure_openai_api_version,
        )
        return OpenAICompatibleEmbedder(
            client, settings.azure_openai_embedding_deployment, settings.embedding_dimensions
        )
    from lakehouse_copilot.agent.llm import openrouter_client

    client = openrouter_client(settings)
    if client is None:
        raise RuntimeError("EMBEDDING_PROVIDER=openrouter needs OPENROUTER_API_KEY.")
    return OpenAICompatibleEmbedder(client, settings.embedding_model, settings.embedding_dimensions)


def cosine_scores(query: list[float], matrix: np.ndarray) -> np.ndarray:
    q = np.asarray(query, dtype=np.float32)
    q /= np.linalg.norm(q) or 1.0
    return matrix @ q


def normalise(vectors: list[list[float]]) -> np.ndarray:
    m = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms
