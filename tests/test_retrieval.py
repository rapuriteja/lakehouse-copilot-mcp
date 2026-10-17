from __future__ import annotations

from types import SimpleNamespace

import pytest

from lakehouse_copilot.catalog import load_catalog
from lakehouse_copilot.catalog.docs import build_chunks
from lakehouse_copilot.rag.local import TfidfRetriever

CHUNKS = build_chunks(load_catalog())


@pytest.fixture(scope="module")
def retriever() -> TfidfRetriever:
    return TfidfRetriever(CHUNKS)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("what is AOV", "metric:average_order_value"),
        ("when is the diwali festive season", "rule:festive_season_definition"),
        ("monthly sales by category", "table:gold.agg_monthly_category_sales"),
        ("what does COD mean", "glossary:cod"),
    ],
)
def test_local_retriever_finds_expected_chunk(retriever, query: str, expected: str) -> None:
    ids = [h.chunk.id for h in retriever.search(query, k=3)]
    assert expected in ids


def test_local_retriever_scores_are_sorted(retriever) -> None:
    scores = [h.score for h in retriever.search("net revenue by city", k=5)]
    assert scores == sorted(scores, reverse=True) and len(scores) == 5


def test_unrelated_query_returns_nothing(retriever) -> None:
    assert retriever.search("zzzz qqqq", k=5) == []


# ---------------------------------------------------------------- Azure (with fakes)

azure = pytest.importorskip("azure.search.documents")


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeSearchClient:
    def __init__(self) -> None:
        self.kwargs: dict = {}
        self.uploaded: list[dict] = []

    def search(self, **kwargs):
        self.kwargs = kwargs
        chunk = CHUNKS[0].to_dict()
        return [chunk | {"@search.score": 0.03, "@search.reranker_score": 2.5}]

    def upload_documents(self, documents):
        self.uploaded = documents
        return [SimpleNamespace(succeeded=True) for _ in documents]


class FakeIndexClient:
    def __init__(self) -> None:
        self.index = None

    def create_or_update_index(self, index):
        self.index = index


def test_azure_retriever_runs_hybrid_semantic_query() -> None:
    from lakehouse_copilot.rag.azure import SEMANTIC_CONFIG, AzureSearchRetriever

    client = FakeSearchClient()
    hits = AzureSearchRetriever(client, FakeEmbedder()).search("revenue by city", k=4)

    assert client.kwargs["search_text"] == "revenue by city"  # keyword part
    assert client.kwargs["vector_queries"][0].vector == [0.1, 0.2, 0.3]  # vector part
    assert client.kwargs["query_type"] == "semantic"
    assert client.kwargs["semantic_configuration_name"] == SEMANTIC_CONFIG
    assert client.kwargs["top"] == 4
    assert hits[0].chunk.id == CHUNKS[0].id and hits[0].score == 2.5


def test_azure_index_definition_has_vector_and_semantic_config() -> None:
    from lakehouse_copilot.rag.azure import VECTOR_FIELD, build_index_definition

    index = build_index_definition("lakehouse-catalog", 1536)
    vector = next(f for f in index.fields if f.name == VECTOR_FIELD)
    assert vector.vector_search_dimensions == 1536
    assert index.semantic_search.configurations[0].prioritized_fields.content_fields


def test_azure_indexer_uploads_chunks_with_embeddings() -> None:
    from lakehouse_copilot.rag.azure import VECTOR_FIELD, AzureSearchIndexer

    index_client, search_client, embedder = FakeIndexClient(), FakeSearchClient(), FakeEmbedder()
    n = AzureSearchIndexer(index_client, search_client, embedder).index(CHUNKS, "idx", 3)
    assert n == len(CHUNKS)
    assert index_client.index.name == "idx"
    assert all(len(d[VECTOR_FIELD]) == 3 for d in search_client.uploaded)
