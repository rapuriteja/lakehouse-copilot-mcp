"""SQL guard: validate model-written SQL before it touches any data.

An LLM writes the SQL, so we treat it like untrusted user input. The guard parses the
query into a syntax tree with sqlglot (it does not use regex) and enforces:

  1. exactly one statement, and it must be a SELECT (or UNION/INTERSECT/EXCEPT of SELECTs)
  2. no write, DDL, admin or file commands anywhere in the tree (INSERT, DROP, COPY, ATTACH...)
  3. every table is in the allowlist (Gold tables only); CTE names are allowed
  4. no file-reading table functions (read_csv, read_parquet, glob, ...) or getenv
  5. no PII columns, and no SELECT * on a table that has PII columns
  6. a row limit: LIMIT is added, or lowered to the maximum

The engine adds a second layer (read-only connection, external file access disabled),
so one bug in the guard is not enough to leak or change data.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

FORBIDDEN_NODES = tuple(
    getattr(exp, name)
    for name in [
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "Command",
        "TruncateTable", "Copy", "Pragma", "Set", "Use", "Grant", "Revoke", "LoadData",
        "Transaction", "Commit", "Rollback", "Attach", "Detach", "Install", "Into", "Kill",
        "Analyze", "Export",
    ]
    if hasattr(exp, name)
)  # fmt: skip

BLOCKED_FUNCTION_PREFIXES = ("read_", "scan_", "parquet_", "csv_", "json_scan", "sniff_")
BLOCKED_FUNCTIONS = {"glob", "getenv", "query", "query_table", "load_extension", "system"}


class UnsafeSQLError(ValueError):
    """Raised when a query is not allowed. The message is safe to show to the model."""


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str  # normalised SQL with the row limit applied
    tables: tuple[str, ...]  # schema.table names the query reads


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.sql_name().lower()


def _selects_star(select: exp.Select) -> bool:
    """True for `SELECT *` or `SELECT t.*`, but not for COUNT(*)."""
    return any(
        isinstance(p, exp.Star) or (isinstance(p, exp.Column) and isinstance(p.this, exp.Star))
        for p in select.expressions
    )


class SQLGuard:
    def __init__(
        self,
        allowed_tables: list[str],
        *,
        dialect: str = "duckdb",
        max_rows: int = 200,
        default_schema: str = "gold",
        pii_columns: set[str] | None = None,
        pii_tables: set[str] | None = None,
    ) -> None:
        self.allowed = {t.lower() for t in allowed_tables}
        self.dialect = dialect
        self.max_rows = max_rows
        self.default_schema = default_schema
        self.pii_columns = {c.lower() for c in (pii_columns or set())}
        self.pii_tables = {t.lower() for t in (pii_tables or set())}

    def validate(self, sql: str) -> ValidatedQuery:
        tree = self._parse_single_statement(sql)
        self._check_statement_type(tree)
        self._check_forbidden_nodes(tree)
        self._check_functions(tree)
        tables = self._check_tables(tree)
        self._check_pii(tree, tables)
        tree = self._apply_limit(tree)
        return ValidatedQuery(sql=tree.sql(dialect=self.dialect), tables=tuple(sorted(tables)))

    # -------------------------------------------------------------- checks

    def _parse_single_statement(self, sql: str) -> exp.Expression:
        if not sql or not sql.strip():
            raise UnsafeSQLError("Empty query.")
        try:
            statements = [s for s in sqlglot.parse(sql, read=self.dialect) if s is not None]
        except ParseError as e:
            raise UnsafeSQLError(f"Could not parse the SQL: {e.errors[0]['description']}") from e
        if len(statements) != 1:
            raise UnsafeSQLError("Only one SQL statement is allowed.")
        return statements[0]

    def _check_statement_type(self, tree: exp.Expression) -> None:
        if not isinstance(tree, (exp.Select, exp.SetOperation)):
            raise UnsafeSQLError(f"Only SELECT queries are allowed, got {tree.key.upper()}.")

    def _check_forbidden_nodes(self, tree: exp.Expression) -> None:
        bad = next(tree.find_all(*FORBIDDEN_NODES), None)
        if bad is not None:
            raise UnsafeSQLError(f"{bad.key.upper()} is not allowed; the copilot is read-only.")

    def _check_functions(self, tree: exp.Expression) -> None:
        for func in tree.find_all(exp.Func):
            name = _function_name(func)
            if name in BLOCKED_FUNCTIONS or name.startswith(BLOCKED_FUNCTION_PREFIXES):
                raise UnsafeSQLError(f"Function {name}() is not allowed.")

    def _check_tables(self, tree: exp.Expression) -> set[str]:
        cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        used: set[str] = set()
        for table in tree.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                raise UnsafeSQLError("Table functions are not allowed; query Gold tables only.")
            name = table.name.lower()
            schema = (table.db or "").lower()
            if not schema and name in cte_names:
                continue
            full = f"{schema or self.default_schema}.{name}"
            if full not in self.allowed:
                allowed = ", ".join(sorted(self.allowed))
                raise UnsafeSQLError(f"Table {full} is not allowed. Allowed tables: {allowed}.")
            # Normalise: always schema-qualified, never catalog-qualified (set by the engine).
            table.set("db", exp.to_identifier(schema or self.default_schema))
            table.set("catalog", None)
            used.add(full)
        return used

    def _check_pii(self, tree: exp.Expression, tables: set[str]) -> None:
        for column in tree.find_all(exp.Column):
            if column.name.lower() in self.pii_columns:
                raise UnsafeSQLError(f"Column {column.name} is personal data (PII) and is blocked.")
        if tables & self.pii_tables and any(_selects_star(s) for s in tree.find_all(exp.Select)):
            raise UnsafeSQLError(
                "SELECT * is not allowed on tables with personal data; list the columns you need."
            )

    def _apply_limit(self, tree: exp.Expression) -> exp.Expression:
        limit = tree.args.get("limit")
        if limit is not None:
            try:
                current = int(limit.expression.this)
            except (AttributeError, TypeError, ValueError):
                current = self.max_rows + 1  # not a plain number: replace it
            if current <= self.max_rows:
                return tree
        return tree.limit(self.max_rows, copy=False)
