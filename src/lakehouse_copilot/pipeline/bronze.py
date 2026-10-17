"""Bronze: land raw source files as-is, plus ingest metadata.

Every column is read as a string. Bronze never rejects or fixes data; it keeps an exact
copy of what the source sent so we can always replay Silver from it.
"""

from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

# Source name -> (sub folder, file format, columns)
SOURCES: dict[str, tuple[str, str, list[str]]] = {
    "customers": (
        "customers",
        "csv",
        [
            "customer_id", "first_name", "last_name", "email", "phone", "city", "state",
            "region", "city_tier", "segment", "signup_date", "updated_at",
        ],
    ),
    "products": (
        "products",
        "json",
        [
            "product_id", "product_name", "category", "sub_category", "brand",
            "unit_price", "cost_price", "updated_at",
        ],
    ),
    "orders": (
        "orders",
        "json",
        ["order_id", "customer_id", "order_ts", "payment_method", "order_status", "channel"],
    ),
    "order_items": (
        "order_items",
        "csv",
        ["order_item_id", "order_id", "product_id", "quantity", "unit_price", "discount_pct"],
    ),
}  # fmt: skip


def _string_schema(columns: list[str]) -> StructType:
    return StructType([StructField(c, StringType(), nullable=True) for c in columns])


def ingest(spark: SparkSession, raw_dir: str, source: str) -> DataFrame:
    """Read one source folder and add `_source_file`, `_batch_id` and `_ingest_ts`."""
    folder, fmt, columns = SOURCES[source]
    reader = spark.read.schema(_string_schema(columns))
    path = f"{raw_dir}/{folder}"
    df = reader.option("header", True).csv(path) if fmt == "csv" else reader.json(path)

    file_path = F.col("_metadata.file_path")
    batch = F.regexp_extract(file_path, r"(\d{4}-\d{2}-\d{2})", 1)
    return df.select(
        *columns,
        file_path.alias("_source_file"),
        F.when(batch != "", batch).alias("_batch_id"),
        F.current_timestamp().alias("_ingest_ts"),
    )
