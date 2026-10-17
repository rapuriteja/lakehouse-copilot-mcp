"""Create or reuse a SparkSession."""

from __future__ import annotations

from pyspark.sql import SparkSession


def get_spark(app_name: str = "lakehouse-copilot") -> SparkSession:
    """On Databricks this returns the existing session. Locally it starts a small one."""
    active = SparkSession.getActiveSession()
    if active is not None:
        return active
    spark = (
        SparkSession.builder.appName(app_name)
        .master("local[2]")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.driver.memory", "2g")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark
