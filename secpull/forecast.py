"""
Five-year projected financial statements: bear / base / bull scenarios.

Each scenario projects the same assumption set at a different revenue growth
rate.  All other assumptions (margins, D&A %, capex %, NWC %, tax rate) are
held constant across scenarios — scenario analysis is purely a growth-rate
sensitivity.

Formula reference (see Phase G design doc):
  Revenue_n    = Revenue_{n-1} × (1 + growth)
  Gross Profit = Revenue × gross_margin          (None when gross_margin is None)
  EBIT         = Revenue × ebit_margin
  D&A          = Revenue × da_pct_revenue
  EBITDA       = EBIT + D&A
  Capex        = Revenue × capex_pct_revenue
  ΔNWC         = nwc_pct_revenue × (Revenue_n − Revenue_{n-1})
  FCFF         = EBIT × (1 − tax) + D&A − Capex − ΔNWC   [unlevered]

Interest / EBT path (gated on interest_rate_on_debt):
  When None  → interest_expense = ebt = tax_expense = None
               net_income = EBIT × (1 − tax_rate)
  When set   → interest_expense = last_historical_LTD × rate  (held flat)
               EBT         = EBIT − interest_expense
               tax_expense = max(0, EBT) × tax_rate
               net_income  = EBT − tax_expense

FCFF always uses NOPAT = EBIT × (1 − tax), independent of the interest path.
"""
from __future__ import annotations

from dataclasses import dataclass

from secpull.assumptions import ForecastAssumptions
from secpull.profile import CompanyProfile
from secpull.quality import QualityIssue


# ── Per-year projection ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ForecastYear:
    year: int                       # calendar year (base_year + n)
    revenue: float
    gross_profit: float | None      # None when gross_margin not available
    ebit: float
    da: float
    ebitda: float
    interest_expense: float | None  # None when interest_rate_on_debt not set
    ebt: float | None
    tax_expense: float | None
    net_income: float               # always present; uses NOPAT path when no interest
    capex: float
    delta_nwc: float                # positive = cash outflow (WC investment)
    fcff: float                     # unlevered free cash flow to firm


# ── Scenario container ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScenarioForecast:
    scenario: str                   # "bear" | "base" | "bull"
    growth_rate: float
    years: tuple[ForecastYear, ...]


# ── Full three-scenario output ────────────────────────────────────────────────


@dataclass(frozen=True)
class ProjectedStatements:
    ticker: str
    base_year: int                  # last historical FY; projection starts base_year + 1
    base_revenue: float             # last historical revenue used as Year 0
    bear: ScenarioForecast
    base: ScenarioForecast
    bull: ScenarioForecast
    quality_issues: tuple[QualityIssue, ...]


# ── Internal projection engine ────────────────────────────────────────────────


def _project_scenario(
    scenario: str,
    growth_rate: float,
    base_revenue: float,
    base_year: int,
    asmp: ForecastAssumptions,
    interest_expense_flat: float | None,
) -> ScenarioForecast:
    fy_list: list[ForecastYear] = []
    prior_rev = base_revenue

    for n in range(1, asmp.n_projection_years + 1):
        rev = prior_rev * (1.0 + growth_rate)

        # Income statement
        gross_profit = rev * asmp.gross_margin if asmp.gross_margin is not None else None
        ebit = rev * asmp.ebit_margin
        da = rev * asmp.da_pct_revenue
        ebitda = ebit + da

        # Interest / EBT path
        if interest_expense_flat is not None:
            ie: float | None = interest_expense_flat
            ebt: float | None = ebit - interest_expense_flat
            tax: float | None = max(0.0, ebt) * asmp.effective_tax_rate
            net_income = ebt - tax
        else:
            ie = None
            ebt = None
            tax = None
            net_income = ebit * (1.0 - asmp.effective_tax_rate)

        # Reinvestment
        capex = rev * asmp.capex_pct_revenue
        delta_nwc = asmp.nwc_pct_revenue * (rev - prior_rev)

        # FCFF (unlevered — always uses NOPAT regardless of interest path)
        nopat = ebit * (1.0 - asmp.effective_tax_rate)
        fcff = nopat + da - capex - delta_nwc

        fy_list.append(ForecastYear(
            year=base_year + n,
            revenue=rev,
            gross_profit=gross_profit,
            ebit=ebit,
            da=da,
            ebitda=ebitda,
            interest_expense=ie,
            ebt=ebt,
            tax_expense=tax,
            net_income=net_income,
            capex=capex,
            delta_nwc=delta_nwc,
            fcff=fcff,
        ))
        prior_rev = rev

    return ScenarioForecast(
        scenario=scenario,
        growth_rate=growth_rate,
        years=tuple(fy_list),
    )


# ── Public builder ────────────────────────────────────────────────────────────


def _last_clean_value(line, years: list[int]) -> float | None:
    """Return the most recent non-UNRELIABLE value from a StatementLine."""
    for yr in sorted(years, reverse=True):
        pt = line.values.get(yr)
        if pt is not None and pt.value is not None and pt.coverage_quality != "UNRELIABLE":
            return pt.value
    return None


def build_forecast(
    assumptions: ForecastAssumptions,
    profile: CompanyProfile,
) -> ProjectedStatements:
    """Build bear/base/bull projected statements from assumptions + profile.

    The profile supplies two inputs that cannot come from assumptions:
      1. base_revenue  — last historical revenue (Year 0 anchor)
      2. last LTD      — needed to compute flat interest expense when
                         interest_rate_on_debt is set

    Raises ValueError if no revenue data is found in the profile.
    """
    is_ = profile.statements.income_statement
    bs = profile.statements.balance_sheet

    # ── Base revenue anchor ───────────────────────────────────────────────────
    base_year = max(profile.years)
    base_revenue = _last_clean_value(is_.revenue, profile.years)
    if base_revenue is None:
        raise ValueError(
            f"Cannot build forecast for {profile.ticker}: no revenue data in profile"
        )

    # ── Flat interest expense (held constant; no BS rollforward) ─────────────
    interest_expense_flat: float | None = None
    if assumptions.interest_rate_on_debt is not None:
        last_ltd = _last_clean_value(bs.long_term_debt, profile.years)
        if last_ltd is not None:
            interest_expense_flat = last_ltd * assumptions.interest_rate_on_debt
        # If last_ltd is None/UNRELIABLE, silently fall back to no-interest path.

    # ── Project all three scenarios ───────────────────────────────────────────
    scenario_map = {
        "bear": assumptions.bear_revenue_growth,
        "base": assumptions.base_revenue_growth,
        "bull": assumptions.bull_revenue_growth,
    }
    projected = {
        name: _project_scenario(
            name, rate, base_revenue, base_year, assumptions, interest_expense_flat
        )
        for name, rate in scenario_map.items()
    }

    return ProjectedStatements(
        ticker=profile.ticker,
        base_year=base_year,
        base_revenue=base_revenue,
        bear=projected["bear"],
        base=projected["base"],
        bull=projected["bull"],
        quality_issues=assumptions.quality_issues,
    )
