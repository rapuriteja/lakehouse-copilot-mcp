"""Where pipeline tables live.

Local mode writes Parquet folders:   lakehouse/<layer>/<table>/
Databricks mode writes Delta tables: <catalog>.<layer>.<table> in Unity Catalog

The pipeline code only calls `read` and `write`, so the same code runs in both places.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession


@dataclass
class Lakehouse:
    spark: SparkSession
    root: str = "lakehouse"
    catalog: str | None = None  # set this to use Unity Catalog + Delta

    @property
    def is_unity_catalog(self) -> bool:
        return self.catalog is not None

    def table_name(self, layer: str, table: str) -> str:
        return f"{self.catalog}.{layer}.{table}"

    def path(self, layer: str, table: str) -> str:
        return str(Path(self.root) / layer / table)

    def write(self, df: DataFrame, layer: str, table: str, mode: str = "overwrite") -> None:
        if self.is_unity_catalog:
            (
                df.write.format("delta")
                .mode(mode)
                .option("overwriteSchema", "true")
                .saveAsTable(self.table_name(layer, table))
            )
        else:
            # Data is small locally, so one file per table keeps the folder tidy.
            df.coalesce(1).write.mode(mode).parquet(self.path(layer, table))

    def read(self, layer: str, table: str) -> DataFrame:
        if self.is_unity_catalog:
            return self.spark.table(self.table_name(layer, table))
        return self.spark.read.parquet(self.path(layer, table))
