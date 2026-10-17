"""Slowly Changing Dimension Type 2 (SCD2).

When a tracked attribute (for example city or segment) changes, we do not overwrite the
row. We close the old version (set `effective_to`, `is_current = false`) and add a new
version. That keeps history, so an order placed in Chennai in 2024 still reports as
Chennai even after the customer moved to Pune.

Columns added:
    effective_from  first day the version is valid (inclusive)
    effective_to    first day it is no longer valid (exclusive); 9999-12-31 if current
    is_current      true for the latest version
    _row_hash       hash of the tracked columns, used to detect a change cheaply

Type 1 columns (for example email) are simply overwritten on every version: we only care
about their latest value.

`apply_scd2` is a pure function: (current dimension, incoming batch) -> new dimension.
It works on plain Parquet. On Delta Lake the same logic can run as a single MERGE.
"""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

HIGH_DATE = "9999-12-31"


def row_hash(columns: list[str]):
    return F.sha2(
        F.concat_ws("||", *[F.coalesce(F.col(c).cast("string"), F.lit("")) for c in columns]), 256
    )


def apply_scd2(
    current: DataFrame | None,
    incoming: DataFrame,
    *,
    key: str,
    tracked: list[str],
    type1: list[str],
    effective_col: str,
) -> DataFrame:
    """Merge one batch (one row per key) into an SCD2 dimension and return the new dimension."""
    attrs = tracked + type1
    inc = incoming.select(
        key, *attrs, F.col(effective_col).cast("date").alias("_eff"), row_hash(tracked).alias("_h")
    )

    def as_version(df: DataFrame) -> DataFrame:
        return df.select(
            key,
            *attrs,
            F.col("_eff").alias("effective_from"),
            F.lit(HIGH_DATE).cast("date").alias("effective_to"),
            F.lit(True).alias("is_current"),
            F.col("_h").alias("_row_hash"),
        )

    if current is None:
        return as_version(inc)

    cols = current.columns
    history = current.filter(~F.col("is_current"))
    open_ = current.filter(F.col("is_current"))

    # Pair each open version with the incoming row for the same key (if any).
    paired = open_.alias("c").join(inc.alias("i"), key, "left")
    has_incoming = F.col("i._h").isNotNull()
    changed = (
        has_incoming
        & (F.col("c._row_hash") != F.col("i._h"))
        & (F.col("i._eff") > F.col("c.effective_from"))
    )

    # 1. Open versions whose tracked columns changed: close them on the new effective date.
    closed = paired.filter(changed).select(
        key,
        *[F.col(f"c.{a}").alias(a) for a in attrs],
        F.col("c.effective_from").alias("effective_from"),
        F.col("i._eff").alias("effective_to"),
        F.lit(False).alias("is_current"),
        F.col("c._row_hash").alias("_row_hash"),
    )
    # 2. Open versions that did not change stay open.
    still_open = paired.filter(~changed).select(
        *[F.col(f"c.{c}").alias(c) for c in cols if c != key], key
    )

    # 3. New versions: changed keys plus brand-new keys.
    changed_keys = paired.filter(changed).select(key)
    new_keys = inc.join(open_.select(key), key, "left_anti").select(key)
    new_versions = as_version(inc.join(changed_keys.unionByName(new_keys), key, "inner"))

    merged = history.unionByName(closed).unionByName(still_open).unionByName(new_versions)

    # Type 1: overwrite these columns on every version with the latest incoming value.
    latest = inc.select(key, *[F.col(a).alias(f"_new_{a}") for a in type1])
    merged = merged.join(latest, key, "left")
    for a in type1:
        merged = merged.withColumn(a, F.coalesce(F.col(f"_new_{a}"), F.col(a))).drop(f"_new_{a}")
    return merged.select(*cols)
