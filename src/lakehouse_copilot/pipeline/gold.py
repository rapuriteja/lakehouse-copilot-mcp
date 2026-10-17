"""Gold: a star schema for analytics, plus pre-aggregated tables.

    dim_date       one row per calendar day, with the Diwali festive season flagged
    dim_customer   SCD2 customer versions with a surrogate key (customer_sk)
    dim_product    one row per product with a surrogate key (product_sk)
    fact_sales     one row per order line (grain: order_item_id)
    agg_*          small summary tables for fast dashboards and simple questions

Surrogate keys are deterministic hashes of the natural key (plus effective_from for SCD2),
so re-running the pipeline produces the same keys. Each dimension has an 'unknown'
member with key -1, so a fact row never silently disappears in an inner join.
"""

from __future__ import annotations

from datetime import date, timedelta

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from lakehouse_copilot.data_gen.reference import (
    DIWALI_DATES,
    FESTIVE_DAYS_AFTER,
    FESTIVE_DAYS_BEFORE,
)

UNKNOWN_SK = -1
REVENUE_STATUSES = ["Delivered", "Shipped"]


def build_dim_date(spark: SparkSession, start: date, end: date) -> DataFrame:
    festive = [
        (d - timedelta(days=FESTIVE_DAYS_BEFORE), d + timedelta(days=FESTIVE_DAYS_AFTER), d)
        for d in DIWALI_DATES.values()
    ]
    is_festive = F.lit(False)
    is_diwali = F.lit(False)
    for lo, hi, diwali in festive:
        is_festive = is_festive | F.col("date").between(F.lit(lo), F.lit(hi))
        is_diwali = is_diwali | (F.col("date") == F.lit(diwali))

    days = spark.sql(
        f"SELECT explode(sequence(DATE'{start}', DATE'{end}', INTERVAL 1 DAY)) AS date"
    )
    return days.select(
        F.date_format("date", "yyyyMMdd").cast("int").alias("date_key"),
        "date",
        F.year("date").alias("year"),
        F.quarter("date").alias("quarter"),
        F.month("date").alias("month"),
        F.date_format("date", "MMMM").alias("month_name"),
        F.date_format("date", "yyyy-MM").alias("year_month"),
        F.dayofmonth("date").alias("day_of_month"),
        F.date_format("date", "EEEE").alias("day_name"),
        F.dayofweek("date").isin(1, 7).alias("is_weekend"),
        is_festive.alias("is_festive_season"),
        is_diwali.alias("is_diwali"),
    )


def build_dim_customer(customers_scd2: DataFrame) -> DataFrame:
    dim = customers_scd2.select(
        F.xxhash64("customer_id", "effective_from").alias("customer_sk"),
        "customer_id",
        F.concat_ws(" ", "first_name", "last_name").alias("full_name"),
        "email",
        "phone",
        "city",
        "state",
        "region",
        "city_tier",
        "segment",
        "signup_date",
        "effective_from",
        "effective_to",
        "is_current",
    )
    spark = customers_scd2.sparkSession
    unknown = spark.createDataFrame(
        [(UNKNOWN_SK, "UNKNOWN", "Unknown", None, None, "Unknown", "Unknown", "Unknown", None,
          "Unknown", None, date(1900, 1, 1), date(9999, 12, 31), True)],
        dim.schema,
    )  # fmt: skip
    return dim.unionByName(unknown)


def build_dim_product(products: DataFrame) -> DataFrame:
    dim = products.select(
        F.xxhash64("product_id").alias("product_sk"),
        "product_id",
        "product_name",
        "category",
        "sub_category",
        "brand",
        "unit_price",
        "cost_price",
    )
    spark = products.sparkSession
    unknown = spark.createDataFrame(
        [(UNKNOWN_SK, "UNKNOWN", "Unknown", "Unknown", "Unknown", "Unknown", None, None)],
        dim.schema,
    )
    return dim.unionByName(unknown)


def build_fact_sales(
    order_items: DataFrame, orders: DataFrame, dim_customer: DataFrame, dim_product: DataFrame
) -> DataFrame:
    lines = order_items.join(
        orders.select("order_id", "customer_id", "order_ts", "payment_method", "order_status",
                      "channel"),
        "order_id",
    ).withColumn("order_date", F.to_date("order_ts"))  # fmt: skip

    # Point-in-time join: pick the customer version that was valid on the order date.
    cust = dim_customer.filter(F.col("customer_sk") != UNKNOWN_SK).select(
        "customer_sk", F.col("customer_id").alias("_cid"), "effective_from", "effective_to"
    )
    lines = lines.join(
        cust,
        (lines.customer_id == cust._cid)
        & (lines.order_date >= cust.effective_from)
        & (lines.order_date < cust.effective_to),
        "left",
    )
    prod = dim_product.select("product_sk", F.col("product_id").alias("_pid"))
    lines = lines.join(prod, lines.product_id == prod._pid, "left")

    gross = F.col("quantity") * F.col("unit_price")
    discount = F.round(gross * F.col("discount_pct") / 100, 2)
    net = gross - discount
    return lines.select(
        "order_item_id",
        "order_id",
        F.date_format("order_date", "yyyyMMdd").cast("int").alias("date_key"),
        "order_ts",
        F.coalesce("customer_sk", F.lit(UNKNOWN_SK)).alias("customer_sk"),
        F.coalesce("product_sk", F.lit(UNKNOWN_SK)).alias("product_sk"),
        "payment_method",
        "order_status",
        "channel",
        "quantity",
        "unit_price",
        "discount_pct",
        gross.cast("decimal(14,2)").alias("gross_amount"),
        discount.cast("decimal(14,2)").alias("discount_amount"),
        net.cast("decimal(14,2)").alias("net_amount"),
        F.col("order_status").isin(REVENUE_STATUSES).alias("is_revenue"),
    )


def add_cost_and_margin(fact: DataFrame, dim_product: DataFrame) -> DataFrame:
    cost = dim_product.select(F.col("product_sk").alias("_psk"), "cost_price")
    return (
        fact.join(cost, fact.product_sk == cost._psk, "left")
        .drop("_psk")
        .withColumn("cost_amount", (F.col("quantity") * F.col("cost_price")).cast("decimal(14,2)"))
        .withColumn(
            "margin_amount", (F.col("net_amount") - F.col("cost_amount")).cast("decimal(14,2)")
        )
        .drop("cost_price")
    )


def _revenue_metrics() -> list:
    rev = F.col("is_revenue")
    return [
        F.countDistinct(F.when(rev, F.col("order_id"))).alias("order_count"),
        F.sum(F.when(rev, F.col("quantity"))).alias("units_sold"),
        F.sum(F.when(rev, F.col("net_amount"))).cast("decimal(16,2)").alias("net_revenue"),
        F.sum(F.when(rev, F.col("margin_amount"))).cast("decimal(16,2)").alias("gross_margin"),
    ]


def build_agg_daily_sales(fact: DataFrame, dim_date: DataFrame) -> DataFrame:
    daily = fact.groupBy("date_key").agg(
        *_revenue_metrics(),
        F.countDistinct("order_id").alias("all_orders"),
        F.countDistinct(F.when(F.col("order_status") == "Cancelled", F.col("order_id"))).alias(
            "cancelled_orders"
        ),
        F.countDistinct(F.when(F.col("order_status") == "Returned", F.col("order_id"))).alias(
            "returned_orders"
        ),
    )
    return daily.join(dim_date.select("date_key", "date", "is_festive_season"), "date_key").orderBy(
        "date_key"
    )


def build_agg_monthly_category_sales(
    fact: DataFrame, dim_date: DataFrame, dim_product: DataFrame
) -> DataFrame:
    return (
        fact.join(dim_date.select("date_key", "year", "month", "year_month"), "date_key")
        .join(dim_product.select("product_sk", "category"), "product_sk")
        .groupBy("year", "month", "year_month", "category")
        .agg(*_revenue_metrics())
        .orderBy("year_month", "category")
    )


def build_agg_monthly_city_sales(
    fact: DataFrame, dim_date: DataFrame, dim_customer: DataFrame
) -> DataFrame:
    return (
        fact.join(dim_date.select("date_key", "year", "month", "year_month"), "date_key")
        .join(dim_customer.select("customer_sk", "city", "state", "region", "city_tier"),
              "customer_sk")
        .groupBy("year", "month", "year_month", "city", "state", "region", "city_tier")
        .agg(*_revenue_metrics())
        .orderBy("year_month", "city")
    )  # fmt: skip
