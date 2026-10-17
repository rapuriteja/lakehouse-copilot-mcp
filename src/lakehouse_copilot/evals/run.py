"""Run the evaluation and write a JSON report plus a Markdown summary.

lhc eval                  # all questions, configured retriever and LLM mode
lhc eval --split test     # held-out questions only
lhc eval --model X        # LLM mode with OpenRouter model X
lhc bench --models A,B,C  # compare models: accuracy, cost, latency
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from lakehouse_copilot.agent.copilot import Copilot
from lakehouse_copilot.agent.rewrite import SynonymRewriter
from lakehouse_copilot.evals.metrics import hit_at_k, reciprocal_rank, results_match
from lakehouse_copilot.tools import LakehouseTools, ToolError

GOLDEN_FILE = Path(__file__).with_name("golden.yaml")
KS = (1, 3, 5)


@dataclass(frozen=True)
class GoldenQuestion:
    id: str
    split: str
    question: str
    relevant_docs: frozenset[str]
    gold_sql: str
    order_matters: bool = False


def load_golden(path: Path = GOLDEN_FILE, split: str = "all") -> list[GoldenQuestion]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["questions"]
    questions = [
        GoldenQuestion(
            id=q["id"],
            split=q["split"],
            question=q["question"],
            relevant_docs=frozenset(q["relevant_docs"]),
            gold_sql=q["gold_sql"],
            order_matters=bool(q.get("order_matters", False)),
        )
        for q in raw
    ]
    return [q for q in questions if split == "all" or q.split == split]


def evaluate_retrieval(tools: LakehouseTools, questions: list[GoldenQuestion]) -> dict:
    """Hit@k and MRR@5 for the raw question and for the rewritten query."""
    rewriter = SynonymRewriter(tools.catalog)
    report: dict = {"per_question": []}
    for variant in ("raw", "rewritten"):
        hits = {k: 0.0 for k in KS}
        mrr = 0.0
        for q in questions:
            query = q.question if variant == "raw" else rewriter.rewrite(q.question).text
            retrieved = [d["id"] for d in tools.search_docs(query, k=max(KS))]
            for k in KS:
                hits[k] += hit_at_k(retrieved, q.relevant_docs, k)
            rr = reciprocal_rank(retrieved, q.relevant_docs, max(KS))
            mrr += rr
            if variant == "rewritten":
                report["per_question"].append(
                    {"id": q.id, "query": query, "retrieved": retrieved, "reciprocal_rank": rr}
                )
        n = len(questions)
        report[variant] = {f"hit@{k}": round(hits[k] / n, 3) for k in KS} | {
            "mrr@5": round(mrr / n, 3)
        }
    return report


def evaluate_sql(copilot: Copilot, questions: list[GoldenQuestion]) -> dict:
    """Execution accuracy: does the copilot's SQL return the same result as the gold SQL?"""
    rows = []
    for q in questions:
        gold = copilot.tools.run_sql(q.gold_sql)["rows"]
        started = time.perf_counter()
        error, usage = None, None
        try:
            answer = copilot.answer(q.question)
            predicted = answer.result["rows"] if answer.result else None
            sql, usage = answer.sql, answer.usage
        except ToolError as e:
            predicted, sql, error = None, None, str(e)
        except Exception as e:  # API errors, timeouts: count as wrong, keep going
            predicted, sql, error = None, None, f"{type(e).__name__}: {e}"
        latency = time.perf_counter() - started
        correct = predicted is not None and results_match(predicted, gold, q.order_matters)
        rows.append(
            {
                "id": q.id,
                "split": q.split,
                "question": q.question,
                "correct": correct,
                "predicted_sql": sql,
                "error": error,
                "latency_s": round(latency, 3),
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "cost_usd": usage.cost_usd if usage else None,
                "llm_calls": usage.calls if usage else 0,
            }
        )
    summary = {}
    for split in ("dev", "test", "all"):
        subset = [r for r in rows if split == "all" or r["split"] == split]
        if subset:
            summary[split] = {
                "questions": len(subset),
                "correct": sum(r["correct"] for r in subset),
                "execution_accuracy": round(sum(r["correct"] for r in subset) / len(subset), 3),
            }
    latencies = sorted(r["latency_s"] for r in rows)
    summary["median_latency_s"] = latencies[len(latencies) // 2] if latencies else None
    summary["errors"] = sum(1 for r in rows if r["error"])
    summary |= _usage_summary(rows)
    return {"summary": summary, "per_question": rows}


def _usage_summary(rows: list[dict]) -> dict:
    calls = sum(r["llm_calls"] for r in rows)
    if not calls:
        return {}
    costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    return {
        "llm_calls": calls,
        "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
        "completion_tokens": sum(r["completion_tokens"] for r in rows),
        # Only a real total when every question reported its cost.
        "total_cost_usd": round(sum(costs), 6) if len(costs) == len(rows) else None,
    }


def run_evaluation(copilot: Copilot, split: str = "all") -> dict:
    questions = load_golden(split=split)
    return {
        "mode": copilot.mode,
        "retriever": getattr(copilot.tools.retriever, "name", "unknown"),
        "sql_engine": copilot.tools.engine.dialect,
        "questions": len(questions),
        "retrieval": {
            split_name: evaluate_retrieval(
                copilot.tools,
                [q for q in questions if split_name == "all" or q.split == split_name],
            )
            for split_name in ("dev", "test", "all")
            if any(split_name == "all" or q.split == split_name for q in questions)
        },
        "text_to_sql": evaluate_sql(copilot, questions),
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Evaluation results",
        "",
        f"- Copilot mode: `{report['mode']}`",
        f"- Retriever: `{report['retriever']}`, SQL engine: `{report['sql_engine']}`",
        f"- Golden questions: {report['questions']} (dev = used while building, test = held out)",
        "",
        "## Retrieval (catalog docs)",
        "",
        "| Split | Query | Hit@1 | Hit@3 | Hit@5 | MRR@5 |",
        "|---|---|---|---|---|---|",
    ]
    for split, data in report["retrieval"].items():
        for variant in ("raw", "rewritten"):
            m = data[variant]
            lines.append(
                f"| {split} | {variant} | {m['hit@1']:.2f} | {m['hit@3']:.2f} | "
                f"{m['hit@5']:.2f} | {m['mrr@5']:.2f} |"
            )
    s = report["text_to_sql"]["summary"]
    lines += [
        "",
        "## Text-to-SQL execution accuracy",
        "",
        "| Split | Correct | Accuracy |",
        "|---|---|---|",
    ]
    for split in ("dev", "test", "all"):
        if split in s:
            lines.append(
                f"| {split} | {s[split]['correct']}/{s[split]['questions']} | "
                f"{s[split]['execution_accuracy']:.0%} |"
            )
    lines += ["", f"Median latency per question: {s['median_latency_s']} s"]
    if "llm_calls" in s:
        cost = s["total_cost_usd"]
        lines += [
            f"LLM calls: {s['llm_calls']}, tokens: {s['prompt_tokens']:,} in / "
            f"{s['completion_tokens']:,} out, cost of the run: "
            + (f"${cost:.4f}" if cost is not None else "not reported"),
            f"Questions that failed with an error: {s['errors']}",
        ]
    lines += [""]
    lines += ["## Per question", "", "| Id | Question | Correct | Note |", "|---|---|---|---|"]
    for r in report["text_to_sql"]["per_question"]:
        note = (r["error"] or "").replace("|", "/").replace("\n", " ")[:120]
        lines.append(
            f"| {r['id']} | {r['question']} | {'yes' if r['correct'] else 'no'} | {note} |"
        )
    return "\n".join(lines) + "\n"


def save_report(report: dict, out_dir: str | Path) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = report["mode"].replace(":", "_").replace("/", "_")
    json_path = out / f"eval_{name}.json"
    md_path = out / f"eval_{name}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
