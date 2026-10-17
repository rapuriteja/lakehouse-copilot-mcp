"""The Copilot agent.

For every question:
  1. rewrite the question into a better search query (synonyms or LLM)
  2. retrieve catalog docs (RAG) with the rewritten query
  3. answer:
       LLM mode     the model plans and calls tools (search_docs, describe_table,
                    run_sql...) in a loop; SQL errors go back to it so it can fix them
       offline mode the semantic planner writes the SQL, no LLM needed
  4. ground the answer: cite the tables the SQL actually read and the docs retrieved.
     Citations are built by our code from real tool calls, never invented by the model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from lakehouse_copilot.agent.format import format_answer_table, format_number
from lakehouse_copilot.agent.llm import ChatModel, ChatResponse, Usage
from lakehouse_copilot.agent.rewrite import LLMRewriter, RewrittenQuery, SynonymRewriter
from lakehouse_copilot.agent.semantic import SemanticPlanner, load_dimension_values
from lakehouse_copilot.tools import LakehouseTools, ToolError

MAX_TOOL_RESULT_CHARS = 6000

SYSTEM_PROMPT = """You are Lakehouse Copilot, a data analyst for an Indian e-commerce company.
You answer business questions using ONLY the governed Gold tables, through tools.

How to work:
1. Read the catalog docs given with the question. Use search_docs for any metric or
   business rule you are not sure about, and describe_table before using a table.
2. Write ONE {dialect} SELECT for run_sql. Use only gold.* tables and the official metric
   SQL from the docs. Revenue, orders, units, AOV and margin only count rows where
   f.is_revenue = true. Count customers with COUNT(DISTINCT customer_id).
3. Start from gold.fact_sales joined to the dimensions. Use a gold.agg_* table only when
   every column you need is already in it; never join agg_* tables to each other or to
   dimensions, because their grain does not match.
4. Return one row per group with the group label first (for example the day name, not a
   weekday number) and only the columns the question asks for.
5. If run_sql returns an error, read it, fix the SQL and try again.
6. Answer in 1-3 short sentences with the numbers from the result. Money is INR;
   use Indian formatting (lakh, crore) for large amounts. Do not invent numbers.
   If the data cannot answer the question, say so.
Data covers 2024-01-01 to 2025-12-31.

Gold tables (name, grain: columns):
{schema}"""

TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": "Search the data catalog: table docs, metric definitions with SQL, "
            "glossary and business rules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for."},
                    "k": {"type": "integer", "description": "Number of results (1-10)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": "List the Gold tables with description and grain.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_table",
            "description": "Columns, types, keys and joins of one Gold table.",
            "parameters": {
                "type": "object",
                "properties": {"table_name": {"type": "string"}},
                "required": ["table_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "Run ONE read-only SELECT on gold.* tables. Returns columns and rows.",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        },
    },
]


@dataclass
class Step:
    tool: str
    arguments: dict[str, Any]
    output: Any = None
    error: str | None = None


@dataclass
class CopilotAnswer:
    question: str
    rewritten: RewrittenQuery
    answer: str
    sql: str | None = None
    result: dict | None = None
    tables: list[str] = field(default_factory=list)
    doc_ids: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    mode: str = "offline"
    usage: Usage = field(default_factory=Usage)

    @property
    def sources(self) -> str:
        parts = []
        if self.tables:
            parts.append("tables " + ", ".join(self.tables))
        if self.doc_ids:
            parts.append("docs " + ", ".join(self.doc_ids))
        return "Sources: " + "; ".join(parts) if parts else ""


class _TrackedModel:
    """Wraps a chat model and adds up the tokens and cost of every call."""

    def __init__(self, llm: ChatModel) -> None:
        self.llm = llm
        self.name = llm.name
        self.usage = Usage()

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResponse:
        response = self.llm.chat(messages, tools)
        self.usage = self.usage + response.usage
        return response


class Copilot:
    def __init__(
        self,
        tools: LakehouseTools,
        llm: ChatModel | None = None,
        *,
        max_steps: int = 8,
        docs_k: int = 4,
        use_rewriting: bool = True,
    ) -> None:
        self.tools = tools
        self.llm = _TrackedModel(llm) if llm else None
        self.max_steps = max_steps
        self.docs_k = docs_k
        self.use_rewriting = use_rewriting
        self.rewriter = LLMRewriter(self.llm) if self.llm else SynonymRewriter(tools.catalog)
        self._planner: SemanticPlanner | None = None
        self._schema: str | None = None

    @property
    def mode(self) -> str:
        return f"llm:{self.llm.name}" if self.llm else "offline"

    @property
    def planner(self) -> SemanticPlanner:
        if self._planner is None:
            self._planner = SemanticPlanner(
                self.tools.catalog, load_dimension_values(self.tools.run_sql)
            )
        return self._planner

    @property
    def schema_text(self) -> str:
        """A short list of every Gold table and its columns, so the model does not guess names."""
        if self._schema is None:
            lines = []
            for table in self.tools.list_tables():
                info = self.tools.describe_table(table["table"])
                columns = ", ".join(c["name"] for c in info["columns"])
                lines.append(f"- {info['table']} ({info['grain']}): {columns}")
            self._schema = "\n".join(lines)
        return self._schema

    # ------------------------------------------------------------ public

    def answer(self, question: str, history: list[dict] | None = None) -> CopilotAnswer:
        history = history or []
        if self.llm:
            self.llm.usage = Usage()
        if self.use_rewriting:
            rewritten = self.rewriter.rewrite(question, history)
        else:
            rewritten = RewrittenQuery(question, question, "none")
        docs = self.tools.search_docs(rewritten.text, k=self.docs_k)
        out = CopilotAnswer(question, rewritten, answer="", mode=self.mode)
        out.steps.append(Step("search_docs", {"query": rewritten.text}, [d["id"] for d in docs]))
        out.doc_ids = [d["id"] for d in docs[:3]]

        if self.llm is None:
            # Expansions help search, but the planner reads the user's own words.
            self._answer_offline(rewritten.question, out)
        else:
            self._answer_with_llm(question, history, docs, out)
            out.usage = self.llm.usage
        return out

    # ------------------------------------------------------------ offline

    def _answer_offline(self, question: str, out: CopilotAnswer) -> None:
        plan = self.planner.plan(question)
        sql = self.planner.to_sql(plan)
        step = Step("run_sql", {"sql": sql, "plan": plan.describe()})
        out.steps.append(step)
        try:
            result = self.tools.run_sql(sql)
        except ToolError as e:
            step.error = str(e)
            out.answer = f"Sorry, I could not answer that. {e}"
            return
        step.output = {"row_count": result["row_count"]}
        out.sql, out.result, out.tables = result["sql"], result, result["tables"]
        out.answer = self._summarise(plan, result)

    @staticmethod
    def _summarise(plan, result: dict) -> str:
        rows, columns = result["rows"], result["columns"]
        if not rows:
            return "No data matches that question."
        metric = plan.metrics[0]
        scope = f" ({plan.filter_text()})" if plan.filters else ""
        if len(rows) == 1 and not plan.group_by:
            values = [
                f"{m.label}{scope}: {format_number(v, m.name)}"
                for m, v in zip(plan.metrics, rows[0], strict=False)
            ]
            return "\n".join(values)
        n_dims = len(plan.group_by)
        if plan.limit == 1 and n_dims == 1:
            label = rows[0][0]
            value = format_number(rows[0][n_dims], metric.name)
            rank = "lowest" if plan.order == "asc" else "highest"
            return f"{label} has the {rank} {metric.label.lower()}{scope}: {value}."
        table = format_answer_table(columns, rows, {m.name for m in plan.metrics})
        return f"{metric.label} by {', '.join(columns[:n_dims])}{scope}:\n\n{table}"

    # ------------------------------------------------------------ LLM

    def _answer_with_llm(
        self, question: str, history: list[dict], docs: list[dict], out: CopilotAnswer
    ) -> None:
        doc_text = "\n".join(f"[{d['id']}] {d['text']}" for d in docs)
        messages: list[dict] = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.format(
                    dialect=self.tools.engine.dialect, schema=self.schema_text
                ),
            },
            *[{"role": m["role"], "content": m["content"]} for m in history[-6:]],
            {
                "role": "user",
                "content": f"{question}\n\nCatalog docs that may help:\n{doc_text}",
            },
        ]
        for _ in range(self.max_steps):
            response = self.llm.chat(messages, TOOL_SPECS)
            messages.append(response.as_message())
            if not response.tool_calls:
                out.answer = (response.content or "").strip()
                return
            for call in response.tool_calls:
                content = self._run_tool(call.name, call.arguments, out)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
        out.answer = "I could not finish within the step limit. Try a more specific question."

    def _run_tool(self, name: str, args: dict, out: CopilotAnswer) -> str:
        step = Step(name, args)
        out.steps.append(step)
        try:
            if name == "run_sql":
                result = self.tools.run_sql(args.get("sql", ""))
                out.sql, out.result = result["sql"], result
                out.tables = sorted(set(out.tables) | set(result["tables"]))
                step.output = {"row_count": result["row_count"]}
            elif name == "search_docs":
                result = self.tools.search_docs(args.get("query", ""), int(args.get("k", 5)))
                new = [d["id"] for d in result if d["id"] not in out.doc_ids]
                out.doc_ids += new[:2]
                step.output = [d["id"] for d in result]
            elif name == "describe_table":
                result = self.tools.describe_table(args.get("table_name", ""))
                step.output = result["table"]
            elif name == "list_tables":
                result = self.tools.list_tables()
                step.output = len(result)
            else:
                raise ToolError(f"Unknown tool {name}")
        except (ToolError, TypeError, ValueError) as e:  # bad arguments from the model too
            step.error = str(e)
            return json.dumps({"error": str(e)})
        return json.dumps(result, default=str)[:MAX_TOOL_RESULT_CHARS]


def build_copilot(settings=None, model: str | None = None) -> Copilot:
    """The copilot from settings. `model` overrides LLM_MODEL (used by the benchmark)."""
    from lakehouse_copilot.agent.llm import get_chat_model
    from lakehouse_copilot.config import get_settings

    settings = settings or get_settings()
    return Copilot(LakehouseTools.from_settings(settings), get_chat_model(settings, model))
