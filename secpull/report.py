from secpull.models import FinancialFact

DISPLAY_ORDER = [
    "revenue", "gross_profit", "operating_income", "net_income",
    "eps_diluted", "total_assets", "cash",
]


def _period_label(fact: FinancialFact) -> str:
    if fact.fiscal_period == "FY":
        return f"FY{fact.fiscal_year} (end {fact.end_date})"
    return f"{fact.fiscal_year} {fact.fiscal_period}"


def _format_value(value: float, unit: str) -> str:
    if unit == "USD/shares":
        return f"${value:.2f}"
    return f"${value / 1e6:,.0f}M"


def build_grid(
    facts: list[FinancialFact],
    periods: str = "FY",
) -> tuple[list[str], list[list[str]]]:
    if periods == "FY":
        filtered = [f for f in facts if f.fiscal_period == "FY"]
    else:
        filtered = [f for f in facts if f.fiscal_period in ("Q1", "Q2", "Q3", "Q4")]

    # Collect unique period labels, keyed to their end_date for sorting
    period_end: dict[str, str] = {}
    for f in filtered:
        period_end[_period_label(f)] = f.end_date

    sorted_labels = sorted(period_end, key=lambda lbl: period_end[lbl])

    # Value lookup: (metric, label) → formatted string
    lookup: dict[tuple[str, str], str] = {}
    for f in filtered:
        lookup[(f.metric, _period_label(f))] = _format_value(f.value, f.unit)

    rows = []
    for metric in DISPLAY_ORDER:
        if not any((metric, lbl) in lookup for lbl in sorted_labels):
            continue
        row = [metric] + [lookup.get((metric, lbl), "N/A") for lbl in sorted_labels]
        rows.append(row)

    return ["Metric"] + sorted_labels, rows


def find_missing(
    facts: list[FinancialFact],
    periods: str = "FY",
) -> list[tuple[str, str]]:
    headers, rows = build_grid(facts, periods)
    period_labels = headers[1:]
    missing = []
    for row in rows:
        metric = row[0]
        for i, lbl in enumerate(period_labels):
            if row[i + 1] == "N/A":
                missing.append((metric, lbl))
    return sorted(missing)


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    all_rows = [headers] + rows
    col_widths = [
        max(len(str(r[i])) for r in all_rows)
        for i in range(len(headers))
    ]
    sep = "  ".join("-" * w for w in col_widths)
    lines = []
    for idx, row in enumerate(all_rows):
        cells = []
        for i, cell in enumerate(row):
            # Right-align value columns, left-align the metric name
            cells.append(str(cell).rjust(col_widths[i]) if i > 0 else str(cell).ljust(col_widths[i]))
        lines.append("  ".join(cells))
        if idx == 0:
            lines.append(sep)
    return "\n".join(lines)
