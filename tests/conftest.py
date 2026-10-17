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
