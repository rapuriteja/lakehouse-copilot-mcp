from __future__ import annotations

from types import SimpleNamespace

import pytest

from lakehouse_copilot.agent.copilot import Copilot
from lakehouse_copilot.agent.format import format_number, indian_grouping
from lakehouse_copilot.agent.llm import ChatResponse, OpenAIChatModel, ToolCall
from lakehouse_copilot.agent.rewrite import SynonymRewriter
from lakehouse_copilot.agent.semantic import SemanticPlanner
from lakehouse_copilot.catalog import load_catalog

catalog = load_catalog()

# ---------------------------------------------------------------- formatting


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1273361650.5, "1,27,33,61,650.50"),
        (99999, "99,999.00"),
        (512, "512.00"),
        (-1500, "-1,500.00"),
    ],
)
def test_indian_grouping(value: float, expected: str) -> None:
    assert indian_grouping(value) == expected


def test_format_number_by_metric() -> None:
    assert format_number(127333616.5, "net_revenue") == "₹12.73 crore (₹12,73,33,616.50)"
    assert format_number(250000, "net_revenue") == "₹2.50 lakh (₹2,50,000.00)"
    assert format_number(14.321, "cancellation_rate") == "14.32%"
    assert format_number(14168, "order_count") == "14,168"


# ---------------------------------------------------------------- rewriting


def test_rewriter_adds_official_names() -> None:
    rewritten = SynonymRewriter(catalog).rewrite("AOV for cash on delivery?")
    assert "average order value" in rewritten.text and "cod" in rewritten.text


def test_rewriter_merges_follow_up_and_replaces_year() -> None:
    history = [{"role": "user", "content": "Net revenue in 2024 for Chennai"}]
    text = SynonymRewriter(catalog).rewrite("and in 2025?", history).text
    assert "2025" in text and "2024" not in text and "Chennai" in text


# ---------------------------------------------------------------- semantic planner

VALUES = {
    "city": ["Chennai", "Bengaluru", "Delhi"],
    "state": ["Tamil Nadu", "Karnataka", "Delhi"],
    "category": ["Electronics", "Fashion", "Home & Kitchen"],
    "brand": ["Samsung"],
    "payment_method": ["UPI", "COD", "Credit Card", "Debit Card"],
    "channel": ["App", "Web"],
    "segment": ["Premium", "Regular"],
    "sub_category": ["Smartphones"],
}
planner = SemanticPlanner(catalog, VALUES)


def test_planner_metric_and_year_filter() -> None:
    plan = planner.plan("What was the net revenue in 2024?")
    assert [m.name for m in plan.metrics] == ["net_revenue"]
    assert plan.filters == {"d.year": [2024]} and not plan.group_by


def test_planner_top_n_by_dimension() -> None:
    plan = planner.plan("Top 5 cities by revenue during the festive season")
    assert [d.key for d in plan.group_by] == ["city"]
    assert plan.order == "desc" and plan.limit == 5
    assert plan.filters == {"d.is_festive_season": [True]}


def test_planner_value_filter_is_not_a_group_by() -> None:
    plan = planner.plan("Revenue for the Electronics category in Bangalore")
    assert plan.filters == {"p.category": ["Electronics"], "c.city": ["Bengaluru"]}
    assert plan.group_by == []


def test_planner_compare_values_groups_by_them() -> None:
    plan = planner.plan("AOV for UPI vs COD orders")
    assert [m.name for m in plan.metrics] == ["average_order_value"]
    assert [d.key for d in plan.group_by] == ["payment_method"]


def test_planner_sql_joins_only_needed_tables() -> None:
    sql = planner.to_sql(planner.plan("How many orders by channel in 2025?"))
    assert "gold.dim_date" in sql and "gold.dim_customer" not in sql
    assert "gold.dim_product" not in sql and "GROUP BY 1" in sql


def test_planner_active_customers_joins_customer_dim() -> None:
    sql = planner.to_sql(planner.plan("How many customers bought something?"))
    assert "COUNT(DISTINCT CASE WHEN f.is_revenue THEN c.customer_id END)" in sql


# ---------------------------------------------------------------- offline copilot (real data)


def test_offline_copilot_answers_with_sources(tools) -> None:
    answer = Copilot(tools).answer("What was the net revenue in 2024?")
    assert answer.answer.startswith("Net revenue (year 2024): ₹")
    assert answer.tables == ["gold.dim_date", "gold.fact_sales"]
    assert "metric:net_revenue" in answer.doc_ids
    assert answer.sources.startswith("Sources: tables")


def test_offline_answer_matches_direct_sql(tools) -> None:
    answer = Copilot(tools).answer("Which payment method has the most orders?")
    direct = tools.run_sql(
        "SELECT payment_method, COUNT(DISTINCT order_id) n FROM gold.fact_sales "
        "WHERE is_revenue GROUP BY 1 ORDER BY 2 DESC LIMIT 1"
    )["rows"][0][0]
    assert answer.answer.startswith(f"{direct} has the highest")


# ---------------------------------------------------------------- LLM tool loop (scripted fake)


class ScriptedLLM:
    """Plays back fixed responses and records what the agent sent."""

    name = "scripted"

    def __init__(self, responses: list[ChatResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None):
        self.calls.append(list(messages))
        if tools is None:  # the query-rewriting call
            return ChatResponse("net revenue in 2024")
        return self.responses.pop(0)


def _sql_call(call_id: str, sql: str) -> ChatResponse:
    return ChatResponse(None, [ToolCall(call_id, "run_sql", {"sql": sql})])


def test_llm_loop_recovers_from_rejected_sql(tools) -> None:
    llm = ScriptedLLM(
        [
            _sql_call("1", "SELECT * FROM silver.orders"),
            _sql_call("2", "SELECT SUM(net_amount) FROM gold.fact_sales WHERE is_revenue"),
            ChatResponse("Net revenue was ₹X."),
        ]
    )
    answer = Copilot(tools, llm).answer("How much did we sell?")

    assert answer.answer == "Net revenue was ₹X."
    assert [s.tool for s in answer.steps] == ["search_docs", "run_sql", "run_sql"]
    assert "not allowed" in answer.steps[1].error
    assert answer.tables == ["gold.fact_sales"]  # citations come from the real query
    # The rejection went back to the model as a tool message so it could fix the SQL.
    tool_messages = [m for m in llm.calls[-1] if m["role"] == "tool"]
    assert "not allowed" in tool_messages[0]["content"]
    # The LLM-rewritten query was used for retrieval.
    assert answer.rewritten.text == "net revenue in 2024"


def test_llm_loop_stops_at_step_limit(tools) -> None:
    looping = [ChatResponse(None, [ToolCall(str(i), "list_tables", {})]) for i in range(10)]
    answer = Copilot(tools, ScriptedLLM(looping), max_steps=3).answer("loop forever")
    assert "step limit" in answer.answer


class FakeCompletions:
    def __init__(self, message) -> None:
        self.message = message
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=self.message)])


def test_openai_chat_model_parses_tool_calls() -> None:
    function = SimpleNamespace(name="run_sql", arguments='{"sql": "SELECT 1"}')
    message = SimpleNamespace(
        content=None, tool_calls=[SimpleNamespace(id="c1", function=function)]
    )
    completions = FakeCompletions(message)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    response = OpenAIChatModel(client, "gpt-4o-mini").chat(
        [{"role": "user", "content": "hi"}], [{}]
    )

    assert response.tool_calls[0].arguments == {"sql": "SELECT 1"}
    assert completions.kwargs["tool_choice"] == "auto" and completions.kwargs["temperature"] == 0
    assert response.as_message()["tool_calls"][0]["function"]["name"] == "run_sql"
