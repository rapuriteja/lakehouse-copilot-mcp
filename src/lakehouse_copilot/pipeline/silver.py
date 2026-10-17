"""Silver: clean, conform, deduplicate and validate Bronze data.

Steps for every table:
  1. Cast strings to proper types. `try_*` functions return NULL instead of failing,
     because Spark 4 runs with ANSI mode on and a bad cast would stop the job.
  2. Standardise values (trim, map messy payment spellings to one canonical name).
  3. Deduplicate on the business key, keeping the latest ingested row.
  4. Apply DQ rules: bad rows go to a quarantine table, warnings are only counted.
Customers additionally go through SCD Type 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

from lakehouse_copilot.pipeline.dq import WARN, DQOutcome, Rule, apply_rules, deduplicate
from lakehouse_copilot.pipeline.scd2 import apply_scd2

PAYMENT_METHOD_MAP = {
    "upi": "UPI",
    "cod": "COD",
    "cash on delivery": "COD",
    "credit card": "Credit Card",
    "credit_card": "Credit Card",
    "debit card": "Debit Card",
    "debit_card": "Debit Card",
    "wallet": "Wallet",
    "net banking": "Net Banking",
    "netbanking": "Net Banking",
}
ORDER_STATUSES = ["Delivered", "Shipped", "Cancelled", "Returned"]
CUSTOMER_TRACKED = ["city", "state", "region", "city_tier", "segment"]
CUSTOMER_TYPE1 = ["first_name", "last_name", "email", "phone", "signup_date"]


@dataclass
class SilverResult:
    tables: dict[str, DataFrame] = field(default_factory=dict)
    quarantine: dict[str, DataFrame] = field(default_factory=dict)
    dq_results: list[dict] = field(default_factory=list)

    def add(self, name: str, outcome: DQOutcome, duplicates_removed: int) -> None:
        self.tables[name] = outcome.valid
        self.quarantine[name] = outcome.quarantine
        self.dq_results.append(
            {
                "table": name,
                "rule": "duplicate_rows_removed",
                "action": "dedupe",
                "failed_rows": duplicates_removed,
            }
        )
        self.dq_results.extend(outcome.results)


def _latest_first() -> list[Column]:
    """Ordering that puts the most recently ingested copy of a row first."""
    return [F.col("_ingest_ts").desc(), F.col("_source_file").desc()]


def _trim(col: str) -> Column:
    return F.nullif(F.trim(F.col(col)), F.lit(""))


def _map_payment(col: str) -> Column:
    mapping = F.create_map(*[F.lit(x) for kv in PAYMENT_METHOD_MAP.items() for x in kv])
    return mapping[F.lower(F.trim(F.col(col)))]


# ---------------------------------------------------------------- products


def clean_products(bronze: DataFrame) -> tuple[DQOutcome, int]:
    typed = bronze.select(
        _trim("product_id").alias("product_id"),
        _trim("product_name").alias("product_name"),
        _trim("category").alias("category"),
        _trim("sub_category").alias("sub_category"),
        _trim("brand").alias("brand"),
        F.expr("try_cast(unit_price AS DECIMAL(12,2))").alias("unit_price"),
        F.expr("try_cast(cost_price AS DECIMAL(12,2))").alias("cost_price"),
        F.to_date("updated_at").alias("updated_at"),
        "_ingest_ts",
        "_source_file",
    )
    deduped, removed = deduplicate(typed, "product_id", _latest_first())
    rules = [
        Rule("product_id_not_null", "product_id IS NOT NULL"),
        Rule("unit_price_positive", "unit_price > 0"),
        Rule("category_not_null", "category IS NOT NULL"),
        Rule("brand_not_null", "brand IS NOT NULL", WARN),
        Rule("cost_below_price", "cost_price <= unit_price", WARN),
    ]
    outcome = apply_rules(deduped, "products", rules)
    outcome.valid = outcome.valid.withColumn("brand", F.coalesce("brand", F.lit("Unknown")))
    return outcome, removed


# ---------------------------------------------------------------- customers


def _typed_customers(bronze: DataFrame) -> DataFrame:
    return bronze.select(
        _trim("customer_id").alias("customer_id"),
        F.initcap(_trim("first_name")).alias("first_name"),
        F.initcap(_trim("last_name")).alias("last_name"),
        F.lower(_trim("email")).alias("email"),
        _trim("phone").alias("phone"),
        _trim("city").alias("city"),
        _trim("state").alias("state"),
        _trim("region").alias("region"),
        F.expr("try_cast(city_tier AS INT)").alias("city_tier"),
        _trim("segment").alias("segment"),
        F.to_date("signup_date").alias("signup_date"),
        F.to_date("updated_at").alias("updated_at"),
        "_batch_id",
        "_ingest_ts",
        "_source_file",
    )


CUSTOMER_RULES = [
    Rule("customer_id_not_null", "customer_id IS NOT NULL"),
    Rule("city_not_null", "city IS NOT NULL"),
    Rule("updated_at_not_null", "updated_at IS NOT NULL"),
    Rule("email_valid", r"email RLIKE '^[^@\\s]+@[^@\\s]+\\.[a-z]{2,}$'", WARN),
    Rule("phone_valid", r"phone RLIKE '^\\+91[6-9][0-9]{9}$'", WARN),
]


def clean_customers(bronze: DataFrame) -> tuple[DQOutcome, int, DataFrame]:
    """Validate customers, then fold each batch (in date order) into an SCD2 dimension.

    Rebuilding the dimension from all Bronze batches on every run makes the step
    idempotent: running the pipeline twice gives the same result.
    """
    typed = _typed_customers(bronze)
    # Dedupe within a batch only; the same customer in two batches is a change, not a duplicate.
    keyed = typed.withColumn("_key", F.concat_ws("|", "customer_id", "_batch_id")).withColumn(
        "_key", F.when(F.col("customer_id").isNotNull(), F.col("_key"))
    )
    deduped, removed = deduplicate(keyed, "_key", _latest_first())
    outcome = apply_rules(deduped.drop("_key"), "customers", CUSTOMER_RULES)

    valid = outcome.valid.cache()
    batches = sorted(r["_batch_id"] for r in valid.select("_batch_id").distinct().collect())
    dim = None
    for batch in batches:
        incoming = valid.filter(F.col("_batch_id") == batch)
        dim = apply_scd2(
            dim,
            incoming,
            key="customer_id",
            tracked=CUSTOMER_TRACKED,
            type1=CUSTOMER_TYPE1,
            effective_col="updated_at",
        )
    return outcome, removed, dim


# ---------------------------------------------------------------- orders


def clean_orders(bronze: DataFrame, customers_scd2: DataFrame) -> tuple[DQOutcome, int]:
    ts = F.coalesce(
        F.try_to_timestamp(F.col("order_ts"), F.lit("yyyy-MM-dd'T'HH:mm:ss")),
        F.try_to_timestamp(F.col("order_ts"), F.lit("dd-MM-yyyy HH:mm:ss")),
    )
    typed = bronze.select(
        _trim("order_id").alias("order_id"),
        _trim("customer_id").alias("customer_id"),
        ts.alias("order_ts"),
        F.col("payment_method").alias("payment_method_raw"),
        _map_payment("payment_method").alias("payment_method"),
        F.initcap(_trim("order_status")).alias("order_status"),
        F.initcap(_trim("channel")).alias("channel"),
        "_ingest_ts",
        "_source_file",
    )
    deduped, removed = deduplicate(typed, "order_id", _latest_first())

    known = (
        customers_scd2.select("customer_id").distinct().withColumn("_customer_known", F.lit(True))
    )
    checked = deduped.join(known, "customer_id", "left").fillna(False, ["_customer_known"])
    statuses = ", ".join(f"'{s}'" for s in ORDER_STATUSES)
    rules = [
        Rule("order_id_not_null", "order_id IS NOT NULL"),
        Rule("customer_id_not_null", "customer_id IS NOT NULL"),
        Rule("customer_exists", "customer_id IS NULL OR _customer_known"),
        Rule("order_ts_parsed", "order_ts IS NOT NULL"),
        Rule("payment_method_valid", "payment_method IS NOT NULL"),
        Rule("order_status_valid", f"order_status IN ({statuses})"),
    ]
    outcome = apply_rules(checked, "orders", rules)
    outcome.valid = outcome.valid.drop("_customer_known")
    return outcome, removed


# ---------------------------------------------------------------- order items


def clean_order_items(
    bronze: DataFrame, orders: DataFrame, products: DataFrame
) -> tuple[DQOutcome, int]:
    typed = bronze.select(
        _trim("order_item_id").alias("order_item_id"),
        _trim("order_id").alias("order_id"),
        _trim("product_id").alias("product_id"),
        F.expr("try_cast(quantity AS INT)").alias("quantity"),
        F.expr("try_cast(unit_price AS DECIMAL(12,2))").alias("unit_price"),
        F.expr("try_cast(discount_pct AS DECIMAL(5,2))").alias("discount_pct"),
        "_ingest_ts",
        "_source_file",
    )
    deduped, removed = deduplicate(typed, "order_item_id", _latest_first())

    known_orders = orders.select("order_id").withColumn("_order_known", F.lit(True))
    known_products = products.select("product_id").withColumn("_product_known", F.lit(True))
    checked = (
        deduped.join(known_orders, "order_id", "left")
        .join(known_products, "product_id", "left")
        .fillna(False, ["_order_known", "_product_known"])
    )
    rules = [
        Rule("order_item_id_not_null", "order_item_id IS NOT NULL"),
        # Lines of an order that was rejected (or never arrived) cannot be trusted either.
        Rule("order_exists", "_order_known"),
        Rule("product_exists", "_product_known"),
        Rule("quantity_positive", "quantity > 0"),
        Rule("unit_price_positive", "unit_price > 0"),
        Rule("discount_in_range", "discount_pct BETWEEN 0 AND 100"),
    ]
    outcome = apply_rules(checked, "order_items", rules)
    outcome.valid = outcome.valid.drop("_order_known", "_product_known")
    return outcome, removed
