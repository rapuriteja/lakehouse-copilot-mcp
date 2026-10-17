"""Integration tests: run the real pipeline on the small dataset and check every layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def manifest(raw_dir: Path) -> dict:
    return json.loads((raw_dir / "_manifest.json").read_text())


@pytest.fixture(scope="module")
def report(lakehouse: Path) -> dict:
    return json.loads((lakehouse / "_reports" / "pipeline_run.json").read_text())


@pytest.fixture(scope="module")
def read(spark, lakehouse: Path):
    return lambda layer, table: spark.read.parquet(str(lakehouse / layer / table))


def _dq(report: dict, table: str, rule: str) -> int:
    return next(
        r["failed_rows"] for r in report["dq_results"] if r["table"] == table and r["rule"] == rule
    )


def test_bronze_keeps_every_raw_row(report: dict, manifest: dict) -> None:
    raw = manifest["raw_row_counts"]
    counts = report["row_counts"]
    assert counts["bronze.orders"] == raw["orders/orders.jsonl"]
    assert counts["bronze.order_items"] == raw["order_items/order_items.csv"]
    assert counts["bronze.products"] == raw["products/products.jsonl"]
    assert counts["bronze.customers"] == sum(v for k, v in raw.items() if k.startswith("customers"))


def test_bronze_has_ingest_metadata(read) -> None:
    row = read("bronze", "customers").select("_source_file", "_batch_id", "_ingest_ts").first()
    assert row._source_file.endswith(".csv") and row._batch_id and row._ingest_ts


def test_dq_catches_every_injected_defect(report: dict, manifest: dict) -> None:
    m = manifest["dirty_counts"]
    assert _dq(report, "orders", "duplicate_rows_removed") == m["orders_duplicate"]
    assert _dq(report, "orders", "customer_id_not_null") == m["orders_null_customer_id"]
    assert _dq(report, "orders", "customer_exists") == m["orders_orphan_customer_id"]
    assert _dq(report, "orders", "payment_method_valid") == m["orders_invalid_payment_method"]
    assert _dq(report, "order_items", "duplicate_rows_removed") == m["items_duplicate"]
    assert _dq(report, "order_items", "quantity_positive") == m["items_non_positive_quantity"]
    assert _dq(report, "order_items", "product_exists") == m["items_orphan_product_id"]
    assert _dq(report, "order_items", "order_exists") == m["items_of_bad_orders"]
    assert _dq(report, "customers", "customer_id_not_null") == m["customers_null_customer_id"]
    assert _dq(report, "customers", "email_valid") == m["customers_invalid_email"]
    assert _dq(report, "products", "brand_not_null") == m["products_null_brand"]


def test_silver_counts_reconcile(report: dict, manifest: dict) -> None:
    m, clean, counts = manifest["dirty_counts"], manifest["clean_counts"], report["row_counts"]
    bad_orders = (
        m["orders_null_customer_id"]
        + m["orders_orphan_customer_id"]
        + m["orders_invalid_payment_method"]
    )
    bad_items = m["items_of_bad_orders"] + m["items_non_positive_quantity"]
    bad_items += m["items_orphan_product_id"]
    assert counts["silver.orders"] == clean["orders"] - bad_orders
    assert counts["silver.quarantine_orders"] == bad_orders
    assert counts["silver.order_items"] == clean["order_items"] - bad_items
    assert counts["silver.quarantine_order_items"] == bad_items
    assert counts["silver.products"] == clean["products"]


def test_silver_payment_methods_are_canonical(read) -> None:
    methods = {
        r.payment_method
        for r in read("silver", "orders").select("payment_method").distinct().collect()
    }
    assert methods <= {"UPI", "COD", "Credit Card", "Debit Card", "Wallet", "Net Banking"}


def test_products_missing_brand_filled(read, manifest: dict) -> None:
    unknown = read("silver", "products").filter("brand = 'Unknown'").count()
    assert unknown == manifest["dirty_counts"]["products_null_brand"]


def test_scd2_versions(read, manifest: dict) -> None:
    dim = read("silver", "customers_scd2")
    changes = manifest["customer_changes"]
    assert dim.select("customer_id").distinct().count() == manifest["clean_counts"]["customers"]
    assert dim.count() == manifest["clean_counts"]["customers"] + changes["type2_changes"]
    # Exactly one current version per customer, and closed versions have a valid range.
    current = dim.filter("is_current")
    assert current.count() == current.select("customer_id").distinct().count()
    assert current.count() == manifest["clean_counts"]["customers"]
    closed = dim.filter("NOT is_current")
    assert closed.filter("effective_to <= effective_from").count() == 0
    assert closed.count() == changes["type2_changes"]


def test_fact_uses_point_in_time_customer_version(spark, lakehouse: Path) -> None:
    for t in ["fact_sales", "dim_customer"]:
        spark.read.parquet(str(lakehouse / "gold" / t)).createOrReplaceTempView(t)
    wrong = (
        spark.sql(
            """
        SELECT count(*) AS n FROM fact_sales f
        JOIN dim_customer c ON f.customer_sk = c.customer_sk
        WHERE to_date(f.order_ts) <  c.effective_from
           OR to_date(f.order_ts) >= c.effective_to
        """
        )
        .first()
        .n
    )
    assert wrong == 0
    unknown = spark.sql(
        "SELECT count(*) AS n FROM fact_sales WHERE customer_sk = -1 OR product_sk = -1"
    )
    assert unknown.first().n == 0


def test_gold_aggregates_reconcile_with_fact(spark, lakehouse: Path, report: dict) -> None:
    def total(table: str, expr: str) -> float:
        df = spark.read.parquet(str(lakehouse / "gold" / table))
        return float(df.selectExpr(f"sum({expr}) AS s").first().s)

    fact_revenue = total("fact_sales", "CASE WHEN is_revenue THEN net_amount END")
    assert total("agg_daily_sales", "net_revenue") == pytest.approx(fact_revenue)
    assert total("agg_monthly_category_sales", "net_revenue") == pytest.approx(fact_revenue)
    assert total("agg_monthly_city_sales", "net_revenue") == pytest.approx(fact_revenue)
    assert report["row_counts"]["gold.fact_sales"] == report["row_counts"]["silver.order_items"]


def test_dim_date_flags_diwali(spark, lakehouse: Path) -> None:
    dim = spark.read.parquet(str(lakehouse / "gold" / "dim_date"))
    diwali = {str(r.date) for r in dim.filter("is_diwali").collect()}
    assert diwali == {"2024-11-01", "2025-10-20"}
    assert dim.filter("is_festive_season").count() == 2 * 24
    assert dim.count() == 731
