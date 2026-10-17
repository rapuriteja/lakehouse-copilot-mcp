"""The four copilot tools, in one place.

Both the MCP server (for any AI client) and the Copilot agent (Azure OpenAI tool calling)
call these same functions, so behaviour and safety rules are identical everywhere.

    list_tables     what Gold tables exist
    describe_table  columns, keys and joins of one table (PII columns hidden)
    search_docs     RAG search over catalog docs, metric definitions and business rules
    run_sql         run one read-only SELECT through the SQL guard
"""

from __future__ import annotations

from dataclasses import dataclass

from lakehouse_copilot.catalog import Catalog, load_catalog
from lakehouse_copilot.config import Settings, get_settings
from lakehouse_copilot.rag import Retriever, get_retriever
from lakehouse_copilot.sql.engine import QueryEngine, get_engine
from lakehouse_copilot.sql.guard import SQLGuard


class ToolError(ValueError):
    """A tool call failed in a way the caller (or model) can fix, such as unsafe SQL."""


@dataclass
class LakehouseTools:
    catalog: Catalog
    retriever: Retriever
    engine: QueryEngine
    guard: SQLGuard

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> LakehouseTools:
        settings = settings or get_settings()
        catalog = load_catalog()
        engine = get_engine(settings)
        guard = make_guard(catalog, engine.dialect, settings.sql_row_limit)
        return cls(catalog, get_retriever(settings), engine, guard)

    def list_tables(self) -> list[dict]:
        return [
            {"table": t.name, "description": t.description, "grain": t.grain}
            for t in self.catalog.tables
        ]

    def describe_table(self, table_name: str) -> dict:
        table = self.catalog.table(table_name)
        if table is None:
            raise ToolError(
                f"Unknown table '{table_name}'. Known tables: {', '.join(self.catalog.table_names)}"
            )
        return {
            "table": table.name,
            "description": table.description,
            "grain": table.grain,
            "primary_key": table.primary_key,
            "joins": list(table.joins),
            "columns": [
                {"name": c.name, "type": c.type, "description": c.description}
                | ({"examples": list(c.examples)} if c.examples else {})
                for c in table.public_columns
            ],
        }

    def search_docs(self, query: str, k: int = 5) -> list[dict]:
        k = max(1, min(k, 10))
        return [
            {
                "id": h.chunk.id,
                "title": h.chunk.title,
                "text": h.chunk.text,
                "tables": list(h.chunk.tables),
                "score": round(h.score, 4),
            }
            for h in self.retriever.search(query, k=k)
        ]

    def run_sql(self, sql: str) -> dict:
        from lakehouse_copilot.sql.guard import UnsafeSQLError

        try:
            query = self.guard.validate(sql)
        except UnsafeSQLError as e:
            raise ToolError(f"Query rejected: {e}") from e
        try:
            result = self.engine.execute(query.sql, self.guard.max_rows)
        except Exception as e:  # syntax/binder errors from the engine go back to the model
            raise ToolError(f"Query failed: {str(e).splitlines()[0]}") from e
        return {"sql": query.sql, "tables": list(query.tables)} | result.to_dict()


def make_guard(catalog: Catalog, dialect: str, max_rows: int) -> SQLGuard:
    pii_tables = {t.name for t in catalog.tables if any(c.pii for c in t.columns)}
    return SQLGuard(
        catalog.table_names,
        dialect=dialect,
        max_rows=max_rows,
        pii_columns=catalog.pii_columns,
        pii_tables=pii_tables,
    )
