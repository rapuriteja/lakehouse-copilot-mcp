"""Offline planner: a small rule-based semantic layer that turns a question into SQL.

It needs no LLM, so the copilot works for free on a laptop and in CI, and it gives the
evaluation a baseline to compare the LLM against. How it works:

  1. find metrics      using the official metric names and synonyms from the catalog
  2. find filters      by matching real dimension values (cities, categories, payment
                       methods...) loaded from the Gold tables, plus years, months,
                       quarters and the festive season
  3. find group-by     from dimension words ("by city", "which category", "monthly")
  4. find ranking      ("top 5", "highest", "lowest")
  5. build SQL         from the metric definitions in the catalog, joining only the
                       dimension tables that are needed

It is deliberately simple: it only answers questions that fit "metric by dimension
with filters". Anything else is a job for the LLM agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lakehouse_copilot.catalog.model import Catalog, Metric

MONTHS = {
    name: i
    for i, names in enumerate(
        [
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}  # fmt: skip

TABLE_ALIASES = {
    "d": "JOIN gold.dim_date d ON f.date_key = d.date_key",
    "c": "JOIN gold.dim_customer c ON f.customer_sk = c.customer_sk",
    "p": "JOIN gold.dim_product p ON f.product_sk = p.product_sk",
}


@dataclass(frozen=True)
class Dimension:
    key: str
    sql: str  # expression, prefixed with its table alias
    keywords: tuple[str, ...]
    is_time: bool = False

    @property
    def alias(self) -> str:
        return self.sql.split(".")[0]

    @property
    def column(self) -> str:
        return self.sql.split(".")[-1]


# Order matters: longer, more specific phrases first ("city tier" before "city").
DIMENSIONS: list[Dimension] = [
    Dimension("sub_category", "p.sub_category", ("sub category", "sub categories", "subcategory")),
    Dimension("city_tier", "c.city_tier", ("city tier", "city tiers", "tier", "tiers")),
    Dimension("payment_method", "f.payment_method",
              ("payment method", "payment methods", "payment mode", "payment modes",
               "payment type", "payment types", "payment", "payments")),
    Dimension("order_status", "f.order_status", ("order status", "status", "statuses")),
    Dimension("day_name", "d.day_name", ("day of week", "day of the week", "weekday")),
    Dimension("festive", "d.is_festive_season",
              ("festive vs", "festive versus", "festive and non festive", "non festive",
               "festive or not")),
    Dimension("city", "c.city", ("city", "cities")),
    Dimension("state", "c.state", ("state", "states")),
    Dimension("region", "c.region", ("region", "regions", "zone", "zones")),
    Dimension("segment", "c.segment", ("segment", "segments", "customer type")),
    Dimension("category", "p.category",
              ("product category", "product categories", "category", "categories")),
    Dimension("brand", "p.brand", ("brand", "brands")),
    Dimension("product", "p.product_name", ("product", "products")),
    Dimension("channel", "f.channel", ("channel", "channels")),
    Dimension("year_month", "d.year_month",
              ("month", "months", "monthly", "month wise", "trend", "per month"), is_time=True),
    Dimension("quarter", "d.quarter", ("quarter", "quarters", "quarterly"), is_time=True),
    Dimension("year", "d.year", ("year", "years", "yearly", "annual", "annually"), is_time=True),
    Dimension("date", "d.date", ("day", "days", "daily", "date", "dates"), is_time=True),
]  # fmt: skip
DIM_BY_KEY = {d.key: d for d in DIMENSIONS}
DIM_BY_SQL = {d.sql: d for d in DIMENSIONS}

VALUE_ALIASES = {
    "bangalore": ("city", "Bengaluru"),
    "blr": ("city", "Bengaluru"),
    "hyd": ("city", "Hyderabad"),
    "bombay": ("city", "Mumbai"),
    "madras": ("city", "Chennai"),
    "calcutta": ("city", "Kolkata"),
    "new delhi": ("city", "Delhi"),
    "trivandrum": ("city", "Thiruvananthapuram"),
    "vizag": ("city", "Visakhapatnam"),
    "mysore": ("city", "Mysuru"),
    "mangalore": ("city", "Mangaluru"),
    "home and kitchen": ("category", "Home & Kitchen"),
    "electronic": ("category", "Electronics"),
    "groceries": ("category", "Grocery"),
    "book": ("category", "Books"),
    "cash on delivery": ("payment_method", "COD"),
    "credit cards": ("payment_method", "Credit Card"),
    "debit cards": ("payment_method", "Debit Card"),
    "netbanking": ("payment_method", "Net Banking"),
    "wallets": ("payment_method", "Wallet"),
    "mobile app": ("channel", "App"),
    "website": ("channel", "Web"),
    "metro": ("city_tier", 1),
    "metros": ("city_tier", 1),
    "metro cities": ("city_tier", 1),
    "premium customers": ("segment", "Premium"),
    "regular customers": ("segment", "Regular"),
}
TIER_PATTERN = re.compile(r"\btier\s*-?\s*(1|2|3|one|two|three)(?:\s+(?:cities|city|towns))?\b")
TIER_WORDS = {"1": 1, "2": 2, "3": 3, "one": 1, "two": 2, "three": 3}
REGION_PATTERN = re.compile(
    r"\b(north|south|east|west|central)(?:ern)?\s+(?:india|region|zone)\b"
    r"|\b(?:region|zone)\s+(north|south|east|west|central)\b"
)

WEAK_METRIC_WORDS = {"orders", "customers"}
RANK_DESC = ("highest", "most", "best", "maximum", "max", "largest", "biggest", "top", "peak")
RANK_ASC = ("lowest", "least", "worst", "minimum", "min", "smallest", "bottom", "fewest")


def normalise(text: str) -> str:
    text = text.lower().replace("%", " percent ").replace("&", " and ").replace("'", "")
    text = re.sub(r"[^a-z0-9\s-]", " ", text).replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()


def _find(phrase: str, text: str) -> re.Match | None:
    return re.search(rf"\b{re.escape(phrase)}\b", text)


def _mask(text: str, match: re.Match) -> str:
    """Blank out a matched span so shorter phrases cannot match inside it again."""
    return text[: match.start()] + " " * (match.end() - match.start()) + text[match.end() :]


def _sql_literal(value: object) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


@dataclass
class QueryPlan:
    metrics: list[Metric] = field(default_factory=list)
    group_by: list[Dimension] = field(default_factory=list)
    filters: dict[str, list[object]] = field(default_factory=dict)  # sql expression -> values
    order: str | None = None  # "desc" | "asc" | None
    limit: int | None = None

    def filter_text(self) -> str:
        """Human-readable filters, like 'year 2024, category Electronics'."""
        parts = []
        for expr, values in self.filters.items():
            column = expr.split(".")[-1].replace("is_", "").replace("_", " ")
            if values == [True]:
                parts.append(column)
            else:
                parts.append(f"{column} {' or '.join(str(v) for v in values)}")
        return ", ".join(parts)

    def describe(self) -> str:
        parts = ["metrics=" + ",".join(m.name for m in self.metrics)]
        if self.group_by:
            parts.append("by=" + ",".join(d.key for d in self.group_by))
        if self.filters:
            parts.append("where=" + "; ".join(f"{k} in {v}" for k, v in self.filters.items()))
        if self.order:
            parts.append(f"order={self.order} limit={self.limit}")
        return " | ".join(parts)


class SemanticPlanner:
    def __init__(self, catalog: Catalog, dimension_values: dict[str, list[object]]) -> None:
        """`dimension_values` maps a dimension key (city, category...) to its real values."""
        self.catalog = catalog
        self.metric_phrases = self._metric_phrases(catalog)
        self.value_phrases = self._value_phrases(dimension_values)

    # ------------------------------------------------------------ setup

    @staticmethod
    def _metric_phrases(catalog: Catalog) -> list[tuple[str, Metric]]:
        phrases: dict[str, Metric] = {}
        for m in catalog.metrics:
            label = m.label.lower()
            candidates = {m.name.replace("_", " "), re.sub(r"\(.*?\)", "", label), *m.synonyms}
            candidates |= set(re.findall(r"\((.*?)\)", label))
            for c in candidates:
                if normalise(c):
                    phrases.setdefault(normalise(c), m)
        return sorted(phrases.items(), key=lambda kv: -len(kv[0]))

    @staticmethod
    def _value_phrases(values: dict[str, list[object]]) -> list[tuple[str, str, object]]:
        phrases: dict[str, tuple[str, object]] = {}
        for dim, vals in values.items():
            for v in vals:
                text = normalise(str(v))
                if len(text) >= 3 and not text.isdigit():
                    phrases.setdefault(text, (dim, v))
        for alias, (dim, v) in VALUE_ALIASES.items():
            phrases.setdefault(normalise(alias), (dim, v))
        return sorted(((p, d, v) for p, (d, v) in phrases.items()), key=lambda x: -len(x[0]))

    # ------------------------------------------------------------ planning

    def plan(self, question: str) -> QueryPlan:
        text = normalise(question)
        plan = QueryPlan()
        text = self._find_metrics(text, plan)
        text = self._find_time_filters(text, plan)
        text = self._find_value_filters(text, plan)
        self._find_group_by(text, plan)
        self._find_ranking(text, plan)
        if not plan.metrics:
            if "customer" in text:
                default = "active_customers"
            elif re.search(r"\b(popular|how many|number of|count)\b", text):
                default = "order_count"
            else:
                default = "net_revenue"
            plan.metrics.append(self.catalog.metric(default))
        return plan

    def _find_metrics(self, text: str, plan: QueryPlan) -> str:
        weak: set[str] = set()  # metrics found only through a bare noun like "orders"
        for phrase, metric in self.metric_phrases:
            match = _find(phrase, text)
            while match:
                if metric not in plan.metrics:
                    plan.metrics.append(metric)
                    if phrase in WEAK_METRIC_WORDS:
                        weak.add(metric.name)
                text = _mask(text, match)
                match = _find(phrase, text)
        # "AOV for COD orders" or "margin from Premium customers": the noun describes a
        # filter, it is not a second metric.
        if len(plan.metrics) > 1:
            plan.metrics = [m for m in plan.metrics if m.name not in weak]
        return text

    def _add_filter(self, plan: QueryPlan, sql: str, value: object) -> None:
        values = plan.filters.setdefault(sql, [])
        if value not in values:
            values.append(value)

    def _find_time_filters(self, text: str, plan: QueryPlan) -> str:
        years = sorted({int(y) for y in re.findall(r"\b(20\d\d)\b", text)})
        for y in years:
            self._add_filter(plan, "d.year", y)
        if len(years) > 1:
            plan.group_by.append(DIM_BY_KEY["year"])  # "2024 vs 2025" compares years
        text = re.sub(r"\b20\d\d\b", " ", text)

        for name, number in sorted(MONTHS.items(), key=lambda kv: -len(kv[0])):
            match = _find(name, text)
            # "may" is also a normal word: only trust it next to a year or after "in".
            if match and (name != "may" or years or re.search(r"\b(in|of) may\b", text)):
                self._add_filter(plan, "d.month", number)
                text = _mask(text, match)

        for q in re.findall(r"\bq([1-4])\b", text):
            self._add_filter(plan, "d.quarter", int(q))
        text = re.sub(r"\bq[1-4]\b", " ", text)

        on_diwali = re.search(r"\b(on diwali|diwali day)\b", text)
        compares_festive = any(_find(k, text) for k in DIM_BY_KEY["festive"].keywords)
        festive = re.search(r"\b(festive|festival|diwali season|diwali sale|during diwali)\b", text)
        if festive and not compares_festive and not on_diwali:
            self._add_filter(plan, "d.is_festive_season", True)
        if on_diwali:
            self._add_filter(plan, "d.is_diwali", True)
        if re.search(r"\bweekends?\b", text):
            self._add_filter(plan, "d.is_weekend", True)
        return text

    def _find_value_filters(self, text: str, plan: QueryPlan) -> str:
        for m in TIER_PATTERN.finditer(text):
            self._add_filter(plan, "c.city_tier", TIER_WORDS[m.group(1)])
        text = TIER_PATTERN.sub(" ", text)
        for m in REGION_PATTERN.finditer(text):
            self._add_filter(plan, "c.region", (m.group(1) or m.group(2)).title())
        text = REGION_PATTERN.sub(" ", text)

        for phrase, dim, value in self.value_phrases:
            match = _find(phrase, text)
            if match:
                self._add_filter(plan, DIM_BY_KEY[dim].sql, value)
                text = _mask(text, match)
        # "card payments" means both card types.
        if re.search(r"\bcards?\b", text) and "f.payment_method" not in plan.filters:
            plan.filters["f.payment_method"] = ["Credit Card", "Debit Card"]
        return text

    def _find_group_by(self, text: str, plan: QueryPlan) -> None:
        # Several values of one dimension ("COD vs UPI") means: compare them side by side.
        for sql, values in plan.filters.items():
            dim = DIM_BY_SQL.get(sql)
            if dim and len(values) > 1 and dim not in plan.group_by:
                plan.group_by.append(dim)
        for dim in DIMENSIONS:
            if dim in plan.group_by:
                continue
            for kw in dim.keywords:
                match = _find(kw, text)
                if not match:
                    continue
                explicit = re.search(rf"\b(by|per|each|across)\s+{re.escape(kw)}\b", text)
                text = _mask(text, match)
                # A dimension that is already filtered to one value is not a group-by
                # ("revenue for the Electronics category"), unless asked "by" it.
                filtered = len(plan.filters.get(dim.sql, [])) == 1
                if filtered and not explicit:
                    break
                plan.group_by.append(dim)
                break
        # Time dimension comes first for readability (month, then city).
        plan.group_by.sort(key=lambda d: not d.is_time)

    def _find_ranking(self, text: str, plan: QueryPlan) -> None:
        match = re.search(r"\b(top|bottom|lowest|highest|best|worst)\s+(\d+)\b", text)
        if match:
            plan.order = "asc" if match.group(1) in RANK_ASC else "desc"
            plan.limit = int(match.group(2))
            return
        words = set(text.split())
        if plan.group_by and words & set(RANK_ASC):
            plan.order, plan.limit = "asc", 1
        elif plan.group_by and words & set(RANK_DESC):
            plan.order, plan.limit = "desc", (10 if "top" in words else 1)

    # ------------------------------------------------------------ SQL

    def to_sql(self, plan: QueryPlan) -> str:
        aliases = {d.alias for d in plan.group_by} | {k.split(".")[0] for k in plan.filters}
        for m in plan.metrics:
            if "gold.dim_customer" in m.requires:
                aliases.add("c")
        select = [f"{d.sql} AS {d.column}" for d in plan.group_by]
        select += [f"ROUND({m.sql}, 2) AS {m.name}" for m in plan.metrics]

        sql = ["SELECT " + ", ".join(select), "FROM gold.fact_sales f"]
        sql += [TABLE_ALIASES[a] for a in ("d", "c", "p") if a in aliases]
        where = []
        for expr, values in plan.filters.items():
            if len(values) == 1:
                where.append(f"{expr} = {_sql_literal(values[0])}")
            else:
                where.append(f"{expr} IN ({', '.join(_sql_literal(v) for v in values)})")
        if where:
            sql.append("WHERE " + " AND ".join(where))
        if plan.group_by:
            sql.append("GROUP BY " + ", ".join(str(i + 1) for i in range(len(plan.group_by))))
            if plan.order:
                sql.append(f"ORDER BY {plan.metrics[0].name} {plan.order.upper()}")
            elif plan.group_by[0].is_time:
                sql.append("ORDER BY " + ", ".join(str(i + 1) for i in range(len(plan.group_by))))
            else:
                sql.append(f"ORDER BY {plan.metrics[0].name} DESC")
        if plan.limit:
            sql.append(f"LIMIT {plan.limit}")
        return "\n".join(sql)


DIMENSION_VALUE_QUERIES = {
    "city": "SELECT DISTINCT city FROM gold.dim_customer WHERE city <> 'Unknown'",
    "state": "SELECT DISTINCT state FROM gold.dim_customer WHERE state <> 'Unknown'",
    "category": "SELECT DISTINCT category FROM gold.dim_product WHERE category <> 'Unknown'",
    "sub_category": "SELECT DISTINCT sub_category FROM gold.dim_product "
    "WHERE sub_category <> 'Unknown'",
    "brand": "SELECT DISTINCT brand FROM gold.dim_product WHERE brand <> 'Unknown'",
    "payment_method": "SELECT DISTINCT payment_method FROM gold.fact_sales",
    "channel": "SELECT DISTINCT channel FROM gold.fact_sales",
    "segment": "SELECT DISTINCT segment FROM gold.dim_customer WHERE segment <> 'Unknown'",
}  # fmt: skip


def load_dimension_values(run_sql) -> dict[str, list[object]]:
    """Read real dimension values from Gold through the same safe `run_sql` tool."""
    return {
        key: [row[0] for row in run_sql(sql)["rows"]]
        for key, sql in DIMENSION_VALUE_QUERIES.items()
    }
