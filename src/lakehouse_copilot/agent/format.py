"""Format numbers the Indian way: 1,27,33,616.50 and 12.73 crore."""

from __future__ import annotations

MONEY_METRICS = {
    "net_revenue",
    "gross_merchandise_value",
    "average_order_value",
    "gross_margin",
    "discount_amount",
}
PERCENT_METRICS = {"gross_margin_pct", "average_discount_pct", "cancellation_rate", "return_rate"}


def indian_grouping(value: float, decimals: int = 2) -> str:
    """12733616.5 -> '1,27,33,616.50' (groups of 3, then 2)."""
    sign = "-" if value < 0 else ""
    whole, _, frac = f"{abs(value):.{decimals}f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    text = ",".join([*groups, tail]) if groups else tail
    return sign + text + (f".{frac}" if decimals else "")


def short_inr(value: float) -> str:
    if abs(value) >= 1e7:
        return f"₹{value / 1e7:.2f} crore"
    if abs(value) >= 1e5:
        return f"₹{value / 1e5:.2f} lakh"
    return f"₹{indian_grouping(value)}"


def format_number(value, metric: str | None = None) -> str:
    if value is None:
        return "n/a"
    if not isinstance(value, (int, float)):
        return str(value)
    if metric in MONEY_METRICS:
        full = f"₹{indian_grouping(value)}"
        return full if abs(value) < 1e5 else f"{short_inr(value)} ({full})"
    if metric in PERCENT_METRICS:
        return f"{value:.2f}%"
    if float(value).is_integer():
        return indian_grouping(value, 0)
    return indian_grouping(value)


def format_answer_table(
    columns: list[str], rows: list[list], metric_columns: set[str], max_rows: int = 15
) -> str:
    """A small Markdown table for chat answers. Only metric columns get number formatting."""
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for row in rows[:max_rows]:
        cells = [
            format_number(v, c) if c in metric_columns else str(v)
            for v, c in zip(row, columns, strict=False)
        ]
        lines.append("| " + " | ".join(cells) + " |")
    if len(rows) > max_rows:
        lines.append(f"\n_{len(rows) - max_rows} more rows not shown._")
    return "\n".join(lines)
