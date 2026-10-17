"""Azure retriever: hybrid search in Azure AI Search with Azure OpenAI embeddings.

Hybrid search runs two searches and merges them with Reciprocal Rank Fusion (RRF):
  - keyword search (BM25) on the text, good for exact names like `net_amount`
  - vector search on embeddings, good for meaning ("money we made" ~ "net revenue")
Then the semantic ranker (a cross-encoder model hosted by Azure) re-orders the top
results by reading the query and each chunk together.

The Azure SDKs are imported lazily so the project installs and tests without them.
"""

from __future__ import annotations

from typing import Any

from lakehouse_copilot.catalog.docs import Chunk
from lakehouse_copilot.rag.base import SearchHit

VECTOR_FIELD = "content_vector"
SEMANTIC_CONFIG = "catalog-semantic"


class AzureOpenAIEmbedder:
    def __init__(self, client: Any, deployment: str) -> None:
        self.client = client  # openai.AzureOpenAI
        self.deployment = deployment

    @classmethod
    def from_settings(cls, settings) -> AzureOpenAIEmbedder:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=settings.azure_openai_api_version,
        )
        return cls(client, settings.azure_openai_embedding_deployment)

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(model=self.deployment, input=texts)
        return [item.embedding for item in response.data]


def build_index_definition(index_name: str, dimensions: int):
    """The Azure AI Search index schema: text fields, a vector field and a semantic config."""
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchableField,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SemanticConfiguration,
        SemanticField,
        SemanticPrioritizedFields,
        SemanticSearch,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SearchableField(name="text", type=SearchFieldDataType.String),
        SimpleField(name="source_type", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="source_name", type=SearchFieldDataType.String, filterable=True),
        SimpleField(
            name="tables",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
        ),
        SearchField(
            name=VECTOR_FIELD,
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name="hnsw-profile",
        ),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
        profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw")],
    )
    semantic = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name=SEMANTIC_CONFIG,
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="text")],
                ),
            )
        ]
    )
    return SearchIndex(
        name=index_name, fields=fields, vector_search=vector_search, semantic_search=semantic
    )


class AzureSearchIndexer:
    """Create the index (if needed) and upload all chunks with their embeddings."""

    def __init__(self, index_client: Any, search_client: Any, embedder: AzureOpenAIEmbedder):
        self.index_client = index_client
        self.search_client = search_client
        self.embedder = embedder

    @classmethod
    def from_settings(cls, settings) -> AzureSearchIndexer:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
        from azure.search.documents.indexes import SearchIndexClient

        credential = AzureKeyCredential(settings.azure_search_api_key.get_secret_value())
        endpoint = settings.azure_search_endpoint
        return cls(
            SearchIndexClient(endpoint, credential),
            SearchClient(endpoint, settings.azure_search_index, credential),
            AzureOpenAIEmbedder.from_settings(settings),
        )

    def index(self, chunks: list[Chunk], index_name: str, dimensions: int) -> int:
        self.index_client.create_or_update_index(build_index_definition(index_name, dimensions))
        vectors = self.embedder.embed([f"{c.title}\n{c.text}" for c in chunks])
        docs = [c.to_dict() | {VECTOR_FIELD: v} for c, v in zip(chunks, vectors, strict=True)]
        results = self.search_client.upload_documents(documents=docs)
        return sum(1 for r in results if r.succeeded)


class AzureSearchRetriever:
    name = "azure-hybrid"

    def __init__(self, search_client: Any, embedder: AzureOpenAIEmbedder, semantic: bool = True):
        self.search_client = search_client
        self.embedder = embedder
        self.semantic = semantic

    @classmethod
    def from_settings(cls, settings) -> AzureSearchRetriever:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient

        client = SearchClient(
            settings.azure_search_endpoint,
            settings.azure_search_index,
            AzureKeyCredential(settings.azure_search_api_key.get_secret_value()),
        )
        return cls(client, AzureOpenAIEmbedder.from_settings(settings))

    def search(self, query: str, k: int = 5) -> list[SearchHit]:
        from azure.search.documents.models import VectorizedQuery

        vector = self.embedder.embed([query])[0]
        options: dict[str, Any] = {
            "search_text": query,
            "vector_queries": [
                VectorizedQuery(vector=vector, k_nearest_neighbors=max(k, 10), fields=VECTOR_FIELD)
            ],
            "top": k,
            "select": ["id", "title", "text", "source_type", "source_name", "tables"],
        }
        if self.semantic:
            options |= {"query_type": "semantic", "semantic_configuration_name": SEMANTIC_CONFIG}
        hits = []
        for doc in self.search_client.search(**options):
            score = doc.get("@search.reranker_score") or doc.get("@search.score") or 0.0
            chunk = Chunk(
                id=doc["id"],
                title=doc["title"],
                text=doc["text"],
                source_type=doc["source_type"],
                source_name=doc["source_name"],
                tables=tuple(doc.get("tables") or ()),
            )
            hits.append(SearchHit(chunk, float(score)))
        return hits
