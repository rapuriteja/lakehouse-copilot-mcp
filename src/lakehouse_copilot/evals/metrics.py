"""Evaluation metrics.

Retrieval
  hit@k   share of questions where at least one relevant doc is in the top k results
  MRR@k   mean reciprocal rank: 1/rank of the first relevant doc (0 if not in top k).
          1.0 means the right doc is always first; 0.5 means it is usually second.

Text-to-SQL
  execution accuracy  share of questions where the predicted SQL returns the same result
                      as the hand-written gold SQL. We compare results, not SQL text,
                      because many different queries are equally correct.
"""

from __future__ import annotations

import math


def hit_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    return 1.0 if any(doc in relevant for doc in retrieved[:k]) else 0.0


def reciprocal_rank(retrieved: list[str], relevant: set[str], k: int) -> float:
    for rank, doc in enumerate(retrieved[:k], start=1):
        if doc in relevant:
            return 1.0 / rank
    return 0.0


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _value_key(value) -> tuple:
    if _is_number(value):
        return (0, round(float(value), 2), "")
    return (1, 0.0, str(value))


def _canonical_row(row: list) -> list:
    """Sort values inside a row so column order does not matter."""
    return sorted(row, key=_value_key)


def _values_equal(a, b) -> bool:
    if _is_number(a) and _is_number(b):
        return math.isclose(float(a), float(b), rel_tol=1e-4, abs_tol=0.01)
    return str(a) == str(b)


def results_match(predicted: list[list], gold: list[list], order_matters: bool = False) -> bool:
    """True when both results hold the same rows (numbers compared with a small tolerance)."""
    if len(predicted) != len(gold):
        return False
    pred = [_canonical_row(r) for r in predicted]
    expected = [_canonical_row(r) for r in gold]
    if not order_matters:
        pred.sort(key=lambda r: [_value_key(v) for v in r])
        expected.sort(key=lambda r: [_value_key(v) for v in r])
    for p_row, g_row in zip(pred, expected, strict=True):
        if len(p_row) != len(g_row):
            return False
        if not all(_values_equal(a, b) for a, b in zip(p_row, g_row, strict=True)):
            return False
    return True
