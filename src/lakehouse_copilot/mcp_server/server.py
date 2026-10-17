"""MCP server (Model Context Protocol) for the lakehouse.

MCP is an open standard that lets AI clients (Claude Desktop, VS Code Copilot, Cursor,
custom agents) discover and call tools on a server. Any MCP client can now explore the
Gold layer, read the catalog docs and run safe SQL, without custom integration code.

Run it:
    lhc mcp                 # stdio transport (the client starts this process)
    lhc mcp --http          # streamable HTTP on http://127.0.0.1:8000/mcp
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
from mcp.types import ToolAnnotations

from lakehouse_copilot.tools import LakehouseTools, ToolError

INSTRUCTIONS = """\
Tools over a governed e-commerce lakehouse (Indian market, amounts in INR, 2024-2025).
Workflow: search_docs for metric definitions and business rules, describe_table for exact
columns and joins, then run_sql with one read-only SELECT on gold.* tables.
Revenue only counts orders where is_revenue = true (Delivered or Shipped)."""

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)


@contextmanager
def _model_visible_errors() -> Iterator[None]:
    """Expected failures (bad SQL, unknown table) go back to the model so it can fix them.
    Anything else stays a server-side crash and the model sees only a generic message."""
    try:
        yield
    except ToolError as e:
        raise MCPToolError(str(e)) from e


def create_server(tools: LakehouseTools | None = None) -> MCPServer:
    tools = tools or LakehouseTools.from_settings()
    mcp = MCPServer("lakehouse-copilot", instructions=INSTRUCTIONS, version="0.1.0")

    @mcp.tool(annotations=READ_ONLY)
    def list_tables() -> list[dict[str, Any]]:
        """List the Gold tables you can query, with a short description and grain of each."""
        return tools.list_tables()

    @mcp.tool(annotations=READ_ONLY)
    def describe_table(table_name: str) -> dict[str, Any]:
        """Show columns (name, type, meaning), primary key and join keys of one Gold table.

        Args:
            table_name: Table name such as 'gold.fact_sales' or 'fact_sales'.
        """
        with _model_visible_errors():
            return tools.describe_table(table_name)

    @mcp.tool(annotations=READ_ONLY)
    def search_docs(query: str, k: int = 5) -> list[dict[str, Any]]:
        """Search the data catalog: table docs, business metric definitions (with their SQL),
        glossary terms and business rules. Use it before writing SQL for a business metric.

        Args:
            query: What you are looking for, such as 'how is net revenue defined'.
            k: Number of results, 1 to 10.
        """
        return tools.search_docs(query, k)

    @mcp.tool(annotations=READ_ONLY)
    def run_sql(sql: str) -> dict[str, Any]:
        """Run ONE read-only SELECT on gold.* tables and return columns and rows.

        Rules: SELECT only; gold tables only; no PII columns (full_name, email, phone);
        results are capped (a LIMIT is added). Use DuckDB SQL syntax locally.

        Args:
            sql: A single SELECT statement, for example
                 "SELECT payment_method, COUNT(DISTINCT order_id) FROM gold.fact_sales GROUP BY 1".
        """
        with _model_visible_errors():
            return tools.run_sql(sql)

    return mcp


def main(http: bool = False, host: str = "127.0.0.1", port: int = 8000) -> None:
    server = create_server()
    if http:
        server.run("streamable-http", host=host, port=port)
    else:
        server.run()
