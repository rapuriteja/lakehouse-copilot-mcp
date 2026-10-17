"""A small data quality (DQ) framework.

A rule is a SQL boolean expression that must be TRUE for a good row.

- action="quarantine": failing rows are moved to a quarantine table with the names of
  the rules they broke. They never reach Gold.
- action="warn": failing rows are kept and only counted (for example a malformed email).

Every rule produces a result row (table, rule, action, failed_rows) so DQ is measurable.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

QUARANTINE = "quarantine"
WARN = "warn"


@dataclass(frozen=True)
class Rule:
    name: str
    condition: str  # SQL expression; TRUE means the row passes
    action: str = QUARANTINE


@dataclass
class DQOutcome:
    valid: DataFrame
    quarantine: DataFrame
    results: list[dict]


def apply_rules(df: DataFrame, table: str, rules: list[Rule]) -> DQOutcome:
    """Evaluate all rules on `df` and split it into valid and quarantined rows.

    A NULL result counts as a failure, so `quantity > 0` also catches a NULL quantity.
    """
    flags = [F.coalesce(F.expr(r.condition), F.lit(False)).alias(f"_ok_{r.name}") for r in rules]
    checked = df.select("*", *flags).cache()

    # One pass to count failures for every rule.
    counts = checked.agg(
        *[F.sum((~F.col(f"_ok_{r.name}")).cast("int")).alias(r.name) for r in rules]
    ).first()
    results = [
        {
            "table": table,
            "rule": r.name,
            "action": r.action,
            "failed_rows": int(counts[r.name] or 0),
        }
        for r in rules
    ]

    blocking = [r for r in rules if r.action == QUARANTINE]
    failed_names = F.array_compact(
        F.array(*[F.when(~F.col(f"_ok_{r.name}"), F.lit(r.name)) for r in blocking])
    )
    flagged = checked.withColumn("_dq_failed_rules", failed_names).drop(
        *[f"_ok_{r.name}" for r in rules]
    )
    is_bad = F.size("_dq_failed_rules") > 0
    valid = flagged.filter(~is_bad).drop("_dq_failed_rules")
    quarantine = flagged.filter(is_bad).withColumn("_quarantined_at", F.current_timestamp())
    return DQOutcome(valid=valid, quarantine=quarantine, results=results)


def deduplicate(df: DataFrame, key: str, order_by: list[str]) -> tuple[DataFrame, int]:
    """Keep one row per `key`: the first by `order_by` (use descending columns for 'latest').

    Rows with a NULL key are left alone so the DQ rules can quarantine each of them.
    Returns the deduplicated frame and how many rows were removed.
    """
    from pyspark.sql.window import Window

    window = Window.partitionBy(key).orderBy(*order_by)
    ranked = df.filter(F.col(key).isNotNull()).withColumn("_rn", F.row_number().over(window))
    deduped = ranked.filter("_rn = 1").drop("_rn").unionByName(df.filter(F.col(key).isNull()))
    removed = df.count() - deduped.count()
    return deduped, removed
