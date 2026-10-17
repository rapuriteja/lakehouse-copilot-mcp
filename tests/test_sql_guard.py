from __future__ import annotations

import pytest

from lakehouse_copilot.catalog import load_catalog
from lakehouse_copilot.sql.guard import UnsafeSQLError
from lakehouse_copilot.tools import make_guard

guard = make_guard(load_catalog(), "duckdb", max_rows=100)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT payment_method, SUM(net_amount) FROM gold.fact_sales GROUP BY 1",
        "select count(*) from fact_sales",  # short name gets the gold schema
        "WITH m AS (SELECT * FROM gold.fact_sales) SELECT COUNT(*) FROM m",
        "SELECT c.city, COUNT(*) FROM gold.fact_sales f "
        "JOIN gold.dim_customer c ON f.customer_sk = c.customer_sk GROUP BY 1",
        "SELECT COUNT(*) FROM gold.dim_customer",  # COUNT(*) is not SELECT *
        "SELECT year FROM gold.dim_date UNION SELECT year FROM gold.agg_monthly_city_sales",
        "SELECT * FROM lakehouse_copilot.gold.dim_product",  # catalog prefix is accepted
    ],
)
def test_allowed_queries(sql: str) -> None:
    query = guard.validate(sql)
    assert "LIMIT" in query.sql.upper()
    assert all(t.startswith("gold.") for t in query.tables)


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("DROP TABLE gold.fact_sales", "Only SELECT"),
        ("DELETE FROM gold.fact_sales", "Only SELECT"),
        ("UPDATE gold.fact_sales SET quantity = 0", "Only SELECT"),
        ("INSERT INTO gold.dim_date SELECT * FROM gold.dim_date", "Only SELECT"),
        ("SELECT 1; DROP TABLE gold.fact_sales", "one SQL statement"),
        ("COPY gold.fact_sales TO 'leak.csv'", "Only SELECT"),
        ("ATTACH 'other.db'", "Only SELECT"),
        ("SELECT * INTO copy_t FROM gold.fact_sales", "INTO"),
        ("SELECT * FROM silver.orders", "not allowed"),
        ("SELECT * FROM bronze.customers", "not allowed"),
        ("SELECT * FROM read_csv('/etc/passwd')", "read_csv"),
        ("SELECT * FROM read_parquet('lakehouse/silver/x/*.parquet')", "read_parquet"),
        ("SELECT * FROM glob('*')", "glob"),
        ("SELECT getenv('AZURE_OPENAI_API_KEY')", "getenv"),
        ("SELECT * FROM 'secrets.parquet'", "not allowed"),
        ("SELECT email FROM gold.dim_customer", "PII"),
        ("SELECT c.phone FROM gold.dim_customer c", "PII"),
        ("SELECT * FROM gold.dim_customer", "SELECT *"),
        ("SELECT c.* FROM gold.dim_customer c", "SELECT *"),
        ("", "Empty"),
        ("SELEC 1", "parse"),
    ],
)
def test_rejected_queries(sql: str, reason: str) -> None:
    with pytest.raises(UnsafeSQLError, match=reason):
        guard.validate(sql)


def test_row_limit_added_or_lowered_but_never_raised() -> None:
    assert guard.validate("SELECT * FROM gold.dim_date").sql.endswith("LIMIT 100")
    assert guard.validate("SELECT * FROM gold.dim_date LIMIT 5000").sql.endswith("LIMIT 100")
    assert guard.validate("SELECT * FROM gold.dim_date LIMIT 7").sql.endswith("LIMIT 7")


def test_databricks_dialect_output() -> None:
    dbx = make_guard(load_catalog(), "databricks", max_rows=10)
    sql = dbx.validate("SELECT date_format(date, 'yyyy-MM') AS m FROM gold.dim_date").sql
    assert sql == "SELECT DATE_FORMAT(date, 'yyyy-MM') AS m FROM gold.dim_date LIMIT 10"
