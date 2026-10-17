"""Query engines that run validated SQL and return JSON-friendly results.

DuckDB (local): Gold Parquet is loaded into a small DuckDB file, the "serving layer".
The copilot opens it read-only with external file access turned off, and PII columns
are not copied into it at all.

Databricks (cloud): the same SQL runs on a Databricks SQL warehouse against Unity
Catalog. Read-only access comes from Unity Catalog grants (SELECT only) for the
service principal the copilot uses.
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from lakehouse_copilot.catalog.model import Catalog


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict:
        return {
            "columns": self.columns,
            "rows": [[_json_value(v) for v in row] for row in self.rows],
            "row_count": self.row_count,
            "truncated": self.truncated,
        }


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


class QueryEngine(Protocol):
    dialect: str

    def execute(self, sql: str, max_rows: int) -> QueryResult: ...


# ---------------------------------------------------------------- DuckDB


def build_serving_db(lakehouse_dir: str | Path, db_path: str | Path, catalog: Catalog) -> Path:
    """Copy Gold Parquet tables into a DuckDB file, leaving out PII columns."""
    import duckdb

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS gold")
        for table in catalog.tables:
            source = Path(lakehouse_dir) / "gold" / table.short_name / "*.parquet"
            cols = ", ".join(c.name for c in table.public_columns)
            con.execute(f"CREATE TABLE {table.name} AS SELECT {cols} FROM read_parquet('{source}')")
    finally:
        con.close()
    tmp.replace(db_path)  # swap in atomically so readers never see a half-built file
    return db_path


class DuckDBEngine:
    dialect = "duckdb"

    def __init__(self, db_path: str | Path, timeout_seconds: float = 20.0) -> None:
        import duckdb

        if not Path(db_path).exists():
            raise FileNotFoundError(f"{db_path} not found. Run `lhc pipeline` first.")
        self.con = duckdb.connect(
            str(db_path),
            read_only=True,
            config={"enable_external_access": False, "lock_configuration": True},
        )
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        with self._lock:  # one DuckDB connection is not safe for concurrent queries
            timer = threading.Timer(self.timeout_seconds, self.con.interrupt)
            timer.start()
            try:
                cursor = self.con.execute(sql)
                columns = [d[0] for d in cursor.description]
                rows = cursor.fetchmany(max_rows + 1)
            finally:
                timer.cancel()
        return QueryResult(columns, [list(r) for r in rows[:max_rows]], len(rows) > max_rows)


# ---------------------------------------------------------------- Databricks


class DatabricksEngine:
    dialect = "databricks"

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    @classmethod
    def from_settings(cls, settings) -> DatabricksEngine:
        from databricks import sql as dbsql

        host = (settings.databricks_host or "").removeprefix("https://").rstrip("/")
        connection = dbsql.connect(
            server_hostname=host,  # the connector wants the bare host name
            http_path=settings.databricks_http_path,
            access_token=settings.databricks_token.get_secret_value(),
            catalog=settings.databricks_catalog,
            schema="gold",
        )
        return cls(connection)

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        with self.connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchmany(max_rows + 1)
        return QueryResult(columns, [list(r) for r in rows[:max_rows]], len(rows) > max_rows)


def get_engine(settings) -> QueryEngine:
    if settings.query_engine == "databricks":
        return DatabricksEngine.from_settings(settings)
    return DuckDBEngine(settings.duckdb_path, settings.sql_timeout_seconds)
