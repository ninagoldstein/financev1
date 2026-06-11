from dataclasses import dataclass

from secpull.models import FinancialFact


@dataclass(frozen=True)
class MarginAssumptions:
    gross_margin: float | None
    operating_margin: float | None
    net_margin: float | None
    diluted_shares_m: float | None  # in millions
    n_years_used: int


def historical_metrics(facts: list[FinancialFact], metric: str) -> dict[int, float]:
    """Returns {fiscal_year: value} for FY annual facts of the given metric.

    When the same fiscal year appears more than once, the latest-filed value wins.
    """
    fy_facts = [f for f in facts if f.fiscal_period == "FY" and f.metric == metric]
    best: dict[int, FinancialFact] = {}
    for f in fy_facts:
        if f.fiscal_year not in best or f.filed_date > best[f.fiscal_year].filed_date:
            best[f.fiscal_year] = f
    return {yr: f.value for yr, f in best.items()}


def avg_margin(values: list[float | None], n: int = 5) -> float | None:
    """Mean of the last n non-None values. Returns None when no values exist."""
    non_none = [v for v in values if v is not None]
    if not non_none:
        return None
    last_n = non_none[-n:]
    return sum(last_n) / len(last_n)


def implied_shares_m(net_income: float, eps: float) -> float | None:
    """Implied diluted share count in millions from net income (raw $) and EPS."""
    if eps == 0:
        return None
    return (net_income / eps) / 1e6


def build_margin_assumptions(
    facts: list[FinancialFact],
    revenue_series: list[tuple[int, float]],
    n: int = 5,
) -> MarginAssumptions:
    """Compute historical average margins and most-recent implied share count."""
    rev_map = dict(revenue_series)
    gross_map = historical_metrics(facts, "gross_profit")
    op_map = historical_metrics(facts, "operating_income")
    net_map = historical_metrics(facts, "net_income")
    eps_map = historical_metrics(facts, "eps_diluted")

    years = [yr for yr, _ in revenue_series]

    def _margin_series(num_map: dict[int, float]) -> list[float | None]:
        return [
            num_map[yr] / rev_map[yr]
            if yr in num_map and rev_map.get(yr, 0) != 0
            else None
            for yr in years
        ]

    # Most recent year where both net_income and eps are available
    shares_m = None
    for yr in reversed(years):
        if yr in net_map and yr in eps_map and eps_map[yr] != 0:
            shares_m = implied_shares_m(net_map[yr], eps_map[yr])
            break

    return MarginAssumptions(
        gross_margin=avg_margin(_margin_series(gross_map), n),
        operating_margin=avg_margin(_margin_series(op_map), n),
        net_margin=avg_margin(_margin_series(net_map), n),
        diluted_shares_m=shares_m,
        n_years_used=min(n, len(years)),
    )


def project_income_statement(
    revenue_projections: dict[str, list[tuple[int, float]]],
    margin_assumptions: MarginAssumptions,
) -> dict[str, dict[str, list[tuple[int, float | None]]]]:
    """Projects all income statement line items from revenue and margin assumptions.

    Returns {scenario: {metric: [(year, value|None), ...]}} where value is
    None when the corresponding margin assumption is None.
    """
    result: dict[str, dict[str, list[tuple[int, float | None]]]] = {}
    gm = margin_assumptions.gross_margin
    om = margin_assumptions.operating_margin
    nm = margin_assumptions.net_margin
    sm = margin_assumptions.diluted_shares_m

    for scenario, rev_list in revenue_projections.items():
        gross: list[tuple[int, float | None]] = []
        op: list[tuple[int, float | None]] = []
        net: list[tuple[int, float | None]] = []
        eps: list[tuple[int, float | None]] = []

        for yr, rev in rev_list:
            gp = rev * gm if gm is not None else None
            oi = rev * om if om is not None else None
            ni = rev * nm if nm is not None else None
            ep = (ni / (sm * 1e6)) if (ni is not None and sm is not None and sm != 0) else None

            gross.append((yr, gp))
            op.append((yr, oi))
            net.append((yr, ni))
            eps.append((yr, ep))

        result[scenario] = {
            "revenue": list(rev_list),
            "gross_profit": gross,
            "operating_income": op,
            "net_income": net,
            "eps_diluted": eps,
        }

    return result
