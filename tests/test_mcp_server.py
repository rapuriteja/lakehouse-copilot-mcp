"""Talk to the MCP server through a real MCP client (in-process transport)."""

from __future__ import annotations

import anyio
import pytest
from mcp import Client

from lakehouse_copilot.mcp_server.server import create_server


@pytest.fixture(scope="module")
def server(tools):
    return create_server(tools)


def _run(coro_fn):
    return anyio.run(coro_fn)


def test_server_lists_four_read_only_tools(server) -> None:
    async def go():
        async with Client(server) as client:
            return (await client.list_tools()).tools

    tools = _run(go)
    assert sorted(t.name for t in tools) == [
        "describe_table",
        "list_tables",
        "run_sql",
        "search_docs",
    ]
    assert all(t.annotations.read_only_hint for t in tools)
    run_sql = next(t for t in tools if t.name == "run_sql")
    assert run_sql.input_schema["required"] == ["sql"]


def test_list_and_describe(server) -> None:
    async def go():
        async with Client(server) as client:
            listed = await client.call_tool("list_tables", {})
            described = await client.call_tool("describe_table", {"table_name": "dim_customer"})
            return listed, described

    listed, described = _run(go)
    assert not listed.is_error and not described.is_error
    columns = {c["name"] for c in described.structured_content["columns"]}
    assert "city" in columns and "email" not in columns  # PII hidden


def test_search_docs_returns_metric_definition(server) -> None:
    async def go():
        async with Client(server) as client:
            return await client.call_tool("search_docs", {"query": "average order value", "k": 3})

    result = _run(go)
    ids = [d["id"] for d in result.structured_content["result"]]
    assert "metric:average_order_value" in ids


def test_run_sql_success_and_rejection(server) -> None:
    async def go():
        async with Client(server) as client:
            sql = "SELECT channel, COUNT(DISTINCT order_id) AS n FROM gold.fact_sales GROUP BY 1"
            ok = await client.call_tool("run_sql", {"sql": sql})
            bad = await client.call_tool("run_sql", {"sql": "DROP TABLE gold.fact_sales"})
            unknown = await client.call_tool("describe_table", {"table_name": "silver.orders"})
            return ok, bad, unknown

    ok, bad, unknown = _run(go)
    assert not ok.is_error and {r[0] for r in ok.structured_content["rows"]} == {"App", "Web"}
    assert bad.is_error and "Only SELECT" in bad.content[0].text
    assert unknown.is_error and "Unknown table" in unknown.content[0].text
