from __future__ import annotations

import pytest

from lakehouse_copilot.agent.copilot import Copilot
from lakehouse_copilot.catalog import load_catalog
from lakehouse_copilot.catalog.docs import build_chunks
from lakehouse_copilot.evals.metrics import hit_at_k, reciprocal_rank, results_match
from lakehouse_copilot.evals.run import load_golden, render_markdown, run_evaluation


def test_hit_and_reciprocal_rank() -> None:
    retrieved = ["a", "b", "c"]
    assert hit_at_k(retrieved, {"b"}, 1) == 0.0 and hit_at_k(retrieved, {"b"}, 3) == 1.0
    assert reciprocal_rank(retrieved, {"b", "c"}, 5) == 0.5
    assert reciprocal_rank(retrieved, {"z"}, 5) == 0.0


@pytest.mark.parametrize(
    ("pred", "gold", "order", "expected"),
    [
        ([["UPI", 10.0]], [["UPI", 10.004]], False, True),  # small numeric tolerance
        ([[10.0, "UPI"]], [["UPI", 10.0]], False, True),  # column order ignored
        ([["A", 1], ["B", 2]], [["B", 2], ["A", 1]], False, True),  # row order ignored
        ([["A", 1], ["B", 2]], [["B", 2], ["A", 1]], True, False),  # unless it matters
        ([["A", 1]], [["A", 2]], False, False),
        ([["A", 1]], [["A", 1], ["B", 2]], False, False),
        ([[2024, 5.0]], [[2024, 5.0, 1]], False, False),  # extra column is wrong
    ],
)
def test_results_match(pred, gold, order, expected) -> None:
    assert results_match(pred, gold, order) is expected


def test_golden_set_is_valid() -> None:
    questions = load_golden()
    chunk_ids = {c.id for c in build_chunks(load_catalog())}
    assert len(questions) == 40
    assert {q.split for q in questions} == {"dev", "test"}
    assert len({q.id for q in questions}) == len(questions)
    for q in questions:
        assert q.relevant_docs <= chunk_ids, q.id


def test_gold_sql_runs_through_the_guard(tools) -> None:
    for q in load_golden():
        assert tools.run_sql(q.gold_sql)["row_count"] >= 1, q.id


def test_evaluation_runs_end_to_end(tools) -> None:
    report = run_evaluation(Copilot(tools), split="dev")
    summary = report["text_to_sql"]["summary"]["dev"]
    assert summary["questions"] == 20 and 0 <= summary["execution_accuracy"] <= 1
    assert 0 <= report["retrieval"]["dev"]["rewritten"]["mrr@5"] <= 1
    assert "Execution accuracy" in render_markdown(
        report
    ) or "execution accuracy" in render_markdown(report)
