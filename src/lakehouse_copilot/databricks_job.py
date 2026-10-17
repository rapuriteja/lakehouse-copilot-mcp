"""Entry point for the Databricks job (see databricks.yml).

Each task of the job calls this with one step, so Bronze, Silver and Gold show up as
separate tasks in the Databricks Jobs UI, with their own logs and retries:

    lakehouse-job generate --catalog lakehouse_copilot
    lakehouse-job bronze   --catalog lakehouse_copilot
    lakehouse-job silver   --catalog lakehouse_copilot
    lakehouse-job gold     --catalog lakehouse_copilot
    lakehouse-job publish-docs --catalog lakehouse_copilot
    lakehouse-job index-search   # catalog chunks -> Azure AI Search, embedded via OpenRouter

On Databricks the tables are Delta tables in Unity Catalog: <catalog>.<layer>.<table>.
Raw files land in a Unity Catalog volume: /Volumes/<catalog>/landing/raw.
Keys (OpenRouter, AI Search) reach the job as environment variables from a Key
Vault-backed secret scope, set on the job cluster in resources/medallion_job.yml.
"""

from __future__ import annotations

import argparse
import json

from lakehouse_copilot.catalog import Catalog, load_catalog
from lakehouse_copilot.pipeline import run
from lakehouse_copilot.pipeline.spark import get_spark
from lakehouse_copilot.pipeline.storage import Lakehouse


def _sql_string(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def catalog_comment_statements(catalog: Catalog, uc_catalog: str) -> list[str]:
    """SQL that copies catalog.yml into Unity Catalog: table and column comments,
    and a `pii` tag on personal-data columns so governance tools can find them."""
    statements = []
    for table in catalog.tables:
        full = f"{uc_catalog}.{table.name}"
        statements.append(f"COMMENT ON TABLE {full} IS {_sql_string(table.description)}")
        for col in table.columns:
            statements.append(
                f"ALTER TABLE {full} ALTER COLUMN {col.name} COMMENT {_sql_string(col.description)}"
            )
            if col.pii:
                statements.append(
                    f"ALTER TABLE {full} ALTER COLUMN {col.name} SET TAGS ('pii' = 'true')"
                )
    return statements


def index_search(settings=None) -> int:
    """Embed the catalog chunks (EMBEDDING_PROVIDER, OpenRouter by default) and upload them."""
    from lakehouse_copilot.catalog.docs import build_chunks
    from lakehouse_copilot.config import get_settings
    from lakehouse_copilot.rag.azure import AzureSearchIndexer

    settings = settings or get_settings()
    if not settings.azure_search_endpoint:
        raise SystemExit("AZURE_SEARCH_ENDPOINT is not set (bundle variable search_endpoint).")
    indexer = AzureSearchIndexer.from_settings(settings)
    chunks = build_chunks(load_catalog())
    return indexer.index(chunks, settings.azure_search_index, settings.embedding_dimensions)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lakehouse-job")
    parser.add_argument(
        "step", choices=["generate", "bronze", "silver", "gold", "publish-docs", "index-search"]
    )
    parser.add_argument("--catalog", default="lakehouse_copilot", help="Unity Catalog name")
    parser.add_argument("--raw-dir", default=None, help="default: /Volumes/<catalog>/landing/raw")
    args = parser.parse_args(argv)

    if args.step == "index-search":
        print(f"Indexed {index_search()} chunks in Azure AI Search.")
        return

    raw_dir = args.raw_dir or f"/Volumes/{args.catalog}/landing/raw"
    spark = get_spark()
    lh = Lakehouse(spark, catalog=args.catalog)

    if args.step == "generate":
        from lakehouse_copilot.data_gen import generate

        manifest = generate(raw_dir)
        print(json.dumps(manifest["raw_row_counts"], indent=2))
    elif args.step == "bronze":
        run.run_bronze(lh, raw_dir)
    elif args.step == "silver":
        failed = [r for r in run.run_silver(lh) if r["failed_rows"]]
        print(json.dumps(failed, indent=2))
    elif args.step == "gold":
        run.run_gold(lh)
    elif args.step == "publish-docs":
        for statement in catalog_comment_statements(load_catalog(), args.catalog):
            spark.sql(statement)
    print(f"Step '{args.step}' finished.")


if __name__ == "__main__":
    main()
