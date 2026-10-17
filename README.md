# Lakehouse Copilot (MCP + RAG)

An AI copilot for a data lakehouse. Ask business questions in plain English and get answers
from governed Gold tables (text-to-SQL), grounded in the data catalog (RAG), exposed to any AI
client through an MCP server.

> Work in progress. The full README (architecture, how to run, evaluation results) is written
> at the end of the build.

## Quick start (local, free)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
lhc generate          # synthetic Indian e-commerce data in data/raw
pytest
```
