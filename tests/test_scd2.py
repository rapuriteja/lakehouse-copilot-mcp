from __future__ import annotations

from datetime import date

from lakehouse_copilot.pipeline.scd2 import apply_scd2

SCHEMA = "id string, city string, email string, updated_at date"
KW = dict(key="id", tracked=["city"], type1=["email"], effective_col="updated_at")


def _rows(dim) -> list[tuple]:
    return sorted(
        (r.id, r.city, r.email, r.effective_from, r.effective_to, r.is_current)
        for r in dim.collect()
    )


def test_first_load_creates_open_versions(spark) -> None:
    batch = spark.createDataFrame([("c1", "Chennai", "a@x.in", date(2024, 1, 1))], SCHEMA)
    dim = apply_scd2(None, batch, **KW)
    assert _rows(dim) == [("c1", "Chennai", "a@x.in", date(2024, 1, 1), date(9999, 12, 31), True)]


def test_tracked_change_closes_old_version_and_opens_new(spark) -> None:
    b1 = spark.createDataFrame(
        [("c1", "Chennai", "a@x.in", date(2024, 1, 1)), ("c2", "Pune", "b@x.in", date(2024, 1, 1))],
        SCHEMA,
    )
    b2 = spark.createDataFrame(
        [
            ("c1", "Bengaluru", "a@x.in", date(2025, 7, 1)),  # moved: Type 2
            ("c2", "Pune", "b2@x.in", date(2025, 7, 1)),  # email only: Type 1
            ("c3", "Delhi", "c@x.in", date(2025, 7, 5)),  # new customer
        ],
        SCHEMA,
    )
    dim = apply_scd2(apply_scd2(None, b1, **KW), b2, **KW)
    assert _rows(dim) == [
        ("c1", "Bengaluru", "a@x.in", date(2025, 7, 1), date(9999, 12, 31), True),
        ("c1", "Chennai", "a@x.in", date(2024, 1, 1), date(2025, 7, 1), False),
        ("c2", "Pune", "b2@x.in", date(2024, 1, 1), date(9999, 12, 31), True),
        ("c3", "Delhi", "c@x.in", date(2025, 7, 5), date(9999, 12, 31), True),
    ]


def test_type1_change_updates_history_too(spark) -> None:
    b1 = spark.createDataFrame([("c1", "Chennai", "old@x.in", date(2024, 1, 1))], SCHEMA)
    b2 = spark.createDataFrame([("c1", "Pune", "old@x.in", date(2025, 1, 1))], SCHEMA)
    b3 = spark.createDataFrame([("c1", "Pune", "new@x.in", date(2025, 6, 1))], SCHEMA)
    dim = apply_scd2(apply_scd2(apply_scd2(None, b1, **KW), b2, **KW), b3, **KW)
    assert {r.email for r in dim.collect()} == {"new@x.in"}
    assert dim.count() == 2


def test_reapplying_same_batch_is_a_no_op(spark) -> None:
    b1 = spark.createDataFrame([("c1", "Chennai", "a@x.in", date(2024, 1, 1))], SCHEMA)
    b2 = spark.createDataFrame([("c1", "Pune", "a@x.in", date(2025, 1, 1))], SCHEMA)
    once = apply_scd2(apply_scd2(None, b1, **KW), b2, **KW)
    twice = apply_scd2(once, b2, **KW)
    assert _rows(once) == _rows(twice)
