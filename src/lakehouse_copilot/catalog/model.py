"""Typed view over catalog.yml."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

CATALOG_FILE = Path(__file__).with_name("catalog.yml")


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    description: str
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class Table:
    name: str  # schema.table, for example gold.fact_sales
    description: str
    grain: str
    primary_key: str | list[str]
    columns: tuple[Column, ...]
    joins: tuple[str, ...] = ()

    @property
    def short_name(self) -> str:
        return self.name.split(".")[-1]

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


@dataclass(frozen=True)
class Metric:
    name: str
    label: str
    description: str
    sql: str
    synonyms: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class GlossaryTerm:
    term: str
    definition: str
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class BusinessRule:
    name: str
    title: str
    text: str


@dataclass(frozen=True)
class Catalog:
    tables: tuple[Table, ...]
    metrics: tuple[Metric, ...]
    glossary: tuple[GlossaryTerm, ...]
    business_rules: tuple[BusinessRule, ...] = field(default_factory=tuple)

    def table(self, name: str) -> Table | None:
        """Find a table by full (gold.fact_sales) or short (fact_sales) name, any case."""
        wanted = name.strip().lower()
        return next(
            (t for t in self.tables if wanted in (t.name.lower(), t.short_name.lower())), None
        )

    def metric(self, name: str) -> Metric | None:
        return next((m for m in self.metrics if m.name == name), None)

    @property
    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]


def _parse(raw: dict) -> Catalog:
    tables = tuple(
        Table(
            name=t["name"],
            description=" ".join(t["description"].split()),
            grain=t["grain"],
            primary_key=t["primary_key"],
            joins=tuple(t.get("joins", [])),
            columns=tuple(
                Column(
                    name=c["name"],
                    type=c["type"],
                    description=c["description"],
                    examples=tuple(str(e) for e in c.get("examples", [])),
                )
                for c in t["columns"]
            ),
        )
        for t in raw["tables"]
    )
    metrics = tuple(
        Metric(
            name=m["name"],
            label=m["label"],
            description=m["description"],
            sql=m["sql"],
            synonyms=tuple(m.get("synonyms", [])),
            requires=tuple(m.get("requires", [])),
        )
        for m in raw["metrics"]
    )
    glossary = tuple(
        GlossaryTerm(g["term"], g["definition"], tuple(g.get("synonyms", [])))
        for g in raw["glossary"]
    )
    rules = tuple(
        BusinessRule(r["name"], r["title"], " ".join(r["text"].split()))
        for r in raw.get("business_rules", [])
    )
    return Catalog(tables=tables, metrics=metrics, glossary=glossary, business_rules=rules)


@lru_cache
def load_catalog(path: str | Path = CATALOG_FILE) -> Catalog:
    return _parse(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
