"""Central settings, read from environment variables or a `.env` file.

Local mode needs no settings at all. Cloud features switch on through
variables such as OPENROUTER_API_KEY, QUERY_ENGINE=databricks or RETRIEVER=azure.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_LLM_MODEL = "openai/gpt-4o-mini"

# Settings field -> secret name in Key Vault (created by Terraform)
KEY_VAULT_SECRETS = {
    "openrouter_api_key": "openrouter-api-key",
    "azure_search_api_key": "azure-search-api-key",
    "azure_openai_api_key": "azure-openai-api-key",
}


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

    # Retrieval: local TF-IDF, local hybrid (TF-IDF + embeddings), or Azure AI Search
    retriever: Literal["local", "hybrid", "azure"] = "local"
    azure_search_endpoint: str | None = None
    azure_search_api_key: SecretStr | None = None
    azure_search_index: str = "lakehouse-catalog"

    # Chat model for the agent. OpenRouter is the default; without OPENROUTER_API_KEY the
    # copilot runs offline (rule-based planner), so nothing here is needed for local mode.
    # LLM_BASE_URL can point at any other OpenAI-compatible server (for example Ollama).
    llm_provider: Literal["openrouter", "azure", "offline"] = "openrouter"
    openrouter_api_key: SecretStr | None = None
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = DEFAULT_LLM_MODEL
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 60.0

    # Embeddings for vector search (RETRIEVER=hybrid or azure)
    embedding_provider: Literal["openrouter", "azure", "local"] = "openrouter"
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_dimensions: int = 1536
    local_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"  # 384 dimensions

    # Optional: Azure OpenAI instead of OpenRouter (LLM_PROVIDER=azure, EMBEDDING_PROVIDER=azure)
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_chat_deployment: str = "gpt-4o-mini"
    azure_openai_embedding_deployment: str = "text-embedding-3-small"

    # Optional: read missing keys (OpenRouter, AI Search) from Azure Key Vault
    key_vault_uri: str | None = None

    def secret(self, field: str) -> str | None:
        """A secret's value from the environment, or else from Key Vault (if configured)."""
        value = getattr(self, field)
        if value is not None and value.get_secret_value():
            return value.get_secret_value()
        if self.key_vault_uri and field in KEY_VAULT_SECRETS:
            from lakehouse_copilot.keyvault import read_secret

            return read_secret(self.key_vault_uri, KEY_VAULT_SECRETS[field])
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
