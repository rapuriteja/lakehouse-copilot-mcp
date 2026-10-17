from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from lakehouse_copilot.catalog import load_catalog
from lakehouse_copilot.sql.engine import DatabricksEngine, DuckDBEngine


def test_serving_db_has_all_tables_without_pii(serving_db: Path) -> None:
    sql = "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='gold'"
    cols = DuckDBEngine(serving_db).execute(sql, 1000).rows
    tables = {t for t, _ in cols}
    assert tables == {t.short_name for t in load_catalog().tables}
    assert not {c for _, c in cols} & {"email", "phone", "full_name"}


def test_engine_returns_json_friendly_rows(serving_db: Path) -> None:
    result = DuckDBEngine(serving_db).execute(
        "SELECT date, net_revenue FROM gold.agg_daily_sales ORDER BY date LIMIT 3", 2
    )
    data = result.to_dict()
    assert data["columns"] == ["date", "net_revenue"]
    assert data["row_count"] == 2 and data["truncated"] is True
    assert isinstance(data["rows"][0][0], str) and isinstance(data["rows"][0][1], float)


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE gold.x AS SELECT 1",
        "SELECT * FROM read_csv('/etc/passwd')",
        "COPY gold.dim_date TO 'leak.csv'",
        "SET enable_external_access = true",
    ],
)
def test_engine_blocks_writes_and_files_even_without_guard(serving_db: Path, sql: str) -> None:
    """Defence in depth: the connection itself is read-only with file access disabled."""
    with pytest.raises(duckdb.Error):
        DuckDBEngine(serving_db).execute(sql, 10)


def test_missing_db_gives_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="lhc pipeline"):
        DuckDBEngine(tmp_path / "nope.duckdb")


class FakeCursor:
    description = [("city",), ("revenue",)]

    def __init__(self) -> None:
        self.sql = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.sql = sql

    def fetchmany(self, n):
        return [("Chennai", 10), ("Pune", 5), ("Delhi", 1)][:n]


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_obj = FakeCursor()

    def cursor(self):
        return self.cursor_obj


def test_databricks_engine_with_fake_connection() -> None:
    conn = FakeConnection()
    result = DatabricksEngine(conn).execute("SELECT 1", 2)
    assert conn.cursor_obj.sql == "SELECT 1"
    assert result.rows == [["Chennai", 10], ["Pune", 5]] and result.truncated
