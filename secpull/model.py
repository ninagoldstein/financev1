from dataclasses import dataclass

from secpull.models import FinancialFact


@dataclass(frozen=True)
class ModelAssumptions:
    base_growth: float
    bear_growth: float
    bull_growth: float
    offset: float
    n_years_used: int


def historical_revenue(facts: list[FinancialFact]) -> list[tuple[int, float]]:
    """Sorted (fiscal_year, value) pairs from annual revenue facts only.

    When the same fiscal year appears more than once (restated filings),
    the latest-filed value is kept.
    """
    fy_facts = [f for f in facts if f.fiscal_period == "FY" and f.metric == "revenue"]
    best: dict[int, FinancialFact] = {}
    for f in fy_facts:
        if f.fiscal_year not in best or f.filed_date > best[f.fiscal_year].filed_date:
            best[f.fiscal_year] = f
    return sorted((yr, f.value) for yr, f in best.items())


def yoy_growth_rates(
    rev_series: list[tuple[int, float]],
) -> list[tuple[int, float | None]]:
    """(year, growth_rate) for each year.

    First year is always None.  A gap in the calendar year sequence also
    yields None — never bridges missing years.
    """
    result: list[tuple[int, float | None]] = []
    for i, (yr, val) in enumerate(rev_series):
        if i == 0:
            result.append((yr, None))
        else:
            prev_yr, prev_val = rev_series[i - 1]
            if yr == prev_yr + 1 and prev_val != 0:
                result.append((yr, (val - prev_val) / prev_val))
            else:
                result.append((yr, None))
    return result


def avg_growth(rates: list[tuple[int, float | None]], n: int = 5) -> float:
    """Mean of the last n non-None growth rates.

    Raises ValueError when no non-None rates exist (insufficient history).
    """
    non_none = [r for _, r in rates if r is not None]
    if not non_none:
        raise ValueError(
            "Not enough consecutive annual revenue data to calculate a growth rate. "
            "At least 2 consecutive fiscal years are required."
        )
    return sum(non_none[-n:]) / len(non_none[-n:])


def build_assumptions(
    base: float,
    offset: float = 0.05,
    n_years_used: int = 0,
) -> ModelAssumptions:
    return ModelAssumptions(
        base_growth=base,
        bear_growth=base - offset,
        bull_growth=base + offset,
        offset=offset,
        n_years_used=n_years_used,
    )


def project_revenue(
    last_revenue: float,
    last_year: int,
    assumptions: ModelAssumptions,
    years: int = 3,
) -> dict[str, list[tuple[int, float]]]:
    """Three-scenario compounding revenue projection.

    Returns {'bear': [...], 'base': [...], 'bull': [...]} where each list
    contains (fiscal_year, projected_value) tuples.
    """
    result: dict[str, list[tuple[int, float]]] = {}
    for scenario, rate in (
        ("bear", assumptions.bear_growth),
        ("base", assumptions.base_growth),
        ("bull", assumptions.bull_growth),
    ):
        proj: list[tuple[int, float]] = []
        prev = last_revenue
        for i in range(1, years + 1):
            value = prev * (1 + rate)
            proj.append((last_year + i, value))
            prev = value
        result[scenario] = proj
    return result
