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

    # Retrieval: local TF-IDF or Azure AI Search (hybrid + semantic ranker)
    retriever: Literal["local", "azure"] = "local"
    azure_search_endpoint: str | None = None
    azure_search_api_key: SecretStr | None = None
    azure_search_index: str = "lakehouse-catalog"

    # Azure OpenAI (embeddings now, chat model for the agent later)
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_embedding_deployment: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536


@lru_cache
def get_settings() -> Settings:
    return Settings()
