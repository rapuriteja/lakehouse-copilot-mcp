"""Turn the catalog into (1) retrieval chunks for RAG and (2) a readable Markdown page.

Chunking strategy: one chunk per meaningful unit instead of fixed-size text windows.
A table overview, a group of columns, one metric, one glossary term or one business rule
is each a chunk. Small, self-contained chunks retrieve precisely, and each chunk knows
which tables it is about, so answers can cite their sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lakehouse_copilot.catalog.model import Catalog

COLUMNS_PER_CHUNK = 6


@dataclass(frozen=True)
class Chunk:
    id: str
    title: str
    text: str
    source_type: str  # table | columns | metric | glossary | rule
    source_name: str
    tables: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "tables": list(self.tables),
        }


def build_chunks(catalog: Catalog) -> list[Chunk]:
    chunks: list[Chunk] = []
    for t in catalog.tables:
        joins = "; ".join(t.joins) if t.joins else "none"
        chunks.append(
            Chunk(
                id=f"table:{t.name}",
                title=f"Table {t.name}",
                text=(
                    f"Table {t.name}. {t.description} Grain: {t.grain}. "
                    f"Primary key: {t.primary_key}. Joins: {joins}. "
                    f"Columns: {', '.join(c.name for c in t.columns)}."
                ),
                source_type="table",
                source_name=t.name,
                tables=(t.name,),
            )
        )
        for part, start in enumerate(range(0, len(t.columns), COLUMNS_PER_CHUNK), start=1):
            cols = t.columns[start : start + COLUMNS_PER_CHUNK]
            lines = []
            for c in cols:
                example = f" Examples: {', '.join(c.examples)}." if c.examples else ""
                lines.append(f"{t.name}.{c.name} ({c.type}): {c.description}{example}")
            chunks.append(
                Chunk(
                    id=f"columns:{t.name}:{part}",
                    title=f"Columns of {t.name} (part {part})",
                    text=" ".join(lines),
                    source_type="columns",
                    source_name=t.name,
                    tables=(t.name,),
                )
            )
    for m in catalog.metrics:
        tables = ("gold.fact_sales", *m.requires)
        chunks.append(
            Chunk(
                id=f"metric:{m.name}",
                title=f"Metric: {m.label}",
                text=(
                    f"Business metric {m.label} ({m.name}). {m.description} "
                    f"Also called: {', '.join(m.synonyms)}. "
                    f"SQL definition over gold.fact_sales f: {m.sql}"
                ),
                source_type="metric",
                source_name=m.name,
                tables=tables,
            )
        )
    for g in catalog.glossary:
        chunks.append(
            Chunk(
                id=f"glossary:{g.term.lower().replace(' ', '_')}",
                title=f"Glossary: {g.term}",
                text=f"{g.term}: {g.definition} Also called: {', '.join(g.synonyms)}.",
                source_type="glossary",
                source_name=g.term,
            )
        )
    for r in catalog.business_rules:
        mentioned = tuple(t.name for t in catalog.tables if t.name in r.text)
        chunks.append(
            Chunk(
                id=f"rule:{r.name}",
                title=f"Business rule: {r.title}",
                text=f"{r.title}. {r.text}",
                source_type="rule",
                source_name=r.name,
                tables=mentioned,
            )
        )
    return chunks


def render_markdown(catalog: Catalog) -> str:
    """A human-friendly data dictionary, generated from the same YAML."""
    out = ["# Gold layer data catalog", "", "_Generated from `catalog.yml`. Do not edit._", ""]
    out += ["## Tables", ""]
    for t in catalog.tables:
        out += [f"### `{t.name}`", "", t.description, "", f"- **Grain:** {t.grain}"]
        out += [f"- **Primary key:** `{t.primary_key}`"]
        if t.joins:
            out += ["- **Joins:** " + "; ".join(f"`{j}`" for j in t.joins)]
        out += ["", "| Column | Type | Description |", "|---|---|---|"]
        out += [f"| `{c.name}` | {c.type} | {c.description} |" for c in t.columns]
        out += [""]
    out += ["## Metrics", "", "| Metric | Definition | SQL |", "|---|---|---|"]
    out += [f"| {m.label} | {m.description} | `{m.sql}` |" for m in catalog.metrics]
    out += ["", "## Glossary", ""]
    out += [f"- **{g.term}:** {g.definition}" for g in catalog.glossary]
    out += ["", "## Business rules", ""]
    out += [f"- **{r.title}:** {r.text}" for r in catalog.business_rules]
    return "\n".join(out) + "\n"
