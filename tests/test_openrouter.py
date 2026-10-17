"""OpenRouter provider, usage/cost tracking, embeddings and the local hybrid retriever.

No test calls the network: OpenAI SDK clients are replaced with small fakes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lakehouse_copilot.agent.copilot import Copilot
from lakehouse_copilot.agent.llm import ChatResponse, OpenAIChatModel, ToolCall, Usage
from lakehouse_copilot.catalog import load_catalog
from lakehouse_copilot.catalog.docs import build_chunks
from lakehouse_copilot.config import Settings
from lakehouse_copilot.evals.bench import benchmark, choose, render_markdown
from lakehouse_copilot.rag.embeddings import OpenAICompatibleEmbedder
from lakehouse_copilot.rag.hybrid import HybridRetriever

FAKE_KEY = "test-" + "not-a-real-key"

# ---------------------------------------------------------------- provider selection


def test_openrouter_is_the_default_and_needs_no_key_to_run(monkeypatch) -> None:
    from lakehouse_copilot.agent.llm import get_chat_model

    monkeypatch.delenv("LLM_PROVIDER")
    settings = Settings(_env_file=None, openrouter_api_key="")
    assert settings.llm_provider == "openrouter"
    assert settings.llm_base_url == "https://openrouter.ai/api/v1"
    assert get_chat_model(settings) is None  # no key -> offline planner


def test_openrouter_client_uses_base_url_model_and_cost_reporting() -> None:
    from lakehouse_copilot.agent.llm import get_chat_model

    settings = Settings(
        _env_file=None,
        llm_provider="openrouter",
        openrouter_api_key=FAKE_KEY,
        llm_model="vendor/model-a",
    )
    llm = get_chat_model(settings)
    assert isinstance(llm, OpenAIChatModel)
    assert str(llm.client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
    assert llm.model == "vendor/model-a" and llm.extra_body == {"usage": {"include": True}}
    assert get_chat_model(settings, "vendor/model-b").model == "vendor/model-b"


def test_azure_openai_is_still_an_option() -> None:
    from lakehouse_copilot.agent.llm import get_chat_model

    settings = Settings(
        _env_file=None,
        llm_provider="azure",
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_api_key=FAKE_KEY,
    )
    assert get_chat_model(settings).model == "gpt-4o-mini"


def test_missing_key_is_read_from_key_vault(monkeypatch) -> None:
    import lakehouse_copilot.keyvault as kv

    asked = []
    monkeypatch.setattr(kv, "read_secret", lambda uri, name: asked.append((uri, name)) or "kv")
    settings = Settings(_env_file=None, key_vault_uri="https://kv.vault.azure.net/")
    assert settings.secret("openrouter_api_key") == "kv"
    assert asked == [("https://kv.vault.azure.net/", "openrouter-api-key")]
    # A key set in the environment wins and Key Vault is not asked.
    assert Settings(_env_file=None, openrouter_api_key="env").secret("openrouter_api_key") == "env"


# ---------------------------------------------------------------- usage and errors


class FakeCompletions:
    def __init__(self, response) -> None:
        self.response = response
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _client(response) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(response)))


def test_chat_model_reads_tokens_and_openrouter_cost() -> None:
    message = SimpleNamespace(content="hi", tool_calls=None)
    usage = SimpleNamespace(prompt_tokens=120, completion_tokens=30, cost=0.000042)
    client = _client(SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage))

    model = OpenAIChatModel(client, "m", extra_body={"usage": {"include": True}})
    response = model.chat([{"role": "user", "content": "hi"}])

    assert response.usage == Usage(120, 30, 0.000042, 1)
    assert client.chat.completions.kwargs["extra_body"] == {"usage": {"include": True}}


def test_chat_model_raises_on_error_body() -> None:
    client = _client(SimpleNamespace(choices=None, error={"message": "rate limited"}))
    with pytest.raises(RuntimeError, match="rate limited"):
        OpenAIChatModel(client, "m").chat([{"role": "user", "content": "hi"}])


def test_usage_adds_up_and_keeps_unknown_cost_unknown() -> None:
    total = Usage(10, 2, 0.001, 1) + Usage(5, 1, None, 1)
    assert total == Usage(15, 3, 0.001, 2)
    assert (Usage(1, 1, None, 1) + Usage(1, 1, None, 1)).cost_usd is None


class PricedLLM:
    """Scripted model that reports a fixed usage for every call."""

    name = "priced"

    def __init__(self, responses: list[ChatResponse]) -> None:
        self.responses = list(responses)

    def chat(self, messages, tools=None):
        usage = Usage(100, 10, 0.0001, 1)
        if tools is None:
            return ChatResponse("net revenue 2025", usage=usage)
        response = self.responses.pop(0)
        response.usage = usage
        return response


def test_copilot_reports_usage_of_every_call(tools) -> None:
    llm = PricedLLM(
        [
            ChatResponse(None, [ToolCall("1", "run_sql", {"sql": "SELECT 1 AS x"})]),
            ChatResponse("One."),
        ]
    )
    answer = Copilot(tools, llm).answer("What is one?")
    # rewrite + 2 agent turns
    assert answer.usage.calls == 3 and answer.usage.prompt_tokens == 300
    assert answer.usage.cost_usd == pytest.approx(0.0003)


def test_bad_tool_arguments_go_back_to_the_model(tools) -> None:
    llm = PricedLLM(
        [
            ChatResponse(None, [ToolCall("1", "search_docs", {"query": "x", "k": "many"})]),
            ChatResponse("ok"),
        ]
    )
    answer = Copilot(tools, llm).answer("anything")
    assert answer.answer == "ok" and answer.steps[1].error


# ---------------------------------------------------------------- benchmark


def test_benchmark_picks_best_test_accuracy_then_cheapest(tools) -> None:
    def summary(test, cost, latency=1.0):
        return {
            "summary": {
                "all": {"questions": 40, "correct": 30, "execution_accuracy": 0.75},
                "test": {"questions": 20, "correct": int(test * 20), "execution_accuracy": test},
                "median_latency_s": latency,
                "errors": 0,
                "llm_calls": 100,
                "total_cost_usd": cost,
            }
        }

    results = {"a": summary(0.8, 0.05), "b": summary(0.8, 0.01), "c": summary(0.7, 0.001)}
    assert choose(results) == "b"
    text = render_markdown(
        {"questions": 40, "retriever": "local-tfidf", "models": results, "choice": "b"}
    )
    assert "`b` **(chosen)**" in text and "$0.0100" in text


def test_benchmark_runs_the_offline_baseline(tools) -> None:
    report = benchmark({"offline planner": Copilot(tools)}, split="dev")
    s = report["models"]["offline planner"]["summary"]
    assert s["dev"]["questions"] == 20 and "llm_calls" not in s
    assert report["choice"] is None  # no LLM in the run


# ---------------------------------------------------------------- embeddings + hybrid


class FakeEmbeddings:
    """Bag-of-words vectors over a tiny vocabulary, in the OpenAI embeddings response shape."""

    VOCAB = ["revenue", "festive", "diwali", "cod", "cash", "delivery", "order", "city"]

    def __init__(self) -> None:
        self.calls = 0

    def create(self, model, input):
        self.calls += 1
        data = []
        for i, text in enumerate(input):
            words = text.lower()
            vector = [float(words.count(w)) + 0.01 for w in self.VOCAB]
            data.append(SimpleNamespace(index=i, embedding=vector))
        return SimpleNamespace(data=list(reversed(data)))  # order must come from .index


def test_embedder_batches_and_keeps_order(monkeypatch) -> None:
    import lakehouse_copilot.rag.embeddings as emb

    monkeypatch.setattr(emb, "BATCH_SIZE", 2)
    fake = FakeEmbeddings()
    embedder = OpenAICompatibleEmbedder(SimpleNamespace(embeddings=fake), "m")
    vectors = embedder.embed(["cod", "diwali", "city"])
    assert fake.calls == 2 and vectors[0][3] > 1 and vectors[1][2] > 1


def test_embedder_checks_dimensions() -> None:
    embedder = OpenAICompatibleEmbedder(SimpleNamespace(embeddings=FakeEmbeddings()), "m", 1536)
    with pytest.raises(RuntimeError, match="8 dimensions"):
        embedder.embed(["revenue"])


def test_hybrid_retriever_merges_keyword_and_vector_and_caches(tmp_path) -> None:
    chunks = build_chunks(load_catalog())
    fake = FakeEmbeddings()
    embedder = OpenAICompatibleEmbedder(SimpleNamespace(embeddings=fake), "fake-model")
    cache = tmp_path / "embeddings.jsonl"

    retriever = HybridRetriever(chunks, embedder, cache)
    ids = [h.chunk.id for h in retriever.search("what does cash on delivery mean", k=5)]
    assert "glossary:cod" in ids

    calls = fake.calls
    HybridRetriever(chunks, embedder, cache)  # all chunk vectors come from the cache
    assert fake.calls == calls and len(cache.read_text().splitlines()) == len(chunks)
