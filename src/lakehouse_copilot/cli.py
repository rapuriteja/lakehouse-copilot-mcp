"""Command line entry point: `lhc <command>`.

lhc generate    create synthetic source files in DATA_DIR
"""

from __future__ import annotations

import argparse
import json

from lakehouse_copilot.config import get_settings


def _cmd_generate(args: argparse.Namespace) -> None:
    from lakehouse_copilot.data_gen import GeneratorConfig, generate

    settings = get_settings()
    cfg = GeneratorConfig(seed=args.seed if args.seed is not None else settings.seed)
    manifest = generate(args.out or settings.data_dir, cfg)
    print(json.dumps({k: manifest[k] for k in ("raw_row_counts", "dirty_counts")}, indent=2))


def _cmd_pipeline(args: argparse.Namespace) -> None:
    from lakehouse_copilot.pipeline.run import run_pipeline

    settings = get_settings()
    summary = run_pipeline(args.raw or settings.data_dir, args.lakehouse or settings.lakehouse_dir)
    print(json.dumps(summary["row_counts"], indent=2))
    failed = [r for r in summary["dq_results"] if r["failed_rows"]]
    print("DQ findings:")
    for r in failed:
        print(f"  {r['table']:<12} {r['rule']:<26} {r['action']:<10} {r['failed_rows']}")
    print(f"Finished in {summary['duration_seconds']}s")


def _cmd_index(args: argparse.Namespace) -> None:
    from pathlib import Path

    from lakehouse_copilot.catalog import load_catalog
    from lakehouse_copilot.catalog.docs import build_chunks, render_markdown

    settings = get_settings()
    catalog = load_catalog()
    chunks = build_chunks(catalog)
    Path("docs").mkdir(exist_ok=True)
    Path("docs/catalog.md").write_text(render_markdown(catalog), encoding="utf-8")
    out = Path(settings.lakehouse_dir) / "_index" / "chunks.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(c.to_dict()) for c in chunks) + "\n", encoding="utf-8")
    print(f"Wrote docs/catalog.md and {len(chunks)} chunks to {out}")

    if settings.retriever == "azure":
        from lakehouse_copilot.rag.azure import AzureSearchIndexer

        indexer = AzureSearchIndexer.from_settings(settings)
        n = indexer.index(chunks, settings.azure_search_index, settings.embedding_dimensions)
        print(f"Uploaded {n} chunks to Azure AI Search index '{settings.azure_search_index}'")


def _cmd_search(args: argparse.Namespace) -> None:
    from lakehouse_copilot.rag import get_retriever

    for hit in get_retriever().search(args.query, k=args.k):
        print(f"{hit.score:.3f}  {hit.chunk.id:<45} {hit.chunk.title}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lhc", description="Lakehouse Copilot command line")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="generate synthetic source data")
    gen.add_argument("--out", help="output folder (default: DATA_DIR)")
    gen.add_argument("--seed", type=int, help="random seed (default: SEED)")
    gen.set_defaults(func=_cmd_generate)

    pipe = sub.add_parser("pipeline", help="run the Bronze/Silver/Gold pipeline locally")
    pipe.add_argument("--raw", help="raw input folder (default: DATA_DIR)")
    pipe.add_argument("--lakehouse", help="output folder (default: LAKEHOUSE_DIR)")
    pipe.set_defaults(func=_cmd_pipeline)

    idx = sub.add_parser("index", help="build catalog docs and the search index")
    idx.set_defaults(func=_cmd_index)

    search = sub.add_parser("search", help="search catalog docs")
    search.add_argument("query")
    search.add_argument("-k", type=int, default=5)
    search.set_defaults(func=_cmd_search)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
