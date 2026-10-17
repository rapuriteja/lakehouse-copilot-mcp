"""Shared test fixtures. Tests use a small dataset so the suite runs fast on a laptop."""

from __future__ import annotations

from pathlib import Path

import pytest

from lakehouse_copilot.data_gen import GeneratorConfig, generate

SMALL = GeneratorConfig(seed=7, n_customers=300, n_products=60, base_daily_orders=4.0)


@pytest.fixture(autouse=True)
def _no_cloud_keys(monkeypatch: pytest.MonkeyPatch):
    """Tests never call a real LLM or Key Vault, even if your .env has keys in it."""
    from lakehouse_copilot.config import get_settings

    monkeypatch.setenv("LLM_PROVIDER", "offline")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("KEY_VAULT_URI", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def raw_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("raw")
    generate(out, SMALL)
    return out


@pytest.fixture(scope="session")
def spark():
    pyspark = pytest.importorskip("pyspark")
    del pyspark
    from lakehouse_copilot.pipeline.spark import get_spark

    session = get_spark("lakehouse-copilot-tests")
    yield session
    session.stop()


@pytest.fixture(scope="session")
def lakehouse(spark, raw_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Run the full pipeline once on the small dataset and share the output."""
    from lakehouse_copilot.pipeline.run import run_pipeline

    out = tmp_path_factory.mktemp("lakehouse")
    run_pipeline(raw_dir, out)
    return out


@pytest.fixture(scope="session")
def serving_db(lakehouse: Path) -> Path:
    """DuckDB serving layer built from the test lakehouse (what the copilot queries)."""
    from lakehouse_copilot.catalog import load_catalog
    from lakehouse_copilot.sql.engine import build_serving_db

    return build_serving_db(lakehouse, lakehouse / "serving.duckdb", load_catalog())


@pytest.fixture(scope="session")
def tools(serving_db: Path):
    """The four copilot tools wired to local DuckDB + TF-IDF."""
    from lakehouse_copilot.catalog import load_catalog
    from lakehouse_copilot.catalog.docs import build_chunks
    from lakehouse_copilot.rag.local import TfidfRetriever
    from lakehouse_copilot.sql.engine import DuckDBEngine
    from lakehouse_copilot.tools import LakehouseTools, make_guard

    catalog = load_catalog()
    engine = DuckDBEngine(serving_db)
    return LakehouseTools(
        catalog, TfidfRetriever(build_chunks(catalog)), engine, make_guard(catalog, "duckdb", 50)
    )
