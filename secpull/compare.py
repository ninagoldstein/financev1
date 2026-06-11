from secpull.models import FinancialFact


def _fmt_value(value: float, unit: str) -> str:
    if unit == "USD/shares":
        return f"${value:.2f}"
    return f"${value / 1e6:,.0f}M"


def yoy_growth(facts: list[FinancialFact]) -> dict[int, float | None]:
    sorted_facts = sorted(
        (f for f in facts if f.fiscal_period == "FY"),
        key=lambda f: f.fiscal_year,
    )
    val_map = {f.fiscal_year: f.value for f in sorted_facts}
    result: dict[int, float | None] = {}
    for f in sorted_facts:
        yr = f.fiscal_year
        prior = val_map.get(yr - 1)
        result[yr] = None if prior is None else (f.value - prior) / prior
    return result


def comparison_rows(
    per_ticker: dict[str, list[FinancialFact]],
    metric: str,
) -> tuple[list[str], list[list[str]]]:
    all_years = sorted({f.fiscal_year for facts in per_ticker.values() for f in facts})
    headers = ["Ticker"] + [f"FY{y}" for y in all_years]

    rows: list[list[str]] = []
    for ticker, facts in per_ticker.items():
        val_map = {f.fiscal_year: (f.value, f.unit) for f in facts}
        growth = yoy_growth(facts)

        value_row = [ticker]
        yoy_row = [f"{ticker}  YoY"]

        for year in all_years:
            if year in val_map:
                v, unit = val_map[year]
                value_row.append(_fmt_value(v, unit))
            else:
                value_row.append("N/A")

            g = growth.get(year)
            if g is None:
                yoy_row.append("N/A")
            else:
                sign = "+" if g >= 0 else ""
                yoy_row.append(f"{sign}{g * 100:.1f}%")

        rows.extend([value_row, yoy_row])

    return headers, rows
