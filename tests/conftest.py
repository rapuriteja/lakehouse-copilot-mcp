"""Shared test fixtures. Tests use a small dataset so the suite runs fast on a laptop."""

from __future__ import annotations

from pathlib import Path

import pytest

from lakehouse_copilot.data_gen import GeneratorConfig, generate

SMALL = GeneratorConfig(seed=7, n_customers=300, n_products=60, base_daily_orders=4.0)


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
