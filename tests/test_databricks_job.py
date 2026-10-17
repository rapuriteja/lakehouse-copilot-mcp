from __future__ import annotations

from lakehouse_copilot.catalog import load_catalog
from lakehouse_copilot.databricks_job import catalog_comment_statements


def test_comment_statements_cover_every_table_and_column() -> None:
    catalog = load_catalog()
    statements = catalog_comment_statements(catalog, "lakehouse_copilot")
    n_columns = sum(len(t.columns) for t in catalog.tables)
    n_pii = sum(c.pii for t in catalog.tables for c in t.columns)
    assert len(statements) == len(catalog.tables) + n_columns + n_pii
    assert statements[0].startswith("COMMENT ON TABLE lakehouse_copilot.gold.")


def test_comments_escape_quotes() -> None:
    statements = catalog_comment_statements(load_catalog(), "c")
    brand = next(s for s in statements if "COLUMN brand COMMENT" in s)
    assert "Haldiram\\'s" in brand  # the apostrophe cannot break the SQL string
