"""Compare LLMs on the golden set: execution accuracy, cost per run and latency.

lhc bench --models openai/gpt-4o-mini,google/gemini-2.5-flash,deepseek/deepseek-chat

Every model runs the same 40 questions through the same agent, tools and retriever, so
the only difference is the model. The offline planner is included as a free baseline.
Cost is what OpenRouter reported for each call (usage accounting), in US dollars.
"""

from __future__ import annotations

import json
from pathlib import Path

from lakehouse_copilot.agent.copilot import Copilot
from lakehouse_copilot.evals.run import evaluate_sql, load_golden


def benchmark(copilots: dict[str, Copilot], split: str = "all") -> dict:
    questions = load_golden(split=split)
    results = {}
    for label, copilot in copilots.items():
        print(f"Running {label} on {len(questions)} questions...", flush=True)
        results[label] = evaluate_sql(copilot, questions)
    return {
        "questions": len(questions),
        "retriever": next(iter(copilots.values())).tools.retriever.name,
        "models": results,
        "choice": choose(results),
    }


def choose(results: dict[str, dict]) -> str | None:
    """Best held-out accuracy among LLMs; on a tie, the cheaper one, then the faster one."""
    llms = {k: v["summary"] for k, v in results.items() if v["summary"].get("llm_calls")}
    if not llms:
        return None

    def key(item):
        s = item[1]
        test = s.get("test", s["all"])["execution_accuracy"]
        cost = s["total_cost_usd"] if s["total_cost_usd"] is not None else float("inf")
        return (-test, -s["all"]["execution_accuracy"], cost, s["median_latency_s"])

    return min(llms.items(), key=key)[0]


def render_markdown(report: dict) -> str:
    lines = [
        "# Model benchmark",
        "",
        f"- {report['questions']} golden questions, retriever `{report['retriever']}`",
        "- Accuracy = execution accuracy (same result rows as the gold SQL)",
        "- Cost = OpenRouter's reported cost for the whole run, in US dollars",
        "",
        "| Model | Dev | Test (held out) | All | Cost per run | Cost per question "
        "| Median latency | Errors |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for label, result in report["models"].items():
        s = result["summary"]

        def acc(split, s=s):
            if split not in s:
                return "-"
            r = s[split]
            return f"{r['correct']}/{r['questions']} ({r['execution_accuracy']:.0%})"

        cost = s.get("total_cost_usd")
        if not s.get("llm_calls"):
            cost_run, cost_q = "free", "free"
        elif cost is None:
            cost_run, cost_q = "not reported", "-"
        else:
            cost_run, cost_q = f"${cost:.4f}", f"${cost / s['all']['questions']:.5f}"
        mark = " **(chosen)**" if label == report["choice"] else ""
        lines.append(
            f"| `{label}`{mark} | {acc('dev')} | {acc('test')} | {acc('all')} | {cost_run} "
            f"| {cost_q} | {s['median_latency_s']:.2f} s | {s.get('errors', 0)} |"
        )
    return "\n".join(lines) + "\n"


def save(report: dict, out_dir: str | Path) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out / "benchmark.json", out / "benchmark.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
