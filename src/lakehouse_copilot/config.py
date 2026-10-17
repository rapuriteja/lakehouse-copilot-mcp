"""Central settings, read from environment variables or a `.env` file.

Local mode needs no settings at all. Cloud features switch on through
variables such as QUERY_ENGINE=databricks or RETRIEVER=azure.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Local paths
    data_dir: Path = Path("data/raw")
    lakehouse_dir: Path = Path("lakehouse")

    # Data generator
    seed: int = 42


@lru_cache
def get_settings() -> Settings:
    return Settings()
