"""Central settings, read from environment variables or a `.env` file.

Local mode needs no settings at all. Cloud features switch on through
variables such as QUERY_ENGINE=databricks or RETRIEVER=azure.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Local paths
    data_dir: Path = Path("data/raw")
    lakehouse_dir: Path = Path("lakehouse")

    # Data generator
    seed: int = 42

    # SQL engine: local DuckDB serving layer, or a Databricks SQL warehouse
    query_engine: Literal["duckdb", "databricks"] = "duckdb"
    duckdb_path: Path = Path("lakehouse/serving.duckdb")
    sql_row_limit: int = 200
    sql_timeout_seconds: float = 20.0
    databricks_host: str | None = None
    databricks_http_path: str | None = None
    databricks_token: SecretStr | None = None
    databricks_catalog: str = "lakehouse_copilot"

    # Retrieval: local TF-IDF or Azure AI Search (hybrid + semantic ranker)
    retriever: Literal["local", "azure"] = "local"
    azure_search_endpoint: str | None = None
    azure_search_api_key: SecretStr | None = None
    azure_search_index: str = "lakehouse-catalog"

    # Chat model for the agent: offline (rule-based planner), azure, or any
    # OpenAI-compatible endpoint (for example a local Ollama server)
    llm_provider: Literal["offline", "azure", "openai"] = "offline"
    azure_openai_chat_deployment: str = "gpt-4o-mini"
    openai_base_url: str | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"

    # Azure OpenAI (chat + embeddings)
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_embedding_deployment: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536


@lru_cache
def get_settings() -> Settings:
    return Settings()
