from __future__ import annotations

from datetime import datetime

from lakehouse_copilot.pipeline.dq import WARN, Rule, apply_rules, deduplicate


def test_apply_rules_splits_valid_and_quarantine(spark) -> None:
    df = spark.createDataFrame(
        [("a", 2, "x@y.in"), ("b", -1, "x@y.in"), ("c", None, "bad"), (None, 1, "bad")],
        "id string, qty int, email string",
    )
    rules = [
        Rule("id_not_null", "id IS NOT NULL"),
        Rule("qty_positive", "qty > 0"),
        Rule("email_valid", "email LIKE '%@%'", WARN),
    ]
    out = apply_rules(df, "t", rules)

    assert sorted(r.id for r in out.valid.collect()) == ["a"]
    quarantined = {r.id: r._dq_failed_rules for r in out.quarantine.collect()}
    assert quarantined == {
        "b": ["qty_positive"],
        "c": ["qty_positive"],  # NULL quantity is treated as a failure
        None: ["id_not_null"],
    }
    failed = {r["rule"]: r["failed_rows"] for r in out.results}
    assert failed == {"id_not_null": 1, "qty_positive": 2, "email_valid": 2}


def test_warn_rules_keep_rows(spark) -> None:
    df = spark.createDataFrame([("a", "bad")], "id string, email string")
    out = apply_rules(df, "t", [Rule("email_valid", "email LIKE '%@%'", WARN)])
    assert out.valid.count() == 1 and out.quarantine.count() == 0


def test_deduplicate_keeps_latest_and_null_keys(spark) -> None:
    from pyspark.sql import functions as F

    df = spark.createDataFrame(
        [
            ("o1", "old", datetime(2025, 1, 1)),
            ("o1", "new", datetime(2025, 1, 2)),
            ("o2", "only", datetime(2025, 1, 1)),
            (None, "junk1", datetime(2025, 1, 1)),
            (None, "junk2", datetime(2025, 1, 1)),
        ],
        "id string, v string, ts timestamp",
    )
    deduped, removed = deduplicate(df, "id", [F.col("ts").desc()])
    assert removed == 1
    assert sorted(r.v for r in deduped.collect()) == ["junk1", "junk2", "new", "only"]
