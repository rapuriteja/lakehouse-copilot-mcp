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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lhc", description="Lakehouse Copilot command line")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="generate synthetic source data")
    gen.add_argument("--out", help="output folder (default: DATA_DIR)")
    gen.add_argument("--seed", type=int, help="random seed (default: SEED)")
    gen.set_defaults(func=_cmd_generate)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
