"""The catalog must describe the real Gold tables: these tests stop docs drifting from data."""

from __future__ import annotations

from pathlib import Path

import pytest

from lakehouse_copilot.catalog import load_catalog
from lakehouse_copilot.catalog.docs import build_chunks, render_markdown
from lakehouse_copilot.pipeline.run import GOLD_TABLES

catalog = load_catalog()


def test_every_gold_table_is_documented() -> None:
    assert sorted(t.short_name for t in catalog.tables) == sorted(GOLD_TABLES)


def test_table_lookup_accepts_short_and_full_names() -> None:
    assert catalog.table("fact_sales") is catalog.table("GOLD.FACT_SALES")
    assert catalog.table("nope") is None


@pytest.mark.parametrize("table", [t.name for t in catalog.tables])
def test_documented_columns_match_real_schema(spark, lakehouse: Path, table: str) -> None:
    real = spark.read.parquet(str(lakehouse / "gold" / table.split(".")[1])).columns
    documented = [c.name for c in catalog.table(table).columns]
    assert sorted(documented) == sorted(real)


def test_every_metric_sql_runs(spark, lakehouse: Path) -> None:
    for name in ["fact_sales", "dim_customer"]:
        spark.read.parquet(str(lakehouse / "gold" / name)).createOrReplaceTempView(name)
    for m in catalog.metrics:
        value = (
            spark.sql(
                f"SELECT {m.sql} AS v FROM fact_sales f "
                "JOIN dim_customer c ON f.customer_sk = c.customer_sk"
            )
            .first()
            .v
        )
        assert value is not None and float(value) > 0, m.name


def test_chunks_are_unique_and_cover_everything() -> None:
    chunks = build_chunks(catalog)
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids))
    assert {f"table:{t.name}" for t in catalog.tables} <= set(ids)
    assert {f"metric:{m.name}" for m in catalog.metrics} <= set(ids)
    assert all(c.text.strip() for c in chunks)


def test_markdown_render_lists_all_tables() -> None:
    md = render_markdown(catalog)
    assert all(f"`{t.name}`" in md for t in catalog.tables)


def test_catalog_yaml_has_no_split_values() -> None:
    """An unquoted comma in a YAML flow mapping silently cuts a description short."""
    import yaml

    from lakehouse_copilot.catalog.model import CATALOG_FILE

    def nulls(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if value is None:
                    yield f"{path}/{key}"
                yield from nulls(value, f"{path}/{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                yield from nulls(item, f"{path}[{i}]")

    assert list(nulls(yaml.safe_load(CATALOG_FILE.read_text(encoding="utf-8")))) == []
