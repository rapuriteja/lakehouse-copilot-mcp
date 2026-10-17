"""Run the whole medallion pipeline: Bronze -> Silver -> Gold.

Each layer is written and then read back by the next layer, exactly like separate jobs
would do in production. The run returns (and saves) a summary with row counts and every
DQ result, so a failed rule is visible without opening Spark.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path

from pyspark.sql import functions as F

from lakehouse_copilot.pipeline import bronze, gold, silver
from lakehouse_copilot.pipeline.spark import get_spark
from lakehouse_copilot.pipeline.storage import Lakehouse

log = logging.getLogger(__name__)

GOLD_TABLES = [
    "dim_date",
    "dim_customer",
    "dim_product",
    "fact_sales",
    "agg_daily_sales",
    "agg_monthly_category_sales",
    "agg_monthly_city_sales",
]


def run_bronze(lh: Lakehouse, raw_dir: str) -> None:
    for source in bronze.SOURCES:
        lh.write(bronze.ingest(lh.spark, raw_dir, source), "bronze", source)


def run_silver(lh: Lakehouse) -> list[dict]:
    result = silver.SilverResult()

    products, removed = silver.clean_products(lh.read("bronze", "products"))
    result.add("products", products, removed)

    customers, removed, customers_scd2 = silver.clean_customers(lh.read("bronze", "customers"))
    result.add("customers", customers, removed)
    lh.write(customers_scd2, "silver", "customers_scd2")
    customers_scd2 = lh.read("silver", "customers_scd2")

    orders, removed = silver.clean_orders(lh.read("bronze", "orders"), customers_scd2)
    result.add("orders", orders, removed)
    lh.write(products.valid, "silver", "products")
    lh.write(orders.valid, "silver", "orders")

    items, removed = silver.clean_order_items(
        lh.read("bronze", "order_items"), lh.read("silver", "orders"), lh.read("silver", "products")
    )
    result.add("order_items", items, removed)
    lh.write(items.valid, "silver", "order_items")

    for name, df in result.quarantine.items():
        lh.write(df, "silver", f"quarantine_{name}")
    lh.write(lh.spark.createDataFrame(result.dq_results), "silver", "dq_results")
    return result.dq_results


def run_gold(lh: Lakehouse) -> None:
    orders = lh.read("silver", "orders")
    bounds = orders.agg(F.min("order_ts").alias("lo"), F.max("order_ts").alias("hi")).first()
    start, end = date(bounds["lo"].year, 1, 1), date(bounds["hi"].year, 12, 31)

    lh.write(gold.build_dim_date(lh.spark, start, end), "gold", "dim_date")
    lh.write(gold.build_dim_customer(lh.read("silver", "customers_scd2")), "gold", "dim_customer")
    lh.write(gold.build_dim_product(lh.read("silver", "products")), "gold", "dim_product")
    dim_date = lh.read("gold", "dim_date")
    dim_customer = lh.read("gold", "dim_customer")
    dim_product = lh.read("gold", "dim_product")

    fact = gold.build_fact_sales(
        lh.read("silver", "order_items"), orders, dim_customer, dim_product
    )
    lh.write(gold.add_cost_and_margin(fact, dim_product), "gold", "fact_sales")
    fact = lh.read("gold", "fact_sales")

    lh.write(gold.build_agg_daily_sales(fact, dim_date), "gold", "agg_daily_sales")
    lh.write(
        gold.build_agg_monthly_category_sales(fact, dim_date, dim_product),
        "gold",
        "agg_monthly_category_sales",
    )
    lh.write(
        gold.build_agg_monthly_city_sales(fact, dim_date, dim_customer),
        "gold",
        "agg_monthly_city_sales",
    )


def row_counts(lh: Lakehouse) -> dict[str, int]:
    tables = [("bronze", s) for s in bronze.SOURCES]
    tables += [
        ("silver", t)
        for t in ["products", "customers_scd2", "orders", "order_items"]
        + [f"quarantine_{n}" for n in ["products", "customers", "orders", "order_items"]]
    ]
    tables += [("gold", t) for t in GOLD_TABLES]
    return {f"{layer}.{t}": lh.read(layer, t).count() for layer, t in tables}


def run_pipeline(
    raw_dir: str | Path, lakehouse_dir: str | Path = "lakehouse", catalog: str | None = None
) -> dict:
    """Run all layers and return a summary. `catalog` switches to Unity Catalog + Delta."""
    lh = Lakehouse(get_spark(), root=str(lakehouse_dir), catalog=catalog)
    started = time.time()
    run_bronze(lh, str(raw_dir))
    dq_results = run_silver(lh)
    run_gold(lh)
    summary = {
        "duration_seconds": round(time.time() - started, 1),
        "row_counts": row_counts(lh),
        "dq_results": dq_results,
    }
    if not lh.is_unity_catalog:
        report = Path(lakehouse_dir) / "_reports" / "pipeline_run.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(summary, indent=2))
    return summary
